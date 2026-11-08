# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""Gatekeeper V3 parity: schedules, backups, console rules, bot admin, player ids and whitelist rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from portcullis.bot.setup import DONATOR_ONLY, WL_DONATOR_BYPASS, WL_REQUIRE_LINK
from portcullis.core.types import Backup, Schedule, ScheduleRun, ServerId
from tests.fakes.discord import FakeInteraction
from tests.test_bot import (  # noqa: F401
    SURVIVAL,
    Harness,
    admin,
    db,
    h,
    member,
    operator,
    owner,
    whitelist_ready,
)

NOW = datetime.now(UTC)


def schedule_view(i: FakeInteraction):  # noqa: ANN201
    view = i.replies[-1]["view"]
    select = view.children[0]
    select._values = ["s1"]
    buttons = {c.label: c for c in view.children[1:]}
    return select, buttons


async def test_schedule_history_and_delete(h: Harness) -> None:
    h.port.servers[ServerId(SURVIVAL)].schedules = [Schedule("s1", "Nightly restart", True, NOW + timedelta(hours=3))]
    h.port.schedule_runs["s1"] = [
        ScheduleRun(NOW - timedelta(hours=21), NOW - timedelta(hours=21) + timedelta(seconds=42), "Succeeded"),
        ScheduleRun(NOW - timedelta(hours=45), None, "Failed", "Shard <@1> did not stop"),
    ]
    i = FakeInteraction(operator())
    await h.m.schedules.list_schedules(i, "Survival")
    select, buttons = schedule_view(i)
    await select.callback(FakeInteraction(operator()))

    press = FakeInteraction(operator())
    await buttons["Run history"].callback(press)
    text = press.replies[-1]["embed"].description
    assert "✅" in text and "took 42s" in text and "❌" in text
    assert "<@1>" not in text  # error text is escaped, so it can't mention anyone

    press = FakeInteraction(operator())
    await buttons["Delete"].callback(press)
    assert "Manage Server" in press.text and not any(c[0] == "delete" for c in h.port.schedule_calls)

    press = FakeInteraction(owner())
    await buttons["Delete"].callback(press)
    assert "can't be undone" in press.text
    confirm = FakeInteraction(owner())
    await press.replies[-1]["view"].children[0].callback(confirm)
    assert h.port.schedule_calls[-1] == ("delete", SURVIVAL, "s1")
    assert "Deleted" in confirm.text
    assert h.port.servers[ServerId(SURVIVAL)].schedules == []


async def test_schedule_history_when_it_never_ran(h: Harness) -> None:
    h.port.servers[ServerId(SURVIVAL)].schedules = [Schedule("s1", "Weekly wipe", False)]
    i = FakeInteraction(operator())
    await h.m.schedules.list_schedules(i, "Survival")
    select, buttons = schedule_view(i)
    await select.callback(FakeInteraction(operator()))
    press = FakeInteraction(operator())
    await buttons["Run history"].callback(press)
    assert "hasn't run yet" in press.text


async def test_backup_delete_needs_manage_server_and_a_confirm(h: Harness) -> None:
    h.port.servers[ServerId(SURVIVAL)].backups = [Backup("b0", "Nightly", NOW - timedelta(days=1), sticky=True),
                                                  Backup("b1", "Old", NOW - timedelta(days=9))]
    i = FakeInteraction(operator())
    await h.m.server.backups(i, "Survival")
    assert not any(getattr(c, "placeholder", "") == "Delete one of these…" for c in i.replies[-1]["view"].children)

    i = FakeInteraction(owner())
    await h.m.server.backups(i, "Survival")
    drop = next(c for c in i.replies[-1]["view"].children if getattr(c, "placeholder", "") == "Delete one of these…")
    drop._values = ["b0"]
    press = FakeInteraction(owner())
    await drop.callback(press)
    assert "can't be undone" in press.text and "marked to be kept" in press.text
    assert not any(c[0] == "delete" for c in h.port.backup_calls)
    row = await h.ctx.servers.find("Survival")
    await h.m.server.backups_delete(row, "b0", "Nightly")(FakeInteraction(operator()))
    assert not any(c[0] == "delete" for c in h.port.backup_calls)  # an operator without Manage Server
    await press.replies[-1]["view"].children[0].callback(FakeInteraction(owner()))
    assert h.port.backup_calls[-1] == ("delete", SURVIVAL, "b0")
    entries = await h.ctx.audit.query(action_prefix="backup.delete")
    assert entries and entries[0].result == "ok"


# ------------------------------------------------------------------------------ console rules

def test_hide_rules_parse_and_match() -> None:
    from portcullis.core.console_rules import hider, parse_hide_rules

    rules, problem = parse_hide_rules("Saving chunks\n\nre:\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\nsaving CHUNKS")
    assert problem is None and rules == ["Saving chunks", "re:\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}"]
    hide = hider(tuple(rules))
    assert hide is not None
    assert hide("[INFO] saving chunks for level 'world'")
    assert hide("Steve joined from 203.0.113.9")
    assert not hide("Steve joined the game")
    assert hider(()) is None


def test_hide_rules_refuse_bad_patterns() -> None:
    from portcullis.core.console_rules import HIDE_LIMIT, parse_hide_rules, stored_rules

    assert "repeats a part" in (parse_hide_rules("re:(a+)+$")[1] or "")
    assert "valid regular expression" in (parse_hide_rules("re:[unclosed")[1] or "")
    assert "after `re:`" in (parse_hide_rules("re:")[1] or "")
    assert "longer than" in (parse_hide_rules("x" * 201)[1] or "")
    assert "Up to" in (parse_hide_rules("\n".join(f"rule {n}" for n in range(HIDE_LIMIT + 1)))[1] or "")
    # Settings can arrive through /admin import, so stored values are checked again.
    assert stored_rules(["ok", "re:(a+)+", 5, "", "re:[bad"]) == ("ok",)
    assert stored_rules("not a list") == ()


def test_denylist_parse_and_match() -> None:
    from portcullis.core.console_rules import blocked_word, parse_denylist, stored_denylist

    words, problem = parse_denylist("op, /deop\nSTOP  ban-ip op")
    assert problem is None and words == ["op", "deop", "stop", "ban-ip"]
    deny = stored_denylist(words)
    assert blocked_word("OP Steve", deny) == "op"
    assert blocked_word("/stop", deny) == "stop"
    assert blocked_word("opinion poll", deny) is None
    assert "isn't a command word" in (parse_denylist("rm;rf")[1] or "")
    assert stored_denylist(["ok", "bad word", 3]) == frozenset({"ok"})


async def test_relay_skips_hidden_console_lines_but_listeners_still_see_them() -> None:
    from portcullis.core.relay import Relay, RelayTarget
    from tests.fakes.amp import FakeAmpPort
    from tests.test_relay import RecordingSink, line, settle

    port, sink = FakeAmpPort(), RecordingSink()
    sid = port.add_server("a" * 32, "Survival")
    seen: list[str] = []

    async def listener(event) -> None:  # noqa: ANN001
        seen.append(getattr(event, "text", ""))

    relay = Relay(port, sink, flush_interval=3600)
    relay.listeners.append(listener)
    ip = "re:\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}"
    await relay.apply([RelayTarget(server=sid, name="Survival", console_channel=7, hide=(ip,))])
    await settle()
    port.emit(line(sid, "Steve connected from 198.51.100.4"))
    port.emit(line(sid, "Steve joined the game"))
    await settle()
    await relay.flush()
    [message] = sink.to(7)
    assert "joined the game" in message and "198.51" not in message
    assert "Steve connected from 198.51.100.4" in seen
    await relay.close()


async def test_console_rules_panel_send_and_console_view(h: Harness) -> None:
    from portcullis.core.console_rules import DENY_KEY, HIDE_KEY

    row = await h.ctx.servers.find("Survival")
    problem = await h.m.setup.save_console_rules(FakeInteraction(owner()), row, "re:(x+)+", "")
    assert "repeats a part" in (problem or "")
    assert await h.ctx.settings.get(HIDE_KEY, scope=SURVIVAL) is None
    assert await h.m.setup.save_console_rules(FakeInteraction(owner()), row, "secret token", "op stop") is None
    assert await h.ctx.settings.get(DENY_KEY, scope=SURVIVAL) == ["op", "stop"]

    i = FakeInteraction(operator())
    await h.m.server.server_send(i, "Survival", "op Steve")
    assert "is blocked" in i.text and not h.port.sent
    denied = await h.ctx.audit.query(action_prefix="server.send.console")
    assert denied[0].result == "denied" and denied[0].target == "op"

    i = FakeInteraction(operator())
    await h.m.server.server_send(i, "Survival", "say hi", "console")
    assert "Sent" in i.text

    i = FakeInteraction(owner())
    await h.m.server.server_send(i, "Survival", "op Steve")
    assert "Sent" in i.text  # owners are never blocked

    for text in ("Server started", "your secret token is abc", "Saved the world"):
        h.port.say(ServerId(SURVIVAL), text)
    i = FakeInteraction(operator())
    await h.m.server.server_console(i, "Survival", lines=10)
    assert "Server started" in i.text and "secret token" not in i.text

    i = FakeInteraction(owner())
    await h.m.setup.setup_server(i, "Survival")
    fields = {f.name: f.value for f in i.replies[-1]["embed"].fields}
    assert fields["Hidden console lines"] == "1 rule(s)" and "`op`" in fields["Blocked console commands"]


# ------------------------------------------------------------------------------ /admin bot, /audit stats


async def test_admin_bot_is_for_owners(h: Harness) -> None:
    from tests.test_bot import admin

    i = FakeInteraction(admin())
    await h.m.admin.bot_action(i, "status")
    assert "Only the bot's owners" in i.text
    assert (await h.ctx.audit.query(action_prefix="admin.bot"))[0].result == "denied"

    i = FakeInteraction(owner())
    await h.m.admin.bot_action(i, "status")
    fields = {f.name: f.value for f in i.replies[-1]["embed"].fields}
    assert "portcullis-api" in fields["Versions"] and "3.0.0.6" in fields["Versions"]
    assert fields["Discord latency"] == "50 ms"

    synced: list[object] = []

    class Tree:
        def copy_global_to(self, *, guild: object) -> None:
            synced.append(("copy", guild.id))

        async def sync(self, *, guild: object) -> list[object]:
            synced.append(("sync", guild.id))
            return [object(), object()]

    h.client.tree = Tree()  # type: ignore[attr-defined]
    i = FakeInteraction(owner())
    await h.m.admin.bot_action(i, "sync")
    assert "Synced 2 command(s)" in i.text and synced[-1][0] == "sync"

    closed: list[bool] = []

    async def close() -> None:
        closed.append(True)

    h.client.close = close  # type: ignore[attr-defined]
    i = FakeInteraction(owner())
    await h.m.admin.bot_action(i, "stop")
    assert "Stop Portcullis?" in i.text and not closed
    confirm = FakeInteraction(owner())
    await i.replies[-1]["view"].children[0].callback(confirm)
    assert "Stopping" in confirm.edits[-1]["content"]
    import asyncio

    await asyncio.sleep(1.1)
    assert closed == [True]


async def test_audit_stats(h: Harness) -> None:
    from tests.test_bot import admin

    for node, result in (("server.power.start", "ok"), ("server.power.start", "ok"), ("server.send.console", "denied")):
        await h.ctx.audit.record(node, actor_id=20, shard_id=SURVIVAL, result=result)
    await h.ctx.audit.record("setup.server.names", actor_id=30, shard_id=SURVIVAL)
    i = FakeInteraction(admin())
    await h.m.admin.audit(i, stats=True)
    embed = i.replies[-1]["embed"]
    fields = {f.name: f.value for f in embed.fields}
    assert "4 action(s)" in embed.description
    assert fields["Most common"].splitlines()[0] == "`server.power.start` · 2"
    assert fields["Most active"].splitlines()[0] == "<@20> · 3"
    assert "denied: 1" in fields["Results"]

    i = FakeInteraction(admin())
    await h.m.admin.audit(i, server="Creative", stats=True)
    assert "Nothing in the audit log" in i.text


# ------------------------------------------------------------------------------ /player id


def test_steam_id_formats() -> None:
    from portcullis.bot.ids import is_vanity_link, steam_ids

    want = ("76561197960287930", "STEAM_0:0:11101", "[U:1:22202]")
    for value in ("76561197960287930", "STEAM_0:0:11101", "STEAM_1:0:11101", "[U:1:22202]", "U:1:22202",
                  "https://steamcommunity.com/profiles/76561197960287930/"):
        ids = steam_ids(value)
        assert ids is not None and (ids.steam64, ids.steam2, ids.steam3) == want, value
    for bad in ("7656119796028793", "STEAM_0:2:1", "[U:1:0]", "hello", "76561197960287930; rm"):
        assert steam_ids(bad) is None, bad
    assert is_vanity_link("https://steamcommunity.com/id/gabelogannewell")


class FakeResponse:
    def __init__(self, status: int, data: dict | None = None) -> None:
        self.status, self.data = status, data

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def json(self, content_type: object = None) -> dict | None:
        return self.data


class FakeSession:
    calls: list[str] = []
    answers: dict[str, FakeResponse] = {}

    def __init__(self, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        FakeSession.calls.append(url)
        for part, answer in FakeSession.answers.items():
            if part in url:
                return answer
        return FakeResponse(404)


async def test_player_id_command(h: Harness) -> None:
    from portcullis.bot.ids import MojangLookup

    FakeSession.calls = []
    FakeSession.answers = {"/minecraft/Notch": FakeResponse(200, {"id": "069a79f444e94726a5befca90e38aaf5",
                                                                    "name": "Notch"}),
                           "/minecraft/Broken": FakeResponse(500)}
    h.m.community.mojang = MojangLookup(session_factory=FakeSession)  # type: ignore[arg-type]

    i = FakeInteraction(member())
    await h.m.community.player_id(i, "STEAM_0:0:11101")
    assert {f.name: f.value for f in i.replies[-1]["embed"].fields}["SteamID64"] == "`76561197960287930`"

    i = FakeInteraction(member())
    await h.m.community.player_id(i, "Notch")
    fields = {f.name: f.value for f in i.replies[-1]["embed"].fields}
    assert fields["UUID"] == "`069a79f4-44e9-4726-a5be-fca90e38aaf5`"

    i = FakeInteraction(member())
    await h.m.community.player_id(i, "Notch")
    assert "One lookup every few seconds" in i.text  # per-member cooldown
    h.m.community.id_asked.clear()
    i = FakeInteraction(member())
    await h.m.community.player_id(i, "notch")
    assert len(FakeSession.calls) == 1  # names ignore case, so this came from the cache
    assert "069a79f4" in i.replies[-1]["embed"].fields[1].value

    h.m.community.id_asked.clear()
    i = FakeInteraction(member())
    await h.m.community.player_id(i, "Broken")
    assert "didn't answer" in i.text

    h.m.community.id_asked.clear()
    i = FakeInteraction(member())
    await h.m.community.player_id(i, "../../evil?x=1")
    assert "Give a Steam ID" in i.text
    assert not any("evil" in c for c in FakeSession.calls)


# ------------------------------------------------------------------------------ whitelist rules


async def test_whitelist_rules_donator_only_require_link_bypass_and_reply(h: Harness) -> None:
    await whitelist_ready(h)
    await h.ctx.settings.set(DONATOR_ONLY, True, scope=SURVIVAL)
    i = FakeInteraction(member(70))
    await h.m.community.whitelist_request(i, "Survival", "Frank")
    assert "Only donators" in i.text and h.port.sent == []

    await h.ctx.users.set_flags(70, donator=True)
    await h.ctx.settings.set(WL_REQUIRE_LINK, True)
    i = FakeInteraction(member(70))
    await h.m.community.whitelist_request(i, "Survival", "Frank")
    assert "/link" in i.text and h.port.sent == []

    await h.ctx.users.add_link(70, "unturned", "Frank")
    i = FakeInteraction(member(70))
    await h.m.community.whitelist_request(i, "Survival", "Frank")
    assert "Asked the moderators" in i.text and h.port.sent == []  # no bypass yet

    await h.ctx.users.add_link(71, "unturned", "Gina")
    await h.ctx.users.set_flags(71, donator=True)
    i = FakeInteraction(admin())
    assert await h.m.setup.save_whitelist_rules(i, "yes", "yes", "Welcome! Read the rules.") is None
    assert await h.ctx.settings.get(WL_DONATOR_BYPASS) is True
    i = FakeInteraction(member(71))
    await h.m.community.whitelist_request(i, "Survival")
    assert "donator status" in i.text and "Welcome! Read the rules." in i.text
    assert h.port.sent[-1] == (SURVIVAL, "whitelist add Gina")
    assert "they are a donator" in str(h.requests.sent[-1]["embed"].fields)

    press = FakeInteraction(operator())
    await h.m.community.decide(press, 1, "approve")  # Frank's request, decided by hand
    assert any("Welcome! Read the rules." in dm for dm in h.client.get_user(70).dms)

    assert "characters" in (await h.m.setup.save_whitelist_rules(FakeInteraction(admin()), "no", "no", "x" * 501))
    assert await h.m.setup.save_whitelist_rules(FakeInteraction(admin()), "no", "no", "") is None
    assert await h.ctx.settings.get(WL_REQUIRE_LINK) is None


# ------------------------------------------------------------------------------ review fixes


def test_hide_rules_refuse_patterns_that_backtrack() -> None:
    from portcullis.core.console_rules import REGEX_LIMIT, hider, parse_hide_rules

    for bad in ("re:(a|aa)+$", "re:(a|a?)+$", "re:((a+))+$", r"re:(\d|\d\d)+$", r"re:(a)\1", r"re:\d*\d*\d*x",
                r"re:\d+\.\d+\.\d+"):
        assert parse_hide_rules(bad)[1], bad
    for good in (r"re:\d{1,3}\.\d{1,3}", r"re:^\[INFO\] .*joined", "re:(foo|bar) logged in", "re:(?:ab)+c"):
        assert parse_hide_rules(good)[1] is None, good
    too_many = "\n".join(f"re:x{i}" for i in range(REGEX_LIMIT + 1))
    assert "re:" in (parse_hide_rules(too_many)[1] or "")
    hide = hider(("re:secret$",))
    assert hide is not None and hide("a secret") and not hide("x" * 400 + " secret")  # long lines: first 300 only


def test_blocked_word_sees_namespaces_and_execute() -> None:
    from portcullis.core.console_rules import blocked_word

    deny = frozenset({"op", "stop"})
    assert blocked_word("minecraft:op Bob", deny) == "op"
    assert blocked_word("execute as @a run op Bob", deny) == "op"
    assert blocked_word("/OP Bob", deny) == "op"
    assert blocked_word("say please stop", deny) is None
    assert blocked_word("", deny) is None


async def test_channels_panel_fits_and_clearing_turns_off(h: Harness) -> None:
    from portcullis.bot.setup import ALERT_CHANNEL, ChannelsPanel

    await h.ctx.settings.set(ALERT_CHANNEL, 123)
    panel = ChannelsPanel(h.m.setup)  # five selects, one per row
    select = panel.children[0]
    select._values = []
    i = FakeInteraction(admin())
    await select.callback(i)
    assert await h.ctx.settings.get(ALERT_CHANNEL) is None and ": off." in i.edits[-1]["content"]
