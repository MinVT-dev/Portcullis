# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/notify``: a member asks for a DM when a server comes online.

``Notifier`` is a relay listener. AMP repeats the current state every few seconds, so it keeps its
own last-seen state per server and only acts on a real change into Online.
"""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands

from portcullis.bot.base import NONE, Ctx, Module, reply
from portcullis.core.relay import escape
from portcullis.core.types import ServerEvent, ServerId, ServerState, StateChanged

log = logging.getLogger(__name__)

QUIET_FOR = 300.0  # seconds: at most one "online" DM per server this often (a crash loop must not spam)
# Coming online from these counts. Unknown doesn't: that is usually Portcullis losing AMP, not the server.
FROM_STATES = frozenset({ServerState.STOPPED, ServerState.BUSY, ServerState.SLEEPING, ServerState.FAILED,
                         ServerState.SUSPENDED})


class Notifier:
    def __init__(self, ctx: Ctx, *, quiet_for: float = QUIET_FOR) -> None:
        self.ctx = ctx
        self.quiet_for = quiet_for
        self.last: dict[ServerId, ServerState] = {}
        self.sent_at: dict[ServerId, float] = {}
        self.tasks: set[asyncio.Task[None]] = set()

    async def __call__(self, event: ServerEvent) -> None:
        if not isinstance(event, StateChanged):
            return
        previous = self.last.get(event.server)
        self.last[event.server] = event.state
        if event.state is not ServerState.RUNNING or previous not in FROM_STATES:
            return
        now = time.monotonic()
        if now - self.sent_at.get(event.server, -self.quiet_for) < self.quiet_for:
            return
        self.sent_at[event.server] = now
        # DMs are slow; don't hold up the relay for this server while they go out.
        task = asyncio.create_task(self.send(event.server), name=f"portcullis-notify-{event.server}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def send(self, server: ServerId) -> int:
        """DM everyone subscribed to ``server``. Returns how many DMs went out."""
        row = await self.ctx.servers.get(server)
        client = self.ctx.client
        if row is None or row.hidden or client is None:
            return 0
        text = f"🟢 **{escape(row.name)}** is online. (You asked to hear about it; `/notify` again to stop.)"
        sent = 0
        for user_id in await self.ctx.notify.for_server(server):
            try:
                user = client.get_user(user_id) or await client.fetch_user(user_id)
                await user.send(text, allowed_mentions=NONE)
                sent += 1
            except Exception as exc:  # noqa: BLE001 - closed DMs or a member who left; skip them
                log.info("could not DM %s about %s: %s", user_id, row.name, exc)
        return sent

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class NotifyModule(Module):
    async def notify(self, interaction: discord.Interaction, server: str | None = None) -> None:
        if not await self.in_guild(interaction):
            return
        if server is None:
            await self.list_mine(interaction)
            return
        row = await self.server(interaction, server)
        if row is None:
            return
        result = await self.ctx.notify.toggle(interaction.user.id, row.shard_id)
        if result is None:
            await reply(interaction, f"You can follow up to {self.ctx.notify.LIMIT} servers. "
                                     "Run `/notify` on one you follow to stop following it.")
        elif result:
            await reply(interaction, f"I'll DM you when **{escape(row.name)}** comes online. Your DMs from "
                                     "server members need to be open. Run this again to stop.")
        else:
            await reply(interaction, f"You'll no longer get a DM when **{escape(row.name)}** comes online.")

    async def list_mine(self, interaction: discord.Interaction) -> None:
        names = []
        for sid in await self.ctx.notify.for_member(interaction.user.id):
            row = await self.ctx.servers.get(sid)
            if row is not None and not row.hidden:
                names.append(f"**{escape(row.name)}**")
        if not names:
            await reply(interaction, "You don't follow any server. Use `/notify server:<name>` to get a DM "
                                     "when it comes online.")
            return
        await reply(interaction, "You get a DM when these come online: " + ", ".join(names) +
                    ". Run `/notify` with one of them to stop.")

    def register(self, tree: app_commands.CommandTree) -> None:
        @app_commands.command(name="notify", description="Get a DM when a server comes online (run again to stop)")
        @app_commands.guild_only()
        @app_commands.describe(server="The game server. Leave out to see what you follow.")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def notify(interaction: discord.Interaction, server: str | None = None) -> None:
            await self.notify(interaction, server)

        tree.add_command(notify)
