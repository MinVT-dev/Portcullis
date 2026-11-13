# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/event create|list``: game nights and other events, with a Going button and a reminder.

Times are typed in the bot's time zone (``/setup bot``, default UTC) and shown to each reader in
their own with Discord timestamps. The reminder goes out ``REMIND_BEFORE`` ahead, pinging only the
members who pressed Going.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands

from portcullis.bot.base import NONE, Ctx, Module, card, reply, valid_line
from portcullis.core.relay import escape
from portcullis.db.ops import Event

log = logging.getLogger(__name__)

TIMEZONE = "timezone"  # bot setting: an IANA name such as Europe/Amsterdam
REMIND_BEFORE = timedelta(minutes=30)
MAX_AHEAD = timedelta(days=365)
PING_LIMIT = 50  # members pinged in one reminder; the rest are counted
_RELATIVE = re.compile(r"^in\s+((?:\d+\s*[dhm]\s*)+)$", re.I)
_PART = re.compile(r"(\d+)\s*([dhm])", re.I)
_CLOCK = re.compile(r"^(?:(today|tomorrow)\s+)?(\d{1,2}):(\d{2})$", re.I)
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})$")


def zone(name: object) -> ZoneInfo:
    try:
        return ZoneInfo(str(name)) if name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def parse_when(text: str, tz: ZoneInfo, now: datetime | None = None) -> datetime | None:
    """``in 2h 30m``, ``20:00``, ``tomorrow 20:00`` or ``2026-11-14 20:00``, as a UTC time in the future."""
    now = now or datetime.now(UTC)
    text = text.strip()
    if match := _RELATIVE.match(text):
        units = {"d": "days", "h": "hours", "m": "minutes"}
        delta = timedelta()
        for amount, unit in _PART.findall(match.group(1)):
            delta += timedelta(**{units[unit.lower()]: int(amount)})
        result = now + delta
    elif match := _CLOCK.match(text):
        day, hour, minute = match.group(1), int(match.group(2)), int(match.group(3))
        if hour > 23 or minute > 59:
            return None
        local = now.astimezone(tz)
        result = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day and day.lower() == "tomorrow":
            result += timedelta(days=1)
        elif not day and result <= local:
            result += timedelta(days=1)  # a bare time that has passed today means tomorrow
        result = result.astimezone(UTC)
    elif match := _DATE.match(text):
        try:
            result = datetime(*(int(g) for g in match.groups()), tzinfo=tz).astimezone(UTC)
        except ValueError:
            return None
    else:
        return None
    if result <= now or result - now > MAX_AHEAD:
        return None
    return result


def event_embed(event: Event, going: list[int], server_name: str | None) -> discord.Embed:
    start = datetime.fromisoformat(event.starts_at)
    embed = card(title=event.title[:256], description=escape(event.description or "")[:2000] or None,
                          colour=0x7F8C8D if event.cancelled else 0x9B59B6)
    embed.add_field(name="When", value=f"{discord.utils.format_dt(start, 'F')} ({discord.utils.format_dt(start, 'R')})",
                    inline=False)
    if server_name:
        embed.add_field(name="Server", value=escape(server_name))
    shown = ", ".join(f"<@{m}>" for m in going[:30])
    more = f" and {len(going) - 30} more" if len(going) > 30 else ""
    embed.add_field(name=f"Going ({len(going)})", value=(shown + more) or "nobody yet", inline=False)
    embed.set_footer(text="Cancelled" if event.cancelled else f"Event {event.id} · press Going for a reminder")
    return embed


class EventGoing(discord.ui.DynamicItem[discord.ui.Button[Any]], template=r"event:going:(?P<id>\d+)"):
    module: EventModule | None = None

    def __init__(self, event_id: int) -> None:
        super().__init__(discord.ui.Button(label="Going", style=discord.ButtonStyle.success,
                                           custom_id=f"event:going:{event_id}"))
        self.event_id = event_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item[Any],
                             match: re.Match[str]) -> EventGoing:
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.module is not None
        await self.module.going(interaction, self.event_id)


class EventCancel(discord.ui.DynamicItem[discord.ui.Button[Any]], template=r"event:cancel:(?P<id>\d+)"):
    module: EventModule | None = None

    def __init__(self, event_id: int) -> None:
        super().__init__(discord.ui.Button(label="Cancel event", style=discord.ButtonStyle.secondary,
                                           custom_id=f"event:cancel:{event_id}"))
        self.event_id = event_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item[Any],
                             match: re.Match[str]) -> EventCancel:
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.module is not None
        await self.module.cancel(interaction, self.event_id)


def event_view(event_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(EventGoing(event_id))
    view.add_item(EventCancel(event_id))
    return view


class EventModule(Module):
    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)
        EventGoing.module = self
        EventCancel.module = self

    async def _server_name(self, shard_id: str | None) -> str | None:
        if not shard_id:
            return None
        row = await self.ctx.servers.get(shard_id)
        return row.name if row else None

    async def create(self, interaction: discord.Interaction, title: str, when_: str, details: str | None = None,
                     server: str | None = None) -> None:
        if not await self.in_guild(interaction) or not await self.require_mod(interaction, "event.create"):
            return
        if not valid_line(title) or len(title) > 100:
            await reply(interaction, "Keep the title to one line of up to 100 characters.")
            return
        if details is not None and len(details) > 1000:
            await reply(interaction, "Keep the details to 1000 characters.")
            return
        tz = zone(await self.ctx.settings.get(TIMEZONE))
        start = parse_when(when_, tz)
        if start is None:
            await reply(interaction, f"I couldn't read that time. Try `in 2h`, `20:00`, `tomorrow 20:00` or "
                                     f"`2026-11-14 20:00` (times are {tz.key}, set in `/setup bot`). It has to be "
                                     "in the next year.")
            return
        row = None
        if server:
            row = await self.server(interaction, server)
            if row is None:
                return
        event = await self.ctx.events.create(title, details, row.shard_id if row else None,
                                             start.isoformat(timespec="seconds"), interaction.channel_id or 0,
                                             interaction.user.id)
        await self.ctx.audit.record("event.create", actor_id=interaction.user.id,
                                    shard_id=row.shard_id if row else None, target=title[:40])
        await interaction.response.send_message(embed=event_embed(event, [], row.name if row else None),
                                                view=event_view(event.id), allowed_mentions=NONE)
        message = await interaction.original_response()
        await self.ctx.events.set_message(event.id, message.id)

    async def list_events(self, interaction: discord.Interaction) -> None:
        if not await self.in_guild(interaction):
            return
        events = await self.ctx.events.upcoming()
        if not events:
            await reply(interaction, "No events coming up.")
            return
        lines = []
        for e in events:
            start = datetime.fromisoformat(e.starts_at)
            where = await self._server_name(e.shard_id)
            on = f" · {escape(where)}" if where else ""
            lines.append(f"{discord.utils.format_dt(start, 'f')} · **{escape(e.title)}**{on} · "
                         f"{len(await self.ctx.events.going(e.id))} going")
        await reply(interaction, embed=card(title="Coming up", description="\n".join(lines)[:4000]))

    async def going(self, interaction: discord.Interaction, event_id: int) -> None:
        event = await self.ctx.events.get(event_id)
        if event is None or event.cancelled:
            await reply(interaction, "That event was cancelled.")
            return
        if datetime.fromisoformat(event.starts_at) < datetime.now(UTC):
            await reply(interaction, "That event has already started.")
            return
        now_going = await self.ctx.events.toggle_rsvp(event_id, interaction.user.id)
        going = await self.ctx.events.going(event_id)
        server_name = await self._server_name(event.shard_id)
        await interaction.response.edit_message(embed=event_embed(event, going, server_name))
        await reply(interaction, "You're going. I'll ping you 30 minutes before it starts. Press Going again to "
                                 "take it back." if now_going else "You're no longer going.")

    async def cancel(self, interaction: discord.Interaction, event_id: int) -> None:
        event = await self.ctx.events.get(event_id)
        if event is None:
            await reply(interaction, "That event no longer exists.")
            return
        if not await self.require_mod(interaction, "event.cancel"):
            return
        if not await self.ctx.events.cancel(event_id):
            await reply(interaction, "That event is already cancelled.")
            return
        await self.ctx.audit.record("event.cancel", actor_id=interaction.user.id, target=event.title[:40])
        cancelled = await self.ctx.events.get(event_id)
        assert cancelled is not None
        await interaction.response.edit_message(
            embed=event_embed(cancelled, await self.ctx.events.going(event_id),
                              await self._server_name(event.shard_id)), view=None)

    async def tick(self, now: datetime | None = None) -> int:
        """Send due reminders. Returns how many went out."""
        now = now or datetime.now(UTC)
        sent = 0
        for event in await self.ctx.events.due_reminders((now + REMIND_BEFORE).isoformat(timespec="seconds")):
            if not await self.ctx.events.mark_reminded(event.id):
                continue
            going = await self.ctx.events.going(event.id)
            pinged = going[:PING_LIMIT]
            extra = f" (and {len(going) - PING_LIMIT} more)" if len(going) > PING_LIMIT else ""
            start = datetime.fromisoformat(event.starts_at)
            text = (f"⏰ **{escape(event.title)}** starts {discord.utils.format_dt(start, 'R')}. "
                    + " ".join(f"<@{m}>" for m in pinged) + extra)
            mentions = discord.AllowedMentions(everyone=False, roles=False,
                                               users=[discord.Object(id=m) for m in pinged])
            try:
                channel = await self.ctx.channel(event.channel_id)
                await channel.send(text[:2000], allowed_mentions=mentions)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("could not send the reminder for event %s: %s", event.id, exc)
        return sent

    def register(self, tree: app_commands.CommandTree) -> None:
        group = app_commands.Group(name="event", description="Game nights and other events", guild_only=True)

        @group.command(name="create", description="Announce an event in this channel (operators)")
        @app_commands.describe(title="What it is", when="When it starts: in 2h, 20:00, tomorrow 20:00 or "
                                                        "2026-11-14 20:00",
                               details="More about it", server="The game server it's on")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def create(interaction: discord.Interaction, title: str, when: str, details: str | None = None,
                         server: str | None = None) -> None:
            await self.create(interaction, title, when, details, server)

        @group.command(name="list", description="Events coming up")
        async def list_(interaction: discord.Interaction) -> None:
            await self.list_events(interaction)

        tree.add_command(group)
