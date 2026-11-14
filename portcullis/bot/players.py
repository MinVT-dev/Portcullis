# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/players``: who is online, per server, with the count and the server's player limit.

What each server shows is the ``players_visibility`` setting (``/setup players``), per server with a
bot-wide default:

* ``names`` (default): everyone sees the names.
* ``count``: everyone sees how many are on; staff also see the names.
* ``staff``: only staff see the server's players at all.

Hidden servers stay hidden as everywhere else. Some games don't report players to AMP at all (Arma
Reforger showed an empty roster with a player on); AMP can't tell those apart from an empty server,
so the reply says so instead of claiming nobody is on.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from portcullis.bot.base import Module, card, reply, thinking
from portcullis.core.relay import escape
from portcullis.core.types import ServerState, ServerStatus
from portcullis.db.repos import ServerRow

log = logging.getLogger(__name__)

VISIBILITY_KEY = "players_visibility"
VISIBILITY = {"names": "Everyone sees names", "count": "Everyone sees the count, staff see names",
              "staff": "Only staff see players"}
STATUS_TIMEOUT = 8.0
NAMES_CHARS = 900  # per server, so a few busy servers still fit in one embed


def names_text(names: list[str]) -> str:
    out: list[str] = []
    size = 0
    for index, name in enumerate(names):
        safe = escape(name)
        if size + len(safe) + 2 > NAMES_CHARS:
            return ", ".join(out) + f" and {len(names) - index} more"
        out.append(safe)
        size += len(safe) + 2
    return ", ".join(out)


def server_block(row: ServerRow, status: ServerStatus | None, mode: str, staff: bool, icon: str) -> str | None:
    """The lines for one server, or None if this member may not see its players."""
    if mode == "staff" and not staff:
        return None
    name = f"**{escape(row.name)}**"
    if status is None:
        return f"{icon} {name} · couldn't reach AMP"
    if status.state is not ServerState.RUNNING:
        return f"{icon} {name} · {status.state.label}"
    limit = f"/{status.max_players}" if status.max_players else ""
    head = f"{icon} {name} · {status.player_count}{limit} online"
    if not status.players:
        return head + "\n　Nobody that AMP can see. Some games don't report players to AMP."
    if mode == "count" and not staff:
        return head
    return head + "\n　" + names_text([p.name for p in status.players])


class PlayersModule(Module):
    async def visibility(self, shard_id: str) -> str:
        mode = await self.ctx.settings.get(VISIBILITY_KEY, scope=shard_id)
        if mode not in VISIBILITY:
            mode = await self.ctx.settings.get(VISIBILITY_KEY, "names")
        return mode if mode in VISIBILITY else "names"

    async def players(self, interaction: discord.Interaction, server: str | None = None) -> None:
        if not await self.in_guild(interaction):
            return
        if server:
            row = await self.server(interaction, server)
            if row is None:
                return
            rows = [row]
        else:
            rows = [r for r in await self.ctx.servers.list() if
                    not r.hidden or await self.ctx.sees_hidden(interaction.user, r.shard_id)]
        if not rows:
            await reply(interaction, "No servers yet.")
            return
        await thinking(interaction)
        staff = await self.ctx.is_staff(interaction.user)

        async def one(row: ServerRow) -> str | None:
            mode = await self.visibility(row.shard_id)
            if mode == "staff" and not staff:
                return None
            try:
                status = await asyncio.wait_for(self.ctx.port.get_status(row.shard_id), STATUS_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - one bad server must not hide the rest
                log.info("players for %s: %s", row.name, exc)
                status = None
            icon = self.ctx.emojis.state(status.state if status else ServerState.UNKNOWN)
            return server_block(row, status, mode, staff, icon)

        blocks = [b for b in await asyncio.gather(*(one(r) for r in rows)) if b]
        if not blocks:
            await reply(interaction, "The player lists here are for staff only.")
            return
        embed = card(title=f"{self.ctx.emojis['players']} Players online", description="\n".join(blocks)[:4000])
        await reply(interaction, embed=embed)

    def register(self, tree: app_commands.CommandTree) -> None:
        @tree.command(name="players", description="Who is online, per server")
        @app_commands.guild_only()
        @app_commands.describe(server="One game server (leave out for all)")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def players(interaction: discord.Interaction, server: str | None = None) -> None:
            await self.players(interaction, server)
