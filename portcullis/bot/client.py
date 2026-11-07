# SPDX-License-Identifier: AGPL-3.0-or-later
"""``PortcullisBot``: the discord.py client that owns the command tree and the relay."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

import discord
from discord import app_commands

from portcullis.bot.commands import Access, Commands, register
from portcullis.core.port import AmpPort
from portcullis.core.relay import GameHooks, Relay, RelayTarget, no_hooks
from portcullis.db.database import Database
from portcullis.db.repos import AuditRepo, ServerRepo

log = logging.getLogger(__name__)

REFRESH_EVERY = 300.0  # seconds between re-reading AMP's server list


def intents() -> discord.Intents:
    """Only the guilds intent: slash commands need no privileged intents (no members, no message content)."""
    wanted = discord.Intents.none()
    wanted.guilds = True
    return wanted


class DiscordSink:
    """The relay's way out: post text to a channel id, with every mention disabled."""

    def __init__(self, client: discord.Client) -> None:
        self.client = client

    async def send(self, channel_id: int, text: str) -> None:
        channel = self.client.get_channel(channel_id)
        if channel is None:
            channel = await self.client.fetch_channel(channel_id)
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none(),  # type: ignore[union-attr]
                           suppress_embeds=True)


class PortcullisBot(discord.Client):
    def __init__(
        self,
        *,
        port: AmpPort,
        db: Database,
        access: Access,
        guild_id: int,
        hooks: Callable[[str | None], GameHooks] = no_hooks,
        refresh_every: float = REFRESH_EVERY,
    ) -> None:
        super().__init__(intents=intents(), allowed_mentions=discord.AllowedMentions.none())
        self.port = port
        self.guild_id = guild_id
        self.servers = ServerRepo(db)
        self.audit = AuditRepo(db)
        self.relay = Relay(port, DiscordSink(self), hooks=hooks)
        self.refresh_every = refresh_every
        self.tree = app_commands.CommandTree(self)
        self.commands = Commands(
            port, self.servers, self.audit, access, guild_id=guild_id,
            on_relay_changed=self.apply_relays,
            has_chat_pattern=lambda game: hooks(game).chat is not None,
        )
        register(self.tree, self.commands)
        self._refresher: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        self.tree.clear_commands(guild=None)
        await self.tree.sync(guild=guild)  # guild commands show up at once
        await self.tree.sync()  # removes any global copies an older build registered
        await self.refresh()
        self._refresher = asyncio.create_task(self._refresh_loop(), name="portcullis-refresh")

    async def on_ready(self) -> None:
        log.info("logged in to Discord as %s", self.user)
        if self.get_guild(self.guild_id) is None:
            log.warning("Portcullis is not in the Discord server set in DISCORD_GUILD_ID (%s). "
                        "Invite it with the URL from docs/SETUP.md.", self.guild_id)

    async def refresh(self) -> None:
        """Sync the server table with AMP, then follow the servers that have relay channels."""
        try:
            new, gone = await self.servers.sync(await self.port.list_servers())
            if new or gone:
                log.info("servers: %d new, %d gone", len(new), len(gone))
        except Exception as exc:  # noqa: BLE001 - keep running on the last known list
            log.warning("could not read the server list from AMP: %s", exc)
        await self.apply_relays()

    async def apply_relays(self) -> None:
        rows = await self.servers.list()
        await self.relay.apply([
            RelayTarget(server=r.shard_id, name=r.name, game=r.template, console_channel=r.console_channel,
                        event_channel=r.event_channel, chat_channel=r.chat_channel)
            for r in rows
        ])

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.refresh_every)
            await self.refresh()

    async def close(self) -> None:
        if self._refresher is not None:
            self._refresher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresher
            self._refresher = None
        await self.relay.close()
        await super().close()
