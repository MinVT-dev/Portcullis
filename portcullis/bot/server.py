# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/server list|status|power|send`` and ``/backups``."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from portcullis.assets import game_art
from portcullis.bot.base import Module, fill, reply, short_error, state_text, valid_line
from portcullis.core.relay import escape
from portcullis.core.types import PowerAction, ServerState, ServerStatus
from portcullis.db.repos import ServerRow

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
VERBS = {"start": "Starting", "stop": "Stopping", "restart": "Restarting"}
STATUS_TIMEOUT = 8.0
SAY_KEY = "say"  # per-server setting: console command template for in-game chat, e.g. "say {message}"


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
    art = game_art(row.template, name=row.amp_name)
    if art.thumbnail_url:
        embed.set_thumbnail(url=art.thumbnail_url)
    embed.set_footer(text=f"{art.name} · server id {row.shard_id}")
    return embed


class PowerButtons(discord.ui.View):
    """Start/Stop/Restart under a status reply, shown to moderators only."""

    def __init__(self, module: ServerModule, row: ServerRow) -> None:
        super().__init__(timeout=600)
        self.module = module
        self.row = row
        for action, style in ((PowerAction.START, discord.ButtonStyle.success),
                              (PowerAction.STOP, discord.ButtonStyle.danger),
                              (PowerAction.RESTART, discord.ButtonStyle.secondary)):
            button: discord.ui.Button[PowerButtons] = discord.ui.Button(label=action.value.capitalize(), style=style)
            button.callback = self._handler(action)  # type: ignore[method-assign]
            self.add_item(button)

    def _handler(self, action: PowerAction):  # noqa: ANN202
        async def press(interaction: discord.Interaction) -> None:
            await self.module.do_power(interaction, self.row, action)
        return press


class ServerModule(Module):
    async def server_list(self, interaction: discord.Interaction) -> None:
        if not await self.in_guild(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.ctx.servers.list(include_hidden=False)
        if not rows:
            await reply(interaction, "No servers yet. Portcullis lists the servers its AMP account can see.")
            return

        async def one(row: ServerRow) -> str:
            try:
                status = await asyncio.wait_for(self.ctx.port.get_status(row.shard_id), STATUS_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - one bad server must not hide the rest
                log.warning("status for %s failed: %s", row.name, exc)
                return f"{state_text(ServerState.UNKNOWN)} · **{escape(row.name)}** · couldn't reach AMP"
            players = f" · {status.player_count} online" if status.state is ServerState.RUNNING else ""
            return f"{state_text(status.state)} · **{escape(row.name)}**{players}"

        lines = await asyncio.gather(*(one(r) for r in rows))
        await reply(interaction, embed=discord.Embed(title="Servers", description="\n".join(lines)[:4000]))

    async def server_status(self, interaction: discord.Interaction, server: str) -> None:
        if not await self.in_guild(interaction):
            return
        row = await self.server(interaction, server)
        if row is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            status = await asyncio.wait_for(self.ctx.port.get_status(row.shard_id), STATUS_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            log.warning("status for %s failed: %s", row.name, exc)
            await reply(interaction, f"Couldn't get the status of {escape(row.name)} from AMP: "
                                     f"{escape(short_error(exc))}")
            return
        extra = {"view": PowerButtons(self, row)} if self.ctx.access.is_mod(interaction.user) else {}
        await reply(interaction, embed=status_embed(row, status), **extra)

    async def server_power(self, interaction: discord.Interaction, server: str, action: str) -> None:
        if not await self.in_guild(interaction):
            return
        power = next((a for a in POWER_ACTIONS if a.value == action), None)
        if power is None:
            await reply(interaction, "Choose start, stop or restart.")
            return
        row = await self.server(interaction, server)
        if row is None:
            return
        await self.do_power(interaction, row, power)

    async def do_power(self, interaction: discord.Interaction, row: ServerRow, power: PowerAction) -> None:
        node = f"server.power.{power.value}"
        if not await self.require_mod(interaction, node, row.shard_id):
            return
        await interaction.response.defer(thinking=True)  # public, so everyone sees who did it
        try:
            await self.ctx.port.power(row.shard_id, power)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s on %s failed: %s", node, row.name, exc)
            await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, result="failed",
                                        detail={"error": short_error(exc)})
            await reply(interaction, f"AMP didn't {power.value} {escape(row.name)}: {escape(short_error(exc))}",
                        ephemeral=False)
            return
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id)
        await reply(interaction, f"{VERBS[power.value]} **{escape(row.name)}** (asked by {interaction.user.mention}).",
                    ephemeral=False)

    async def server_send(self, interaction: discord.Interaction, server: str, text: str, as_: str = "console") -> None:
        if not await self.in_guild(interaction):
            return
        row = await self.server(interaction, server)
        if row is None:
            return
        node = f"server.send.{as_}"
        if not await self.require_mod(interaction, node, row.shard_id):
            return
        if not valid_line(text):
            await reply(interaction, "Send one line of up to 500 characters, without line breaks.")
            return
        if as_ == "chat":
            template = await self.ctx.settings.get(SAY_KEY, scope=row.shard_id)
            if not template:
                await reply(interaction, f"{escape(row.name)} has no chat command set up. An admin can add one in "
                                         "`/setup server` (for example `say {message}`).")
                return
            line = fill(template, message=text)
        else:
            line = text
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.ctx.port.send_console(row.shard_id, line)
        except Exception as exc:  # noqa: BLE001
            await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, result="failed",
                                        detail={"error": short_error(exc)})
            await reply(interaction, f"AMP didn't take the command: {escape(short_error(exc))}")
            return
        # Only the command word is audited: arguments can hold passwords (DESIGN.md, audit table).
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id,
                                    target=text.split(" ", 1)[0][:40] if as_ == "console" else None)
        await reply(interaction, f"Sent to **{escape(row.name)}**.")

    async def backups(self, interaction: discord.Interaction, server: str) -> None:
        if not await self.in_guild(interaction):
            return
        row = await self.server(interaction, server)
        if row is None or not await self.require_mod(interaction, "backup.list", row.shard_id):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            items = await self.ctx.port.list_backups(row.shard_id)
        except Exception as exc:  # noqa: BLE001
            await reply(interaction, f"Couldn't list backups for {escape(row.name)}: {escape(short_error(exc))}")
            return
        if not items:
            await reply(interaction, f"{escape(row.name)} has no backups.")
            return
        lines = []
        for b in items[:15]:
            when = discord.utils.format_dt(b.created_at, "f") if b.created_at else "unknown time"
            size = f" · {b.size_mb} MB" if b.size_mb is not None else ""
            pin = " · 📌 kept" if b.sticky else ""
            lines.append(f"**{escape(b.name or b.id)}** · {when}{size}{pin}")
        more = f"\n…and {len(items) - 15} older" if len(items) > 15 else ""
        embed = discord.Embed(title=f"Backups of {row.name}", description="\n".join(lines) + more)
        embed.set_footer(text="Making and deleting backups from Discord comes later (see docs/SETUP.md).")
        await reply(interaction, embed=embed)

    def register(self, tree: app_commands.CommandTree) -> None:
        group = app_commands.Group(name="server", description="Game servers", guild_only=True)
        auto = self.autocomplete_server

        @group.command(name="list", description="List the game servers and whether they are online")
        async def server_list(interaction: discord.Interaction) -> None:
            await self.server_list(interaction)

        @group.command(name="status", description="Show one server's state and who is online")
        @app_commands.describe(server="The game server")
        @app_commands.autocomplete(server=auto)
        async def server_status(interaction: discord.Interaction, server: str) -> None:
            await self.server_status(interaction, server)

        @group.command(name="power", description="Start, stop or restart a server (operators)")
        @app_commands.describe(server="The game server", action="What to do")
        @app_commands.autocomplete(server=auto)
        @app_commands.choices(action=[app_commands.Choice(name=a.value.capitalize(), value=a.value)
                                      for a in POWER_ACTIONS])
        async def server_power(interaction: discord.Interaction, server: str, action: str) -> None:
            await self.server_power(interaction, server, action)

        @group.command(name="send", description="Send a console command or a chat message to a server (operators)")
        @app_commands.describe(server="The game server", text="One line to send",
                               as_="Console command (default) or in-game chat")
        @app_commands.rename(as_="as")
        @app_commands.autocomplete(server=auto)
        @app_commands.choices(as_=[app_commands.Choice(name="Console command", value="console"),
                                   app_commands.Choice(name="In-game chat", value="chat")])
        async def server_send(interaction: discord.Interaction, server: str, text: str, as_: str = "console") -> None:
            await self.server_send(interaction, server, text, as_)

        @app_commands.command(name="backups", description="List a server's backups (operators)")
        @app_commands.guild_only()
        @app_commands.describe(server="The game server")
        @app_commands.autocomplete(server=auto)
        async def backups(interaction: discord.Interaction, server: str) -> None:
            await self.backups(interaction, server)

        tree.add_command(group)
        tree.add_command(backups)
