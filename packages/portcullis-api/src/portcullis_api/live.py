# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live events for one shard over the ``/hubs/shard`` SignalR hub.

What was verified on a real AMP 3.0.0.6 panel (2026-11-07):

* Join with ``JoinShard(tenantId, shardId)`` and leave with ``LeaveShard(tenantId, shardId)``. A
  single-install panel has one tenant, ``_root``. The join returns the feeds it subscribed to
  (``core``, ``console``, ``snapshots``, ``schedule``).
* Group membership does not survive a reconnect, so the join runs again after every connect.
* ``ConsoleLine`` carries no shard id and its ``seq`` counts across all shards. Use **one connection
  per shard** if you need to know which shard a console line came from.
* ``Metrics`` ``[shardId, sample]`` about every 3 s, ``UserRoster`` ``[shardId, users]`` about every
  10 s, ``ShardStateChanged`` ``[tenantId, shardId, state]`` about every 9 s, for every shard of the
  tenant (filter on the shard id).

    async with AmpClient(url, Credentials(user, password)) as amp:
        async for event in watch_shard(amp, shard_id):
            print(event.target, event.arguments)
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from portcullis_api.signalr import HubConnection, HubEvent, Mode

if TYPE_CHECKING:
    from portcullis_api.client import AmpClient

DEFAULT_TENANT = "_root"  # the only tenant on a single-install AMP 3; used if a shard reports none


async def shard_tenant(client: AmpClient, shard_id: str) -> str:
    """The tenant a shard belongs to, which ``JoinShard`` needs as its first argument."""
    shard = await client.shards.get_shards_by_id(shard_id)
    return shard.tenant_id or DEFAULT_TENANT


def shard_hub(client: AmpClient, shard_id: str, tenant: str, *, mode: Mode = "direct",
              **kwargs: object) -> HubConnection:
    """A hub connection that joins ``shard_id`` after every (re)connect. Call ``run`` or ``events``."""

    async def join(conn: HubConnection) -> None:
        await conn.invoke("JoinShard", tenant, shard_id)

    return client.hub("shard", mode=mode, on_connected=join, **kwargs)  # type: ignore[arg-type]


async def leave_shard(conn: HubConnection, shard_id: str, tenant: str) -> None:
    """Best-effort ``LeaveShard`` before closing; errors are ignored because the socket may be gone."""
    if conn.connected:
        with contextlib.suppress(Exception):
            await conn.send("LeaveShard", tenant, shard_id)


async def watch_shard(client: AmpClient, shard_id: str, *, mode: Mode = "direct",
                      queue_size: int = 1000) -> AsyncIterator[HubEvent]:
    """Yield every hub event for one shard until the caller stops iterating.

    Events for other shards of the same tenant (``ShardStateChanged`` is sent for all of them) are
    filtered out when they name a shard id. ``ConsoleLine`` has no shard id and is always yielded,
    because this connection has joined only ``shard_id``.
    """
    tenant = await shard_tenant(client, shard_id)
    conn = shard_hub(client, shard_id, tenant, mode=mode)
    queue: asyncio.Queue[HubEvent] = asyncio.Queue(queue_size)
    wanted = shard_id.lower()

    async def handle(event: HubEvent) -> None:
        named = [a.lower() for a in event.arguments[:2] if isinstance(a, str)]
        if named and any(_looks_like_id(a) for a in named) and wanted not in named:
            return
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)

    task = asyncio.create_task(conn.run(handle), name=f"watch-shard-{shard_id[:8]}")
    try:
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield getter.result()
                continue
            getter.cancel()
            task.result()  # re-raise why the connection ended (HubGaveUp, AuthFailed, ...)
            return
    finally:
        await leave_shard(conn, shard_id, tenant)
        await conn.close()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def _looks_like_id(value: str) -> bool:
    return len(value) == 36 and value.count("-") == 4
