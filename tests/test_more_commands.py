# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""/server console, quick commands, "Up since", /notify and the live server board."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from portcullis.bot.board import BOARD_CHANNEL, BOARD_MESSAGE, StatusBoard
from portcullis.bot.notify import Notifier
from portcullis.bot.server import QUICK_KEY, console_block
from portcullis.core.types import ServerId, ServerState, StateChanged
from tests.fakes.amp import FakeAmpError
from tests.fakes.discord import FakeInteraction
from tests.test_bot import CREATIVE, SECRET, SURVIVAL, Harness, admin, db, h, member, operator  # noqa: F401

NOW = datetime.now(UTC)


def state(sid: str, value: ServerState) -> StateChanged:
    return StateChanged(server=ServerId(sid), at=NOW, state=value)


# ------------------------------------------------------------------------------ /server console


async def test_console_shows_recent_lines_to_mods_only(h: Harness) -> None:
    for text in ("Server started", "say ``` @everyone", "", "Saved the world"):
        h.port.say(ServerId(SURVIVAL), text)

    i = FakeInteraction(member())
    await h.m.server.server_console(i, "Survival")
    assert "operator role" in i.text
    assert ([e for e in await h.ctx.audit.query() if e.action == "server.console.read"])[0].result == "denied"

    i = FakeInteraction(operator())
    await h.m.server.server_console(i, "Survival", lines=10)
    text = i.text
    assert text.count("```") == 2 and "Server started" in text and "Saved the world" in text
    assert "@everyone" not in text  # mention broken up, fence neutralised

    i = FakeInteraction(operator())
    await h.m.server.server_console(i, "Creative")
    assert "no recent console output" in i.text


async def test_console_failure_and_size_limit(h: Harness) -> None:
    async def broken(server: ServerId, limit: int = 50) -> list[str]:
        raise FakeAmpError("console.read missing")

    h.port.recent_console = broken  # type: ignore[method-assign]
    i = FakeInteraction(operator())
    await h.m.server.server_console(i, "Survival")
    assert "Couldn't read the console" in i.text and "console.read missing" in i.text

    block = console_block([f"line {n} " + "x" * 90 for n in range(60)])
    assert len(block) <= 1810 and "line 59" in block and "line 0 " not in block
    assert block.index("line 58") < block.index("line 59")  # oldest first


# ------------------------------------------------------------------------------ quick commands


async def test_quick_commands_setup_and_buttons(h: Harness) -> None:
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None
    setup = h.m.setup
    assert "label and a command" in (await setup.save_quick(FakeInteraction(admin()), row, "just text") or "")
    assert "one line" in (await setup.save_quick(FakeInteraction(admin()), row, "Bad = a\x07b") or "")
    six = "\n".join(f"C{n} = cmd {n}" for n in range(6))
    assert "Up to 5" in (await setup.save_quick(FakeInteraction(admin()), row, six) or "")
    assert await setup.save_quick(FakeInteraction(admin()), row, "Save world = save\n\nAnnounce = say hi") is None
    assert await h.ctx.settings.get(QUICK_KEY, scope=SURVIVAL) == [
        {"label": "Save world", "command": "save"}, {"label": "Announce", "command": "say hi"}]
    embed = await setup.server_embed(row)
    assert any("Save world" in f.value for f in embed.fields)

    i = FakeInteraction(operator())
    await h.m.server.server_status(i, "Survival")
    view = i.replies[-1]["view"]
    assert [b.label for b in view.children] == ["Start", "Stop", "Restart", "Save world", "Announce"]

    press = FakeInteraction(operator())
    await view.children[3].callback(press)
    assert h.port.sent == [(SURVIVAL, "save")] and "Ran **Save world**" in press.text
    entry = ([e for e in await h.ctx.audit.query() if e.action == "server.quick"])[0]
    assert (entry.target, entry.result) == ("Save world", "ok")

    press = FakeInteraction(member())  # a member who got hold of the buttons can't use them
    await view.children[4].callback(press)
    assert h.port.sent == [(SURVIVAL, "save")] and "operator role" in press.text

    assert await setup.save_quick(FakeInteraction(admin()), row, "") is None  # clearing removes them
    assert await h.ctx.settings.get(QUICK_KEY, scope=SURVIVAL) is None


async def test_status_shows_up_since_while_running(h: Harness) -> None:
    original = h.port.get_status

    async def with_start(server: ServerId):  # noqa: ANN202
        return replace(await original(server), started_at=NOW - timedelta(hours=2))

    h.port.get_status = with_start  # type: ignore[method-assign]
    i = FakeInteraction(member())
    await h.m.server.server_status(i, "Survival")
    assert "Up since" in {f.name for f in i.embed.fields}
    i = FakeInteraction(member())
    await h.m.server.server_status(i, "Creative")  # stopped: no uptime
    assert "Up since" not in {f.name for f in i.embed.fields}


# ------------------------------------------------------------------------------ /notify


async def test_notify_toggle_and_list(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.notify.notify(i)
    assert "don't follow any server" in i.text

    i = FakeInteraction(member())
    await h.m.notify.notify(i, "Survival")
    assert "I'll DM you" in i.text
    i = FakeInteraction(member())
    await h.m.notify.notify(i)
    assert "**Survival**" in i.text

    i = FakeInteraction(member())
    await h.m.notify.notify(i, SECRET)  # hidden from members
    assert "don't know a server" in i.text

    i = FakeInteraction(member())
    await h.m.notify.notify(i, "Survival")
    assert "no longer" in i.text and await h.ctx.notify.for_member(20) == []


async def test_notify_limit(h: Harness) -> None:
    for n in range(h.ctx.notify.LIMIT):
        assert await h.ctx.notify.toggle(20, f"{n:032x}") is True
    assert await h.ctx.notify.toggle(20, SURVIVAL) is None
    i = FakeInteraction(member())
    await h.m.notify.notify(i, "Survival")
    assert "up to 25" in i.text


async def test_notifier_dms_on_real_change_to_online(h: Harness) -> None:
    await h.ctx.notify.toggle(20, CREATIVE)
    await h.ctx.notify.toggle(21, CREATIVE)
    notifier = Notifier(h.ctx)

    async def settle() -> None:
        await asyncio.gather(*notifier.tasks)

    await notifier(state(CREATIVE, ServerState.RUNNING))  # first sighting: nothing to compare with
    await notifier(state(CREATIVE, ServerState.RUNNING))  # AMP repeating the state
    await settle()
    assert h.client.get_user(20).dms == []

    await notifier(state(CREATIVE, ServerState.STOPPED))
    await notifier(state(CREATIVE, ServerState.RUNNING))
    await settle()
    assert len(h.client.get_user(20).dms) == 1 and "**Creative** is online" in h.client.get_user(21).dms[0]

    await notifier(state(CREATIVE, ServerState.STOPPED))  # crash loop: quiet for a while
    await notifier(state(CREATIVE, ServerState.RUNNING))
    await settle()
    assert len(h.client.get_user(20).dms) == 1
    await notifier.close()


async def test_notifier_skips_unknown_and_hidden(h: Harness) -> None:
    await h.ctx.notify.toggle(20, CREATIVE)
    await h.ctx.notify.toggle(20, SECRET)
    notifier = Notifier(h.ctx, quiet_for=0)
    for sid in (CREATIVE, SECRET):
        await notifier(state(sid, ServerState.UNKNOWN))  # AMP unreachable, then back: not news
        await notifier(state(sid, ServerState.RUNNING))
    await notifier(state(SECRET, ServerState.STOPPED))
    await notifier(state(SECRET, ServerState.RUNNING))  # hidden server: never announced
    await asyncio.gather(*notifier.tasks)
    assert h.client.get_user(20).dms == []
    await notifier.close()


# ------------------------------------------------------------------------------ server board


async def test_board_posts_then_edits_and_turns_off(h: Harness) -> None:
    await h.ctx.live.seed([await h.port.get_status(ServerId(SURVIVAL))], [ServerId(SURVIVAL), ServerId(CREATIVE)])
    h.port.join(ServerId(SURVIVAL), "Bob")
    board = h.ctx.board
    assert isinstance(board, StatusBoard)
    channel = h.client.channels[55]

    await board.update()  # no channel set: nothing happens
    assert channel.sent == []

    await h.m.setup.set_board_channel(FakeInteraction(admin()), 55)
    assert await h.ctx.settings.get(BOARD_CHANNEL) == 55
    [posted] = channel.sent
    text = posted["embed"].description
    assert "🟢 Online · **Survival**" in text and "Creative" in text and "Secret" not in text
    message_id = await h.ctx.settings.get(BOARD_MESSAGE)
    assert message_id == 1

    board.every = 0
    await board.changed()
    assert channel.sent[-1]["edit"] == message_id and len(channel.sent) == 2

    embed = await h.m.setup.bot_embed()
    assert "<#55>" in {f.name: f.value for f in embed.fields}["Live server board"]

    await h.m.setup.set_board_channel(FakeInteraction(admin()), None)
    assert await h.ctx.settings.get(BOARD_CHANNEL) is None and await h.ctx.settings.get(BOARD_MESSAGE) is None
    entries = [e for e in await h.ctx.audit.query() if e.action == "setup.bot.board_channel"]
    assert len(entries) == 2


async def test_board_edits_are_spaced_out(h: Harness) -> None:
    await h.ctx.settings.set(BOARD_CHANNEL, 55)
    board = StatusBoard(h.ctx, every=0.05)
    await board.changed()  # first one goes out at once
    await board.changed()  # these two become one later edit
    await board.changed()
    channel = h.client.channels[55]
    assert len(channel.sent) == 1
    await asyncio.sleep(0.1)
    assert len(channel.sent) == 2
    await board.close()


async def test_board_survives_a_missing_channel(h: Harness) -> None:
    await h.ctx.settings.set(BOARD_CHANNEL, 999)  # not a channel the client knows
    await StatusBoard(h.ctx).update()  # logged, not raised
