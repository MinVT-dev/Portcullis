# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stopping the bot: Ctrl+C, SIGTERM and /admin bot stop end cleanly, with exit code 0 and no traceback."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import discord
import pytest
from aiohttp.test_utils import TestServer
from discord import app_commands

import portcullis.__main__ as entry
from portcullis.bot.base import MUTED
from portcullis.bot.client import PortcullisBot
from portcullis.bot.emojis import EMOJI
from portcullis.bot.livecard import STOPPED_FOOTER, LiveCards
from portcullis.config import Settings
from portcullis.core.relay import GameHooks
from portcullis.db.database import Database
from portcullis.shutdown import StopSignals, cancel_leftovers, step
from portcullis_api.auth import Credentials
from portcullis_api.client import AmpClient
from portcullis_api.testing import PASSWORD, USERNAME, FakeAmp
from tests.fakes.amp import FakeAmpPort
from tests.test_bot import GUILD, SURVIVAL, Harness


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    database = await Database.open(":memory:")
    yield database
    await database.close()


# ------------------------------------------------------------------ the crash Min saw


async def test_modules_close_does_not_crash(db: Database) -> None:
    """Regression: ``Modules.close`` read ``self.ctx``, which ``Modules`` never set, so every stop crashed."""
    h = await Harness(db).ready()
    h.ctx.live_cards.start()
    await h.m.close()
    assert h.ctx.live_cards._task is None


async def test_bot_close_keeps_going_after_a_failing_step(db: Database, caplog: pytest.LogCaptureFixture) -> None:
    bot = PortcullisBot(port=FakeAmpPort(), db=db, access=SimpleNamespace(), guild_id=GUILD,  # type: ignore[arg-type]
                        hooks=lambda game: GameHooks(chat=None))
    closed: list[str] = []

    async def broken() -> None:
        raise RuntimeError("board is stuck")

    async def relay_close() -> None:
        closed.append("relay")

    bot.board.close = broken  # type: ignore[method-assign]
    bot.relay.close = relay_close  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR):
        await bot.close()
        await bot.close()  # /admin bot stop, then __main__: the second call must not redo the steps
    assert closed == ["relay"]
    assert "could not stop the server board cleanly" in caplog.text
    assert bot.is_closed()


# ------------------------------------------------------------------ signals and steps


async def test_first_signal_asks_second_forces() -> None:
    with StopSignals() as stopper:
        stopper.serving()
        signal.raise_signal(signal.SIGINT)
        assert await asyncio.wait_for(stopper.wait(), 1) == "Ctrl+C"
        assert not stopper.forced
        with pytest.raises(asyncio.CancelledError):
            signal.raise_signal(signal.SIGINT)
            await asyncio.sleep(1)  # the second Ctrl+C cancels this (the main) task
        asyncio.current_task().uncancel()  # type: ignore[union-attr]
        assert stopper.forced


async def test_signal_during_startup_cancels_at_once() -> None:
    with StopSignals() as stopper:
        with pytest.raises(asyncio.CancelledError):
            stopper.request("Ctrl+C")
            await asyncio.sleep(1)
        asyncio.current_task().uncancel()  # type: ignore[union-attr]
    assert stopper.forced


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="no SIGTERM")
async def test_sigterm_and_handlers_restored() -> None:
    before = signal.getsignal(signal.SIGTERM)
    with StopSignals() as stopper:
        stopper.serving()
        signal.raise_signal(signal.SIGTERM)
        assert await asyncio.wait_for(stopper.wait(), 1) == "SIGTERM"
    assert signal.getsignal(signal.SIGTERM) is before


async def test_step_times_out_logs_and_skips_when_forced(caplog: pytest.LogCaptureFixture) -> None:
    async def hangs() -> None:
        await asyncio.sleep(60)

    async def fails() -> None:
        raise OSError("socket gone")

    ran: list[str] = []

    async def ok() -> None:
        ran.append("ok")

    with caplog.at_level(logging.WARNING):
        assert not await step("the hub", hangs, 0.05)
        assert not await step("the board", fails, 1)
    assert "the hub did not stop within" in caplog.text and "could not stop the board" in caplog.text
    stopper = StopSignals()
    stopper.forced = True
    assert not await step("Discord", ok, 1, stopper=stopper)
    assert await step("the database", ok, 1, stopper=stopper, essential=True)
    assert ran == ["ok"]


async def test_cancel_leftovers() -> None:
    async def stubborn() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise RuntimeError("cleanup failed") from None

    tasks = [asyncio.create_task(asyncio.sleep(60)), asyncio.create_task(stubborn())]
    await asyncio.sleep(0)
    assert await cancel_leftovers(1) >= 2
    assert all(t.done() for t in tasks)


# ------------------------------------------------------------------ AMP session and live cards


async def test_amp_close_closes_the_session_when_logout_hangs() -> None:
    fake = FakeAmp()
    server = TestServer(fake.app())
    await server.start_server()
    try:
        amp = AmpClient(str(server.make_url("")).rstrip("/"), Credentials(USERNAME, PASSWORD))
        await amp.start()

        async def hang() -> None:
            await asyncio.sleep(60)

        amp.auth.logout = hang  # type: ignore[method-assign]
        await asyncio.wait_for(amp.close(logout_timeout=0.05), 2)
        assert amp.session.closed
    finally:
        await server.close()


class Partial:
    def __init__(self, edits: list[tuple[int, discord.Embed]], message_id: int, fail: bool) -> None:
        self.edits, self.id, self.fail = edits, message_id, fail

    async def edit(self, *, embed: discord.Embed) -> None:
        if self.fail:
            raise discord.HTTPException(SimpleNamespace(status=404, reason="gone"), "Unknown Message")  # type: ignore[arg-type]
        self.edits.append((self.id, embed))


async def test_live_cards_marked_stopped(db: Database) -> None:
    h = await Harness(db).ready()
    cards: LiveCards = h.ctx.live_cards
    edits: list[tuple[int, discord.Embed]] = []
    gone = 72

    def partial_messageable(channel_id: int) -> Any:
        return SimpleNamespace(get_partial_message=lambda mid: Partial(edits, mid, fail=mid == gone))

    h.client.get_partial_messageable = partial_messageable  # type: ignore[attr-defined]
    live = discord.Embed(title="Survival", colour=0x2ECC71)
    live.set_footer(text="Updates every 3 minutes")
    cards._shown = {SURVIVAL: (55, 71, live), "other": (55, gone, live)}
    assert await cards.mark_stopped() == 1
    [(message_id, embed)] = edits
    assert message_id == 71 and embed.footer.text == STOPPED_FOOTER and embed.colour == discord.Colour(MUTED)
    assert live.footer.text == "Updates every 3 minutes"  # the kept copy is untouched


# ------------------------------------------------------------------ end to end: python -m portcullis, then SIGTERM


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="no SIGTERM")
def test_run_stops_cleanly_on_sigterm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole run() as ``python -m portcullis`` runs it (its own loop, the main task), Discord stubbed."""
    sessions: list[aiohttp.ClientSession] = []
    real_init = aiohttp.ClientSession.__init__

    def track(self: aiohttp.ClientSession, *a: Any, **k: Any) -> None:
        real_init(self, *a, **k)
        sessions.append(self)

    async def login(self: PortcullisBot, token: str) -> None:  # Discord without the network
        await self.setup_hook()

    async def connect(self: PortcullisBot, *, reconnect: bool = True) -> None:
        asyncio.get_running_loop().call_later(0.2, signal.raise_signal, signal.SIGTERM)
        await asyncio.Event().wait()

    async def no_sync(self: app_commands.CommandTree, *, guild: Any = None) -> list[Any]:
        return []

    monkeypatch.setattr(PortcullisBot, "login", login)
    monkeypatch.setattr(PortcullisBot, "connect", connect)
    async def no_emojis(client: Any) -> list[str]:
        return []

    monkeypatch.setattr(app_commands.CommandTree, "sync", no_sync)
    monkeypatch.setattr(EMOJI, "fetch", no_emojis)  # the shared registry; a failed lookup would leak into other tests

    async def scenario() -> tuple[str, list[asyncio.Task[Any]]]:
        server = TestServer(FakeAmp().app())
        await server.start_server()
        monkeypatch.setattr(aiohttp.ClientSession, "__init__", track)
        settings = Settings(amp_url=str(server.make_url("")).rstrip("/"), amp_username=USERNAME,
                            amp_password=PASSWORD, discord_token="x.y.z", guild_id=GUILD,
                            database=tmp_path / "bot.db")
        try:
            reason = await entry.run(settings)  # not wait_for: on 3.11 that adds a task run() would cancel
        finally:
            monkeypatch.undo()
            await server.close()
        left = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        return reason, left

    before = signal.getsignal(signal.SIGTERM)
    reason, left = asyncio.run(scenario())
    assert reason == "SIGTERM"
    assert left == []
    assert sessions and all(s.closed for s in sessions)
    assert signal.getsignal(signal.SIGTERM) is before


async def test_admin_stop_greys_cards_then_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    from portcullis.bot import admin

    calls: list[str] = []

    async def no_wait(_seconds: float) -> None:
        return None

    class Client:
        async def mark_stopped(self) -> int:
            calls.append("cards")
            raise discord.HTTPException(SimpleNamespace(status=429, reason="slow"), "rate limited")  # type: ignore[arg-type]

        async def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(admin.asyncio, "sleep", no_wait)
    await admin.stop_soon(Client())
    assert calls == ["cards", "close"]  # a failed card edit never blocks the stop
