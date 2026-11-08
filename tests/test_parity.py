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
