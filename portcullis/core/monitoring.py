# SPDX-License-Identifier: AGPL-3.0-or-later
"""The arithmetic behind thresholds, history and graphs, with no Discord or database in it.

* ``sample_from`` reads AMP's ``Metrics`` hub event (``[shardId, ShardMetricsSample]``, about every 3 to 5
  seconds per followed server, seen live on 3.0.0.6). Thresholds use it, so watching CPU and memory costs
  no extra AMP calls. Field names are read leniently because the live payload's keys are not captured yet.
* ``Hysteresis`` decides when a value has been high long enough to alert, and low enough to clear.
* ``HistoryBuffer`` folds one-minute samples into 5-minute windows for the history table.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from portcullis.core.types import OtherEvent, ServerEvent, ServerId

BUCKET_SECONDS = 300


def _field(data: Any, *names: str) -> Any:
    if not isinstance(data, Mapping):
        return None
    for name in names:
        for key in (name, name[:1].upper() + name[1:], re.sub(r"([A-Z])", r"_\1", name).lower()):
            if key in data and data[key] is not None:
                return data[key]
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class MetricSample:
    server: ServerId
    at: datetime
    cpu: float | None = None  # percent, 0 to 100
    memory_mb: float | None = None
    memory_limit_mb: float | None = None  # 0 or None: no limit, so no percentage
    players: int | None = None  # None: AMP doesn't know (it sends -1)

    @property
    def memory_percent(self) -> float | None:
        if self.memory_mb is None or not self.memory_limit_mb:
            return None
        return 100 * self.memory_mb / self.memory_limit_mb


def sample_from(event: ServerEvent) -> MetricSample | None:
    """A ``Metrics`` hub event as a sample, or None for any other event or an unreadable payload."""
    if not isinstance(event, OtherEvent) or event.name != "Metrics":
        return None
    data = event.payload
    inner = _field(data, "metrics", "sample")
    if isinstance(inner, Mapping):
        data = inner
    if not isinstance(data, Mapping):
        return None
    players = _number(_field(data, "activeUsers", "activeConnections"))
    sample = MetricSample(
        server=event.server,
        at=event.at,
        cpu=_number(_field(data, "cpuPercent", "cpuUsagePercent")),
        memory_mb=_number(_field(data, "memoryMb", "memoryUsageMB")),
        memory_limit_mb=_number(_field(data, "memoryLimitMb", "memoryLimitMB")),
        players=int(players) if players is not None and players >= 0 else None,
    )
    if sample.cpu is None and sample.memory_mb is None and sample.players is None:
        return None
    return sample


Change = Literal["fire", "clear"]


@dataclass
class Hysteresis:
    """One limit on one value. It fires after the value stays at or above ``high`` for ``sustain`` seconds,
    and clears only after it stays at or below ``clear`` for ``clear_after`` seconds. The gap between the
    two keeps a value hovering around the limit from posting alert, clear, alert, clear."""

    high: float
    clear: float
    sustain: float = 300.0
    clear_after: float = 120.0
    alerted: bool = False
    since: datetime | None = None  # when the current run above (or below, once alerted) began

    def update(self, value: float, at: datetime) -> Change | None:
        if not self.alerted:
            if value < self.high:
                self.since = None
                return None
            self.since = self.since or at
            if (at - self.since).total_seconds() >= self.sustain:
                self.alerted, self.since = True, None
                return "fire"
            return None
        if value > self.clear:
            self.since = None
            return None
        self.since = self.since or at
        if (at - self.since).total_seconds() >= self.clear_after:
            self.alerted, self.since = False, None
            return "clear"
        return None


def clear_level(high: float, gap: float) -> float:
    """Where a limit clears: ``gap`` points under it, never below zero."""
    return max(0.0, high - max(0.0, gap))


@dataclass
class Window:
    start: int
    samples: int = 0
    up: int = 0
    players_max: int = 0
    players_sum: int = 0
    cpu_max: float | None = None
    memory_max: float | None = None

    def add(self, *, online: bool, players: int, cpu: float | None, memory: float | None) -> None:
        self.samples += 1
        self.up += online
        self.players_max = max(self.players_max, players)
        self.players_sum += players
        if cpu is not None:
            self.cpu_max = cpu if self.cpu_max is None else max(self.cpu_max, cpu)
        if memory is not None:
            self.memory_max = memory if self.memory_max is None else max(self.memory_max, memory)


@dataclass
class HistoryBuffer:
    """Samples per server, held until their 5-minute window is over."""

    bucket: int = BUCKET_SECONDS
    open: dict[str, Window] = field(default_factory=dict)

    def window_of(self, at: datetime) -> int:
        ts = int(at.timestamp())
        return ts - ts % self.bucket

    def observe(self, server: str, at: datetime, *, online: bool, players: int, cpu: float | None = None,
                memory: float | None = None) -> list[tuple[str, Window]]:
        """Add one sample. Returns the server's previous window if this sample started a new one."""
        start = self.window_of(at)
        done: list[tuple[str, Window]] = []
        current = self.open.get(server)
        if current is not None and current.start != start:
            done.append((server, current))
            current = None
        if current is None:
            current = self.open[server] = Window(start)
        current.add(online=online, players=players, cpu=cpu, memory=memory)
        return done

    def drain(self, now: datetime | None = None) -> list[tuple[str, Window]]:
        """Windows that ended before ``now``, or every open window when ``now`` is None (shutting down)."""
        cutoff = None if now is None else self.window_of(now)
        out = [(s, w) for s, w in self.open.items() if cutoff is None or w.start < cutoff]
        for server, _ in out:
            del self.open[server]
        return out
