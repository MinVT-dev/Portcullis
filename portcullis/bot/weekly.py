# SPDX-License-Identifier: AGPL-3.0-or-later
"""The weekly summary: uptime, peak players, starts, crashes and alerts per server, with a player graph.

Off by default. ``/monitor weekly enabled:True`` turns it on; it posts once a week on the chosen day and
hour (the bot's time zone, ``/setup bot``) in the chosen channel, else the summary channel, else the alert
channel. It reads only what Portcullis recorded itself (``server_history`` and ``server_incidents``), so
hours when Portcullis was off are missing from the numbers, and the summary says how much was recorded.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any

import discord

from portcullis.bot.base import ALERT_CHANNEL, SUMMARY_CHANNEL, Ctx, as_id, card
from portcullis.bot.events import TIMEZONE, zone
from portcullis.bot.graphs import LINE_COLOURS, Series, render
from portcullis.core.relay import escape
from portcullis.db.history import HistoryRepo, IncidentRepo

log = logging.getLogger(__name__)

WEEKLY_KEY = "weekly_summary"  # bot scope: {"enabled": bool, "channel": id|null, "day": 0-6 (Mon=0), "hour": 0-23}
POSTED_KEY = "weekly_posted_for"  # bot scope: the local date of the last summary
DEFAULT = {"enabled": False, "channel": None, "day": 0, "hour": 9}
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_SERVERS = 15
GRAPH_SERVERS = 6
WEEK = 7 * 86400


@dataclass(frozen=True)
class WeeklyConfig:
    enabled: bool
    channel: int | None
    day: int
    hour: int


def stored_config(value: object) -> WeeklyConfig:
    data = value if isinstance(value, dict) else {}
    day, hour = data.get("day"), data.get("hour")
    return WeeklyConfig(
        enabled=data.get("enabled") is True,
        channel=as_id(data.get("channel")),
        day=day if isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6 else 0,
        hour=hour if isinstance(hour, int) and not isinstance(hour, bool) and 0 <= hour <= 23 else 9,
    )


@dataclass(frozen=True)
class ServerWeek:
    name: str
    uptime: float | None
    recorded: float  # share of the week Portcullis has samples for
    peak: int
    starts: int
    crashes: int
    alerts: int

    def line(self) -> str:
        up = "no data" if self.uptime is None else f"{100 * self.uptime:.1f}% up"
        parts = [up, f"peak {self.peak} player{'s' if self.peak != 1 else ''}",
                 f"{self.starts} start{'s' if self.starts != 1 else ''}"]
        if self.crashes:
            parts.append(f"**{self.crashes} crash{'es' if self.crashes != 1 else ''}**")
        if self.alerts:
            parts.append(f"{self.alerts} alert{'s' if self.alerts != 1 else ''}")
        return f"**{escape(self.name)}**: " + " · ".join(parts)


class Weekly:
    def __init__(self, ctx: Ctx, history: HistoryRepo, incidents: IncidentRepo) -> None:
        self.ctx = ctx
        self.history = history
        self.incidents = incidents

    async def config(self) -> WeeklyConfig:
        return stored_config(await self.ctx.settings.get(WEEKLY_KEY, DEFAULT))

    async def tick(self, now: datetime | None = None) -> bool:
        cfg = await self.config()
        if not cfg.enabled:
            return False
        now = now or datetime.now(UTC)
        tz = zone(await self.ctx.settings.get(TIMEZONE))
        local = now.astimezone(tz)
        if local.weekday() != cfg.day or local.hour < cfg.hour:
            return False
        if await self.ctx.settings.get(POSTED_KEY) == local.date().isoformat():
            return False
        await self.ctx.settings.set(POSTED_KEY, local.date().isoformat())
        until = datetime.combine(local.date(), time(cfg.hour), tz).astimezone(UTC)
        return await self.post(cfg, until)

    async def post(self, cfg: WeeklyConfig, until: datetime) -> bool:
        embed, png = await self.build(until)
        channel = cfg.channel or as_id(await self.ctx.settings.get(SUMMARY_CHANNEL)) or \
            as_id(await self.ctx.settings.get(ALERT_CHANNEL))
        if channel is None or self.ctx.client is None:
            return False
        extra: dict[str, Any] = {}
        if png is not None:
            embed.set_image(url="attachment://week.png")
            extra["file"] = discord.File(io.BytesIO(png), filename="week.png")
        try:
            target = await self.ctx.channel(channel)
            await target.send(embed=embed, allowed_mentions=discord.AllowedMentions.none(), **extra)
        except Exception as exc:  # noqa: BLE001 - a gone channel must not break the tick
            log.warning("could not post the weekly summary in %s: %s", channel, exc)
            return False
        return True

    async def week(self, until: datetime) -> list[tuple[str, ServerWeek]]:
        end = int(until.timestamp())
        start = end - WEEK
        out = []
        for row in (await self.ctx.servers.list(include_hidden=False))[:MAX_SERVERS]:
            stats = await self.history.stats(row.shard_id, start, end)
            counts = await self.incidents.counts(row.shard_id, start, end)
            out.append((row.shard_id, ServerWeek(
                name=row.name, uptime=stats.uptime, recorded=min(1.0, stats.samples * 60 / WEEK),
                peak=stats.players_max, starts=counts.get("start", 0), crashes=counts.get("crash", 0),
                alerts=counts.get("alert", 0))))
        return out

    async def build(self, until: datetime) -> tuple[discord.Embed, bytes | None]:
        tz = zone(await self.ctx.settings.get(TIMEZONE))
        servers = await self.week(until)
        end = int(until.timestamp())
        start = end - WEEK
        recorded = max((s.recorded for _, s in servers), default=0.0)
        since_local = datetime.fromtimestamp(start, tz)
        title = f"Week of {since_local:%d %b}"
        lines = [s.line() for _, s in servers] or ["No servers yet."]
        embed = card(title=title, description="\n".join(lines)[:4000],
                     footer=f"Portcullis recorded {100 * recorded:.0f}% of this week. Hours it was off aren't counted.")
        busiest = sorted(servers, key=lambda s: s[1].peak, reverse=True)[:GRAPH_SERVERS]
        if not any(s.peak for _, s in busiest):
            return embed, None
        series = []
        for (shard_id, info), colour in zip(busiest, LINE_COLOURS, strict=False):
            points = await self.history.series(shard_id, start, end, 3600)
            series.append(Series(info.name, colour, [(p.start, p.players_max) for p in points]))
        png = render("Players online", "Most at once, per hour", series, since=start, until=end, step=3600, tz=tz)
        return embed, png
