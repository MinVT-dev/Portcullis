# SPDX-License-Identifier: AGPL-3.0-or-later
"""``AmpServerPort``: the real ``AmpPort``, built on the generated client and the hub connection.

Generated models stop here; everything returned is a ``portcullis.core.types`` value.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from portcullis.amp import models as m
from portcullis.amp.client import AmpClient
from portcullis.amp.errors import AmpError, MissingCapability
from portcullis.amp.events import parse_hub_event, state_from, to_int
from portcullis.amp.signalr import HubConnection, HubEvent, Mode
from portcullis.core.types import (
    Backup,
    Player,
    PowerAction,
    ServerEvent,
    ServerId,
    ServerInfo,
    ServerStatus,
)

log = logging.getLogger(__name__)


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ShardEvents:
    """One ``/hubs/shard`` connection shared by every subscriber.

    Joins a shard when its first subscriber arrives, leaves when the last one goes, and joins all
    of them again after every reconnect (group membership does not survive a new connection).
    """

    def __init__(self, client: AmpClient, *, mode: Mode = "direct", queue_size: int = 1000) -> None:
        self._client = client
        self._mode = mode
        self._queue_size = queue_size
        self._subs: dict[str, set[asyncio.Queue[ServerEvent]]] = {}
        self.conn: HubConnection | None = None
        self._task: asyncio.Task[None] | None = None
        self.dropped = 0  # events lost because a subscriber fell behind

    async def _join_all(self, conn: HubConnection) -> None:
        for sid in list(self._subs):
            await conn.invoke("JoinShard", sid)

    async def _handle(self, event: HubEvent) -> None:
        parsed = parse_hub_event(event, self._subs.keys())
        if parsed is None:
            return
        for queue in self._subs.get(parsed.server.lower(), ()):
            try:
                queue.put_nowait(parsed)
            except asyncio.QueueFull:
                self.dropped += 1

    def _ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self.conn = self._client.hub("shard", mode=self._mode, on_connected=self._join_all)
            self._task = asyncio.create_task(self.conn.run(self._handle), name="amp-shard-hub")

    async def subscribe(self, server: ServerId) -> AsyncIterator[ServerEvent]:
        sid = server.lower()
        queue: asyncio.Queue[ServerEvent] = asyncio.Queue(self._queue_size)
        first = sid not in self._subs
        self._subs.setdefault(sid, set()).add(queue)
        self._ensure_running()
        if first and self.conn is not None and self.conn.connected:
            with contextlib.suppress(AmpError):
                await self.conn.invoke("JoinShard", sid)
        try:
            while True:
                yield await queue.get()
        finally:
            subs = self._subs.get(sid)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    del self._subs[sid]
                    if self.conn is not None and self.conn.connected:
                        with contextlib.suppress(Exception):
                            await self.conn.send("LeaveShard", sid)

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task


class AmpServerPort:
    def __init__(self, client: AmpClient, *, hub_mode: Mode = "direct") -> None:
        self.client = client
        self.hub = ShardEvents(client, mode=hub_mode)

    async def list_servers(self) -> list[ServerInfo]:
        shards = await self.client.shards.get_shards()
        return [self._info(s) for s in shards if s.id is not None and not s.deleted]

    @staticmethod
    def _info(shard: m.DeployedShard) -> ServerInfo:
        return ServerInfo(
            id=ServerId(str(shard.id)),
            name=shard.display_name or shard.name or str(shard.id),
            template=shard.template_slug,
            suspended=bool(shard.suspended),
            tags=tuple(shard.tags or ()),
        )

    async def get_status(self, server: ServerId) -> ServerStatus:
        shard, state = await asyncio.gather(
            self.client.shards.get_shards_by_id(server), self.client.shards.get_shards_state(server)
        )
        try:
            users = await self.client.shards.get_shards_users(server)
        except MissingCapability:
            users = []  # the bot's role lacks the roster permission; status still works
        mapped, raw = state_from(state.model_dump(by_alias=True), suspended=bool(shard.suspended))
        metrics = state.metrics
        return ServerStatus(
            id=server,
            state=mapped,
            raw_status=raw,
            players=tuple(Player(name=u.name, id=u.id, joined_at=u.joined_at) for u in users),
            cpu_percent=_float(metrics.cpu_usage_percent) if metrics else None,
            memory_mb=to_int(metrics.memory_usage_mb) if metrics else None,
            memory_limit_mb=to_int(metrics.memory_limit_mb) if metrics else None,
            message=state.status_message or state.last_error_message,
            started_at=state.started_at,
        )

    async def power(self, server: ServerId, action: PowerAction) -> None:
        calls = {
            PowerAction.START: self.client.shards.post_shards_start,
            PowerAction.STOP: self.client.shards.post_shards_stop,
            PowerAction.RESTART: self.client.shards.post_shards_restart,
            PowerAction.SLEEP: self.client.shards.post_shards_sleep,
            PowerAction.WAKE: self.client.shards.post_shards_wake,
            PowerAction.UPDATE: self.client.shards.post_shards_update,
        }
        await calls[action](server)

    async def send_console(self, server: ServerId, line: str) -> None:
        await self.client.shards.post_shards_console(server, m.ConsoleInputBody(command=line))

    async def recent_console(self, server: ServerId, limit: int = 50) -> list[str]:
        page = await self.client.shards.get_shards_console_history(server, limit=limit)
        return [line.text for line in page.lines]

    def events(self, server: ServerId) -> AsyncIterator[ServerEvent]:
        return self.hub.subscribe(server)

    async def list_backups(self, server: ServerId) -> list[Backup]:
        graph = await self.client.files_backups.get_shards_snapshots(server)
        out = []
        for node in graph.nodes or []:
            if node.id is None:
                continue
            out.append(Backup(
                id=str(node.id),
                name=node.name,
                created_at=node.created_at,
                size_mb=to_int(node.size_mb),
                status=str(node.status.root) if node.status is not None else None,
                sticky=bool(node.sticky),
            ))
        oldest = datetime.min.replace(tzinfo=UTC)
        out.sort(key=lambda b: b.created_at or oldest, reverse=True)
        return out

    async def close(self) -> None:
        await self.hub.close()
