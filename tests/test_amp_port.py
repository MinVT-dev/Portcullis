# SPDX-License-Identifier: AGPL-3.0-or-later
"""The real AmpPort against the fake AMP server: REST mapping and the shared shard hub."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from portcullis.amp.port import AmpServerPort
from portcullis.core.port import AmpPort
from portcullis.core.types import ConsoleLine, PlayerJoined, PowerAction, ServerId, ServerState, StateChanged
from portcullis_api.auth import Credentials
from portcullis_api.client import AmpClient
from portcullis_api.errors import AuthFailed
from portcullis_api.testing import PASSWORD, SHARD_ID, TENANT, USERNAME, FakeAmp, wait_for

SID = ServerId(SHARD_ID)


@pytest.fixture
async def port(base_url: str, fake: FakeAmp) -> AsyncIterator[AmpServerPort]:
    client = AmpClient(base_url, Credentials(USERNAME, PASSWORD), keep_session_renewed=False,
                       refresh_margin=timedelta(minutes=30))
    client.auth._clock = fake.clock  # share the fake's clock
    p = AmpServerPort(client)
    yield p
    await p.close()
    await client.close()


def test_is_an_amp_port(port: AmpServerPort) -> None:
    assert isinstance(port, AmpPort)


async def test_list_servers_maps_to_core_types(port: AmpServerPort) -> None:
    servers = await port.list_servers()
    assert [(s.id, s.name) for s in servers] == [(SHARD_ID, "Survival")]


async def test_status_combines_state_roster_and_metrics(port: AmpServerPort) -> None:
    status = await port.get_status(SID)
    assert status.state is ServerState.RUNNING and status.state.label == "Online"
    assert status.raw_status == 20
    assert [p.name for p in status.players] == ["Steve"]
    assert status.cpu_percent == 12.5 and status.memory_mb == 2048 and status.memory_limit_mb == 4096


async def test_status_without_roster_permission(port: AmpServerPort, fake: FakeAmp) -> None:
    fake.roster_forbidden = True
    status = await port.get_status(SID)
    assert status.players == () and status.state is ServerState.RUNNING


async def test_power_console_history_backups(port: AmpServerPort, fake: FakeAmp) -> None:
    for action in PowerAction:
        await port.power(SID, action)
    assert [c for c in fake.calls if c.startswith("power:")] == [f"power:{a.value}" for a in PowerAction]
    await port.send_console(SID, "say hello")
    assert "console:say hello" in fake.calls
    assert await port.recent_console(SID, limit=2) == ["line 4", "line 5"]
    backups = await port.list_backups(SID)
    assert [b.name for b in backups] == ["new", "old"]
    assert backups[0].sticky and backups[1].size_mb == 10


async def test_events_share_one_connection_and_rejoin(port: AmpServerPort, fake: FakeAmp) -> None:
    got_a: list = []
    got_b: list = []

    async def consume(target: list) -> None:
        async with contextlib.aclosing(port.events(SID)) as events:
            async for event in events:
                target.append(event)

    a = asyncio.create_task(consume(got_a))
    b = asyncio.create_task(consume(got_b))
    await wait_for(lambda: len(fake.joined) >= 1)
    await asyncio.sleep(0.05)
    assert fake.calls.count("ws") == 1  # one socket for both subscribers

    await fake.broadcast("ConsoleLine", {"shardId": SHARD_ID, "text": "<Steve> hi", "seq": "7"})
    await wait_for(lambda: len(got_a) == 1 and len(got_b) == 1)
    assert isinstance(got_a[0], ConsoleLine) and got_a[0].seq == 7

    await fake.drop_all()
    await wait_for(lambda: fake.calls.count("ws") == 2 and len(fake.joined) >= 2)
    await fake.broadcast("UserJoined", SHARD_ID, {"name": "Alex"})
    await wait_for(lambda: len(got_a) == 2)
    assert isinstance(got_a[1], PlayerJoined) and got_a[1].player.name == "Alex"

    a.cancel()
    await asyncio.gather(a, return_exceptions=True)
    assert fake.left == []  # b still listens
    b.cancel()
    await asyncio.gather(b, return_exceptions=True)
    await wait_for(lambda: fake.left == [SHARD_ID])


async def test_events_join_with_tenant_and_parse_live_shapes(port: AmpServerPort, fake: FakeAmp) -> None:
    got: list = []

    async def consume() -> None:
        async for event in port.events(SID):
            got.append(event)

    task = asyncio.create_task(consume())
    await wait_for(lambda: fake.joined == [SHARD_ID])
    assert fake.join_attempts == [[TENANT, SHARD_ID]]
    # Shape seen live on AMP 3.0.0.6: [tenantId, shardId, ShardState].
    await fake.broadcast("ShardStateChanged", TENANT, SHARD_ID,
                         {"shardId": SHARD_ID, "tenantId": TENANT, "status": 25, "isRunning": True,
                          "isStopped": False, "isBusy": False})
    await wait_for(lambda: len(got) == 1)
    assert isinstance(got[0], StateChanged) and got[0].state is ServerState.RUNNING and got[0].raw_status == 25
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await wait_for(lambda: fake.left == [SHARD_ID])


async def test_subscribers_are_told_when_the_hub_stops_for_good(port: AmpServerPort, fake: FakeAmp) -> None:
    """A refused login ends the hub task. The subscriber must get an error to retry on, not wait forever."""
    from portcullis.amp.port import EventStreamEnded

    events = port.events(SID)
    waiter = asyncio.create_task(anext(events))
    await wait_for(lambda: len(fake.joined) >= 1)
    conn, task = port.hub._conns[SID]

    async def refused() -> None:
        raise AuthFailed("AMP refused the login", status=401)

    conn._ticket_source = refused  # type: ignore[assignment]
    await fake.drop_all()
    with pytest.raises(EventStreamEnded):
        await asyncio.wait_for(waiter, 5)
    assert SID not in port.hub._conns  # the next subscribe opens a fresh connection
    with contextlib.suppress(Exception):
        await events.aclose()
