# SPDX-License-Identifier: AGPL-3.0-or-later
"""The live server board: one message in a channel that Portcullis keeps editing.

It shows every visible server, its state and player count, from the same live state as the bot's
profile status. Edits are spaced out (``EVERY`` seconds) so a burst of joins makes one edit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime

import discord

from portcullis.bot.base import NONE, Ctx, card, state_text
from portcullis.core.relay import escape
from portcullis.core.types import ServerState

log = logging.getLogger(__name__)

EVERY = 30.0
BOARD_CHANNEL, BOARD_MESSAGE = "board_channel", "board_message"


class StatusBoard:
    def __init__(self, ctx: Ctx, *, every: float = EVERY) -> None:
        self.ctx = ctx
        self.every = every
        self._at = -every
        self._pending: asyncio.Task[None] | None = None

    async def embed(self) -> discord.Embed:
        live = self.ctx.live
        rows = await self.ctx.servers.list(include_hidden=False)
        lines = []
        for row in rows:
            state = live.states.get(row.shard_id, ServerState.UNKNOWN)
            players = len(live.players.get(row.shard_id, ())) if state is ServerState.RUNNING else 0
            count = f" · {players} online" if state is ServerState.RUNNING else ""
            lines.append(f"{state_text(state)} · **{escape(row.name)}**{count}")
        summary = live.summary()
        embed = card(title="Servers", description="\n".join(lines)[:4000] or "No servers yet.",
                              timestamp=datetime.now(UTC))
        embed.set_footer(text=f"{summary.text()} · updates by itself")
        return embed

    async def changed(self) -> None:
        """Ask for an edit. Several calls within ``every`` seconds become one edit."""
        if self._pending is not None and not self._pending.done():
            return
        wait = self._at + self.every - time.monotonic()
        if wait > 0:
            self._pending = asyncio.create_task(self._later(wait), name="portcullis-board")
            return
        await self.update()

    async def _later(self, wait: float) -> None:
        await asyncio.sleep(wait)
        await self.update()

    async def update(self) -> None:
        channel_id = await self.ctx.settings.get(BOARD_CHANNEL)
        if not channel_id or self.ctx.client is None:
            return
        self._at = time.monotonic()
        embed = await self.embed()
        message_id = await self.ctx.settings.get(BOARD_MESSAGE)
        try:
            channel = await self.ctx.channel(int(channel_id))
            if message_id:
                try:
                    message = await channel.fetch_message(int(message_id))
                    await message.edit(embed=embed, allowed_mentions=NONE)
                    return
                except discord.NotFound:
                    pass  # someone deleted it: post a new one
            message = await channel.send(embed=embed, allowed_mentions=NONE)
            await self.ctx.settings.set(BOARD_MESSAGE, message.id)
        except Exception as exc:  # noqa: BLE001 - cosmetic; never let it break the relay or presence
            log.warning("could not update the server board in channel %s: %s", channel_id, exc)

    async def move(self, channel_id: int | None, by: int | None = None) -> None:
        """Point the board at a channel (posting a fresh message there), or turn it off."""
        await self.ctx.settings.delete(BOARD_MESSAGE)
        if channel_id is None:
            await self.ctx.settings.delete(BOARD_CHANNEL)
            return
        await self.ctx.settings.set(BOARD_CHANNEL, channel_id, by=by)
        await self.update()

    async def close(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pending
