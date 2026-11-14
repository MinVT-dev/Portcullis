# SPDX-License-Identifier: AGPL-3.0-or-later
"""CPU, memory and disk limits per server, with hysteresis.

CPU and memory come from AMP's ``Metrics`` hub events, which Portcullis already receives for every
followed server (about every 3 to 5 seconds), so watching them costs no extra AMP calls. Disk is not in
those events: it is read from ``GET /api/Shards/{id}/disk`` (AMP's cached scan, not a live walk) at most
every 30 minutes, and only for servers that have a disk limit set.

A limit fires after the value stays at or above it for ``sustain`` (5 minutes by default) and clears only
once it is ``gap`` points lower (10 by default) for 2 minutes, so a value hovering at the limit can't
post alert, clear, alert. Settings are per server: ``/monitor thresholds``, or CPU and memory in
``/setup server``, which writes the same keys.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from portcullis.bot.alerting import Alert, AlertRouter
from portcullis.bot.alerts import CPU_KEY, MEMORY_KEY
from portcullis.bot.base import Ctx
from portcullis.core.monitoring import Hysteresis, MetricSample, clear_level, sample_from
from portcullis.core.relay import escape
from portcullis.core.types import ServerEvent, ServerId, ServerState, StateChanged

log = logging.getLogger(__name__)

DISK_KEY = "disk_alert"  # server scope: percent of the server's disk budget
GAP_KEY = "alert_clear_gap"  # server scope: points under the limit before "back to normal" (default 10)
SUSTAIN_KEY = "alert_sustain_minutes"  # server scope: minutes above the limit before an alert (default 5)
DEFAULT_GAP = 10
DEFAULT_SUSTAIN = 5
CLEAR_AFTER = 120.0
DISK_EVERY = 1800.0
DISK_TIMEOUT = 8.0
SETTINGS_EVERY = 60.0  # re-read a server's limits at most this often while samples stream in
LABELS = {"cpu": "CPU", "memory": "memory", "disk": "disk"}


def as_limit(value: object) -> float | None:
    """A stored percentage limit, or None when it is off or unreadable."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if 0 < value <= 100 else None


class Thresholds:
    """A relay listener (Metrics and state events) plus a once-a-minute ``tick`` for disk."""

    def __init__(self, ctx: Ctx, router: AlertRouter, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.ctx = ctx
        self.router = router
        self.clock = clock
        self.latest: dict[str, MetricSample] = {}  # newest sample per server, also read by the history sampler
        self.limits: dict[tuple[str, str], Hysteresis] = {}
        self._read_at: dict[str, float] = {}
        self._disk_at: dict[str, float] = {}

    async def _limits(self, server: str) -> dict[str, Hysteresis]:
        """The server's limits, rebuilt from settings at most once a minute. A changed limit starts fresh."""
        now = self.clock()
        if now - self._read_at.get(server, -SETTINGS_EVERY) >= SETTINGS_EVERY:
            self._read_at[server] = now
            gap = await self.ctx.settings.get(GAP_KEY, DEFAULT_GAP, scope=server)
            gap = float(gap) if isinstance(gap, int | float) and not isinstance(gap, bool) and 0 <= gap < 100 \
                else DEFAULT_GAP
            minutes = await self.ctx.settings.get(SUSTAIN_KEY, DEFAULT_SUSTAIN, scope=server)
            minutes = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) and 0 <= minutes <= 120 \
                else DEFAULT_SUSTAIN
            for metric, key in (("cpu", CPU_KEY), ("memory", MEMORY_KEY), ("disk", DISK_KEY)):
                high = as_limit(await self.ctx.settings.get(key, scope=server))
                current = self.limits.get((server, metric))
                sustain = 0.0 if metric == "disk" else minutes * 60.0
                clear = clear_level(high, gap) if high is not None else 0.0
                if high is None:
                    self.limits.pop((server, metric), None)
                elif current is None or (current.high, current.clear, current.sustain) != (high, clear, sustain):
                    self.limits[(server, metric)] = Hysteresis(high, clear, sustain=sustain,
                                                               clear_after=0.0 if metric == "disk" else CLEAR_AFTER)
        return {m: h for (s, m), h in self.limits.items() if s == server}

    def forget(self, server: str) -> None:
        self._read_at.pop(server.lower(), None)

    async def __call__(self, event: ServerEvent) -> None:
        server = event.server.lower()
        if isinstance(event, StateChanged) and event.state is not ServerState.RUNNING:
            # A stopped server has no load. Its limits start over next time, without a "back to normal" note.
            for (s, metric), limit in list(self.limits.items()):
                if s == server and metric != "disk":
                    limit.alerted, limit.since = False, None
            self.latest.pop(server, None)
            return
        sample = sample_from(event)
        if sample is None:
            return
        self.latest[server] = sample
        if self.ctx.live.states.get(ServerId(server)) not in (ServerState.RUNNING, None):
            return
        limits = await self._limits(server)
        values = {"cpu": sample.cpu, "memory": sample.memory_percent}
        for metric, value in values.items():
            limit = limits.get(metric)
            if limit is not None and value is not None:
                await self._apply(server, metric, limit, value, sample.at)

    async def _apply(self, server: str, metric: str, limit: Hysteresis, value: float, at: datetime) -> None:
        change = limit.update(value, at)
        if change is None:
            return
        row = await self.ctx.servers.get(server)
        name = escape(row.name) if row else server
        label = LABELS[metric]
        if change == "fire":
            mins = round(limit.sustain / 60)
            held = f" for {mins} minute{'s' if mins != 1 else ''}" if mins else ""
            await self.router.send(Alert(
                kind=f"load_{metric}", server=server, severity="warn", title=f"High {label}",
                text=f"🟠 **{name}**: {label} at {value:.0f}%{held} (limit {limit.high:.0f}%).",
                fields=(("Clears at", f"{limit.clear:.0f}% or lower"),)))
        else:
            await self.router.send(Alert(
                kind=f"load_{metric}_clear", server=server, severity="good", ping=False, cooldown=False,
                title=f"{label.capitalize()} back to normal",
                text=f"✅ **{name}**: {label} is back to {value:.0f}% (limit {limit.high:.0f}%)."))

    async def tick(self, now: datetime | None = None) -> None:
        """Read disk use for servers with a disk limit, at most every 30 minutes each."""
        now = now or datetime.now(UTC)
        for row in await self.ctx.servers.list():
            server = row.shard_id.lower()
            limit = (await self._limits(server)).get("disk")
            if limit is None or self.clock() - self._disk_at.get(server, -DISK_EVERY) < DISK_EVERY:
                continue
            self._disk_at[server] = self.clock()
            try:
                usage = await asyncio.wait_for(self.ctx.port.disk_usage(ServerId(server)), DISK_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - no disk route or permission: no disk alerts
                log.debug("disk check for %s failed: %s", row.name, exc)
                continue
            if usage.size_mb is None or not usage.limit_mb:
                continue  # AMP has no budget for this server, so there is no percentage to compare
            await self._apply(server, "disk", limit, 100 * usage.size_mb / usage.limit_mb, now)
