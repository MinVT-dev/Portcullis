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

from portcullis.amp.events import field_of, parse_hub_event, state_from, to_int
from portcullis.core.types import (
    Backup,
    BackupTimeline,
    Player,
    PowerAction,
    Schedule,
    ScheduleRun,
    ServerEvent,
    ServerId,
    ServerInfo,
    ServerStatus,
    TryLater,
)
from portcullis_api import models as m
from portcullis_api.auth import parse_instant
from portcullis_api.client import AmpClient
from portcullis_api.errors import Conflict, MissingCapability, Unreachable
from portcullis_api.live import leave_shard, shard_tenant
from portcullis_api.signalr import HubConnection, HubEvent, Mode

log = logging.getLogger(__name__)


class EventStreamEnded(Exception):
    """A shard's hub connection stopped for good (AMP refused the login or the join). Raised to its
    subscribers so they can retry, instead of leaving them waiting for events that will never come."""


@contextlib.asynccontextmanager
async def _snapshot_errors() -> AsyncIterator[None]:
    """The snapshot calls document 409 and 503 since the live spec of 2026-11-13. Say what they mean."""
    try:
        yield
    except Conflict as exc:
        raise TryLater("AMP is busy with another backup job on this server. Try again in a minute.") from exc
    except Unreachable as exc:
        if exc.status == 503:
            wait = f" AMP asked to wait {max(1, round(exc.retry_after))} s." if exc.retry_after else ""
            raise TryLater(f"AMP's backup storage isn't available right now.{wait} Try again later.") from exc
        raise


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
        self._subs: dict[str, set[asyncio.Queue[ServerEvent | EventStreamEnded]]] = {}
        self._conns: dict[str, tuple[HubConnection, asyncio.Task[None]]] = {}
        self.dropped = 0  # events lost because a subscriber fell behind
        self._tenants: dict[str, str] = {}  # shard id -> tenant id

    @property
    def connections(self) -> dict[str, HubConnection]:
        return {sid: conn for sid, (conn, _) in self._conns.items()}

    async def _tenant(self, sid: str) -> str:
        if sid not in self._tenants:
            self._tenants[sid] = await shard_tenant(self._client, sid)
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

        def ended(task: asyncio.Task[None]) -> None:
            # ``run`` only returns after close(), and only raises when reconnecting can't help. Either
            # way no more events will arrive, so wake every subscriber that is still listening.
            if task.cancelled():
                return
            reason = task.exception()
            if reason is not None:
                log.warning("live events for shard %s stopped: %s", sid[:8], reason)
            current = self._conns.get(sid)
            if current is not None and current[1] is task:
                del self._conns[sid]
            for queue in self._subs.get(sid, ()):
                while True:  # make room: the notice matters more than the oldest events
                    try:
                        queue.put_nowait(EventStreamEnded(str(reason) if reason else "the connection was closed"))
                        break
                    except asyncio.QueueFull:
                        queue.get_nowait()
                        self.dropped += 1

        conn = self._client.hub("shard", mode=self._mode, on_connected=join)
        task = asyncio.create_task(conn.run(handle), name=f"amp-shard-hub-{sid[:8]}")
        task.add_done_callback(ended)
        self._conns[sid] = (conn, task)

    async def _close(self, sid: str) -> None:
        entry = self._conns.pop(sid, None)
        if entry is None:
            return
        conn, task = entry
        tenant = self._tenants.get(sid)
        if tenant is not None:
            await leave_shard(conn, sid, tenant)
        await conn.close()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def subscribe(self, server: ServerId) -> AsyncIterator[ServerEvent]:
        sid = server.lower()
        queue: asyncio.Queue[ServerEvent | EventStreamEnded] = asyncio.Queue(self._queue_size)
        self._subs.setdefault(sid, set()).add(queue)
        if sid not in self._conns or self._conns[sid][1].done():
            self._open(sid)
        try:
            while True:
                item = await queue.get()
                if isinstance(item, EventStreamEnded):
                    raise item
                yield item
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
        # The last check before AMP: a line break would let one command smuggle in a second one. Callers
        # validate what members type, but templates can also arrive through /admin import.
        if not line or any(ord(c) < 32 or ord(c) == 127 for c in line):
            raise ValueError("a console command must be one line without control characters")
        await self.client.shards.post_shards_console(server, m.ConsoleInputBody(command=line))

    async def recent_console(self, server: ServerId, limit: int = 50) -> list[str]:
        page = await self.client.shards.get_shards_console_history(server, limit=limit)
        return [line.text for line in page.lines]

    def events(self, server: ServerId) -> AsyncIterator[ServerEvent]:
        return self.hub.subscribe(server)

    async def list_backups(self, server: ServerId) -> list[Backup]:
        return (await self.backup_timeline(server)).backups

    async def backup_timeline(self, server: ServerId) -> BackupTimeline:
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
        return BackupTimeline(out, head_id=str(graph.head_id) if graph.head_id else None,
                              restoring_id=str(graph.restoring_id) if graph.restoring_id else None)

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
        async with _snapshot_errors():
            await self.client.files_backups.post_shards_snapshots(server, {
                "kind": latest.kind.root, "content": latest.content.root, "name": name,
                "description": "Made from Discord", "sticky": False})

    async def restore_backup(self, server: ServerId, backup_id: str) -> str | None:
        """AMP takes a PreRestore snapshot of the current state first (per the spec), then rewinds in
        the background. The returned node is the one to follow in the timeline."""
        async with _snapshot_errors():
            node = await self.client.files_backups.post_shards_snapshots_restore(server, backup_id)
        return str(node.id) if node is not None and node.id is not None else None

    async def delete_backup(self, server: ServerId, backup_id: str) -> None:
        async with _snapshot_errors():
            await self.client.files_backups.delete_shards_snapshots_by_snapshot_id(server, backup_id)

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

    async def delete_schedule(self, server: ServerId, schedule_id: str) -> None:
        await self.client.scheduling.delete_shards_schedules_by_schedule_id(server, schedule_id)

    async def schedule_history(self, server: ServerId, schedule_id: str, limit: int = 10) -> list[ScheduleRun]:
        runs = await self.client.scheduling.get_shards_schedules_history(server, schedule_id, limit=limit)
        return [ScheduleRun(started_at=r.started_at or r.queued_at, finished_at=r.completed_at,
                            status=r.status.value if r.status is not None else None, error=r.error_message)
                for r in runs]

    async def answer_licence(self, server: ServerId, accept: bool) -> None:
        # accept needs settings.write and decline needs power.start on the bot's AMP account.
        if accept:
            await self.client.shards.post_shards_eula_accept(server)
        else:
            await self.client.shards.post_shards_eula_decline(server)

    async def permissions(self) -> list[str]:
        """The bot account's grant strings. ``">"`` means everything (seen on an admin account)."""
        mine = await self.client.my_account.get_me_permissions()
        return list(mine.grants)

    async def preview_expires_at(self) -> datetime | None:
        # Read as plain JSON, not as the generated AppStatus: the spec types ``daysRemaining`` as a
        # required number but documents it as null until AMP has verified the time.
        status = await self.client.transport.request("GET", "/api/Health/status")
        expires = field_of(field_of(status, "preview"), "expiresAt")
        return parse_instant(expires) if isinstance(expires, str) else None

    async def close(self) -> None:
        await self.hub.close()
