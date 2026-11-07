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
from portcullis.amp.errors import MissingCapability
from portcullis.amp.events import parse_hub_event, state_from, to_int
from portcullis.amp.signalr import HubConnection, HubEvent, Mode
from portcullis.core.types import (
    Backup,
    Player,
    PowerAction,
    Schedule,
    ServerEvent,
    ServerId,
    ServerInfo,
    ServerStatus,
)

log = logging.getLogger(__name__)

DEFAULT_TENANT = "_root"  # the only tenant on a single-install AMP 3; used if a shard reports none


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ShardEvents:
    """Live events for each subscribed shard, over one ``/hubs/shard`` connection per shard.

    One connection per shard because AMP's ``ConsoleLine`` carries no shard id, and its ``seq`` is
    shared by every shard: on a connection that has joined several shards, console lines can't be
    told apart (seen live on AMP 3.0.0.6). Each connection joins with
    ``JoinShard(tenantId, shardId)``, as the panel does, and joins again after every reconnect.
    The connection opens with the first subscriber and closes after the last one leaves.
    """

    def __init__(self, client: AmpClient, *, mode: Mode = "direct", queue_size: int = 1000) -> None:
        self._client = client
        self._mode = mode
        self._queue_size = queue_size
        self._subs: dict[str, set[asyncio.Queue[ServerEvent]]] = {}
        self._conns: dict[str, tuple[HubConnection, asyncio.Task[None]]] = {}
        self.dropped = 0  # events lost because a subscriber fell behind
        self._tenants: dict[str, str] = {}  # shard id -> tenant id

    @property
    def connections(self) -> dict[str, HubConnection]:
        return {sid: conn for sid, (conn, _) in self._conns.items()}

    async def _tenant(self, sid: str) -> str:
        if sid not in self._tenants:
            shard = await self._client.shards.get_shards_by_id(sid)
            self._tenants[sid] = shard.tenant_id or DEFAULT_TENANT
        return self._tenants[sid]

    def _open(self, sid: str) -> None:
        async def join(conn: HubConnection) -> None:
            await conn.invoke("JoinShard", await self._tenant(sid), sid)

        async def handle(event: HubEvent) -> None:
            parsed = parse_hub_event(event, (sid,))
            if parsed is None or parsed.server.lower() != sid:
                return
            for queue in self._subs.get(sid, ()):
                try:
                    queue.put_nowait(parsed)
                except asyncio.QueueFull:
                    self.dropped += 1

        conn = self._client.hub("shard", mode=self._mode, on_connected=join)
        self._conns[sid] = (conn, asyncio.create_task(conn.run(handle), name=f"amp-shard-hub-{sid[:8]}"))

    async def _close(self, sid: str) -> None:
        entry = self._conns.pop(sid, None)
        if entry is None:
            return
        conn, task = entry
        tenant = self._tenants.get(sid)
        if tenant is not None and conn.connected:
            with contextlib.suppress(Exception):
                await conn.send("LeaveShard", tenant, sid)
        await conn.close()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def subscribe(self, server: ServerId) -> AsyncIterator[ServerEvent]:
        sid = server.lower()
        queue: asyncio.Queue[ServerEvent] = asyncio.Queue(self._queue_size)
        self._subs.setdefault(sid, set()).add(queue)
        if sid not in self._conns or self._conns[sid][1].done():
            self._open(sid)
        try:
            while True:
                yield await queue.get()
        finally:
            subs = self._subs.get(sid)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    del self._subs[sid]
                    await self._close(sid)

    async def close(self) -> None:
        for sid in list(self._conns):
            await self._close(sid)


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
            out.append(
                Backup(
                    id=str(node.id),
                    name=node.name,
                    created_at=node.created_at,
                    size_mb=to_int(node.size_mb),
                    status=str(node.status.root) if node.status is not None else None,
                    sticky=bool(node.sticky),
                )
            )
        oldest = datetime.min.replace(tzinfo=UTC)
        out.sort(key=lambda b: b.created_at or oldest, reverse=True)
        return out

    async def create_backup(self, server: ServerId, name: str) -> None:
        """Take a snapshot shaped like the newest existing one.

        The spec doesn't document the integer values for a snapshot's kind and content, so they're
        copied from a backup AMP already made rather than guessed. AMP may stop or pause the server
        while it captures.
        """
        graph = await self.client.files_backups.get_shards_snapshots(server)
        usable = [n for n in graph.nodes or [] if n.kind is not None and n.content is not None]
        if not usable:
            raise ValueError("there is no earlier backup to copy the settings from; make the first one in AMP")
        latest = max(usable, key=lambda n: n.created_at or datetime.min.replace(tzinfo=UTC))
        await self.client.files_backups.post_shards_snapshots(server, {
            "kind": latest.kind.root, "content": latest.content.root, "name": name,
            "description": "Made from Discord", "sticky": False})

    async def restore_backup(self, server: ServerId, backup_id: str) -> None:
        await self.client.files_backups.post_shards_snapshots_restore(server, backup_id)

    async def list_schedules(self, server: ServerId) -> list[Schedule]:
        out = []
        for dto in await self.client.scheduling.get_shards_schedules(server):
            d = dto.definition
            out.append(Schedule(id=str(d.id), name=d.name or str(d.id), enabled=bool(d.enabled),
                                next_fire=dto.next_fire_time))
        return out

    async def run_schedule(self, server: ServerId, schedule_id: str) -> None:
        await self.client.scheduling.post_shards_schedules_run(server, schedule_id)

    async def set_schedule_enabled(self, server: ServerId, schedule_id: str, enabled: bool) -> None:
        await self.client.scheduling.post_shards_schedules_enabled(server, schedule_id, {"enabled": enabled})

    async def permissions(self) -> list[str]:
        """The bot account's grant strings. ``">"`` means everything (seen on an admin account)."""
        mine = await self.client.my_account.get_me_permissions()
        return list(mine.grants)

    async def close(self) -> None:
        await self.hub.close()
