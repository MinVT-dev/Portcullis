# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""Access-control audit (2026-11-14): who can press what, and the guarded backup restore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from portcullis.bot.backups import BackupView, RestoreModal, RestorePrompt, ScheduleView, names_match
from portcullis.bot.base import OwnedView, scrub, short_error, use_scrubber
from portcullis.bot.community import UnlinkView, WhitelistDecision
from portcullis.bot.events import EventCancel
from portcullis.bot.polls import PollClose, PollVote
from portcullis.bot.rules import RuleView
from portcullis.bot.server import RESTORE_CHECKS
from portcullis.core.types import Backup, Schedule, ServerId
from tests.fakes.discord import FakeInteraction, FakeMember, FakeRole
from tests.test_bot import (  # noqa: F401
    OPERATOR_ROLE,
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


def admin_operator() -> FakeMember:
    """Manage Server and an operator role: the only non-owner who may restore."""
    return FakeMember(41, roles=[FakeRole(OPERATOR_ROLE)], manage_guild=True)


@pytest.fixture(autouse=True)
def _no_scrubber() -> Any:
    yield
    use_scrubber(lambda text: text)


async def audit(h: Harness, prefix: str) -> list[Any]:
    return await h.ctx.audit.query(action_prefix=prefix)


# ------------------------------------------------------------------ secrets in member-facing errors


def test_error_text_shown_in_discord_masks_secrets() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJib3QifQ.c2lnbmF0dXJl"
    text = scrub(f"POST /api/x: [401] Bearer abc123 refused; token={jwt}&x=1 ticket=t1ck3t {jwt}")
    assert jwt not in text and "abc123" not in text and "t1ck3t" not in text
    assert "x=1" in text and text.count("<redacted>") == 4
    use_scrubber(lambda t: t.replace("hunter2-password", "<redacted>"))
    assert "hunter2" not in short_error(RuntimeError("login as bot with hunter2-password failed"))


async def test_failed_backup_never_posts_a_token(h: Harness) -> None:
    h.port.servers[ServerId(SURVIVAL)].backups = [Backup("b0", "Nightly", NOW)]

    async def boom(server: Any, name: str) -> None:
        raise RuntimeError("POST /api/shards/x/snapshots?access_token=SECRETVALUE failed")

    h.port.create_backup = boom  # type: ignore[method-assign]
    i = FakeInteraction(operator())
    await h.m.server.backups_make(await h.ctx.servers.find("Survival"))(i)
    assert "SECRETVALUE" not in i.text
    assert "SECRETVALUE" not in str((await audit(h, "backup.create"))[0].detail)


# ------------------------------------------------------------------ panels belong to whoever asked


async def test_owned_view_refuses_someone_else() -> None:
    view = OwnedView(30)
    other = FakeInteraction(member(99))
    assert not await view.interaction_check(other)  # type: ignore[arg-type]
    assert "belongs to someone else" in other.text
    assert await view.interaction_check(FakeInteraction(member(30)))  # type: ignore[arg-type]


async def test_backup_and_schedule_panels_only_answer_their_owner(h: Harness) -> None:
    h.port.servers[ServerId(SURVIVAL)].backups = [Backup("b0", "Nightly", NOW)]
    h.port.servers[ServerId(SURVIVAL)].schedules = [Schedule("s1", "Nightly restart", True, NOW)]
    i = FakeInteraction(owner())
    await h.m.server.backups(i, "Survival")
    view = i.replies[-1]["view"]
    assert isinstance(view, BackupView) and view.owner_id == owner().id
    assert not await view.interaction_check(FakeInteraction(operator()))  # type: ignore[arg-type]
    i = FakeInteraction(operator())
    await h.m.schedules.list_schedules(i, "Survival")
    view = i.replies[-1]["view"]
    assert isinstance(view, ScheduleView) and view.owner_id == operator().id
    assert not await view.interaction_check(FakeInteraction(admin()))  # type: ignore[arg-type]


async def test_rule_and_unlink_panels_are_owned(h: Harness) -> None:
    assert issubclass(RuleView, OwnedView) and issubclass(UnlinkView, OwnedView)
    view = UnlinkView(h.m.community, 20)
    assert not await view.interaction_check(FakeInteraction(member(21)))  # type: ignore[arg-type]


async def test_persistent_buttons_ignore_other_discord_servers(h: Harness) -> None:
    for item in (WhitelistDecision("approve", 1), PollVote(1, 0), PollClose(1), EventCancel(1)):
        elsewhere = FakeInteraction(owner(), guild_id=2)
        assert not await item.interaction_check(elsewhere)  # type: ignore[arg-type]
        assert "only works in the Discord server" in elsewhere.text
        assert await item.interaction_check(FakeInteraction(owner()))  # type: ignore[arg-type]


async def test_removed_server_cant_be_reached_by_its_id(h: Harness) -> None:
    await h.ctx.servers.sync([s.info for s in h.port.servers.values() if s.info.id != SURVIVAL])
    i = FakeInteraction(owner())
    assert await h.m.server.server(i, SURVIVAL) is None
    assert "don't know a server" in i.text


# ------------------------------------------------------------------ restoring a backup


@pytest.fixture
async def rh(h: Harness) -> Harness:
    h.port.servers[ServerId(SURVIVAL)].backups = [Backup("b0", "Nightly", NOW - timedelta(days=1))]

    async def no_wait(seconds: float) -> None:
        return None

    h.m.server.sleep = no_wait
    return h


async def restore(h: Harness, user: FakeMember, typed: str = "Survival", backup: str = "b0") -> FakeInteraction:
    """Pick a backup, press Restore…, type ``typed``; returns the modal's interaction once AMP is followed."""
    row = await h.ctx.servers.find("Survival")
    assert row is not None
    prompt = FakeInteraction(user)
    await RestorePrompt(h.m.server, row, backup, "Nightly", user.id).go.callback(prompt)  # type: ignore[call-arg]
    modal = prompt.modal
    assert isinstance(modal, RestoreModal) and "Survival" in modal.title
    modal.typed._value = typed
    submit = FakeInteraction(user)
    await modal.on_submit(submit)  # type: ignore[arg-type]
    task = h.m.server.restore_watch.get(row.shard_id)
    if task is not None:
        await task
    return submit


def test_names_match() -> None:
    assert names_match("  survival ", "Survival")
    assert not names_match("Survival 2", "Survival") and not names_match("", " ")


async def test_restore_picker_names_server_and_backup(rh: Harness) -> None:
    i = FakeInteraction(owner())
    await rh.m.server.backups(i, "Survival")
    view = i.replies[-1]["view"]
    select = next(c for c in view.children if getattr(c, "placeholder", "") == "Restore one of these…")
    select._values = ["b0"]
    press = FakeInteraction(owner())
    await select.callback(press)
    assert "Restore Survival to the backup Nightly" in press.text and "type the server's name" in press.text
    assert isinstance(press.replies[-1]["view"], RestorePrompt) and rh.port.backup_calls == []


async def test_backup_with_a_numeric_status_can_still_be_restored(rh: Harness) -> None:
    rh.port.servers[ServerId(SURVIVAL)].backups = [Backup("b0", "Nightly", NOW, status="2")]
    i = FakeInteraction(owner())
    await rh.m.server.backups(i, "Survival")
    assert any(getattr(c, "placeholder", "") == "Restore one of these…" for c in i.replies[-1]["view"].children)


@pytest.mark.parametrize("who", [operator, admin])
async def test_restore_needs_manage_server_and_the_backups_power(rh: Harness, who: Any) -> None:
    done = await restore(rh, who())
    assert rh.port.backup_calls == [] and "Manage Server permission and the backups power" in done.text
    assert [e.result for e in await audit(rh, "backup.restore")] == ["denied"]


async def test_restore_needs_the_server_name_typed(rh: Harness) -> None:
    done = await restore(rh, admin_operator(), typed="Creative")
    assert rh.port.backup_calls == [] and "nothing was restored" in done.text


async def test_restore_reports_success_and_the_safety_copy(rh: Harness) -> None:
    done = await restore(rh, admin_operator(), typed="survival")
    assert rh.port.backup_calls == [("restore", SURVIVAL, "b0")] and rh.ctx.offline_expected(SURVIVAL)
    assert "Restoring **Survival** to **Nightly**" in done.text
    assert "is restored to **Nightly**" in done.text and "saved the state from before as **PreRestore**" in done.text
    assert all(r.get("ephemeral") is False for r in done.replies)  # everyone sees who rolled it back
    finished = await audit(rh, "backup.restore.finished")
    assert finished[0].result == "ok" and finished[0].detail["safety_copy"] is True


async def test_restore_that_doesnt_land_says_so(rh: Harness) -> None:
    rh.port.restore_outcome, rh.port.restore_safety_copy = "failed", False
    done = await restore(rh, owner())
    assert "probably failed" in done.text and "couldn't find the PreRestore backup" in done.text
    assert (await audit(rh, "backup.restore.finished"))[0].result == "failed"


async def test_restore_still_running_when_the_bot_stops_watching(rh: Harness) -> None:
    rh.port.restore_outcome = "running"
    calls = 0
    timeline = rh.port.backup_timeline

    async def counted(server: Any) -> Any:
        nonlocal calls
        calls += 1
        return await timeline(server)

    rh.port.backup_timeline = counted  # type: ignore[method-assign]
    done = await restore(rh, owner())
    assert "still restoring" in done.text and calls == RESTORE_CHECKS + 1  # one check before starting
    assert (await audit(rh, "backup.restore.finished"))[0].detail["outcome"] == "unknown"


async def test_restore_refuses_a_backup_from_another_list(rh: Harness) -> None:
    done = await restore(rh, owner(), backup="someone-elses")
    assert rh.port.backup_calls == [] and "isn't in the backup list" in done.text
    assert (await audit(rh, "backup.restore"))[0].result == "failed"


async def test_restore_refused_by_amp_changes_nothing(rh: Harness) -> None:
    async def busy(server: Any, backup_id: str) -> None:
        raise RuntimeError("[409] another job is running")

    rh.port.restore_backup = busy  # type: ignore[method-assign]
    done = await restore(rh, owner())
    assert "Nothing was restored" in done.text and "409" in done.text
    assert not rh.m.server.restore_watch


async def test_delete_and_make_check_again_when_confirmed(rh: Harness) -> None:
    row = await rh.ctx.servers.find("Survival")
    assert row is not None
    i = FakeInteraction(admin())  # Manage Server but no backups power
    await rh.m.server.backups_delete(row, "b0", "Nightly")(i)
    assert rh.port.backup_calls == [] and "backups power" in i.text
    i = FakeInteraction(member())
    await rh.m.server.backups_make(row)(i)
    assert rh.port.backup_calls == [] and "operator role" in i.text
