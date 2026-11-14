# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/poll create`` and ``/poll list``. Votes are buttons that keep working across bot restarts."""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord import app_commands

from portcullis.bot.base import MUTED, NONE, Ctx, Module, card, reply, same_guild, when
from portcullis.core.relay import escape
from portcullis.db.features import Poll

log = logging.getLogger(__name__)

MAX_OPTIONS = 10
BAR = 12


def parse_options(raw: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[;|\n]", raw)]
    return [p[:80] for p in parts if p]


def poll_embed(poll: Poll, tally: dict[int, int]) -> discord.Embed:
    total = sum(tally.values())
    lines = []
    for i, option in enumerate(poll.options):
        votes = tally.get(i, 0)
        filled = round(BAR * votes / total) if total else 0
        lines.append(f"**{i + 1}. {escape(option)}**\n`{'█' * filled}{'░' * (BAR - filled)}` {votes}")
    embed = card(title=poll.question[:256], description="\n".join(lines),
                          colour=MUTED if poll.closed else 0x3498DB)
    if poll.closed:
        footer = f"Closed · {total} vote(s)"
    elif poll.closes_at:
        footer = f"{total} vote(s) · one vote each, you can change it"
        embed.add_field(name="Closes", value=when(poll.closes_at))
    else:
        footer = f"{total} vote(s) · one vote each, you can change it"
    embed.set_footer(text=footer)
    return embed


class PollVote(discord.ui.DynamicItem[discord.ui.Button[Any]], template=r"poll:(?P<id>\d+):(?P<opt>\d+)"):
    module: PollModule | None = None

    def __init__(self, poll_id: int, option: int, label: str = "") -> None:
        super().__init__(discord.ui.Button(label=(label or str(option + 1))[:80], style=discord.ButtonStyle.primary,
                                           custom_id=f"poll:{poll_id}:{option}"))
        self.poll_id = poll_id
        self.option = option

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item[Any],
                             match: re.Match[str]) -> PollVote:
        return cls(int(match["id"]), int(match["opt"]))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        assert self.module is not None
        return await same_guild(self.module.ctx, interaction)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.module is not None
        await self.module.vote(interaction, self.poll_id, self.option)


class PollClose(discord.ui.DynamicItem[discord.ui.Button[Any]], template=r"pollclose:(?P<id>\d+)"):
    module: PollModule | None = None

    def __init__(self, poll_id: int) -> None:
        super().__init__(discord.ui.Button(label="Close poll", style=discord.ButtonStyle.secondary,
                                           custom_id=f"pollclose:{poll_id}"))
        self.poll_id = poll_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item[Any],
                             match: re.Match[str]) -> PollClose:
        return cls(int(match["id"]))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        assert self.module is not None
        return await same_guild(self.module.ctx, interaction)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.module is not None
        await self.module.close_by_button(interaction, self.poll_id)


def poll_view(poll: Poll) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for i, option in enumerate(poll.options):
        view.add_item(PollVote(poll.id, i, f"{i + 1}. {option}"))
    view.add_item(PollClose(poll.id))
    return view


class PollModule(Module):
    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)
        PollVote.module = self
        PollClose.module = self

    async def create(self, interaction: discord.Interaction, question: str, options: str,
                     hours: float | None = None) -> None:
        if not await self.in_guild(interaction) or not await self.require_mod(interaction, "poll.create"):
            return
        choices = parse_options(options)
        if not 2 <= len(choices) <= MAX_OPTIONS:
            await reply(interaction, f"Give 2 to {MAX_OPTIONS} options separated by `;`, e.g. `Yes; No; Maybe`.")
            return
        if hours is not None and not 0 < hours <= 24 * 30:
            await reply(interaction, "A poll can run for up to 30 days (720 hours).")
            return
        closes = (datetime.now(UTC) + timedelta(hours=hours)).isoformat(timespec="seconds") if hours else None
        poll = await self.ctx.polls.create(interaction.channel_id or 0, question[:256], choices, interaction.user.id,
                                           closes)
        await interaction.response.send_message(embed=poll_embed(poll, {}), view=poll_view(poll),
                                                allowed_mentions=NONE)
        message = await interaction.original_response()
        await self.ctx.polls.set_message(poll.id, message.id)
        await self.ctx.audit.record("poll.create", actor_id=interaction.user.id, target=str(poll.id))

    async def vote(self, interaction: discord.Interaction, poll_id: int, option: int) -> None:
        poll = await self.ctx.polls.get(poll_id)
        if poll is None or poll.closed or not 0 <= option < len(poll.options):
            await reply(interaction, "This poll is closed.")
            return
        await self.ctx.polls.vote(poll_id, interaction.user.id, option)
        await interaction.response.edit_message(embed=poll_embed(poll, await self.ctx.polls.tally(poll_id)))

    async def close_by_button(self, interaction: discord.Interaction, poll_id: int) -> None:
        poll = await self.ctx.polls.get(poll_id)
        if poll is None:
            await reply(interaction, "That poll no longer exists.")
            return
        if interaction.user.id != poll.created_by and not await self.require_mod(interaction, "poll.close"):
            return
        await self.ctx.polls.close(poll_id)
        closed = await self.ctx.polls.get(poll_id)
        assert closed is not None
        await self.ctx.audit.record("poll.close", actor_id=interaction.user.id, target=str(poll_id))
        await interaction.response.edit_message(embed=poll_embed(closed, await self.ctx.polls.tally(poll_id)),
                                                view=None)

    async def close_due(self) -> int:
        """Close polls whose time is up and update their messages. Run from the bot's refresh loop."""
        count = 0
        for poll in await self.ctx.polls.due():
            if not await self.ctx.polls.close(poll.id):
                continue
            count += 1
            closed = await self.ctx.polls.get(poll.id)
            if closed is None or closed.message_id is None:
                continue
            with contextlib.suppress(Exception):  # the message or channel may be gone
                channel = await self.ctx.channel(closed.channel_id)
                message = await channel.fetch_message(closed.message_id)
                await message.edit(embed=poll_embed(closed, await self.ctx.polls.tally(poll.id)), view=None)
        return count

    async def list_open(self, interaction: discord.Interaction) -> None:
        if not await self.in_guild(interaction) or not await self.require_mod(interaction, "poll.list"):
            return
        polls = await self.ctx.polls.open_polls()
        if not polls:
            await reply(interaction, "No open polls.")
            return
        lines = []
        for p in polls[:20]:
            link = (f"https://discord.com/channels/{self.ctx.guild_id}/{p.channel_id}/{p.message_id}"
                    if p.message_id else "")
            closes = f" · closes {when(p.closes_at)}" if p.closes_at else ""
            lines.append(f"[{escape(p.question[:80])}]({link}) · by <@{p.created_by}>{closes}")
        await reply(interaction, embed=card(title="Open polls", description="\n".join(lines)))

    def register(self, tree: app_commands.CommandTree) -> None:
        group = app_commands.Group(name="poll", description="Community polls", guild_only=True)

        @group.command(name="create", description="Post a poll in this channel (operators)")
        @app_commands.describe(question="The question", options="2 to 10 answers separated by ;",
                               hours="Close automatically after this many hours (leave out to close by hand)")
        async def create(interaction: discord.Interaction, question: str, options: str,
                         hours: float | None = None) -> None:
            await self.create(interaction, question, options, hours)

        @group.command(name="list", description="Open polls (operators)")
        async def list_open(interaction: discord.Interaction) -> None:
            await self.list_open(interaction)

        tree.add_command(group)
