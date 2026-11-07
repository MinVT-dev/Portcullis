# SPDX-License-Identifier: AGPL-3.0-or-later
"""Slash command handlers.

The handlers are plain methods on ``Commands`` taking an interaction, so tests call them with a fake
interaction. ``register`` wires them into a ``CommandTree``.

Commands (a first slice of DESIGN.md section 5):

- ``/server list`` and ``/server status <server>``: everyone in the guild;
- ``/server power <server> <start|stop|restart>``: owners and members with an operator role;
- ``/setup relay <server> <console|events|chat> [channel]``: owners and members with Manage Server.
  An interim command until the ``/setup`` panel exists.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands

from portcullis.core.port import AmpPort
from portcullis.core.relay import STATE_ICONS, escape
from portcullis.core.types import PowerAction, ServerState, ServerStatus
from portcullis.db.repos import AuditRepo, ServerRepo, ServerRow

log = logging.getLogger(__name__)

STATE_COLOURS = {
    ServerState.RUNNING: 0x2ECC71,
    ServerState.STOPPED: 0xE74C3C,
    ServerState.BUSY: 0xF1C40F,
    ServerState.SLEEPING: 0x95A5A6,
    ServerState.FAILED: 0xC0392B,
    ServerState.SUSPENDED: 0x7F8C8D,
    ServerState.UNKNOWN: 0x7F8C8D,
}

POWER_ACTIONS = (PowerAction.START, PowerAction.STOP, PowerAction.RESTART)
RELAY_KINDS = {"console": "console_channel", "events": "event_channel", "chat": "chat_channel"}
STATUS_TIMEOUT = 8.0


@dataclass(frozen=True)
class Access:
    """Who may do what. Owners can do everything; operators can use power actions."""

    owner_ids: frozenset[int] = frozenset()
    operator_role_ids: frozenset[int] = frozenset()

    def is_owner(self, user: Any) -> bool:
        return user.id in self.owner_ids

    def can_power(self, user: Any) -> bool:
        roles = {r.id for r in getattr(user, "roles", ())}
        return self.is_owner(user) or bool(roles & self.operator_role_ids)

    def can_setup(self, user: Any) -> bool:
        perms = getattr(user, "guild_permissions", None)
        return self.is_owner(user) or bool(perms and perms.manage_guild)


def short_error(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    return text if len(text) <= 300 else text[:299] + "…"


def state_text(state: ServerState) -> str:
    """Status is never colour-only (DESIGN.md section 7): icon plus a word."""
    return f"{STATE_ICONS.get(state, '❔')} {state.label}"


def status_embed(row: ServerRow, status: ServerStatus) -> discord.Embed:
    embed = discord.Embed(title=row.name, colour=STATE_COLOURS.get(status.state, 0x7F8C8D))
    embed.add_field(name="State", value=state_text(status.state))
    count = f"{status.player_count}" + (f" / {status.max_players}" if status.max_players else "")
    embed.add_field(name="Players", value=count)
    if status.cpu_percent is not None:
        embed.add_field(name="CPU", value=f"{status.cpu_percent:.0f}%")
    if status.memory_mb is not None:
        limit = f" / {status.memory_limit_mb} MB" if status.memory_limit_mb else " MB"
        embed.add_field(name="Memory", value=f"{status.memory_mb}{limit}")
    if status.players:
        names = ", ".join(escape(p.name) for p in status.players)
        embed.add_field(name="Online now", value=names if len(names) <= 1000 else names[:999] + "…", inline=False)
    if status.message:
        embed.add_field(name="Message", value=escape(status.message)[:1000], inline=False)
    embed.set_footer(text=f"Server id {row.shard_id}")
    return embed


class Commands:
    def __init__(
        self,
        port: AmpPort,
        servers: ServerRepo,
        audit: AuditRepo,
        access: Access,
        *,
        guild_id: int,
        on_relay_changed: Callable[[], Awaitable[None]],
        has_chat_pattern: Callable[[str | None], bool] = lambda game: False,
    ) -> None:
        self.port = port
        self.servers = servers
        self.audit = audit
        self.access = access
        self.guild_id = guild_id
        self.on_relay_changed = on_relay_changed
        self.has_chat_pattern = has_chat_pattern

    # -------------------------------------------------------------------------- helpers

    async def _reply(self, interaction: discord.Interaction, content: str | None = None, *,
                     embed: discord.Embed | None = None, ephemeral: bool = True) -> None:
        kwargs: dict[str, Any] = {"ephemeral": ephemeral, "allowed_mentions": discord.AllowedMentions.none()}
        if embed is not None:
            kwargs["embed"] = embed
        if interaction.response.is_done():
            await interaction.followup.send(content, **kwargs)
        else:
            await interaction.response.send_message(content, **kwargs)

    async def _in_guild(self, interaction: discord.Interaction) -> bool:
        """Refuse use from any other Discord server the bot ended up in."""
        if interaction.guild_id == self.guild_id:
            return True
        await self._reply(interaction, "Portcullis only works in the Discord server it was set up for.")
        return False

    async def _visible_server(self, interaction: discord.Interaction, name: str) -> ServerRow | None:
        row = await self.servers.find(name)
        if row is None or (row.hidden and not self.access.can_power(interaction.user)):
            await self._reply(interaction, f"I don't know a server called {escape(name)}. "
                                           "Pick one from the list as you type.")
            return None
        return row

    async def autocomplete_server(self, interaction: discord.Interaction,
                                  current: str) -> list[app_commands.Choice[str]]:
        rows = await self.servers.list(include_hidden=self.access.can_power(interaction.user))
        needle = current.lower()
        return [app_commands.Choice(name=r.name[:100], value=r.shard_id)
                for r in rows if needle in r.name.lower()][:25]

    # -------------------------------------------------------------------------- /server

    async def server_list(self, interaction: discord.Interaction) -> None:
        if not await self._in_guild(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.servers.list(include_hidden=False)
        if not rows:
            await self._reply(interaction, "No servers yet. Portcullis lists the servers its AMP account can see.")
            return

        async def one(row: ServerRow) -> str:
            try:
                status = await asyncio.wait_for(self.port.get_status(row.shard_id), STATUS_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - one bad server must not hide the rest
                log.warning("status for %s failed: %s", row.name, exc)
                return f"{state_text(ServerState.UNKNOWN)} · **{escape(row.name)}** · couldn't reach AMP"
            players = f" · {status.player_count} online" if status.state is ServerState.RUNNING else ""
            return f"{state_text(status.state)} · **{escape(row.name)}**{players}"

        lines = await asyncio.gather(*(one(r) for r in rows))
        embed = discord.Embed(title="Servers", description="\n".join(lines)[:4000])
        await self._reply(interaction, embed=embed)

    async def server_status(self, interaction: discord.Interaction, server: str) -> None:
        if not await self._in_guild(interaction):
            return
        row = await self._visible_server(interaction, server)
        if row is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            status = await asyncio.wait_for(self.port.get_status(row.shard_id), STATUS_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            log.warning("status for %s failed: %s", row.name, exc)
            await self._reply(interaction, f"Couldn't get the status of {escape(row.name)} from AMP: "
                                           f"{escape(short_error(exc))}")
            return
        await self._reply(interaction, embed=status_embed(row, status))

    async def server_power(self, interaction: discord.Interaction, server: str, action: str) -> None:
        if not await self._in_guild(interaction):
            return
        power = next((a for a in POWER_ACTIONS if a.value == action), None)
        if power is None:
            await self._reply(interaction, "Choose start, stop or restart.")
            return
        node = f"server.power.{power.value}"
        row = await self._visible_server(interaction, server)
        if row is None:
            return
        if not self.access.can_power(interaction.user):
            await self.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, result="denied")
            await self._reply(interaction, "You need an operator role to start, stop or restart servers.")
            return
        await interaction.response.defer(thinking=True)  # public, so everyone sees who did it
        try:
            await self.port.power(row.shard_id, power)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s on %s failed: %s", node, row.name, exc)
            await self.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, result="failed",
                                    detail={"error": short_error(exc)})
            await self._reply(interaction, f"AMP didn't {power.value} {escape(row.name)}: "
                                           f"{escape(short_error(exc))}", ephemeral=False)
            return
        await self.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id)
        verb = {"start": "Starting", "stop": "Stopping", "restart": "Restarting"}[power.value]
        await self._reply(interaction, f"{verb} **{escape(row.name)}** (asked by {interaction.user.mention}).",
                          ephemeral=False)

    # -------------------------------------------------------------------------- /setup

    async def setup_relay(self, interaction: discord.Interaction, server: str, kind: str,
                          channel: discord.TextChannel | None = None) -> None:
        if not await self._in_guild(interaction):
            return
        if not self.access.can_setup(interaction.user):
            await self._reply(interaction, "You need the Manage Server permission to change relays.")
            return
        column = RELAY_KINDS.get(kind)
        if column is None:
            await self._reply(interaction, "Choose console, events or chat.")
            return
        row = await self.servers.find(server)
        if row is None:
            await self._reply(interaction, f"I don't know a server called {escape(server)}.")
            return
        if channel is not None:
            me = interaction.guild.me if interaction.guild else None
            perms = channel.permissions_for(me) if me is not None else None  # unknown: let the relay log it
            if perms is not None and not (perms.view_channel and perms.send_messages):
                await self._reply(interaction, f"I can't post in {channel.mention}. Give Portcullis View Channel "
                                               "and Send Messages there, then try again.")
                return
        await self.servers.update(row.shard_id, **{column: channel.id if channel else None})
        await self.audit.record(f"setup.relay.{kind}", actor_id=interaction.user.id, shard_id=row.shard_id,
                                target=str(channel.id) if channel else None)
        await self.on_relay_changed()
        if channel is None:
            text = f"Stopped relaying {kind} for **{escape(row.name)}**."
        else:
            text = f"Relaying {kind} for **{escape(row.name)}** to {channel.mention}."
            if kind == "chat" and not self.has_chat_pattern(row.template):
                text += (" Portcullis doesn't know how this game writes chat to its console yet, so nothing will "
                         "be posted there until a chat pattern for it is added.")
        await self._reply(interaction, text)


def register(tree: app_commands.CommandTree, cmds: Commands) -> None:
    """Add the commands to ``tree``. Kept thin: every rule lives in ``Commands``."""
    server = app_commands.Group(name="server", description="Game servers", guild_only=True)

    @server.command(name="list", description="List the game servers and whether they are online")
    async def server_list(interaction: discord.Interaction) -> None:
        await cmds.server_list(interaction)

    @server.command(name="status", description="Show one server's state and who is online")
    @app_commands.describe(server="The game server")
    @app_commands.autocomplete(server=cmds.autocomplete_server)
    async def server_status(interaction: discord.Interaction, server: str) -> None:
        await cmds.server_status(interaction, server)

    @server.command(name="power", description="Start, stop or restart a server (operators only)")
    @app_commands.describe(server="The game server", action="What to do")
    @app_commands.autocomplete(server=cmds.autocomplete_server)
    @app_commands.choices(action=[app_commands.Choice(name=a.value.capitalize(), value=a.value)
                                  for a in POWER_ACTIONS])
    async def server_power(interaction: discord.Interaction, server: str, action: str) -> None:
        await cmds.server_power(interaction, server, action)

    setup = app_commands.Group(name="setup", description="Portcullis settings", guild_only=True,
                               default_permissions=discord.Permissions(manage_guild=True))

    @setup.command(name="relay", description="Post a server's console, events or chat in a channel")
    @app_commands.describe(server="The game server", kind="What to relay",
                           channel="Where to post it. Leave out to stop relaying.")
    @app_commands.autocomplete(server=cmds.autocomplete_server)
    @app_commands.choices(kind=[app_commands.Choice(name="Console (game output)", value="console"),
                                app_commands.Choice(name="Events (joins, leaves, state)", value="events"),
                                app_commands.Choice(name="Chat (in-game chat)", value="chat")])
    async def setup_relay(interaction: discord.Interaction, server: str, kind: str,
                          channel: discord.TextChannel | None = None) -> None:
        await cmds.setup_relay(interaction, server, kind, channel)

    tree.add_command(server)
    tree.add_command(setup)
