# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""The idea-list batch: access roles, server operations, alerts, joins, moderation, events, setup."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from portcullis.bot.admin import grant_covers
from portcullis.bot.alerts import BACKUP_DAYS, CPU_KEY, Alerts
from portcullis.bot.base import (
    ALERT_CHANNEL,
    ALERT_ROLE,
    AUDIT_CHANNEL,
    MOD_CHANNEL,
    ROLE_GRANTS,
    SERVER_OPERATORS,
    role_problem,
)
from portcullis.bot.events import TIMEZONE, parse_when
from portcullis.bot.joins import NAME_RULES, JoinWatch, parse_rules, rule_hit
from portcullis.bot.server import CONNECT_KEY, SAY_KEY, START_KEY, valid_address
from portcullis.bot.setup import AUTO_APPROVE_ROLE, WHITELIST_CHANNEL, WL_ADD
from portcullis.core.types import Backup, Player, PlayerJoined, PowerAction, ServerId, ServerState, StateChanged
from portcullis.db.ops import Event
from tests.fakes.discord import FakeChannel, FakeInteraction, FakeMember, FakeRole
from tests.test_bot import (  # noqa: F401
    CREATIVE,
    OWNER,
    REQUESTS,
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

NOW = datetime.now(UTC)
ALERTS, MODS, AUDIT, EVENTS = 400, 401, 402, 403
POWER_ROLE, SECRET_ROLE, LINKED_ROLE = 600, 601, 602


def add_channels(h: Harness, *ids: int) -> dict[int, FakeChannel]:
    out = {}
    for cid in ids:
        channel = FakeChannel(cid)
        h.client.channels[cid] = channel
        h.client.cached.add(cid)
        out[cid] = channel
    return out


def with_role(uid: int, *roles: int) -> FakeMember:
    return FakeMember(uid, roles=[FakeRole(r) for r in roles])


def state(sid: str, value: ServerState) -> StateChanged:
    return StateChanged(server=ServerId(sid), at=NOW, state=value)


# ------------------------------------------------------------------------------ access


async def test_role_grants_and_server_operators(h: Harness) -> None:
    await h.ctx.settings.set(ROLE_GRANTS, {"power": [POWER_ROLE]})
    await h.ctx.settings.set(SERVER_OPERATORS, [SECRET_ROLE], scope=SECRET)
    powerful = with_role(50, POWER_ROLE)
    i = FakeInteraction(powerful)
    await h.m.server.server_power(i, "Creative", "start")
    assert h.port.power_calls == [(CREATIVE, PowerAction.START)]
    i = FakeInteraction(powerful)
    await h.m.server.server_send(i, "Creative", "save")
    assert "operator role" in i.text  # power only, not console

    secret_op = with_role(51, SECRET_ROLE)
    i = FakeInteraction(secret_op)
    await h.m.server.server_send(i, "Secret", "save")
    assert h.port.sent == [(SECRET, "save")]
    i = FakeInteraction(secret_op)
    await h.m.server.server_send(i, "Creative", "save")
    assert "operator role" in i.text
    names = [c.name for c in await h.m.server.autocomplete_server(FakeInteraction(secret_op), "")]
    assert "Secret" in names
    assert await h.ctx.is_staff(secret_op) and not await h.ctx.is_staff(member())


async def test_only_owners_hand_out_powers(h: Harness) -> None:
    role = SimpleNamespace(id=POWER_ROLE, is_default=lambda: False)
    note = await h.m.setup.set_grant(FakeInteraction(admin()), "power", [role])
    assert "Only the bot's owners" in note and await h.ctx.settings.get(ROLE_GRANTS) is None
    everyone = SimpleNamespace(id=1, is_default=lambda: True)
    assert "@everyone" in await h.m.setup.set_grant(FakeInteraction(owner()), "power", [everyone])
    assert "Start, stop" in await h.m.setup.set_grant(FakeInteraction(owner()), "power", [role])
    assert await h.ctx.settings.get(ROLE_GRANTS) == {"power": [POWER_ROLE]}
    row = await h.ctx.servers.get(SECRET)
    assert row is not None
    assert "Only the bot's owners" in await h.m.setup.set_server_operators(FakeInteraction(admin()), row, [role])

    data = {"portcullis_export": 1, "servers": {},
            "settings": {"bot": {ROLE_GRANTS: {"power": [1]}}, SECRET: {SERVER_OPERATORS: [2]}}}
    _, written, problems = await h.m.admin.import_data(data, by=40)
    assert written == 0 and len(problems) == 2 and "only owners" in problems[0]
    _, written, _ = await h.m.admin.import_data(data, by=OWNER, owner=True)
    assert written == 2


def test_role_problem() -> None:
    from portcullis.bot.base import Access

    access = Access(operator_role_ids=frozenset({500}))
    perms = SimpleNamespace(administrator=False, manage_roles=False)
    top = 10

    class Role(SimpleNamespace):
        def __ge__(self, other: Role) -> bool:
            return bool(self.position >= other.position)

    def role(rid: int, *, default: bool = False, managed: bool = False, position: int = 1, **flags: bool) -> Role:
        return Role(id=rid, managed=managed, permissions=SimpleNamespace(**{**vars(perms), **flags}),
                    position=position, is_default=lambda: default)

    guild = SimpleNamespace(me=SimpleNamespace(top_role=role(9, position=top)))
    assert role_problem(role(1, default=True), guild, access, handed_out=False)
    assert role_problem(role(2), guild, access, handed_out=False) is None
    assert "moderator permissions" in (role_problem(role(3, administrator=True), guild, access, handed_out=True) or "")
    assert "operator role" in (role_problem(role(500), guild, access, handed_out=True) or "")
    assert "above Portcullis" in (role_problem(role(4, position=11), guild, access, handed_out=True) or "")
    assert "integration" in (role_problem(role(5, managed=True), guild, access, handed_out=True) or "")
    assert role_problem(role(6), guild, access, handed_out=True) is None


# ------------------------------------------------------------------------------ server operations


async def test_members_can_start_but_not_stop(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.server.server_power(i, "Creative", "start")
    assert "operator role" in i.text
    await h.ctx.settings.set(START_KEY, True, scope=CREATIVE)
    i = FakeInteraction(member())
    await h.m.server.server_status(i, "Creative")
    assert [b.label for b in i.replies[-1]["view"].children] == ["Start"]
    i = FakeInteraction(member())
    await h.m.server.server_power(i, "Creative", "start")
    assert h.port.power_calls == [(CREATIVE, PowerAction.START)]
    [entry] = [e for e in await h.ctx.audit.query() if e.result == "ok"]
    assert entry.detail == {"member_start": True}
    h.port.servers[ServerId(CREATIVE)].state = ServerState.STOPPED
    i = FakeInteraction(member(21))
    await h.m.server.server_power(i, "Creative", "start")
    assert "a moment ago" in i.text and len(h.port.power_calls) == 1
    i = FakeInteraction(member())
    await h.m.server.server_power(i, "Creative", "stop")
    assert "operator role" in i.text


async def test_stop_without_players_needs_no_confirmation(h: Harness) -> None:
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Survival", "stop")  # running, nobody on
    assert h.port.power_calls == [(SURVIVAL, PowerAction.STOP)]
    assert h.ctx.offline_expected(SURVIVAL)


async def test_restart_with_warning(h: Harness) -> None:
    waits: list[float] = []

    async def no_wait(seconds: float) -> None:
        waits.append(seconds)

    h.m.server.sleep = no_wait
    h.port.join(ServerId(SURVIVAL), "Alice")
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Survival", "restart_warned")
    assert "no chat command" in i.text and h.port.power_calls == []

    await h.ctx.settings.set(SAY_KEY, "say {message}", scope=SURVIVAL)
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Survival", "restart_warned")
    assert "Restarting **Survival** in 5 minutes" in i.text
    await h.m.server.restarts[SURVIVAL]
    assert h.port.sent == [(SURVIVAL, "say Server restart in 5 minutes."),
                           (SURVIVAL, "say Server restart in 1 minute.")]
    assert waits == [240, 60] and h.port.power_calls == [(SURVIVAL, PowerAction.RESTART)]
    assert "Restarted **Survival** after warning players" in i.edits[-1]["content"]


async def test_restart_with_warning_can_be_cancelled(h: Harness) -> None:
    import asyncio

    gate = asyncio.Event()

    async def wait(seconds: float) -> None:
        await gate.wait()

    h.m.server.sleep = wait
    h.port.join(ServerId(SURVIVAL), "Alice")
    await h.ctx.settings.set(SAY_KEY, "say {message}", scope=SURVIVAL)
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Survival", "restart_warned")
    await asyncio.sleep(0)
    again = FakeInteraction(operator())
    await h.m.server.server_power(again, "Survival", "restart_warned")
    assert "already counting down" in again.text
    press = FakeInteraction(operator())
    await i.replies[-1]["view"].children[0].callback(press)
    assert "cancelled by <@30>" in press.edits[-1]["content"]
    assert h.port.sent[-1] == (SURVIVAL, "say The restart is cancelled.") and h.port.power_calls == []


async def test_power_all_with_tag(h: Harness) -> None:
    for sid in (CREATIVE, SECRET):
        server = h.port.servers[ServerId(sid)]
        server.info = replace(server.info, tags=("Event",))
    await h.ctx.servers.sync(await h.port.list_servers())
    await h.ctx.live.seed([await h.port.get_status(ServerId(s)) for s in (SURVIVAL, CREATIVE, SECRET)],
                          [SURVIVAL, CREATIVE, SECRET])
    i = FakeInteraction(member())
    await h.m.server.power_all(i, "start")
    assert "allowed to" in i.text
    i = FakeInteraction(operator())
    await h.m.server.power_all(i, "start", tag="event")
    assert "Start 2 server(s): **Creative**, **Secret**?" in i.text and h.port.power_calls == []
    confirm = FakeInteraction(operator())
    await i.replies[-1]["view"].children[0].callback(confirm)
    assert sorted(h.port.power_calls) == sorted([(CREATIVE, PowerAction.START), (SECRET, PowerAction.START)])
    assert "Starting **Creative**, **Secret**" in confirm.text


async def test_list_with_players_and_address(h: Harness) -> None:
    h.port.join(ServerId(SURVIVAL), "Alice")
    h.port.join(ServerId(SURVIVAL), "B_ob")
    i = FakeInteraction(member())
    await h.m.server.server_list(i, players=True)
    assert r"Alice, B\_ob" in i.embed.description
    assert valid_address("play.example.com:27015") and not valid_address("a b") and not valid_address("x;y")
    await h.ctx.settings.set(CONNECT_KEY, "play.example.com:27015", scope=SURVIVAL)
    i = FakeInteraction(member())
    await h.m.server.server_status(i, "Survival")
    assert {f.name: f.value for f in i.embed.fields}["Connect"] == "```\nplay.example.com:27015\n```"


# ------------------------------------------------------------------------------ alerts


async def test_offline_and_crash_alerts(h: Harness) -> None:
    channels = add_channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    await h.ctx.settings.set(ALERT_ROLE, 700)
    alerts = Alerts(h.ctx)
    await alerts(state(SURVIVAL, ServerState.RUNNING))  # first state: nothing to compare with
    await alerts(state(SURVIVAL, ServerState.STOPPED))
    sent = channels[ALERTS].sent
    assert len(sent) == 1 and sent[0]["content"].startswith("<@&700> 🔴 **Survival** went offline")
    assert [r.id for r in sent[0]["allowed_mentions"].roles] == [700] and not sent[0]["allowed_mentions"].everyone

    await h.ctx.settings.set(ALERT_ROLE, 1)  # the guild's own id is @everyone: never pinged
    await alerts(state(SECRET, ServerState.RUNNING))
    await alerts(state(SECRET, ServerState.FAILED))
    assert not sent[-1]["content"].startswith("<@&") and sent[-1]["allowed_mentions"].roles is False
    await h.ctx.settings.set(ALERT_ROLE, "not an id")
    await alerts(state(SECRET, ServerState.RUNNING))
    await alerts(state(SECRET, ServerState.STOPPED))
    assert "Secret" in sent[-1]["content"]

    h.ctx.expect_offline(CREATIVE)
    before = len(sent)
    await alerts(state(CREATIVE, ServerState.RUNNING))
    await alerts(state(CREATIVE, ServerState.STOPPED))
    assert len(sent) == before  # Portcullis stopped it
    await alerts(state(CREATIVE, ServerState.FAILED))
    assert "crashed" in sent[-1]["content"]


async def test_unreachable_alert_waits_and_recovers(h: Harness) -> None:
    channels = add_channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    alerts = Alerts(h.ctx, unreachable_after=0)
    await alerts.reachability(True)
    assert channels[ALERTS].sent == []
    await alerts.reachability(False)
    await alerts.reachability(False)
    assert len(channels[ALERTS].sent) == 1 and "can't reach AMP" in channels[ALERTS].sent[0]["content"]
    await alerts.reachability(True)
    assert "can reach AMP again" in channels[ALERTS].sent[-1]["content"]

    slow = Alerts(h.ctx)
    await slow.reachability(False)
    assert len(channels[ALERTS].sent) == 2  # not yet: three minutes have not passed


async def test_load_and_backup_alerts(h: Harness) -> None:
    channels = add_channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    await h.ctx.settings.set(CPU_KEY, 90, scope=SURVIVAL)
    await h.ctx.live.seed([await h.port.get_status(ServerId(SURVIVAL))], [SURVIVAL])
    real = h.port.get_status

    async def busy(server: ServerId):  # noqa: ANN202
        return replace(await real(server), cpu_percent=97.0)

    h.port.get_status = busy  # type: ignore[method-assign]
    alerts = Alerts(h.ctx, sustain=0)
    await alerts.check_load()
    assert "CPU at 97%" in channels[ALERTS].sent[-1]["content"]
    h.port.get_status = real  # type: ignore[method-assign]
    await alerts.check_load()
    assert "back under its load limits" in channels[ALERTS].sent[-1]["content"]

    await h.ctx.settings.set(BACKUP_DAYS, 3)
    h.port.servers[ServerId(SURVIVAL)].backups = [Backup("b", "Old", NOW - timedelta(days=5))]
    h.port.servers[ServerId(CREATIVE)].backups = [Backup("c", "New", NOW - timedelta(hours=2))]
    before = len(channels[ALERTS].sent)
    await alerts.check_backups(NOW)
    text = channels[ALERTS].sent[-1]["content"]
    assert "**Survival**: newest backup" in text and "Creative" not in text and "Secret" not in text
    await alerts.check_backups(NOW + timedelta(hours=1))
    assert len(channels[ALERTS].sent) == before + 1  # once a day


async def test_audit_channel(h: Harness) -> None:
    import asyncio

    channels = add_channels(h, AUDIT)
    await h.ctx.settings.set(AUDIT_CHANNEL, AUDIT)
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Creative", "start")
    await asyncio.gather(*h.m.audit_feed.tasks)
    assert channels[AUDIT].sent[-1]["content"] == "`server.power.start` by <@30> on **Creative**"
    await h.ctx.audit.record("link.verified", actor_id=1)
    assert not h.m.audit_feed.tasks  # member noise isn't copied


# ------------------------------------------------------------------------------ joins


def test_name_rules() -> None:
    rules = parse_rules("Hacker\n, admin \n\nhacker")
    assert rules == ["hacker", "admin"]
    assert rule_hit("xX_H4CK3R_Xx", rules) == "hacker"
    assert rule_hit("a.d.m.i.n", rules) == "admin"
    assert rule_hit("Alice", rules) is None


async def test_joins_watchlist_rules_and_welcome(h: Harness) -> None:
    channels = add_channels(h, MODS, EVENTS)
    await h.ctx.settings.set(MOD_CHANNEL, MODS)
    await h.ctx.settings.set(NAME_RULES, ["hacker"])
    await h.ctx.servers.update(SURVIVAL, event_channel=EVENTS)
    i = FakeInteraction(operator())
    await h.m.community.watch(i, "Griefer", "burned the spawn")
    assert "Watching **Griefer**" in i.text
    watch = JoinWatch(h.ctx)

    def joined(name: str) -> PlayerJoined:
        return PlayerJoined(server=ServerId(SURVIVAL), at=NOW, player=Player(name))

    await watch(joined("griefer"))
    await watch(joined("h4ck3r"))
    notes = [m["content"] for m in channels[MODS].sent]
    assert "**griefer** (on the watchlist) joined **Survival**. Note: burned the spawn" in notes[0]
    assert "name rule `hacker`" in notes[1]
    assert channels[EVENTS].sent == []  # no history yet, so no welcomes

    await h.ctx.players.joined(SURVIVAL, "Old", at=(NOW - timedelta(days=3)).isoformat(timespec="seconds"))
    await watch(joined("Old"))
    await watch(joined("Newbie"))
    assert [m["content"] for m in channels[EVENTS].sent] == [
        "👋 **Newbie** joined **Survival** for the first time."]

    i = FakeInteraction(operator())
    await h.m.community.watchlist(i)
    assert "Griefer" in i.embed.description
    i = FakeInteraction(member())
    await h.m.community.watch(i, "Alice")
    assert "operator role" in i.text


# ------------------------------------------------------------------------------ moderation and whitelist


async def test_kick_and_ban(h: Harness) -> None:
    i = FakeInteraction(operator())
    await h.m.community.moderate(i, "Survival", "Bob", "kick")
    assert "no kick command" in i.text
    await h.ctx.settings.set("kick", "kick {name} {reason}", scope=SURVIVAL)
    await h.ctx.settings.set("ban", "ban {name}", scope=SURVIVAL)
    i = FakeInteraction(operator())
    await h.m.community.moderate(i, "Survival", "Bob", "kick", "spawn camping")
    i = FakeInteraction(operator())
    await h.m.community.moderate(i, "Survival", "Bob", "kick")
    i = FakeInteraction(operator())
    await h.m.community.moderate(i, "Survival", "Bob", "ban", "x")
    assert h.port.sent == [(SURVIVAL, "kick Bob spawn camping"), (SURVIVAL, "kick Bob no reason given"),
                           (SURVIVAL, "ban Bob")]
    for name, reason in (("Bob; stop", None), ("Bob", "bye; stop"), ("Bob", "a" * 101)):
        i = FakeInteraction(operator())
        await h.m.community.moderate(i, "Survival", name, "kick", reason)
    assert len(h.port.sent) == 3
    i = FakeInteraction(member())
    await h.m.community.moderate(i, "Survival", "Bob", "ban")
    assert "operator role" in i.text and len(h.port.sent) == 3


async def test_whitelist_list_and_auto_approve(h: Harness) -> None:
    await h.ctx.settings.set(WL_ADD, "whitelist add {name}", scope=SURVIVAL)
    await h.ctx.settings.set("whitelist_remove", "whitelist remove {name}", scope=SURVIVAL)
    await h.ctx.settings.set(WHITELIST_CHANNEL, REQUESTS)
    i = FakeInteraction(operator())
    await h.m.community.whitelist_change(i, "Survival", "Carol", add=True)
    i = FakeInteraction(operator())
    await h.m.community.whitelist_list(i, "Survival")
    assert "**Carol**" in i.embed.description and "added by <@30>" in i.embed.description

    await h.ctx.settings.set(AUTO_APPROVE_ROLE, LINKED_ROLE)
    await h.ctx.users.add_link(60, "unturned", "Dave")
    i = FakeInteraction(with_role(60, LINKED_ROLE))
    await h.m.community.whitelist_request(i, "Survival")
    assert "straight away" in i.text and h.port.sent[-1] == (SURVIVAL, "whitelist add Dave")
    assert "Approved by Portcullis" in str(h.requests.sent[-1]["embed"].fields)
    assert "view" not in h.requests.sent[-1]
    i = FakeInteraction(with_role(61, LINKED_ROLE))  # role but no linked name: the moderators decide
    await h.m.community.whitelist_request(i, "Survival", "Eve")
    assert "Asked the moderators" in i.text

    i = FakeInteraction(operator())
    await h.m.community.whitelist_change(i, "Survival", "Carol", add=False)
    i = FakeInteraction(operator())
    await h.m.community.whitelist_list(i, "Survival")
    assert "Carol" not in i.embed.description and "**Dave** · <@60> · added by Portcullis" in i.embed.description


# ------------------------------------------------------------------------------ events


def test_parse_when() -> None:
    now = datetime(2026, 11, 7, 18, 0, tzinfo=UTC)
    ams = ZoneInfo("Europe/Amsterdam")  # UTC+1 in November
    assert parse_when("in 2h 30m", ams, now) == now + timedelta(hours=2, minutes=30)
    assert parse_when("20:00", ams, now) == datetime(2026, 11, 7, 19, 0, tzinfo=UTC)
    assert parse_when("18:30", ams, now) == datetime(2026, 11, 8, 17, 30, tzinfo=UTC)  # passed: tomorrow
    assert parse_when("tomorrow 20:00", ams, now) == datetime(2026, 11, 8, 19, 0, tzinfo=UTC)
    assert parse_when("2026-11-14 20:00", ams, now) == datetime(2026, 11, 14, 19, 0, tzinfo=UTC)
    for bad in ("yesterday", "25:00", "2025-01-01 10:00", "2028-01-01 10:00", "in 0m", "2026-02-30 10:00"):
        assert parse_when(bad, ams, now) is None, bad


async def test_event_create_rsvp_remind_cancel(h: Harness) -> None:
    channels = add_channels(h, 55)
    i = FakeInteraction(member())
    await h.m.events.create(i, "Game night", "in 2h")
    assert "operator role" in i.text
    i = FakeInteraction(operator())
    await h.m.events.create(i, "Game night", "whenever")
    assert "couldn't read that time" in i.text and "UTC" in i.text
    await h.ctx.settings.set(TIMEZONE, "Europe/Amsterdam")
    i = FakeInteraction(operator())
    await h.m.events.create(i, "Game night", "in 20m", "Bring snacks", "Survival")
    [event] = await h.ctx.events.upcoming()
    assert event.message_id == 4242 and i.embed.title == "Game night"

    for uid in (70, 71, 70):
        press = FakeInteraction(member(uid))
        await h.m.events.going(press, event.id)
    assert await h.ctx.events.going(event.id) == [71]
    assert "no longer going" in press.text

    assert await h.m.events.tick() == 1
    reminder = channels[55].sent[-1]
    assert "**Game night** starts" in reminder["content"] and "<@71>" in reminder["content"]
    assert [u.id for u in reminder["allowed_mentions"].users] == [71]
    assert await h.m.events.tick() == 0  # only once

    i = FakeInteraction(operator())
    await h.m.events.list_events(i)
    assert "Game night" in i.embed.description and "1 going" in i.embed.description
    press = FakeInteraction(member())
    await h.m.events.cancel(press, event.id)
    assert "operator role" in press.text
    press = FakeInteraction(operator())
    await h.m.events.cancel(press, event.id)
    assert press.edits[-1]["view"] is None and await h.ctx.events.upcoming() == []
    assert isinstance(await h.ctx.events.get(event.id), Event)


# ------------------------------------------------------------------------------ setup check and diagnostics


async def test_setup_check(h: Harness) -> None:
    await h.ctx.settings.set(WHITELIST_CHANNEL, REQUESTS)
    await h.ctx.settings.set(ALERT_ROLE, 700)
    problems = await h.m.setup.check_setup(FakeInteraction(admin()))
    text = "\n".join(problems)
    assert "No server has a relay channel" in text
    assert "no server has a whitelist add command" in text
    assert "no alert channel" in text
    await h.ctx.settings.set(WL_ADD, "whitelist add {name}", scope=SURVIVAL)
    await h.ctx.servers.update(SURVIVAL, event_channel=55)
    await h.ctx.settings.delete(ALERT_ROLE)
    assert await h.m.setup.check_setup(FakeInteraction(admin())) == []


async def test_server_and_bot_options(h: Harness) -> None:
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None
    setup = h.m.setup
    i = FakeInteraction(admin())
    assert "host name" in (await setup.save_options(i, row, "bad address!", "no", "yes", "", "") or "")
    assert "1 to 100" in (await setup.save_options(i, row, "", "no", "yes", "150", "") or "")
    assert await setup.save_options(i, row, "1.2.3.4:2302", "yes", "no", "90%", "") is None
    assert await h.ctx.settings.get(CONNECT_KEY, scope=SURVIVAL) == "1.2.3.4:2302"
    assert await h.ctx.settings.get(START_KEY, scope=SURVIVAL) is True
    assert await h.ctx.settings.get(CPU_KEY, scope=SURVIVAL) == 90
    assert "{name}" in (await setup.save_mod_commands(i, row, {"kick": "kick", "ban": "", "unban": ""}) or "")
    embed = await setup.server_embed(row)
    assert any(f.name == "Members can start it" and f.value == "Yes" for f in embed.fields)

    assert "time zone name" in (await setup.save_bot_options(i, "Mars/Base", "", "") or "")
    assert "1 to 365" in (await setup.save_bot_options(i, "", "0", "") or "")
    assert await setup.save_bot_options(i, "Europe/Amsterdam", "7", "hacker\nadmin") is None
    assert await h.ctx.settings.get(NAME_RULES) == ["hacker", "admin"]


async def test_diagnostics_shows_amp_permissions(h: Harness) -> None:
    i = FakeInteraction(admin())
    await h.m.admin.diagnostics(i)
    assert "admin account" in {f.name: f.value for f in i.embed.fields}["AMP account permissions"]
    h.port.grants = ["spire.*.shard.*.view", "spire.*.shard.*.power.start"]
    i = FakeInteraction(admin())
    await h.m.admin.diagnostics(i)
    text = {f.name: f.value for f in i.embed.fields}["AMP account permissions"]
    assert "`spire.*.shard.*.view`" in text and "`power.stop`" in text and "power.start`," not in text


async def test_diagnostics_reads_wildcard_grants(h: Harness) -> None:
    # Regression: the grants seen on a real panel (2026-11-08) cover every server node through
    # spire.*.shard.*.>, but a substring check listed power.start and console.read as "not found".
    h.port.grants = ["spire.*.view", "spire.*.shard.*.>", "spire.*.settings.write.>", "users.*", "roles.*"]
    i = FakeInteraction(admin())
    await h.m.admin.diagnostics(i)
    assert "Not found" not in {f.name: f.value for f in i.embed.fields}["AMP account permissions"]


def test_grant_covers() -> None:
    assert grant_covers("spire.*.shard.*.>", "power.start")
    assert grant_covers("spire.abc.shard.def.console.read", "console.read")
    assert grant_covers("spire.*.shard.*.power.*", "power.stop")
    assert grant_covers("power.start", "power.start")
    assert not grant_covers("spire.*.view", "view")  # the spire's own view, not a server's
    assert not grant_covers("spire.*.shard.*.power.start", "power.stop")
    assert not grant_covers("spire.*.shard.*.console.read.>", "console.read")  # > needs one more token
    assert not grant_covers("users.*", "view")


async def test_help_lists_new_commands_for_granted_roles(h: Harness) -> None:
    await h.ctx.settings.set(ROLE_GRANTS, {"events": [POWER_ROLE]})
    i = FakeInteraction(with_role(50, POWER_ROLE))
    await h.m.admin.help(i)
    assert "/event create" in i.embed.description
    i = FakeInteraction(member())
    await h.m.admin.help(i)
    assert "/event list" in i.embed.description and "/event create" not in i.embed.description


async def test_update_and_sleep_ask_first_wake_does_not(h: Harness) -> None:
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Survival", "update")
    assert "hasn't seen this work" in i.text and h.port.power_calls == []
    confirm = FakeInteraction(operator())
    await i.replies[-1]["view"].children[0].callback(confirm)
    assert h.port.power_calls == [(SURVIVAL, PowerAction.UPDATE)]
    assert h.ctx.offline_expected(SURVIVAL)
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Survival", "wake")
    assert h.port.power_calls[-1] == (SURVIVAL, PowerAction.WAKE)
    i = FakeInteraction(member())
    await h.m.server.server_power(i, "Survival", "sleep")
    assert "operator role" in i.text


async def test_daily_summary_and_milestones(h: Harness) -> None:
    from portcullis.bot.base import SUMMARY_CHANNEL
    from portcullis.bot.digest import SUMMARY_DAY, Digest

    summary = 404
    channels = add_channels(h, summary)
    digest = Digest(h.ctx)
    await digest.tick(NOW)
    assert channels[summary].sent == []  # no channel set: nothing happens
    await h.ctx.settings.set(SUMMARY_CHANNEL, summary)
    midnight = datetime.combine(NOW.date(), datetime.min.time(), UTC)
    day = midnight - timedelta(days=1)

    def at(hours: float) -> str:
        return (day + timedelta(hours=hours)).isoformat(timespec="seconds")

    await h.ctx.players.joined(SURVIVAL, "Old", at=(day - timedelta(days=3)).isoformat(timespec="seconds"))
    await h.ctx.players.left(SURVIVAL, "Old", at=(day - timedelta(days=3, hours=-1)).isoformat(timespec="seconds"))
    await h.ctx.players.joined(SURVIVAL, "Old", at=at(1))
    await h.ctx.players.joined(SURVIVAL, "Alice", at=at(2))
    await h.ctx.players.left(SURVIVAL, "Old", at=at(3))
    await h.ctx.players.joined(SURVIVAL, "Bob", at=at(4))
    await h.ctx.players.left(SURVIVAL, "Bob", at=at(5))
    await h.ctx.players.left(SURVIVAL, "Alice", at=at(6))
    assert await h.ctx.players.day(SURVIVAL, day, midnight) == (3, 2, 2)

    assert await digest.summary(midnight + timedelta(hours=1))
    embed = channels[summary].sent[-1]["embed"]
    assert "**Survival**: 3 players, 2 new, peak 2 at once" in embed.description
    assert await h.ctx.settings.get(SUMMARY_DAY) == day.date().isoformat()
    assert not await digest.summary(midnight + timedelta(hours=2))  # once a day

    await h.ctx.players.joined(CREATIVE, "Vet", at=(NOW - timedelta(hours=120)).isoformat(timespec="seconds"))
    await digest.milestones()
    assert channels[summary].sent[-1]["content"] == "🏆 **Vet** has played about 100 hours."
    count = len(channels[summary].sent)
    await digest.milestones()
    assert len(channels[summary].sent) == count  # each milestone once


async def test_count_channel_waits_between_renames(h: Harness) -> None:
    from portcullis.bot.base import COUNT_CHANNEL
    from portcullis.bot.digest import Digest

    renames: list[str] = []

    class Voice(FakeChannel):
        async def edit(self, *, name: str, reason: str) -> None:
            renames.append(name)

    voice = Voice(405)
    h.client.channels[405] = voice
    h.client.cached.add(405)
    digest = Digest(h.ctx)
    assert not await digest.count_channel()  # nothing chosen yet
    await h.ctx.settings.set(COUNT_CHANNEL, 405)
    assert await digest.count_channel()
    assert renames == ["⚪ nobody online"]
    await h.ctx.players.joined(SURVIVAL, "Alice")
    assert not await digest.count_channel()  # too soon after the last rename
    digest.renamed_at -= 400
    assert await digest.count_channel() and renames[-1] == "🟢 1 online"
    assert not await digest.count_channel()  # same name, no call


async def test_backups_make_and_restore(h: Harness) -> None:
    h.port.servers[ServerId(SURVIVAL)].backups = [Backup("b0", "Nightly", NOW - timedelta(days=1))]
    i = FakeInteraction(operator())
    await h.m.server.backups(i, "Survival")
    labels = [c.label or c.placeholder for c in i.replies[-1]["view"].children]
    assert labels == ["Make a backup"]  # restoring needs Manage Server
    press = FakeInteraction(operator())
    await i.replies[-1]["view"].children[0].callback(press)
    assert "may pause or stop" in press.text and h.port.backup_calls == []
    confirm = FakeInteraction(operator())
    await press.replies[-1]["view"].children[0].callback(confirm)
    assert h.port.backup_calls[0][:2] == ("create", SURVIVAL)
    assert "Asked by" in confirm.text
    i = FakeInteraction(owner())
    await h.m.server.backups(i, "Survival")
    assert any(getattr(c, "placeholder", "") == "Restore one of these…" for c in i.replies[-1]["view"].children)
    await h.m.server.backups_restore(await h.ctx.servers.find("Survival"), "b0", "Nightly")(FakeInteraction(operator()))
    assert not any(c[0] == "restore" for c in h.port.backup_calls)  # not an admin
    h.m.server.sleep = lambda seconds: asyncio.sleep(0)
    await h.m.server.backups_restore(await h.ctx.servers.find("Survival"), "b0", "Nightly")(FakeInteraction(owner()))
    assert h.port.backup_calls[-1] == ("restore", SURVIVAL, "b0") and h.ctx.offline_expected(SURVIVAL)
    await h.m.server.restore_watch[SURVIVAL]  # tests/test_access_restore.py covers what it reports


async def test_backup_without_a_model_explains(h: Harness) -> None:
    i = FakeInteraction(operator())
    await h.m.server.backups_make(await h.ctx.servers.find("Survival"))(i)
    assert "no earlier backup" in i.text


async def test_schedules_list_and_act(h: Harness) -> None:
    from portcullis.core.types import Schedule

    h.port.servers[ServerId(SURVIVAL)].schedules = [Schedule("s1", "Nightly restart", True, NOW + timedelta(hours=3))]
    i = FakeInteraction(member())
    await h.m.schedules.list_schedules(i, "Survival")
    assert "operator role" in i.text
    i = FakeInteraction(operator())
    await h.m.schedules.list_schedules(i, "Survival")
    assert "Nightly restart" in i.replies[-1]["embed"].description
    view = i.replies[-1]["view"]
    select, run, on, off, _history, _delete = view.children
    select._values = ["s1"]
    await select.callback(FakeInteraction(operator()))
    await off.callback(FakeInteraction(operator()))
    await run.callback(FakeInteraction(operator()))
    assert h.port.schedule_calls == [("disabled", SURVIVAL, "s1"), ("run", SURVIVAL, "s1")]


async def test_a_crash_in_a_command_still_answers_the_member() -> None:
    import discord

    from portcullis.bot.client import PortcullisBot, _ui_error

    i = FakeInteraction(member())
    await PortcullisBot.on_command_error(SimpleNamespace(), i, RuntimeError("boom"))  # type: ignore[arg-type]
    assert "Something went wrong on my side" in i.text and "`" in i.text
    i = FakeInteraction(member())
    await i.response.defer()
    await _ui_error(discord.ui.View(), i, RuntimeError("boom"))  # type: ignore[arg-type]
    assert "give them this code" in i.text
