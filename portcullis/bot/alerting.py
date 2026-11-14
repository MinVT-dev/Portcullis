# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where alerts go, and how a bad night is kept from turning into fifty pings.

Every alert (crash, offline, needs licence, back online, load, update, AMP unreachable) goes through
``AlertRouter.send``. It decides, in order:

1. **Mute.** ``/monitor mute`` silences one server for a while. Portcullis-wide alerts are never muted.
2. **Cooldown.** The same kind of alert for the same server goes out at most once per ``quiet_for``.
3. **Crash loop.** The third crash of one server within 15 minutes posts one "crash loop" alert and
   mutes that server's crash alerts for an hour. ``/monitor mute minutes:0`` lifts it.
4. **Grouping.** Alerts that arrive within ``window`` seconds of each other go out as one message per
   destination (an AMP host restart takes every server down at once; that is one message, not ten).
5. **Destinations.** The server's own alert channel if one is set (``/monitor alerts server:``), else the
   bot's alert channel from ``/setup bot``. Plus a DM to every member on the bot-wide DM list and the
   server's own DM list. Only the alert role is ever pinged, and only in a channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import discord

from portcullis.bot.base import ALERT_CHANNEL, ALERT_ROLE, NONE, Ctx, as_id, card, id_list
from portcullis.core.relay import escape

log = logging.getLogger(__name__)

ALERT_DM = "alert_dm"  # bot scope: [user ids] who get every alert by DM
ROUTE_KEY = "alert_route"  # server scope: {"channel": id or null, "dm": [user ids]}
MUTED_KEY = "alert_muted_until"  # server scope: ISO time
DM_LIMIT = 10  # per list; DMs are slow and Discord limits how many a bot opens
QUIET_FOR = 300.0
GROUP_WINDOW = 10.0  # production value; tests use 0 so alerts go out at once
CRASH_LOOP_COUNT = 3
CRASH_LOOP_WINDOW = 900.0
CRASH_LOOP_MUTE = timedelta(hours=1)
GROUPED_MAX = 20  # lines in one grouped message; the rest are counted


@dataclass(frozen=True)
class Alert:
    kind: str  # crash, offline, licence, recovered, load, load_clear, update, unreachable, reachable, test
    text: str  # one line of Markdown, shown as the message text (what a phone notification shows)
    server: str | None = None  # None: about Portcullis itself
    severity: str = "bad"  # a ``base.KINDS`` colour
    ping: bool = True
    title: str | None = None
    fields: tuple[tuple[str, str], ...] = ()  # extra detail, shown in an embed
    cooldown: bool = True


@dataclass
class Route:
    channel: int | None
    dms: list[int] = field(default_factory=list)


def stored_route(value: object) -> Route:
    if not isinstance(value, dict):
        return Route(None)
    return Route(as_id(value.get("channel")), id_list(value.get("dm"))[:DM_LIMIT])


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class AlertRouter:
    def __init__(self, ctx: Ctx, *, window: float = 0.0, quiet_for: float = QUIET_FOR,
                 clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.ctx = ctx
        self.window = window
        self.quiet_for = quiet_for
        self.clock = clock
        self.now = now
        self.sent_at: dict[tuple[str | None, str], float] = {}
        self.crashes: dict[str, list[float]] = {}
        self.pending: list[Alert] = []
        self._flusher: asyncio.Task[None] | None = None
        self.on_sent: list[Callable[[Alert], Any]] = []  # incident log (bot/monitor.py)

    # ---------------------------------------------------------------- settings

    async def muted_until(self, server: str) -> datetime | None:
        until = parse_time(await self.ctx.settings.get(MUTED_KEY, scope=server))
        return until if until is not None and until > self.now() else None

    async def mute(self, server: str, minutes: int, *, by: int | None = None) -> datetime | None:
        if minutes <= 0:
            await self.ctx.settings.delete(MUTED_KEY, scope=server)
            self.crashes.pop(server.lower(), None)
            return None
        until = self.now() + timedelta(minutes=minutes)
        await self.ctx.settings.set(MUTED_KEY, until.isoformat(timespec="seconds"), scope=server, by=by)
        return until

    async def route(self, server: str | None) -> Route:
        """Where an alert about ``server`` goes."""
        bot_dms = id_list(await self.ctx.settings.get(ALERT_DM))[:DM_LIMIT]
        default = as_id(await self.ctx.settings.get(ALERT_CHANNEL))
        if server is None:
            return Route(default, bot_dms)
        own = stored_route(await self.ctx.settings.get(ROUTE_KEY, scope=server))
        dms = list(dict.fromkeys(bot_dms + own.dms))
        return Route(own.channel or default, dms)

    # ---------------------------------------------------------------- sending

    async def send(self, alert: Alert) -> bool:
        """Queue or post one alert. Returns False when it was muted, cooling down or had nowhere to go."""
        server = alert.server.lower() if alert.server else None
        if server is not None and alert.kind != "test" and await self.muted_until(server) is not None:
            log.info("alert %s for %s not sent: the server is muted", alert.kind, server)
            return False
        if alert.kind == "crash" and server is not None:
            alert = await self._crash_loop(server, alert)
            if alert is None:
                return False
        if alert.cooldown and alert.kind != "test":
            key = (server, alert.kind)
            now = self.clock()
            if now - self.sent_at.get(key, -self.quiet_for) < self.quiet_for:
                return False
            self.sent_at[key] = now
        if self.window <= 0:
            return await self.deliver([alert])
        self.pending.append(alert)
        if self._flusher is None or self._flusher.done():
            self._flusher = asyncio.create_task(self._flush_later(), name="portcullis-alert-group")
        return True

    async def _crash_loop(self, server: str, alert: Alert) -> Alert | None:
        now = self.clock()
        recent = [t for t in self.crashes.get(server, []) if now - t < CRASH_LOOP_WINDOW] + [now]
        self.crashes[server] = recent
        if len(recent) < CRASH_LOOP_COUNT:
            return alert
        self.crashes[server] = []
        until = self.now() + CRASH_LOOP_MUTE
        await self.ctx.settings.set(MUTED_KEY, until.isoformat(timespec="seconds"), scope=server)
        name = await self._name(server)
        return Alert(kind="crash_loop", server=server, cooldown=False, title="Crash loop",
                     text=f"🔥 **{name}** crashed {len(recent)} times in {CRASH_LOOP_WINDOW / 60:.0f} minutes. "
                          f"Its alerts are muted until <t:{int(until.timestamp())}:t>; "
                          "`/monitor mute minutes:0` turns them back on.",
                     fields=alert.fields)

    async def _name(self, server: str) -> str:
        row = await self.ctx.servers.get(server)
        return escape(row.name) if row else server

    async def _flush_later(self) -> None:
        await asyncio.sleep(self.window)
        await self.flush()

    async def flush(self) -> None:
        batch, self.pending = self.pending, []
        if batch:
            await self.deliver(batch)

    async def deliver(self, alerts: list[Alert]) -> bool:
        """Post ``alerts`` (one, or a group) to every destination they have. True if any post went out."""
        channels: dict[int | None, list[Alert]] = {}
        dms: dict[int, list[Alert]] = {}
        for alert in alerts:
            route = await self.route(alert.server.lower() if alert.server else None)
            channels.setdefault(route.channel, []).append(alert)
            for user_id in route.dms:
                dms.setdefault(user_id, []).append(alert)
        role = as_id(await self.ctx.settings.get(ALERT_ROLE))
        posted = False
        for channel_id, group in channels.items():
            if channel_id is None:
                continue
            content, embed = render(group)
            ping = role if any(a.ping for a in group) else None
            posted |= await self.ctx.post_to(channel_id, content, embed=embed, ping_role=ping, label="alert channel")
        for user_id, group in dms.items():
            posted |= await self._dm(user_id, group)
        if posted:
            for alert in alerts:
                for hook in self.on_sent:
                    with contextlib.suppress(Exception):
                        await hook(alert)
        return posted

    async def _dm(self, user_id: int, alerts: list[Alert]) -> bool:
        client = self.ctx.client
        if client is None:
            return False
        content, embed = render(alerts)
        extra: dict[str, Any] = {"embed": embed} if embed is not None else {}
        try:
            user = client.get_user(user_id) or await client.fetch_user(user_id)
            await user.send(content, allowed_mentions=NONE, **extra)
        except Exception as exc:  # noqa: BLE001 - closed DMs or a member who left; the channel still has it
            log.info("could not DM alert to %s: %s", user_id, exc)
            return False
        return True

    async def close(self) -> None:
        if self._flusher is not None and not self._flusher.done():
            self._flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._flusher
        with contextlib.suppress(Exception):
            await self.flush()


WORST = ("bad", "warn", "info", "good", "muted")


def render(alerts: list[Alert]) -> tuple[str, discord.Embed | None]:
    """Message text and embed for one alert or a group."""
    if len(alerts) == 1:
        alert = alerts[0]
        if not alert.fields:
            return alert.text, None
        embed = card(kind=alert.severity, title=alert.title, footer="Portcullis alert")
        for name, value in alert.fields[:10]:
            embed.add_field(name=name, value=value[:1024], inline=len(value) <= 40)
        return alert.text, embed
    kind = min((a.severity for a in alerts), key=lambda k: WORST.index(k) if k in WORST else len(WORST))
    lines = [a.text for a in alerts[:GROUPED_MAX]]
    if len(alerts) > GROUPED_MAX:
        lines.append(f"…and {len(alerts) - GROUPED_MAX} more.")
    servers = {a.server for a in alerts if a.server}
    about = f" on {len(servers)} servers" if len(servers) > 1 else ""
    embed = card(kind=kind, description="\n".join(lines)[:4000], footer="Grouped because they arrived together")
    return f"⚠️ {len(alerts)} alerts{about}.", embed
