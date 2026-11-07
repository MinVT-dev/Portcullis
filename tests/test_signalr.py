# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hub connection: fresh ticket per connect, re-join after reconnect, recovery from every kind of drop."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import aiohttp
import pytest

from portcullis.amp.auth import TokenManager
from portcullis.amp.signalr import HubConnection, HubEvent, Mode, decode_frame, encode
from tests.fakes.amp_server import SHARD_ID, FakeAmp, wait_for


class Harness:
    def __init__(self, conn: HubConnection) -> None:
        self.conn = conn
        self.events: list[HubEvent] = []
        self.lifecycle: list[tuple[str, dict]] = []
        self.task: asyncio.Task[None] | None = None

    async def handler(self, event: HubEvent) -> None:
        self.events.append(event)

    def targets(self) -> list[str]:
        return [e.target for e in self.events]


@pytest.fixture
async def make_hub(base_url: str, session: aiohttp.ClientSession,
                   tokens: TokenManager) -> AsyncIterator:
    harnesses: list[Harness] = []

    def build(mode: Mode = "direct", **kw) -> Harness:
        async def join(c: HubConnection) -> None:
            await c.invoke("JoinShard", SHARD_ID)

        h: Harness
        conn = HubConnection(base_url, "shard", session, tokens.hub_ticket, mode=mode, on_connected=join,
                             on_lifecycle=lambda kind, d: h.lifecycle.append((kind, d)),
                             backoff=(0, 0.05), keepalive=0.2, **kw)
        h = Harness(conn)
        h.task = asyncio.create_task(conn.run(h.handler))
        harnesses.append(h)
        return h

    yield build
    for h in harnesses:
        await h.conn.close()
        assert h.task is not None
        h.task.cancel()
        await asyncio.gather(h.task, return_exceptions=True)


async def test_direct_connect_uses_one_ticket_and_joins(make_hub, fake: FakeAmp) -> None:
    h = make_hub("direct")
    await wait_for(lambda: fake.joined == [SHARD_ID])
    await fake.broadcast("UserJoined", {"shardId": SHARD_ID, "name": "Steve"})
    await wait_for(lambda: h.targets() == ["UserJoined"])
    assert h.events[0].arguments[0]["name"] == "Steve"
    assert h.conn.tickets_used == 1
    assert "negotiate" not in fake.calls


async def test_reconnect_after_drop_uses_fresh_ticket_and_rejoins(make_hub, fake: FakeAmp) -> None:
    h = make_hub("direct")
    await wait_for(lambda: len(fake.joined) == 1)
    await fake.drop_all()
    await wait_for(lambda: len(fake.joined) == 2)
    assert h.conn.tickets_used == 2
    assert fake.refused_tickets == 0  # never reused a ticket
    await fake.broadcast("UserLeft", {"name": "Steve"})
    await wait_for(lambda: h.targets() == ["UserLeft"])
    kinds = [k for k, _ in h.lifecycle]
    assert kinds[:3] == ["connected", "disconnected", "connected"]


async def test_server_close_message_triggers_reconnect(make_hub, fake: FakeAmp) -> None:
    h = make_hub("direct")
    await wait_for(lambda: len(fake.joined) == 1)
    await fake.send_close("Server is restarting")
    await wait_for(lambda: len(fake.joined) == 2)
    assert "Server is restarting" in h.lifecycle[1][1]["error"]


async def test_silent_server_is_detected_and_replaced(make_hub, fake: FakeAmp) -> None:
    h = make_hub("direct", server_timeout=0.5)
    await wait_for(lambda: len(fake.joined) == 1)
    fake.mute_all()  # the socket stays open but nothing comes back: a half-open connection
    await wait_for(lambda: len(fake.joined) == 2)
    assert "no message from the server" in h.lifecycle[1][1]["error"]


async def test_reconnect_after_session_revoked(make_hub, fake: FakeAmp, tokens: TokenManager) -> None:
    make_hub("direct")
    await wait_for(lambda: len(fake.joined) == 1)
    fake.revoke_all()  # the old session dies, and with it the socket
    await fake.drop_all()
    await wait_for(lambda: len(fake.joined) == 2)
    assert tokens.logins == 2


async def test_negotiate_mode_uses_two_fresh_tickets(make_hub, fake: FakeAmp) -> None:
    h = make_hub("negotiate")
    await wait_for(lambda: len(fake.joined) == 1)
    assert h.conn.tickets_used == 2
    assert fake.calls.count("negotiate") == 1
    assert fake.refused_tickets == 0


async def test_negotiate_reuse_fails_when_negotiate_consumes_ticket(make_hub, fake: FakeAmp) -> None:
    fake.negotiate_consumes = True
    h = make_hub("negotiate-reuse")
    await wait_for(lambda: fake.refused_tickets >= 2)
    assert fake.joined == []
    assert h.conn.last_error and "401" in h.conn.last_error


async def test_negotiate_reuse_works_when_negotiate_only_peeks(make_hub, fake: FakeAmp) -> None:
    fake.negotiate_consumes = False
    h = make_hub("negotiate-reuse")
    await wait_for(lambda: len(fake.joined) == 1)
    assert h.conn.tickets_used == 1


async def test_ticket_values_never_reach_lifecycle_or_errors(make_hub, fake: FakeAmp) -> None:
    fake.negotiate_consumes = True
    h = make_hub("negotiate-reuse")
    await wait_for(lambda: len(h.lifecycle) >= 2)
    tickets = list(fake.tickets)
    text = repr(h.lifecycle) + str(h.conn.last_error)
    assert tickets and not any(t in text for t in tickets)


def test_frame_codec_roundtrip() -> None:
    frame = encode({"type": 1, "target": "A", "arguments": []}) + encode({"type": 6})
    assert [m["type"] for m in decode_frame(frame)] == [1, 6]


async def test_events_iterator(base_url: str, session: aiohttp.ClientSession, tokens: TokenManager,
                               fake: FakeAmp) -> None:
    async def join(c: HubConnection) -> None:
        await c.invoke("JoinShard", SHARD_ID)

    conn = HubConnection(base_url, "shard", session, tokens.hub_ticket, on_connected=join)

    async def produce() -> None:
        await wait_for(lambda: len(fake.joined) == 1)
        await fake.broadcast("ShardStateChanged", {"state": 20})

    producer = asyncio.create_task(produce())
    async with contextlib.aclosing(conn.events()) as events:
        async for event in events:
            assert event.target == "ShardStateChanged"
            break
    await producer
    assert not conn.connected
