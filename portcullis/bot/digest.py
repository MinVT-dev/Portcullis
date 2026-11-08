# SPDX-License-Identifier: AGPL-3.0-or-later
"""The daily summary and play-time milestones, both posted in the summary channel from ``/setup bot``.

Play time counts open sessions up to now, and AMP's leave events haven't been seen yet, so a player
who left without a leave event keeps counting until the server stops. Milestones say "about" for that reason.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from time import monotonic

from portcullis.bot.base import COUNT_CHANNEL, SUMMARY_CHANNEL, Ctx, as_id, card
from portcullis.bot.events import TIMEZONE, zone
from portcullis.core.relay import escape

log = logging.getLogger(__name__)

SUMMARY_DAY = "summary_posted_for"  # bot: the local date the last summary covered
MILESTONE_HOURS = (10, 50, 100, 250, 500, 1000)
SUMMARY_COLOUR = 0x5865F2
RENAME_EVERY = 330.0


class Digest:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.named = ""
        self.renamed_at = -RENAME_EVERY

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        await self.count_channel()
        if not await self.ctx.settings.get(SUMMARY_CHANNEL):
            return
        await self.summary(now)
        await self.milestones()

    async def count_channel(self) -> bool:
        """Rename the chosen channel to "N online". Discord allows two renames per ten minutes, so this
        waits at least 330 seconds between renames and the number can lag."""
        channel_id = as_id(await self.ctx.settings.get(COUNT_CHANNEL))
        if channel_id is None or self.ctx.client is None:
            return False
        online = 0
        for row in await self.ctx.servers.list(include_hidden=False):
            online += len(await self.ctx.players.online(row.shard_id))
        name = f"🟢 {online} online" if online else "⚪ nobody online"
        now = monotonic()
        if name == self.named or now - self.renamed_at < RENAME_EVERY:
            return False
        try:
            channel = await self.ctx.channel(channel_id)
            await channel.edit(name=name, reason="Portcullis player count")
        except Exception as exc:  # noqa: BLE001 - a missing channel or permission must not break the loop
            log.warning("could not rename the count channel %s: %s", channel_id, exc)
            self.renamed_at = now  # don't retry every minute
            return False
        self.named, self.renamed_at = name, now
        return True

    async def summary(self, now: datetime) -> bool:
        """After midnight (in the bot's time zone), post yesterday's numbers once."""
        tz = zone(await self.ctx.settings.get(TIMEZONE))
        today = now.astimezone(tz).date()
        yesterday = today - timedelta(days=1)
        if await self.ctx.settings.get(SUMMARY_DAY) == yesterday.isoformat():
            return False
        await self.ctx.settings.set(SUMMARY_DAY, yesterday.isoformat())
        until = datetime.combine(today, time(), tz).astimezone(UTC)
        since = until - timedelta(days=1)
        lines = []
        for row in await self.ctx.servers.list(include_hidden=False):
            players, new, peak = await self.ctx.players.day(row.shard_id, since, until)
            if players == 0:
                continue
            fresh = f", {new} new" if new else ""
            lines.append(f"**{escape(row.name)}**: {players} player{'s' if players != 1 else ''}{fresh}, "
                         f"peak {peak} at once")
        embed = card(title=f"Yesterday on the servers ({yesterday:%a %d %b})", colour=SUMMARY_COLOUR,
                              description="\n".join(lines) or "Nobody played.")
        return await self.ctx.post(SUMMARY_CHANNEL, embed=embed)

    async def milestones(self) -> None:
        for name in await self.ctx.players.online_everywhere():
            summary = await self.ctx.players.lookup(name)
            if summary is None:
                continue
            hours = summary.seconds / 3600
            reached = [h for h in MILESTONE_HOURS if hours >= h]
            if not reached:
                continue
            # Only the highest new one: a first run on old history shouldn't post a burst.
            top = reached[-1]
            if not await self.ctx.players.milestone_reached(summary.name, top):
                continue
            for lower in reached[:-1]:
                await self.ctx.players.milestone_reached(summary.name, lower)
            await self.ctx.post(SUMMARY_CHANNEL, f"🏆 **{escape(summary.name)}** has played about {top} hours.")
