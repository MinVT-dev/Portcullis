# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keeping the bot useful while the AMP panel is down or slow.

``GuardedPort`` wraps the real ``AmpPort`` and adds three things:

* **Retries with back-off** for reads only (status, lists, history). A power action, a console
  line or a backup is never sent twice: if it fails, the member is told and decides.
* **A circuit breaker.** After ``trip_after`` panel-down failures in a row the circuit opens and
  every call fails at once with ``PanelUnavailable`` instead of waiting on a timeout. After a
  cool-down (10 s, doubling up to a minute) one call is let through to test the panel; if it
  answers, the circuit closes. Opening and closing are each logged once, so an outage is one log
  line, not one per card, command and refresh.
* **Last-known statuses.** Every status AMP returns is kept with the time it arrived.
  ``cached_status`` hands it back so a live card can show the last data, marked stale, instead of
  "Unknown". Callers opt in; alerts and commands never see cached data by accident.

"Panel down" means AMP did not answer: a connection error, a timeout, or a 5xx. A 4xx (no
permission, not found, busy) is a real answer and neither trips the circuit nor gets retried.
Errors are read by shape, because core code may not import the AMP client.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from portcullis.core.port import AmpPort
from portcullis.core.types import (
    Backup,
    BackupTimeline,
    DiskUsage,
    Player,
    PowerAction,
    Schedule,
    ScheduleRun,
    ServerEvent,
    ServerId,
    ServerInfo,
    ServerState,
    ServerStatus,
)

log = logging.getLogger("portcullis.panel")

T = TypeVar("T")

DEFAULT_RETRIES = 2  # extra tries for a read, so three in all
DEFAULT_TRIP_AFTER = 3  # panel-down failures in a row before the circuit opens
RETRY_BASE = 0.5  # seconds before the first retry; doubles, with jitter
COOLDOWN_MIN = 10.0
COOLDOWN_MAX = 60.0


class PanelUnavailable(ConnectionError):
    """The circuit is open: the panel stopped answering and the bot isn't asking it for now.

    A ``ConnectionError`` so ``bot.errors.plain_error`` already words it for members, and
    ``retry_after`` says when the next test call goes through.
    """

    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(0.0, retry_after)
        super().__init__(f"The panel isn't answering; the next check is in {max(1, round(self.retry_after))} s.")


def panel_down(exc: BaseException) -> bool:
    """Did AMP fail to answer (as opposed to answering "no")?"""
    if isinstance(exc, PanelUnavailable):
        return False  # already counted when the circuit opened
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    if type(exc).__name__ == "Unreachable":
        return True
    status = getattr(exc, "status", None)
    return isinstance(status, int) and not isinstance(status, bool) and status >= 500


@dataclass
class PanelStats:
    """Counters for the metrics endpoint."""

    calls: int = 0
    failures: int = 0
    retries: int = 0
    rejected: int = 0  # calls refused at once because the circuit was open
    trips: int = 0


class Breaker:
    """Closed (normal), open (refuse at once) or half-open (one test call in flight)."""

    def __init__(self, trip_after: int = DEFAULT_TRIP_AFTER, *, clock: Callable[[], float] = time.monotonic,
                 on_open: Callable[[], None] | None = None, on_close: Callable[[float], None] | None = None) -> None:
        self.trip_after = max(1, trip_after)
        self.clock = clock
        self.on_open = on_open
        self.on_close = on_close
        self.failures = 0
        self.opened_at: float | None = None
        self.down_since: float | None = None
        self.retry_at = 0.0
        self.cooldown = COOLDOWN_MIN
        self.testing = False

    @property
    def open(self) -> bool:
        return self.opened_at is not None

    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half-open" if self.testing or self.clock() >= self.retry_at else "open"

    def before_call(self) -> bool:
        """True if this call may go out. While open, only one test call goes out at a time."""
        if self.opened_at is None:
            return True
        if self.testing or self.clock() < self.retry_at:
            return False
        self.testing = True
        return True

    def wait(self) -> float:
        return max(0.0, self.retry_at - self.clock())

    def success(self) -> None:
        was_open = self.opened_at is not None
        now = self.clock()
        down_for = now - (self.down_since if self.down_since is not None else now)
        self.failures = 0
        self.opened_at = None
        self.down_since = None
        self.testing = False
        self.cooldown = COOLDOWN_MIN
        if was_open and self.on_close is not None:
            self.on_close(down_for)

    def failure(self) -> None:
        now = self.clock()
        if self.opened_at is not None:  # the test call failed: stay open, wait longer
            self.testing = False
            self.cooldown = min(COOLDOWN_MAX, self.cooldown * 2)
            self.retry_at = now + self.cooldown
            return
        self.failures += 1
        if self.down_since is None:
            self.down_since = now
        if self.failures >= self.trip_after:
            self.opened_at = now
            self.retry_at = now + self.cooldown
            if self.on_open is not None:
                self.on_open()


# --------------------------------------------------------------------------- status <-> JSON


def status_to_json(status: ServerStatus) -> dict[str, Any]:
    def when(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "id": status.id, "state": status.state.value, "max_players": status.max_players,
        "players": [{"name": p.name, "id": p.id, "joined_at": when(p.joined_at)} for p in status.players],
        "cpu_percent": status.cpu_percent, "memory_mb": status.memory_mb, "memory_limit_mb": status.memory_limit_mb,
        "message": status.message, "started_at": when(status.started_at),
    }


def status_from_json(data: object) -> ServerStatus | None:
    """The reverse, or None for anything unreadable (an old or hand-edited state file)."""
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return None

    def when(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def number(value: object) -> Any:
        return value if isinstance(value, int | float) and not isinstance(value, bool) else None

    try:
        state = ServerState(data.get("state"))
    except ValueError:
        state = ServerState.UNKNOWN
    players = tuple(Player(name=p["name"], id=p.get("id") if isinstance(p.get("id"), str) else None,
                           joined_at=when(p.get("joined_at")))
                    for p in data.get("players") or [] if isinstance(p, dict) and isinstance(p.get("name"), str))
    memory = number(data.get("memory_mb"))
    limit = number(data.get("memory_limit_mb"))
    max_players = number(data.get("max_players"))
    return ServerStatus(id=ServerId(data["id"]), state=state, players=players,
                        max_players=int(max_players) if max_players is not None else None,
                        cpu_percent=number(data.get("cpu_percent")),
                        memory_mb=int(memory) if memory is not None else None,
                        memory_limit_mb=int(limit) if limit is not None else None,
                        message=data["message"] if isinstance(data.get("message"), str) else None,
                        started_at=when(data.get("started_at")))


# --------------------------------------------------------------------------- the wrapper


class GuardedPort:
    """An ``AmpPort`` with retries for reads, a circuit breaker and a last-known status cache."""

    def __init__(self, inner: AmpPort, *, retries: int = DEFAULT_RETRIES, trip_after: int = DEFAULT_TRIP_AFTER,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.inner = inner
        self.retries = max(0, retries)
        self.stats = PanelStats()
        self.breaker = Breaker(trip_after, clock=clock, on_open=self._opened, on_close=self._closed)
        self._sleep = sleep
        self._statuses: dict[str, tuple[ServerStatus, datetime]] = {}

    @property
    def reachable(self) -> bool:
        return not self.breaker.open

    def _opened(self) -> None:
        self.stats.trips += 1
        log.warning("The panel stopped answering. Portcullis checks it again every %.0f to %.0f s and keeps "
                    "showing the last known server status, marked as stale.", COOLDOWN_MIN, COOLDOWN_MAX)

    def _closed(self, down_for: float) -> None:
        log.info("The panel is answering again (it was unreachable for about %.0f s).", down_for)

    # ---------------------------------------------------------------- calling

    async def _call(self, fn: Callable[[], Awaitable[T]], *, read: bool) -> T:
        if not self.breaker.before_call():
            self.stats.rejected += 1
            raise PanelUnavailable(self.breaker.wait())
        testing = self.breaker.testing
        tries = (self.retries if read and not testing else 0) + 1
        attempt = 0
        while True:
            self.stats.calls += 1
            try:
                result = await fn()
            except asyncio.CancelledError:
                self.breaker.testing = False if testing else self.breaker.testing
                raise
            except Exception as exc:
                if not panel_down(exc):
                    self.breaker.success()  # AMP answered, even if it said no
                    raise
                self.stats.failures += 1
                attempt += 1
                if attempt < tries:
                    self.stats.retries += 1
                    await self._sleep(RETRY_BASE * 2 ** (attempt - 1) * random.uniform(0.8, 1.2))
                    continue
                self.breaker.failure()
                raise
            self.breaker.success()
            return result

    # ---------------------------------------------------------------- last-known statuses

    def cached_status(self, server: str) -> tuple[ServerStatus, datetime] | None:
        """The last status AMP returned for ``server`` and when, for showing as stale. None if never seen."""
        return self._statuses.get(str(server))

    def export_statuses(self) -> dict[str, Any]:
        return {sid: {"at": at.isoformat(), "status": status_to_json(st)} for sid, (st, at) in self._statuses.items()}

    def import_statuses(self, data: object) -> int:
        """Load statuses saved by an earlier run (restart recovery). Never replaces fresher ones."""
        if not isinstance(data, dict):
            return 0
        loaded = 0
        for sid, entry in data.items():
            if not isinstance(entry, dict) or sid in self._statuses:
                continue
            status = status_from_json(entry.get("status"))
            try:
                at = datetime.fromisoformat(entry.get("at", ""))
            except (TypeError, ValueError):
                continue
            if status is None or status.id != sid:
                continue
            self._statuses[sid] = (status, at if at.tzinfo else at.replace(tzinfo=UTC))
            loaded += 1
        return loaded

    # ---------------------------------------------------------------- AmpPort: reads (retried)

    async def list_servers(self) -> list[ServerInfo]:
        return await self._call(self.inner.list_servers, read=True)

    async def get_status(self, server: ServerId) -> ServerStatus:
        status = await self._call(lambda: self.inner.get_status(server), read=True)
        self._statuses[str(server)] = (status, datetime.now(UTC))
        return status

    async def disk_usage(self, server: ServerId) -> DiskUsage:
        return await self._call(lambda: self.inner.disk_usage(server), read=True)

    async def recent_console(self, server: ServerId, limit: int = 50) -> list[str]:
        return await self._call(lambda: self.inner.recent_console(server, limit), read=True)

    async def list_backups(self, server: ServerId) -> list[Backup]:
        return await self._call(lambda: self.inner.list_backups(server), read=True)

    async def backup_timeline(self, server: ServerId) -> BackupTimeline:
        return await self._call(lambda: self.inner.backup_timeline(server), read=True)

    async def list_schedules(self, server: ServerId) -> list[Schedule]:
        return await self._call(lambda: self.inner.list_schedules(server), read=True)

    async def schedule_history(self, server: ServerId, schedule_id: str, limit: int = 10) -> list[ScheduleRun]:
        return await self._call(lambda: self.inner.schedule_history(server, schedule_id, limit), read=True)

    async def permissions(self) -> list[str]:
        return await self._call(self.inner.permissions, read=True)

    async def preview_expires_at(self) -> datetime | None:
        return await self._call(self.inner.preview_expires_at, read=True)

    # ---------------------------------------------------------------- AmpPort: actions (never retried)

    async def power(self, server: ServerId, action: PowerAction) -> None:
        await self._call(lambda: self.inner.power(server, action), read=False)

    async def send_console(self, server: ServerId, line: str) -> None:
        await self._call(lambda: self.inner.send_console(server, line), read=False)

    async def create_backup(self, server: ServerId, name: str) -> None:
        await self._call(lambda: self.inner.create_backup(server, name), read=False)

    async def restore_backup(self, server: ServerId, backup_id: str) -> str | None:
        return await self._call(lambda: self.inner.restore_backup(server, backup_id), read=False)

    async def delete_backup(self, server: ServerId, backup_id: str) -> None:
        await self._call(lambda: self.inner.delete_backup(server, backup_id), read=False)

    async def run_schedule(self, server: ServerId, schedule_id: str) -> None:
        await self._call(lambda: self.inner.run_schedule(server, schedule_id), read=False)

    async def set_schedule_enabled(self, server: ServerId, schedule_id: str, enabled: bool) -> None:
        await self._call(lambda: self.inner.set_schedule_enabled(server, schedule_id, enabled), read=False)

    async def delete_schedule(self, server: ServerId, schedule_id: str) -> None:
        await self._call(lambda: self.inner.delete_schedule(server, schedule_id), read=False)

    async def answer_licence(self, server: ServerId, accept: bool) -> None:
        await self._call(lambda: self.inner.answer_licence(server, accept), read=False)

    # ---------------------------------------------------------------- passed through

    def events(self, server: ServerId) -> AsyncIterator[ServerEvent]:
        return self.inner.events(server)  # the hub connection has its own reconnect and back-off

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)  # close() and anything AmpServerPort adds later
