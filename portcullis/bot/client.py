# SPDX-License-Identifier: AGPL-3.0-or-later
"""``PortcullisBot``: the discord.py client that owns the commands, the relay and the profile status."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

import discord
from discord import app_commands

from portcullis.bot.base import Access, Ctx, problem_text
from portcullis.bot.commands import DYNAMIC_ITEMS, Modules
from portcullis.bot.notify import Notifier
from portcullis.core.console_rules import HIDE_KEY, stored_rules
from portcullis.core.live import LinkVerifier, LiveState, PlayerTracker
from portcullis.core.port import AmpPort
from portcullis.core.relay import GameHooks, Relay, RelayTarget, no_hooks
from portcullis.db.database import Database

log = logging.getLogger(__name__)

REFRESH_EVERY = 300.0  # seconds between re-reading AMP's server list and states
RETRY_EVERY = 60.0  # while AMP is unreachable, so the outage alert and recovery come sooner
TICK_EVERY = 60.0  # reminders and alert checks
PRESENCE_EVERY = 15.0  # at most one profile status change this often (Discord rate-limits presence)
STATUS_TIMEOUT = 8.0


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


async def _ui_error(self: discord.ui.View | discord.ui.Modal, interaction: discord.Interaction,
                    error: Exception, *args: object) -> None:
    """A button, menu or form that raised. discord.py only logs it, which leaves the member with
    "This interaction failed", so tell them and log a code they can quote."""
    ref = format(int(time.time() * 1000) % 0xFFFFFF, "06x")
    log.error("interaction failed [%s]: %r", ref, error, exc_info=error)
    text = problem_text(ref)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        log.warning("could not tell the member about [%s]", ref)


discord.ui.View.on_error = _ui_error  # type: ignore[assignment,method-assign]
discord.ui.Modal.on_error = _ui_error  # type: ignore[assignment,method-assign]


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
        self.refresh_every = refresh_every
        self.live = LiveState(on_change=self._live_changed)
        self.ctx = Ctx(port, db, access, guild_id, live=self.live, on_relay_changed=self.apply_relays,
                       has_chat=lambda game: hooks(game).chat is not None, client=self)
        self.servers = self.ctx.servers
        self.audit = self.ctx.audit
        self.modules = Modules(self.ctx)
        self.board = self.ctx.board
        self.notifier = Notifier(self.ctx)
        self.relay = Relay(port, DiscordSink(self), hooks=hooks, follow_all=True)
        # JoinWatch first: it needs to know whether a join is someone's first, before PlayerTracker records it.
        self.relay.listeners += [
            self.modules.joins,
            self.live,
            self.notifier,
            self.modules.alerts,
            PlayerTracker(self.ctx.players),
            LinkVerifier(self.ctx.links, self.ctx.users, on_linked=self.modules.community.on_linked),
        ]
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = self.on_command_error  # type: ignore[method-assign]
        self.modules.register(self.tree)
        self._refresher: asyncio.Task[None] | None = None
        self._ticker: asyncio.Task[None] | None = None
        self._presence_text: str | None = None
        self._presence_at = 0.0
        self._presence_pending: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        self.add_dynamic_items(*DYNAMIC_ITEMS)
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        self.tree.clear_commands(guild=None)
        await self.tree.sync(guild=guild)  # guild commands show up at once
        await self.tree.sync()  # removes any global copies an older build registered
        closed = await self.ctx.players.close_all()  # sessions still open from before a restart
        if closed:
            log.info("closed %d player session(s) left open by the last run", closed)
        await self.refresh()
        self._refresher = asyncio.create_task(self._refresh_loop(), name="portcullis-refresh")
        self._ticker = asyncio.create_task(self._tick_loop(), name="portcullis-tick")

    async def on_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Any command or button that raised: log it with a short code and tell the member, never go silent."""
        ref = format(int(time.time() * 1000) % 0xFFFFFF, "06x")
        original = getattr(error, "original", error)
        log.error("command failed [%s]: %r", ref, original, exc_info=original)
        text = problem_text(ref)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            log.warning("could not tell the member about [%s]", ref)

    async def on_ready(self) -> None:
        log.info("logged in to Discord as %s", self.user)
        if self.get_guild(self.guild_id) is None:
            log.warning("Portcullis is not in the Discord server set in DISCORD_GUILD_ID (%s). "
                        "Invite it with the URL from docs/SETUP.md.", self.guild_id)
        self._presence_text = None  # a reconnect clears the presence; set it again
        await self.update_presence()
        await self.board.changed()

    # ---------------------------------------------------------------- refresh

    async def refresh(self) -> None:
        """Sync servers with AMP, poll their states for the profile status, follow them, close due polls."""
        try:
            new, gone = await self.servers.sync(await self.port.list_servers())
            if new or gone:
                log.info("servers: %d new, %d gone", len(new), len(gone))
            await self.live.set_reachable(True)
        except Exception as exc:  # noqa: BLE001 - keep running on the last known list
            log.warning("could not read the server list from AMP: %s", exc)
            await self.live.set_reachable(False)
        with contextlib.suppress(Exception):
            await self.modules.alerts.reachability(self.live.reachable)
        await self._seed_live()
        await self.apply_relays()
        with contextlib.suppress(Exception):
            await self.modules.polls.close_due()

    async def _seed_live(self) -> None:
        rows = await self.servers.list(include_hidden=False)

        async def one(shard_id: str):  # noqa: ANN202
            try:
                return await asyncio.wait_for(self.port.get_status(shard_id), STATUS_TIMEOUT)
            except Exception:  # noqa: BLE001
                return None

        statuses = await asyncio.gather(*(one(r.shard_id) for r in rows))
        await self.live.seed([s for s in statuses if s is not None], [r.shard_id for r in rows])

    async def apply_relays(self) -> None:
        rows = await self.servers.list()
        targets = []
        for r in rows:
            hide = stored_rules(await self.ctx.settings.get(HIDE_KEY, scope=r.shard_id)) if r.console_channel else ()
            targets.append(RelayTarget(server=r.shard_id, name=r.name, game=r.template,
                                       console_channel=r.console_channel, event_channel=r.event_channel,
                                       chat_channel=r.chat_channel, hide=hide))
        await self.relay.apply(targets)

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.refresh_every if self.live.reachable else min(self.refresh_every, RETRY_EVERY))
            await self.refresh()

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_EVERY)
            if self.is_ready():
                await self.modules.tick()

    # ---------------------------------------------------------------- profile status and server board

    async def _live_changed(self) -> None:
        await self.update_presence()
        if self.is_ready():
            await self.board.changed()

    def presence(self) -> tuple[discord.Status, discord.BaseActivity]:
        summary = self.live.summary()
        status = discord.Status.online if summary.reachable else discord.Status.idle
        return status, discord.CustomActivity(name=summary.text())

    async def update_presence(self) -> None:
        """Show the summary on the bot's profile, at most once per PRESENCE_EVERY seconds."""
        if self._presence_pending is not None and not self._presence_pending.done():
            return  # a change is already scheduled and will pick up the latest summary
        wait = self._presence_at + PRESENCE_EVERY - time.monotonic()
        if wait > 0:
            self._presence_pending = asyncio.create_task(self._presence_later(wait), name="portcullis-presence")
            return
        await self._apply_presence()

    async def _presence_later(self, wait: float) -> None:
        await asyncio.sleep(wait)
        await self._apply_presence()

    async def _apply_presence(self) -> None:
        status, activity = self.presence()
        text = f"{status}:{activity.name}"
        if text == self._presence_text or not self.is_ready():
            return
        try:
            await self.change_presence(status=status, activity=activity)
        except Exception as exc:  # noqa: BLE001 - cosmetic; never let it break anything
            log.debug("could not set the profile status: %s", exc)
            return
        self._presence_text = text
        self._presence_at = time.monotonic()

    async def close(self) -> None:
        for task in (self._refresher, self._ticker, self._presence_pending):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._refresher = None
        await self.modules.close()
        await self.board.close()
        await self.notifier.close()
        await self.relay.close()
        await super().close()
