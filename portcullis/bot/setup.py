# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/setup relay``, ``/setup server`` (one server's panel) and ``/setup bot`` (bot-wide panel).

Admins only (Manage Server). Every change is audited. Panels are ephemeral and stop working after
15 minutes; run the command again for a fresh one.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands

from portcullis.bot.base import Module, reply
from portcullis.bot.server import SAY_KEY
from portcullis.core.relay import escape
from portcullis.db.repos import ServerRow

log = logging.getLogger(__name__)

RELAY_KINDS = {"console": "console_channel", "events": "event_channel", "chat": "chat_channel"}
WL_ADD, WL_REMOVE = "whitelist_add", "whitelist_remove"
WHITELIST_CHANNEL = "whitelist_channel"
COMMAND_KEYS = {WL_ADD: "Whitelist add", WL_REMOVE: "Whitelist remove", SAY_KEY: "Chat (say)"}


def can_post(interaction: discord.Interaction, channel_id: int) -> bool | None:
    """True/False if we can tell whether the bot may post in the channel, None if we can't."""
    guild = interaction.guild
    if guild is None or getattr(guild, "me", None) is None:
        return None
    channel = guild.get_channel(channel_id) if hasattr(guild, "get_channel") else None
    if channel is None:
        return None
    perms = channel.permissions_for(guild.me)
    return bool(perms.view_channel and perms.send_messages)


def mention(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if channel_id else "off"


class SetupModule(Module):
    # ---------------------------------------------------------------- /setup relay

    async def setup_relay(self, interaction: discord.Interaction, server: str, kind: str,
                          channel: Any | None = None) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, f"setup.relay.{kind}"):
            return
        column = RELAY_KINDS.get(kind)
        if column is None:
            await reply(interaction, "Choose console, events or chat.")
            return
        row = await self.ctx.servers.find(server)
        if row is None:
            await reply(interaction, f"I don't know a server called {escape(server)}.")
            return
        if channel is not None:
            me = interaction.guild.me if interaction.guild else None
            perms = channel.permissions_for(me) if me is not None else None  # unknown: let the relay log it
            if perms is not None and not (perms.view_channel and perms.send_messages):
                await reply(interaction, f"I can't post in {channel.mention}. Give Portcullis View Channel "
                                         "and Send Messages there, then try again.")
                return
        await self.set_relay(interaction, row, kind, channel.id if channel else None)
        if channel is None:
            text = f"Stopped relaying {kind} for **{escape(row.name)}**."
        else:
            text = f"Relaying {kind} for **{escape(row.name)}** to {channel.mention}."
            if kind == "chat" and not self.ctx.has_chat(row.template):
                text += (" Portcullis doesn't know how this game writes chat to its console yet, so nothing will "
                         "be posted there until a chat pattern for it is added.")
        await reply(interaction, text)

    async def set_relay(self, interaction: discord.Interaction, row: ServerRow, kind: str,
                        channel_id: int | None) -> ServerRow:
        updated = await self.ctx.servers.update(row.shard_id, **{RELAY_KINDS[kind]: channel_id})
        await self.ctx.audit.record(f"setup.relay.{kind}", actor_id=interaction.user.id, shard_id=row.shard_id,
                                    target=str(channel_id) if channel_id else None)
        await self.ctx.relay_changed()
        return updated

    # ---------------------------------------------------------------- /setup server

    async def server_embed(self, row: ServerRow) -> discord.Embed:
        commands = await self.ctx.settings.all(scope=row.shard_id)
        embed = discord.Embed(title=f"Settings for {row.name}")
        embed.add_field(name="Display name", value=escape(row.display_name or f"{row.amp_name} (from AMP)"))
        embed.add_field(name="Hidden from members", value="Yes" if row.hidden else "No")
        embed.add_field(name="Chat prefix", value=escape(row.prefix) if row.prefix else "none")
        embed.add_field(name="Console relay", value=mention(row.console_channel))
        embed.add_field(name="Events relay", value=mention(row.event_channel))
        chat = mention(row.chat_channel)
        if row.chat_channel and not self.ctx.has_chat(row.template):
            chat += " (no chat pattern for this game yet)"
        embed.add_field(name="Chat relay", value=chat)
        for key, label in COMMAND_KEYS.items():
            value = commands.get(key)
            embed.add_field(name=label, value=f"`{escape(value)}`" if value else "not set", inline=False)
        embed.set_footer(text="Pick a channel below to change a relay. This panel works for 15 minutes.")
        return embed

    async def setup_server(self, interaction: discord.Interaction, server: str) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "setup.server"):
            return
        row = await self.ctx.servers.find(server)
        if row is None:
            await reply(interaction, f"I don't know a server called {escape(server)}.")
            return
        await reply(interaction, embed=await self.server_embed(row), view=ServerPanel(self, row))

    async def save_names(self, interaction: discord.Interaction, row: ServerRow, display: str, prefix: str,
                         hidden: str) -> ServerRow:
        hidden_flag = hidden.strip().lower() in ("yes", "y", "true", "1")
        updated = await self.ctx.servers.update(row.shard_id, display_name=display.strip() or None,
                                                prefix=prefix.strip() or None, hidden=hidden_flag)
        await self.ctx.audit.record("setup.server.names", actor_id=interaction.user.id, shard_id=row.shard_id)
        await self.ctx.relay_changed()  # the relay shows the display name
        return updated

    async def save_commands(self, interaction: discord.Interaction, row: ServerRow,
                            values: dict[str, str]) -> str | None:
        """Store console command templates. Returns a problem to show, or None."""
        for key, value in values.items():
            value = value.strip()
            if value and ("\n" in value or len(value) > 200):
                return f"{COMMAND_KEYS[key]} must be one line of up to 200 characters."
            placeholder = "{message}" if key == SAY_KEY else "{name}"
            if value and placeholder not in value:
                return f"{COMMAND_KEYS[key]} must contain {placeholder}, e.g. `whitelist add {{name}}`."
        for key, value in values.items():
            if value.strip():
                await self.ctx.settings.set(key, value.strip(), scope=row.shard_id, by=interaction.user.id)
            else:
                await self.ctx.settings.delete(key, scope=row.shard_id)
        await self.ctx.audit.record("setup.server.commands", actor_id=interaction.user.id, shard_id=row.shard_id)
        return None

    # ---------------------------------------------------------------- /setup bot

    async def bot_embed(self) -> discord.Embed:
        embed = discord.Embed(title="Portcullis settings")
        channel = await self.ctx.settings.get(WHITELIST_CHANNEL)
        embed.add_field(name="Whitelist requests go to", value=mention(channel), inline=False)
        roles = ", ".join(f"<@&{r}>" for r in sorted(self.ctx.access.operator_role_ids)) or "none"
        embed.add_field(name="Operator roles", value=f"{roles}\n(set in `.env`: PORTCULLIS_OPERATOR_ROLE_IDS)",
                        inline=False)
        embed.set_footer(text="Per-server settings: /setup server. This panel works for 15 minutes.")
        return embed

    async def setup_bot(self, interaction: discord.Interaction) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "setup.bot"):
            return
        await reply(interaction, embed=await self.bot_embed(), view=BotPanel(self))

    async def set_whitelist_channel(self, interaction: discord.Interaction, channel_id: int | None) -> None:
        if channel_id is None:
            await self.ctx.settings.delete(WHITELIST_CHANNEL)
        else:
            await self.ctx.settings.set(WHITELIST_CHANNEL, channel_id, by=interaction.user.id)
        await self.ctx.audit.record("setup.bot.whitelist_channel", actor_id=interaction.user.id,
                                    target=str(channel_id) if channel_id else None)

    def register(self, tree: app_commands.CommandTree) -> None:
        group = app_commands.Group(name="setup", description="Portcullis settings (admins)", guild_only=True,
                                   default_permissions=discord.Permissions(manage_guild=True))

        @group.command(name="relay", description="Post a server's console, events or chat in a channel")
        @app_commands.describe(server="The game server", kind="What to relay",
                               channel="Where to post it. Leave out to stop relaying.")
        @app_commands.autocomplete(server=self.autocomplete_server)
        @app_commands.choices(kind=[app_commands.Choice(name="Console (game output)", value="console"),
                                    app_commands.Choice(name="Events (joins, leaves, state)", value="events"),
                                    app_commands.Choice(name="Chat (in-game chat)", value="chat")])
        async def setup_relay(interaction: discord.Interaction, server: str, kind: str,
                              channel: discord.TextChannel | None = None) -> None:
            await self.setup_relay(interaction, server, kind, channel)

        @group.command(name="server", description="All settings for one game server")
        @app_commands.describe(server="The game server")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def setup_server(interaction: discord.Interaction, server: str) -> None:
            await self.setup_server(interaction, server)

        @group.command(name="bot", description="Bot-wide settings")
        async def setup_bot(interaction: discord.Interaction) -> None:
            await self.setup_bot(interaction)

        tree.add_command(group)


# ------------------------------------------------------------------------------ panels


class AdminView(discord.ui.View):
    def __init__(self, module: SetupModule) -> None:
        super().__init__(timeout=900)
        self.module = module

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.module.ctx.access.is_admin(interaction.user):
            return True
        await reply(interaction, "You need the Manage Server permission for that.")
        return False


class RelaySelect(discord.ui.ChannelSelect["ServerPanel"]):
    def __init__(self, kind: str, row: int) -> None:
        super().__init__(placeholder=f"{kind.capitalize()} relay channel…", channel_types=[discord.ChannelType.text],
                         min_values=1, max_values=1, row=row)
        self.kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.pick(interaction, self.kind, self.values[0].id)


class ServerPanel(AdminView):
    def __init__(self, module: SetupModule, row: ServerRow) -> None:
        super().__init__(module)
        self.row = row
        for i, kind in enumerate(RELAY_KINDS):
            self.add_item(RelaySelect(kind, i))

    async def refresh(self, interaction: discord.Interaction, note: str | None = None) -> None:
        row = await self.module.ctx.servers.get(self.row.shard_id)
        if row is not None:
            self.row = row
        embed = await self.module.server_embed(self.row)
        if interaction.response.is_done():
            await interaction.edit_original_response(content=note, embed=embed, view=self)
        else:
            await interaction.response.edit_message(content=note, embed=embed, view=self)

    async def pick(self, interaction: discord.Interaction, kind: str, channel_id: int) -> None:
        if can_post(interaction, channel_id) is False:
            await self.refresh(interaction, f"I can't post in <#{channel_id}>. Give Portcullis View Channel and "
                                            "Send Messages there first.")
            return
        await self.module.set_relay(interaction, self.row, kind, channel_id)
        await self.refresh(interaction, f"Relaying {kind} to <#{channel_id}>.")

    @discord.ui.button(label="Name and visibility", style=discord.ButtonStyle.primary, row=3)
    async def names(self, interaction: discord.Interaction, button: discord.ui.Button[ServerPanel]) -> None:
        await interaction.response.send_modal(NamesModal(self))

    @discord.ui.button(label="Game commands", style=discord.ButtonStyle.primary, row=3)
    async def commands(self, interaction: discord.Interaction, button: discord.ui.Button[ServerPanel]) -> None:
        values = await self.module.ctx.settings.all(scope=self.row.shard_id)
        await interaction.response.send_modal(CommandsModal(self, values))

    @discord.ui.button(label="Stop all relays", style=discord.ButtonStyle.danger, row=3)
    async def stop_relays(self, interaction: discord.Interaction, button: discord.ui.Button[ServerPanel]) -> None:
        for kind in RELAY_KINDS:
            await self.module.set_relay(interaction, self.row, kind, None)
        await self.refresh(interaction, "Stopped every relay for this server.")


class NamesModal(discord.ui.Modal, title="Name and visibility"):
    def __init__(self, panel: ServerPanel) -> None:
        super().__init__()
        self.panel = panel
        row = panel.row
        self.display: discord.ui.TextInput[NamesModal] = discord.ui.TextInput(
            label="Display name (empty = AMP's name)", default=row.display_name or "", required=False, max_length=64)
        self.prefix: discord.ui.TextInput[NamesModal] = discord.ui.TextInput(
            label="Chat prefix (short tag)", default=row.prefix or "", required=False, max_length=16)
        self.hidden: discord.ui.TextInput[NamesModal] = discord.ui.TextInput(
            label="Hide from members? (yes or no)", default="yes" if row.hidden else "no", max_length=3)
        for item in (self.display, self.prefix, self.hidden):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.panel.module.save_names(interaction, self.panel.row, self.display.value, self.prefix.value,
                                           self.hidden.value)
        await self.panel.refresh(interaction, "Saved.")


class CommandsModal(discord.ui.Modal, title="Game commands"):
    def __init__(self, panel: ServerPanel, values: dict[str, Any]) -> None:
        super().__init__()
        self.panel = panel
        self.inputs: dict[str, discord.ui.TextInput[CommandsModal]] = {}
        hints = {WL_ADD: "whitelist add {name}", WL_REMOVE: "whitelist remove {name}", SAY_KEY: "say {message}"}
        for key, label in COMMAND_KEYS.items():
            field: discord.ui.TextInput[CommandsModal] = discord.ui.TextInput(
                label=label, default=str(values.get(key) or ""), placeholder=hints[key], required=False,
                max_length=200)
            self.inputs[key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        problem = await self.panel.module.save_commands(interaction, self.panel.row,
                                                        {k: v.value for k, v in self.inputs.items()})
        await self.panel.refresh(interaction, problem or "Saved.")


class BotPanel(AdminView):
    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="Channel for whitelist requests…",
                       channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
    async def whitelist_channel(self, interaction: discord.Interaction,
                                select: discord.ui.ChannelSelect[BotPanel]) -> None:
        channel_id = select.values[0].id
        if can_post(interaction, channel_id) is False:
            await interaction.response.edit_message(content=f"I can't post in <#{channel_id}>.",
                                                    embed=await self.module.bot_embed(), view=self)
            return
        await self.module.set_whitelist_channel(interaction, channel_id)
        await interaction.response.edit_message(content="Saved.", embed=await self.module.bot_embed(), view=self)

    @discord.ui.button(label="Turn whitelist requests off", style=discord.ButtonStyle.danger)
    async def whitelist_off(self, interaction: discord.Interaction, button: discord.ui.Button[BotPanel]) -> None:
        await self.module.set_whitelist_channel(interaction, None)
        await interaction.response.edit_message(content="Whitelist requests are off.",
                                                embed=await self.module.bot_embed(), view=self)
