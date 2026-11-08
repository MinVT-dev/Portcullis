# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""Gatekeeper V3 parity: schedule history and delete, backup delete."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from portcullis.core.types import Backup, Schedule, ScheduleRun, ServerId
from tests.fakes.discord import FakeInteraction
from tests.test_bot import SURVIVAL, Harness, db, h, member, operator, owner  # noqa: F401

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

    rules, problem = parse_hide_rules("Saving chunks\n\nre:\\d+\\.\\d+\\.\\d+\\.\\d+\nsaving CHUNKS")
    assert problem is None and rules == ["Saving chunks", "re:\\d+\\.\\d+\\.\\d+\\.\\d+"]
    hide = hider(tuple(rules))
    assert hide is not None
    assert hide("[INFO] saving chunks for level 'world'")
    assert hide("Steve joined from 203.0.113.9")
    assert not hide("Steve joined the game")
    assert hider(()) is None


def test_hide_rules_refuse_bad_patterns() -> None:
    from portcullis.core.console_rules import HIDE_LIMIT, parse_hide_rules, stored_rules

    assert "repeats a group" in (parse_hide_rules("re:(a+)+$")[1] or "")
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
    ip = "re:\\d+\\.\\d+\\.\\d+\\.\\d+"
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
    assert "repeats a group" in (problem or "")
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
