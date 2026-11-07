# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alerts for admins, and the live audit log channel.

``Alerts`` is a relay listener (offline and crash alerts) and is also called by the bot's loops:
``reachability`` after every refresh, ``tick`` once a minute (high load, old backups).
Alerts go to the alert channel from ``/setup bot`` and ping its alert role, if one is set.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from portcullis.bot.base import ALERT_CHANNEL, ALERT_ROLE, AUDIT_CHANNEL, Ctx, as_id, duration
from portcullis.core.relay import escape
from portcullis.core.types import ServerEvent, ServerState, StateChanged
from portcullis.db.repos import AuditEntry

log = logging.getLogger(__name__)

CPU_KEY, MEMORY_KEY = "cpu_alert", "memory_alert"  # per server: percent, alert when above it for SUSTAIN
BACKUP_DAYS = "backup_reminder_days"  # bot: warn when a server's newest backup is older than this
BACKUP_CHECKED = "backup_checked_at"  # bot: when the backup check last ran (ISO), so restarts don't repeat it

QUIET_FOR = 300.0  # at most one alert of a kind per server this often
UNREACHABLE_AFTER = 180.0  # AMP must be unreachable this long before anyone is told
SUSTAIN = 300.0  # load must stay high this long before it's an alert
BACKUP_EVERY = timedelta(days=1)
BACKUP_TIMEOUT = 15.0


class Alerts:
    def __init__(self, ctx: Ctx, *, quiet_for: float = QUIET_FOR, unreachable_after: float = UNREACHABLE_AFTER,
                 sustain: float = SUSTAIN) -> None:
        self.ctx = ctx
        self.quiet_for = quiet_for
        self.unreachable_after = unreachable_after
        self.sustain = sustain
        self.last: dict[str, ServerState] = {}
        self.sent_at: dict[tuple[str, str], float] = {}
        self.unreachable_since: float | None = None
        self.unreachable_told = False
        self.high_since: dict[str, float] = {}
        self.high_told: set[str] = set()

    async def _alert(self, server: str, kind: str, text: str, *, ping: bool = True) -> bool:
        now = time.monotonic()
        key = (server, kind)
        if now - self.sent_at.get(key, -self.quiet_for) < self.quiet_for:
            return False
        self.sent_at[key] = now
        role = as_id(await self.ctx.settings.get(ALERT_ROLE)) if ping else None
        return await self.ctx.post(ALERT_CHANNEL, text, ping_role=role)

    async def __call__(self, event: ServerEvent) -> None:
        if not isinstance(event, StateChanged):
            return
        server = event.server.lower()
        previous = self.last.get(server)
        self.last[server] = event.state
        if previous is None or previous is event.state:
            return  # the first state after start-up says nothing about a change
        row = await self.ctx.servers.get(server)
        name = escape(row.name) if row else server
        if event.state is ServerState.FAILED:
            await self._alert(server, "failed", f"🔥 **{name}** crashed: AMP reports it as Failed.")
        elif event.state is ServerState.STOPPED and previous is ServerState.RUNNING \
                and not self.ctx.offline_expected(server):
            await self._alert(server, "offline", f"🔴 **{name}** went offline, and nobody stopped it through "
                                                 "Portcullis. (A stop from AMP's own panel also counts.)")

    async def reachability(self, reachable: bool) -> None:
        """Called after each refresh with whether AMP answered."""
        now = time.monotonic()
        if reachable:
            if self.unreachable_told:
                down = duration(now - (self.unreachable_since or now))
                await self.ctx.post(ALERT_CHANNEL, f"✅ Portcullis can reach AMP again (it was gone for about {down}).")
            self.unreachable_since = None
            self.unreachable_told = False
            return
        if self.unreachable_since is None:
            self.unreachable_since = now
        if not self.unreachable_told and now - self.unreachable_since >= self.unreachable_after:
            self.unreachable_told = await self.ctx.post(
                ALERT_CHANNEL, "⚠️ Portcullis can't reach AMP. Commands and relays are down until it's back; "
                               "I'll post here when it is.", ping_role=as_id(await self.ctx.settings.get(ALERT_ROLE)))

    async def tick(self, now: datetime | None = None) -> None:
        await self.check_load()
        await self.check_backups(now or datetime.now(UTC))

    async def check_load(self) -> None:
        for row in await self.ctx.servers.list():
            cpu_limit = await self.ctx.settings.get(CPU_KEY, scope=row.shard_id)
            memory_limit = await self.ctx.settings.get(MEMORY_KEY, scope=row.shard_id)
            if not cpu_limit and not memory_limit:
                continue
            if self.ctx.live.states.get(row.shard_id) is not ServerState.RUNNING:
                self.high_since.pop(row.shard_id, None)
                self.high_told.discard(row.shard_id)
                continue
            try:
                status = await asyncio.wait_for(self.ctx.port.get_status(row.shard_id), 8)
            except Exception as exc:  # noqa: BLE001 - the unreachable alert covers AMP being down
                log.debug("load check for %s failed: %s", row.name, exc)
                continue
            reasons = []
            if cpu_limit and status.cpu_percent is not None and status.cpu_percent >= cpu_limit:
                reasons.append(f"CPU at {status.cpu_percent:.0f}%")
            if memory_limit and status.memory_mb is not None and status.memory_limit_mb:
                used = 100 * status.memory_mb / status.memory_limit_mb
                if used >= memory_limit:
                    reasons.append(f"memory at {used:.0f}% of its limit")
            await self._load(row.shard_id, row.name, reasons)

    async def _load(self, server: str, name: str, reasons: list[str]) -> None:
        now = time.monotonic()
        if not reasons:
            self.high_since.pop(server, None)
            if server in self.high_told:
                self.high_told.discard(server)
                await self.ctx.post(ALERT_CHANNEL, f"✅ **{escape(name)}** is back under its load limits.")
            return
        since = self.high_since.setdefault(server, now)
        if server not in self.high_told and now - since >= self.sustain:
            self.high_told.add(server)
            await self._alert(server, "load", f"🟠 **{escape(name)}** has been busy for {duration(now - since)}: "
                                              f"{', '.join(reasons)}.")

    async def check_backups(self, now: datetime) -> None:
        days = await self.ctx.settings.get(BACKUP_DAYS)
        if not isinstance(days, int) or days <= 0:
            return
        last = await self.ctx.settings.get(BACKUP_CHECKED)
        if isinstance(last, str) and now - datetime.fromisoformat(last) < BACKUP_EVERY:
            return
        await self.ctx.settings.set(BACKUP_CHECKED, now.isoformat(timespec="seconds"))
        cutoff = now - timedelta(days=days)
        lines = []
        for row in await self.ctx.servers.list(include_hidden=False):
            try:
                backups = await asyncio.wait_for(self.ctx.port.list_backups(row.shard_id), BACKUP_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                log.info("backup check for %s failed: %s", row.name, exc)
                continue
            dated = [b.created_at for b in backups if b.created_at is not None]
            if not dated:
                lines.append(f"**{escape(row.name)}** has no backups.")
            elif max(dated) < cutoff:
                lines.append(f"**{escape(row.name)}**: newest backup is from "
                             f"<t:{int(max(dated).timestamp())}:R>.")
        if lines:
            await self.ctx.post(ALERT_CHANNEL, f"🗄️ Backups older than {days} day(s):\n" + "\n".join(lines[:25]))


# Member actions that would only be noise in the audit channel.
QUIET_ACTIONS = ("whitelist.request", "link.", "notify", "poll.vote", "event.rsvp")


class AuditFeed:
    """Posts each audit entry in the audit channel as it happens (``AuditRepo.listeners``)."""

    PER_MINUTE = 20  # someone spamming a denied command shouldn't flood the channel

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.tasks: set[asyncio.Task[None]] = set()
        self.recent: list[float] = []
        self.skipped = 0

    def __call__(self, entry: AuditEntry) -> None:
        if entry.action.startswith(QUIET_ACTIONS):
            return
        now = time.monotonic()
        self.recent = [t for t in self.recent if now - t < 60]
        if len(self.recent) >= self.PER_MINUTE:
            self.skipped += 1
            return
        self.recent.append(now)
        task = asyncio.create_task(self.post(entry), name="portcullis-audit-feed")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def post(self, entry: AuditEntry) -> None:
        try:
            await self._post(entry)
        except Exception as exc:  # noqa: BLE001 - the entry is safe in the database either way
            log.debug("could not copy audit entry %s to the channel: %s", entry.id, exc)

    async def _post(self, entry: AuditEntry) -> None:
        if not await self.ctx.settings.get(AUDIT_CHANNEL):
            return
        who = f"<@{entry.actor_id}>" if entry.actor_id else "Portcullis"
        where = ""
        if entry.shard_id:
            row = await self.ctx.servers.get(entry.shard_id)
            where = f" on **{escape(row.name if row else entry.shard_id)}**"
        what = f" · {escape(entry.target)[:100]}" if entry.target else ""
        mark = "" if entry.result == "ok" else f" · **{entry.result}**"
        skipped, self.skipped = self.skipped, 0
        note = f"\n({skipped} more entries in the last minute weren't posted; see `/audit`.)" if skipped else ""
        await self.ctx.post(AUDIT_CHANNEL, f"`{entry.action}` by {who}{where}{what}{mark}{note}")

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
