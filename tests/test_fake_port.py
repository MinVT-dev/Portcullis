# SPDX-License-Identifier: AGPL-3.0-or-later
"""The in-memory fake behaves like an AmpPort, so core code can be built against it."""

from __future__ import annotations

import asyncio
import contextlib

from portcullis.core.port import AmpPort
from portcullis.core.types import ConsoleLine, PlayerJoined, PlayerLeft, PowerAction, ServerState, StateChanged
from tests.fakes.amp import FakeAmpPort


async def test_fake_port_lifecycle() -> None:
    amp = FakeAmpPort()
    assert isinstance(amp, AmpPort)
    sid = amp.add_server("s1", "Survival")
    seen: list = []

    async def listen() -> None:
        async with contextlib.aclosing(amp.events(sid)) as events:
            async for e in events:
                seen.append(e)

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    await amp.power(sid, PowerAction.START)
    amp.join(sid, "Steve")
    amp.say(sid, "<Steve> hi")
    await amp.send_console(sid, "say welcome")
    await amp.power(sid, PowerAction.STOP)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    kinds = [type(e) for e in seen]
    assert kinds == [StateChanged, PlayerJoined, ConsoleLine, PlayerLeft, StateChanged]
    assert (await amp.get_status(sid)).state is ServerState.STOPPED
    assert amp.sent == [(sid, "say welcome")]
    assert await amp.recent_console(sid) == ["<Steve> hi"]
