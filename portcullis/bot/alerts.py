# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alerts for admins, and the live audit log channel.

``Alerts`` is a relay listener (crash, offline, needs-licence and back-online alerts) and is also called
by the bot's loops: ``reachability`` after every refresh, ``tick`` once a minute (old backups, the preview
build's expiry). Server alerts go through ``alerting.AlertRouter``, which picks the channel and DMs and
handles mutes, cooldowns, crash loops and grouping. CPU, memory and disk limits live in ``thresholds.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from portcullis.bot.alerting import Alert, AlertRouter
from portcullis.bot.base import ALERT_CHANNEL, ALERT_ROLE, AUDIT_CHANNEL, Ctx, as_id, duration, short_error
from portcullis.core.relay import escape
from portcullis.core.types import ServerEvent, ServerState, StateChanged
from portcullis.db.repos import AuditEntry

log = logging.getLogger(__name__)

CPU_KEY, MEMORY_KEY = "cpu_alert", "memory_alert"  # per server: percent; checked by thresholds.Thresholds
BACKUP_DAYS = "backup_reminder_days"  # bot: warn when a server's newest backup is older than this
BACKUP_CHECKED = "backup_checked_at"  # bot: when the backup check last ran (ISO), so restarts don't repeat it

QUIET_FOR = 300.0  # at most one alert of a kind per server this often
UNREACHABLE_AFTER = 180.0  # AMP must be unreachable this long before anyone is told
BACKUP_EVERY = timedelta(days=1)
PREVIEW_CHECKED = "preview_checked_at"  # bot: when the preview-build expiry was last checked (ISO)
PREVIEW_WARN_DAYS = 14  # start warning this many days before the AMP preview build stops working
PREVIEW_PING_DAYS = 3  # and ping the alert role from this many days before
BACKUP_TIMEOUT = 15.0


LAST_ERROR_TIMEOUT = 5.0


class Alerts:
    def __init__(self, ctx: Ctx, *, quiet_for: float = QUIET_FOR, unreachable_after: float = UNREACHABLE_AFTER,
                 router: AlertRouter | None = None) -> None:
        self.ctx = ctx
        self.router = router or AlertRouter(ctx, quiet_for=quiet_for)
        self.unreachable_after = unreachable_after
        self.last: dict[str, ServerState] = {}
        self.down: dict[str, tuple[str, float]] = {}  # server -> (what was alerted, when), for "back online"
        self.unreachable_since: float | None = None
        self.unreachable_told = False

    async def _alert(self, server: str, kind: str, text: str, *, title: str, severity: str = "bad",
                     fields: tuple[tuple[str, str], ...] = ()) -> bool:
        self.down[server] = (kind, time.monotonic())
        return await self.router.send(Alert(kind=kind, text=text, server=server, severity=severity, title=title,
                                            fields=fields))

    async def _last_error(self, server: str) -> tuple[tuple[str, str], ...]:
        """AMP's own reason for a crash, when it gives one. One status read, only when a server crashed."""
        try:
            status = await asyncio.wait_for(self.ctx.port.get_status(server), LAST_ERROR_TIMEOUT)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - the alert goes out without the reason
            log.debug("could not read the last error of %s: %s", server, exc)
            return ()
        error = status.last_error or status.message
        return (("AMP says", short_error(Exception(error))[:300]),) if error else ()

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
        when = (("When", f"<t:{int(event.at.timestamp())}:f>"),)
        if event.state is ServerState.FAILED:
            await self._alert(server, "crash", f"🔥 **{name}** crashed: AMP reports it as Failed.", title="Crashed",
                              fields=when + await self._last_error(server))
        elif event.state is ServerState.AWAITING_LICENCE:
            await self._alert(server, "licence", f"📜 **{name}** is waiting for its game licence (EULA) to be "
                                                 "accepted before it can start. An admin can answer it from "
                                                 "`/server status`.", title="Needs licence", severity="warn")
        elif event.state is ServerState.STOPPED and previous is ServerState.RUNNING \
                and not self.ctx.offline_expected(server):
            await self._alert(server, "offline", f"🔴 **{name}** went offline, and nobody stopped it through "
                                                 "Portcullis. (A stop from AMP's own panel also counts.)",
                              title="Offline", fields=when)
        elif event.state is ServerState.RUNNING and server in self.down:
            kind, since = self.down.pop(server)
            why = {"crash": "it crashed", "offline": "it went offline", "licence": "it waited for its licence"}
            await self.router.send(Alert(
                kind="recovered", server=server, severity="good", ping=False, cooldown=False, title="Back online",
                text=f"🟢 **{name}** is back online after {duration(time.monotonic() - since)} "
                     f"({why.get(kind, kind)})."))

    async def reachability(self, reachable: bool) -> None:
        """Called after each refresh with whether AMP answered."""
        now = time.monotonic()
        if reachable:
            if self.unreachable_told:
                down = duration(now - (self.unreachable_since or now))
                await self.router.send(Alert(kind="reachable", severity="good", ping=False, cooldown=False,
                                             text=f"✅ Portcullis can reach AMP again (it was gone for about {down})."))
            self.unreachable_since = None
            self.unreachable_told = False
            return
        if self.unreachable_since is None:
            self.unreachable_since = now
        if not self.unreachable_told and now - self.unreachable_since >= self.unreachable_after:
            self.unreachable_told = await self.router.send(Alert(
                kind="unreachable", severity="warn", cooldown=False,
                text="⚠️ Portcullis can't reach AMP. Commands and relays are down until it's back; "
                     "I'll post here when it is."))

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        await self.check_backups(now)
        await self.check_preview(now)

    async def _due(self, key: str, now: datetime, every: timedelta = BACKUP_EVERY) -> bool:
        """True at most once per ``every``, across restarts. A stored time that can't be read counts as due."""
        last = await self.ctx.settings.get(key)
        try:
            before = datetime.fromisoformat(last) if isinstance(last, str) else None
            if before is not None and before.tzinfo is None:
                before = before.replace(tzinfo=UTC)
        except ValueError:
            before = None
        if before is not None and timedelta(0) <= now - before < every:
            return False
        await self.ctx.settings.set(key, now.isoformat(timespec="seconds"))
        return True

    async def check_preview(self, now: datetime) -> None:
        """Once a day: warn when the AMP preview build is about to stop working. AMP reports the date itself."""
        if not self.ctx.live.reachable or not await self._due(PREVIEW_CHECKED, now):
            return
        try:
            expires = await asyncio.wait_for(self.ctx.port.preview_expires_at(), 8)
        except Exception as exc:  # noqa: BLE001 - a build without this route just gets no warning
            log.debug("could not read the preview expiry: %s", exc)
            return
        text = preview_warning(expires, now)
        if text is None:
            return
        log.warning(text.replace("**", ""))
        ping = expires is not None and (expires - now) <= timedelta(days=PREVIEW_PING_DAYS)
        await self.ctx.post(ALERT_CHANNEL, f"⏳ {text}",
                            ping_role=as_id(await self.ctx.settings.get(ALERT_ROLE)) if ping else None)

    async def check_backups(self, now: datetime) -> None:
        days = await self.ctx.settings.get(BACKUP_DAYS)
        if not isinstance(days, int) or days <= 0:
            return
        if not await self._due(BACKUP_CHECKED, now):
            return
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


def preview_warning(expires: datetime | None, now: datetime) -> str | None:
    """What to tell admins about the AMP preview build's expiry, or None when it is far off or not a preview."""
    if expires is None:
        return None
    left = expires - now
    if left > timedelta(days=PREVIEW_WARN_DAYS):
        return None
    stamp = f"<t:{int(expires.timestamp())}:D>"
    advice = ("Update AMP, then check the new build's API with spec-diff (docs/SETUP.md section 9) before "
              "relying on Portcullis: a newer build can change the API.")
    if left <= timedelta(0):
        return f"**This AMP preview build expired on {stamp}.** AMP says it stops working then. {advice}"
    days = left.days
    soon = "in less than a day" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
    return f"**This AMP preview build stops working {soon}** ({stamp}). {advice}"


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
