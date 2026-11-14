# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""Confirmations, staff alerts for blocked attempts, the audit log line, /players and the update report."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from discord import app_commands

from portcullis.bot.auditlog import audit_line
from portcullis.bot.base import AUDIT_CHANNEL, use_scrubber
from portcullis.bot.guard import (
    ALERT_WINDOW,
    CONFIRM_KEY,
    DENIED_ALERTS,
    STAFF_CHANNEL,
    STAFF_ROLE,
    Confirm,
    StaffAlerts,
    action_label,
)
from portcullis.bot.players import VISIBILITY_KEY, names_text
from portcullis.bot.updates import follow_update, update_error
from portcullis.core.types import Player, PowerAction, ServerId, ServerState, ServerStatus
from portcullis.db.repos import AuditEntry
from tests.fakes.discord import FakeChannel, FakeInteraction, FakeMember, FakeRole, press_yes
from tests.test_bot import (  # noqa: F401
    CREATIVE,
    OPERATOR_ROLE,
    SECRET,
    SURVIVAL,
    Harness,
    admin,
    db,
    h,
    member,
    operator,
    owner,
)

STAFF, AUDIT = 700, 701
STAFF_PING = 800


def channel(h: Harness, cid: int) -> FakeChannel:
    found = FakeChannel(cid)
    h.client.channels[cid] = found
    h.client.cached.add(cid)
    return found


# ------------------------------------------------------------------------------ confirmations


async def test_every_power_action_names_server_and_action(h: Harness) -> None:
    for action, title in (("start", "Start Creative?"), ("stop", "Stop Creative?"), ("restart", "Restart Creative?"),
                          ("update", "Update Creative?")):
        i = FakeInteraction(operator())
        await h.m.server.server_power(i, "Creative", action)
        fields = {f.name: f.value for f in i.embed.fields}
        assert i.embed.title == title and fields["Server"] == "Creative" and "Expires" in fields
        assert i.replies[-1]["ephemeral"] is True
        assert i.replies[-1]["view"].timeout == 60
    assert h.port.power_calls == []


async def test_only_the_asker_can_answer(h: Harness) -> None:
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Creative", "start")
    view = i.replies[-1]["view"]
    stranger = FakeInteraction(owner())  # even an owner can't answer someone else's question
    assert await view.interaction_check(stranger) is False
    assert "Only the person who asked" in stranger.text
    assert await view.interaction_check(FakeInteraction(operator())) is True


async def test_question_times_out_and_double_presses_do_nothing(h: Harness) -> None:
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Creative", "start")
    view: Confirm = i.replies[-1]["view"]
    await view.on_timeout()
    assert i.edits[-1]["content"] == "Timed out. Nothing was changed." and i.edits[-1]["view"] is None

    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Creative", "start")
    view = i.replies[-1]["view"]
    await press_yes(i)
    late = FakeInteraction(operator())
    await view.children[0].callback(late)
    assert "already answered" in late.text and h.port.power_calls == [(CREATIVE, PowerAction.START)]
    edits = len(i.edits)
    await view.on_timeout()  # answered: the question isn't touched again
    assert len(i.edits) == edits
    cancel = FakeInteraction(operator())
    await h.m.server.server_power(cancel, "Creative", "stop")
    no = FakeInteraction(operator())
    await cancel.replies[-1]["view"].children[1].callback(no)
    assert no.edits[-1]["content"] == "Cancelled. Nothing was changed." and len(h.port.power_calls) == 1


async def test_permission_is_checked_again_on_confirm(h: Harness) -> None:
    user = FakeMember(31, roles=[FakeRole(OPERATOR_ROLE)])
    i = FakeInteraction(user)
    await h.m.server.server_power(i, "Creative", "start")
    user.roles.clear()  # the role was taken away while the question was open
    press = await press_yes(i)
    assert "operator role" in press.text and h.port.power_calls == []


async def test_start_can_skip_the_question(h: Harness) -> None:
    await h.ctx.settings.set(CONFIRM_KEY, "down")
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Creative", "start")
    assert h.port.power_calls == [(CREATIVE, PowerAction.START)]
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Creative", "stop")
    assert i.embed.title == "Stop Creative?" and len(h.port.power_calls) == 1


# ------------------------------------------------------------------------------ staff alerts


async def test_blocked_attempts_reach_staff_but_not_the_member(h: Harness) -> None:
    staff = channel(h, STAFF)
    await h.ctx.settings.set(STAFF_CHANNEL, STAFF)
    await h.ctx.settings.set(STAFF_ROLE, STAFF_PING)
    i = FakeInteraction(member())
    await h.m.server.server_power(i, "Survival", "stop")
    assert i.text == "You need an operator role for that."  # nothing about staff or the audit log
    await asyncio.gather(*h.m.staff_alerts.tasks)
    [sent] = staff.sent
    fields = {f.name: f.value for f in sent["embed"].fields}
    assert sent["content"] == f"<@&{STAFF_PING}>"
    assert [r.id for r in sent["allowed_mentions"].roles] == [STAFF_PING] and not sent["allowed_mentions"].everyone
    assert fields == {"Member": "<@20> (`20`)", "Action": "Stop", "Server": "Survival"}


async def test_staff_alerts_are_rate_limited_and_counted() -> None:
    posted: list[tuple[AuditEntry, int]] = []
    now = [0.0]

    class Probe(StaffAlerts):
        async def post(self, entry: AuditEntry, earlier: int = 0) -> bool:
            posted.append((entry, earlier))
            return True

    alerts = Probe(SimpleNamespace(), clock=lambda: now[0])  # type: ignore[arg-type]

    def entry(action: str, result: str = "denied") -> AuditEntry:
        return AuditEntry(1, "2026-11-14T00:00:00+00:00", 20, action, SURVIVAL, None, result, None)

    alerts(entry("server.power.stop"))
    alerts(entry("server.power.stop"))
    alerts(entry("server.power.stop"))
    alerts(entry("server.send"))  # not a gated action
    alerts(entry("server.power.start", result="ok"))
    now[0] = ALERT_WINDOW + 1
    alerts(entry("server.power.stop"))
    await asyncio.gather(*alerts.tasks)
    assert [(e.action, n) for e, n in posted] == [("server.power.stop", 0), ("server.power.stop", 2)]


async def test_staff_alerts_off_or_without_channel(h: Harness) -> None:
    staff = channel(h, STAFF)
    alerts = StaffAlerts(h.ctx)
    denied = AuditEntry(1, "x", 20, "backup.restore", SURVIVAL, None, "denied", None)
    assert await alerts.post(denied) is False  # no channel set
    await h.ctx.settings.set(STAFF_CHANNEL, STAFF)
    await h.ctx.settings.set(DENIED_ALERTS, False)
    assert await alerts.post(denied) is False and staff.sent == []
    await h.ctx.settings.set(DENIED_ALERTS, True)
    assert await alerts.post(denied, earlier=3) is True
    fields = {f.name: f.value for f in staff.sent[-1]["embed"].fields}
    assert fields["Action"] == "Restore a backup" and fields["Earlier tries"].startswith("3 more")


def test_action_labels() -> None:
    assert action_label("server.power.stop.all") == "Stop (every server)"
    assert action_label("server.power.restart.warned") == "Restart with a warning"
    assert action_label("server.power.update") == "Update"


# ------------------------------------------------------------------------------ audit log channel


def test_audit_line_shows_result_and_hides_secrets() -> None:
    use_scrubber(lambda text: text.replace("hunter2", "<redacted>"))
    try:
        entry = AuditEntry(1, "x", 30, "server.power.update", SURVIVAL, None, "failed",
                           {"error": "login hunter2 eyJabcdefgh.ijklmnop.qrs", "password": "hunter2",
                            "confirmed": True})
        line = audit_line(entry, "Survival")
    finally:
        use_scrubber(lambda text: text)
    assert line.startswith("❌ failed · `server.power.update` · **Survival** · by <@30> · confirmed")
    assert "hunter2" not in line and "eyJ" not in line
    assert audit_line(AuditEntry(2, "x", None, "backup.made", None, None, "ok", None), None) == \
        "✅ ok · `backup.made` · by Portcullis"


async def test_audit_channel_gets_denied_and_failed_lines(h: Harness) -> None:
    audit = channel(h, AUDIT)
    await h.ctx.settings.set(AUDIT_CHANNEL, AUDIT)

    async def broken(server: ServerId, action: PowerAction) -> None:
        raise RuntimeError("boom")

    h.port.power = broken  # type: ignore[method-assign]
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Creative", "start")
    await press_yes(i)
    await h.m.server.server_power(FakeInteraction(member()), "Creative", "start")
    await asyncio.gather(*h.m.audit_feed.tasks)
    lines = sorted(s["content"] for s in audit.sent)
    assert lines[0] == "⛔ denied · `server.power.start` · **Creative** · by <@20>"
    assert lines[1].startswith("❌ failed · `server.power.start` · **Creative** · by <@30>")


# ------------------------------------------------------------------------------ /players


async def test_players_names_counts_and_limits(h: Harness) -> None:
    h.port.join(ServerId(SURVIVAL), "Al_ice")
    h.port.join(ServerId(SURVIVAL), "Bob")
    i = FakeInteraction(member())
    await h.m.players.players(i)
    text = i.embed.description
    assert "**Survival** · 2 online\n　Al\\_ice, Bob" in text
    assert "**Creative** · Offline" in text and "Secret" not in text  # hidden from members

    async def status(server: ServerId) -> ServerStatus:
        return ServerStatus(id=server, state=ServerState.RUNNING, players=(), max_players=20)

    h.port.get_status = status  # type: ignore[method-assign]
    i = FakeInteraction(member())
    await h.m.players.players(i, "Survival")
    assert "0/20 online" in i.embed.description and "don't report players" in i.embed.description


async def test_players_privacy(h: Harness) -> None:
    h.port.join(ServerId(SURVIVAL), "Alice")
    await h.ctx.settings.set(VISIBILITY_KEY, "count", scope=SURVIVAL)
    i = FakeInteraction(member())
    await h.m.players.players(i, "Survival")
    assert "1 online" in i.embed.description and "Alice" not in i.embed.description
    i = FakeInteraction(operator())
    await h.m.players.players(i, "Survival")
    assert "Alice" in i.embed.description

    await h.ctx.settings.set(VISIBILITY_KEY, "staff")  # the default for servers without their own
    await h.ctx.settings.set(VISIBILITY_KEY, "staff", scope=SURVIVAL)
    i = FakeInteraction(member())
    await h.m.players.players(i)
    assert "for staff only" in i.text


async def test_hidden_server_players_need_access(h: Harness) -> None:
    h.port.join(ServerId(SECRET), "Eve")
    i = FakeInteraction(member())
    await h.m.players.players(i, "Secret")
    assert "I don't know a server called" in i.text


def test_names_are_cut_to_fit() -> None:
    text = names_text([f"player{n:03d}" for n in range(200)])
    assert len(text) < 1000 and text.endswith("more")


# ------------------------------------------------------------------------------ settings commands


async def test_setup_safety_and_players(h: Harness) -> None:
    role = SimpleNamespace(id=STAFF_PING, is_default=lambda: False)
    i = FakeInteraction(member())
    await h.m.safety.setup_safety(i, confirm="down")
    assert "Manage Server" in i.text and await h.ctx.settings.get(CONFIRM_KEY) is None
    i = FakeInteraction(admin())
    await h.m.safety.setup_safety(i, confirm="down", staff_channel=SimpleNamespace(id=STAFF),  # type: ignore[arg-type]
                                  staff_role=role)  # type: ignore[arg-type]
    fields = {f.name: f.value for f in i.embed.fields}
    assert fields["Blocked attempts go to"] == f"<#{STAFF}>" and fields["Ping"] == f"<@&{STAFF_PING}>"
    assert await h.ctx.settings.get(CONFIRM_KEY) == "down"
    everyone = SimpleNamespace(id=1, is_default=lambda: True)
    i = FakeInteraction(admin())
    await h.m.safety.setup_safety(i, staff_role=everyone)  # type: ignore[arg-type]
    assert "@everyone" in i.text and await h.ctx.settings.get(STAFF_ROLE) == STAFF_PING
    i = FakeInteraction(admin())
    await h.m.safety.setup_safety(i, clear=True)
    assert await h.ctx.settings.get(STAFF_CHANNEL) is None

    i = FakeInteraction(admin())
    await h.m.safety.setup_players(i, "count", "Survival")
    assert await h.ctx.settings.get(VISIBILITY_KEY, scope=SURVIVAL) == "count"


# ------------------------------------------------------------------------------ updates


async def test_update_result_is_reported(h: Harness) -> None:
    states = iter([ServerState.BUSY, ServerState.BUSY, ServerState.RUNNING])

    async def status(server: ServerId) -> ServerStatus:
        return ServerStatus(id=server, state=next(states), players=(Player("x"),))

    async def no_wait(seconds: float) -> None:
        pass

    h.port.get_status = status  # type: ignore[method-assign]
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None
    i = FakeInteraction(operator())
    await i.response.defer()
    await follow_update(h.ctx, i, row, sleep=no_wait)
    assert "The update of **Survival** finished" in i.text
    [entry] = await h.ctx.audit.query(action_prefix="server.power.update.finished")
    assert entry.detail == {"outcome": "ok"}

    async def failed(server: ServerId) -> ServerStatus:
        return ServerStatus(id=server, state=ServerState.FAILED, last_error="disk full")

    h.port.get_status = failed  # type: ignore[method-assign]
    i = FakeInteraction(operator())
    await i.response.defer()
    await follow_update(h.ctx, i, row, sleep=no_wait)
    assert "failed. AMP says: disk full" in i.text


def test_update_errors_in_plain_words() -> None:
    class Refused(Exception):
        status = 409

    assert "Steam" in update_error(Refused("shards.error.steamLoginRequired"))
    assert "suspended" in update_error(Refused("shards.error.suspended"))
    assert "busy" in update_error(Refused("other"))


def test_new_commands_register() -> None:
    import discord

    from portcullis.bot import players, safety, setup
    from portcullis.bot.base import Access
    from portcullis.bot.client import intents

    tree = app_commands.CommandTree(discord.Client(intents=intents()))
    ctx = SimpleNamespace(access=Access())
    for cls in (setup.SetupModule, players.PlayersModule, safety.SafetyModule):
        module = cls.__new__(cls)
        module.ctx = ctx  # type: ignore[assignment]
        module.register(tree)
    groups = {c.name: c for c in tree.get_commands()}
    assert {"safety", "players"} <= {c.name for c in groups["setup"].commands}  # type: ignore[union-attr]
    assert groups["setup"].default_permissions == discord.Permissions(manage_guild=True)
    assert "players" in groups and groups["players"].guild_only
