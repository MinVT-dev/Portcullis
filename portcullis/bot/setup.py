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

from portcullis.bot.alerts import BACKUP_DAYS, CPU_KEY, MEMORY_KEY
from portcullis.bot.base import (
    ALERT_CHANNEL,
    ALERT_ROLE,
    AUDIT_CHANNEL,
    GROUPS,
    MOD_CHANNEL,
    ROLE_GRANTS,
    SERVER_OPERATORS,
    Module,
    as_id,
    code,
    id_list,
    reply,
    role_problem,
    valid_line,
)
from portcullis.bot.board import BOARD_CHANNEL
from portcullis.bot.events import TIMEZONE, valid_zone
from portcullis.bot.joins import NAME_RULES, WELCOME_KEY, name_rules, parse_rules
from portcullis.bot.server import (
    CONNECT_KEY,
    QUICK_KEY,
    QUICK_LIMIT,
    SAY_KEY,
    START_KEY,
    quick_commands,
    valid_address,
)
from portcullis.core.relay import escape
from portcullis.db.repos import ServerRow

log = logging.getLogger(__name__)

RELAY_KINDS = {"console": "console_channel", "events": "event_channel", "chat": "chat_channel"}
WL_ADD, WL_REMOVE = "whitelist_add", "whitelist_remove"
WHITELIST_CHANNEL = "whitelist_channel"
COMMAND_KEYS = {WL_ADD: "Whitelist add", WL_REMOVE: "Whitelist remove", SAY_KEY: "Chat (say)"}
KICK, BAN, UNBAN = "kick", "ban", "unban"
MOD_KEYS = {KICK: "Kick", BAN: "Ban", UNBAN: "Unban"}  # templates with {name} and, optionally, {reason}
AUTO_APPROVE_ROLE = "auto_approve_role"  # bot: members with it and a linked name skip the moderators
LINK_ROLE = "link_role"  # bot: given to members once they have a linked name


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


def role_mention(role_id: int | None) -> str:
    return f"<@&{role_id}>" if role_id else "none"


def yes(text: str) -> bool:
    return text.strip().lower() in ("yes", "y", "true", "1", "on")


def percent(text: str) -> int | None | str:
    """A 1-100 percentage, None for empty, or a problem to show."""
    text = text.strip().rstrip("%")
    if not text:
        return None
    if not text.isdigit() or not 1 <= int(text) <= 100:
        return "Load limits must be a whole number from 1 to 100, or empty for no alert."
    return int(text)


BOT_CHANNELS = {ALERT_CHANNEL: "Alerts (outages, crashes, high load, old backups)",
                MOD_CHANNEL: "Moderator notes (watchlist, name checks)",
                AUDIT_CHANNEL: "Audit log (every action, as it happens)"}
BOT_ROLES = {ALERT_ROLE: "Pinged on alerts",
             AUTO_APPROVE_ROLE: "Auto-approve whitelist requests (with a linked name)",
             LINK_ROLE: "Given to members with a linked name"}


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
        mod = [f"{label}: {code(commands[key])}" for key, label in MOD_KEYS.items() if commands.get(key)]
        embed.add_field(name="Moderation commands", value="\n".join(mod) or "not set", inline=False)
        quick = quick_commands(commands.get(QUICK_KEY))
        listed = "\n".join(f"{escape(q['label'])} → `{escape(q['command'])}`" for q in quick)
        embed.add_field(name="Quick commands (buttons on /server status)", value=listed or "none", inline=False)
        address = commands.get(CONNECT_KEY)
        embed.add_field(name="Address on /server status", value=code(address) if address else "not shown")
        embed.add_field(name="Members can start it", value="Yes" if commands.get(START_KEY) else "No")
        embed.add_field(name="First-join welcome", value="On" if commands.get(WELCOME_KEY, True) else "Off")
        limits = [f"CPU {commands[CPU_KEY]}%" if commands.get(CPU_KEY) else "",
                  f"memory {commands[MEMORY_KEY]}%" if commands.get(MEMORY_KEY) else ""]
        embed.add_field(name="Load alert above", value=" · ".join(x for x in limits if x) or "off")
        operators = id_list(commands.get(SERVER_OPERATORS))
        embed.add_field(name="Server operators (can do everything on this server)",
                        value=", ".join(role_mention(r) for r in operators) or "none", inline=False)
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

    async def save_quick(self, interaction: discord.Interaction, row: ServerRow, text: str) -> str | None:
        """Store quick commands from ``Label = command`` lines. Returns a problem to show, or None."""
        items = []
        for number, raw in enumerate(text.splitlines(), start=1):
            if not raw.strip():
                continue
            label, sep, command = raw.partition("=")
            label, command = label.strip(), command.strip()
            if not sep or not label or not command:
                return f"Line {number} needs a label and a command, like `Save world = save`."
            if len(label) > 40:
                return f"Line {number}: keep the label to 40 characters."
            if len(command) > 200 or not valid_line(command):
                return f"Line {number}: the command must be one line of up to 200 characters."
            items.append({"label": label, "command": command})
        if len(items) > QUICK_LIMIT:
            return f"Up to {QUICK_LIMIT} quick commands per server."
        if items:
            await self.ctx.settings.set(QUICK_KEY, items, scope=row.shard_id, by=interaction.user.id)
        else:
            await self.ctx.settings.delete(QUICK_KEY, scope=row.shard_id)
        await self.ctx.audit.record("setup.server.quick", actor_id=interaction.user.id, shard_id=row.shard_id,
                                    target=str(len(items)))
        return None

    async def save_mod_commands(self, interaction: discord.Interaction, row: ServerRow,
                                values: dict[str, str]) -> str | None:
        for key, value in values.items():
            value = value.strip()
            if value and ("\n" in value or len(value) > 200):
                return f"{MOD_KEYS[key]} must be one line of up to 200 characters."
            if value and "{name}" not in value:
                return f"{MOD_KEYS[key]} must contain {{name}}, e.g. `kick {{name}} {{reason}}`."
        for key, value in values.items():
            if value.strip():
                await self.ctx.settings.set(key, value.strip(), scope=row.shard_id, by=interaction.user.id)
            else:
                await self.ctx.settings.delete(key, scope=row.shard_id)
        await self.ctx.audit.record("setup.server.moderation", actor_id=interaction.user.id, shard_id=row.shard_id)
        return None

    async def save_options(self, interaction: discord.Interaction, row: ServerRow, address: str, can_start: str,
                           welcome: str, cpu: str, memory: str) -> str | None:
        address = address.strip()
        if address and not valid_address(address):
            return "The address must be a host name or IP, with an optional :port (e.g. play.example.com:27015)."
        cpu_limit, memory_limit = percent(cpu), percent(memory)
        for value in (cpu_limit, memory_limit):
            if isinstance(value, str):
                return value
        scope, by = row.shard_id, interaction.user.id
        for key, value in ((CONNECT_KEY, address or None), (START_KEY, yes(can_start) or None),
                           (CPU_KEY, cpu_limit), (MEMORY_KEY, memory_limit)):
            if value is None:
                await self.ctx.settings.delete(key, scope=scope)
            else:
                await self.ctx.settings.set(key, value, scope=scope, by=by)
        await self.ctx.settings.set(WELCOME_KEY, yes(welcome), scope=scope, by=by)
        await self.ctx.audit.record("setup.server.options", actor_id=by, shard_id=scope)
        return None

    async def set_server_operators(self, interaction: discord.Interaction, row: ServerRow, roles: list[Any]) -> str:
        if not self.ctx.access.is_owner(interaction.user):
            await self.ctx.audit.record("setup.server.operators", actor_id=interaction.user.id,
                                        shard_id=row.shard_id, result="denied")
            return "Only the bot's owners (PORTCULLIS_OWNER_IDS) can hand out operator roles."
        for role in roles:
            if role.is_default():
                return "@everyone can't be a server operator role."
        ids = [r.id for r in roles]
        if ids:
            await self.ctx.settings.set(SERVER_OPERATORS, ids, scope=row.shard_id, by=interaction.user.id)
        else:
            await self.ctx.settings.delete(SERVER_OPERATORS, scope=row.shard_id)
        await self.ctx.audit.record("setup.server.operators", actor_id=interaction.user.id, shard_id=row.shard_id,
                                    target=",".join(map(str, ids)) or None)
        return "Saved."

    # ---------------------------------------------------------------- /setup bot

    async def bot_embed(self) -> discord.Embed:
        embed = discord.Embed(title="Portcullis settings")
        channel = await self.ctx.settings.get(WHITELIST_CHANNEL)
        embed.add_field(name="Whitelist requests go to", value=mention(channel), inline=False)
        board = await self.ctx.settings.get(BOARD_CHANNEL)
        embed.add_field(name="Live server board", value=mention(board), inline=False)
        for key, label in BOT_CHANNELS.items():
            embed.add_field(name=label, value=mention(await self.ctx.settings.get(key)), inline=False)
        for key, label in BOT_ROLES.items():
            embed.add_field(name=label, value=role_mention(await self.ctx.settings.get(key)), inline=False)
        roles = ", ".join(f"<@&{r}>" for r in sorted(self.ctx.access.operator_role_ids)) or "none"
        embed.add_field(name="Operator roles", value=f"{roles}\n(set in `.env`: PORTCULLIS_OPERATOR_ROLE_IDS)",
                        inline=False)
        grants = await self.ctx.settings.get(ROLE_GRANTS, {})
        granted = [f"{GROUPS[g]}: {', '.join(role_mention(r) for r in id_list(grants.get(g)))}"
                   for g in GROUPS if isinstance(grants, dict) and id_list(grants.get(g))]
        embed.add_field(name="Extra roles per task", value="\n".join(granted) or "none", inline=False)
        rules = name_rules(await self.ctx.settings.get(NAME_RULES))
        days = await self.ctx.settings.get(BACKUP_DAYS)
        embed.add_field(name="Time zone for events", value=str(await self.ctx.settings.get(TIMEZONE) or "UTC"))
        embed.add_field(name="Backup reminder", value=f"after {days} day(s)" if days else "off")
        embed.add_field(name="Name rules", value=f"{len(rules)} word(s)" if rules else "none")
        embed.set_footer(text="Per-server settings: /setup server. This panel works for 15 minutes.")
        return embed

    async def set_bot_channel(self, interaction: discord.Interaction, key: str, channel_id: int | None) -> None:
        if channel_id is None:
            await self.ctx.settings.delete(key)
        else:
            await self.ctx.settings.set(key, channel_id, by=interaction.user.id)
        await self.ctx.audit.record(f"setup.bot.{key}", actor_id=interaction.user.id,
                                    target=str(channel_id) if channel_id else None)

    async def set_bot_role(self, interaction: discord.Interaction, key: str, role: Any | None) -> str:
        if role is not None:
            problem = role_problem(role, interaction.guild, self.ctx.access, handed_out=key == LINK_ROLE)
            if problem:
                return problem
            await self.ctx.settings.set(key, role.id, by=interaction.user.id)
        else:
            await self.ctx.settings.delete(key)
        await self.ctx.audit.record(f"setup.bot.{key}", actor_id=interaction.user.id,
                                    target=str(role.id) if role else None)
        if key == LINK_ROLE and role is not None:
            return "Saved. Members get it the next time they link a name; existing links aren't changed."
        return "Saved."

    async def save_bot_options(self, interaction: discord.Interaction, timezone: str, backup_days: str,
                               rules: str) -> str | None:
        timezone, backup_days = timezone.strip(), backup_days.strip()
        if timezone and not valid_zone(timezone):
            return "Use a time zone name like Europe/Amsterdam or America/New_York."
        if backup_days and (not backup_days.isdigit() or not 1 <= int(backup_days) <= 365):
            return "The backup reminder needs a number of days from 1 to 365, or empty to turn it off."
        by = interaction.user.id
        for key, value in ((TIMEZONE, timezone or None), (BACKUP_DAYS, int(backup_days) if backup_days else None),
                           (NAME_RULES, parse_rules(rules) or None)):
            if value is None:
                await self.ctx.settings.delete(key)
            else:
                await self.ctx.settings.set(key, value, by=by)
        await self.ctx.audit.record("setup.bot.options", actor_id=by)
        return None

    async def set_grant(self, interaction: discord.Interaction, group: str, roles: list[Any]) -> str:
        if not self.ctx.access.is_owner(interaction.user):
            await self.ctx.audit.record("setup.bot.grants", actor_id=interaction.user.id, result="denied")
            return "Only the bot's owners (PORTCULLIS_OWNER_IDS) can hand out operator powers."
        if group not in GROUPS:
            return "Pick what the roles may do first."
        if any(r.is_default() for r in roles):
            return "@everyone can't be given operator powers."
        grants = await self.ctx.settings.get(ROLE_GRANTS, {})
        grants = dict(grants) if isinstance(grants, dict) else {}
        ids = [r.id for r in roles]
        if ids:
            grants[group] = ids
        else:
            grants.pop(group, None)
        await self.ctx.settings.set(ROLE_GRANTS, grants, by=interaction.user.id)
        await self.ctx.audit.record("setup.bot.grants", actor_id=interaction.user.id, target=group,
                                    detail={"roles": ids})
        return f"{GROUPS[group]}: " + (", ".join(role_mention(i) for i in ids) or "full operators only") + "."

    async def check_setup(self, interaction: discord.Interaction) -> list[str]:
        """Everything that looks missing or broken, as lines to show. Empty when all is well."""
        problems: list[str] = []
        rows = await self.ctx.servers.list()
        if not rows:
            problems.append("Portcullis sees no servers. Check the AMP account's permissions with "
                            "`/admin diagnostics`.")
        if not any(r.console_channel or r.event_channel or r.chat_channel for r in rows):
            problems.append("No server has a relay channel. Set one with `/setup relay`.")
        channels: dict[int, str] = {}
        for r in rows:
            for kind, column in RELAY_KINDS.items():
                if getattr(r, column):
                    channels[getattr(r, column)] = f"the {kind} relay of {escape(r.name)}"
            if r.chat_channel and not self.ctx.has_chat(r.template):
                problems.append(f"{escape(r.name)} relays chat, but Portcullis can't read this game's chat yet.")
        settings = await self.ctx.settings.all()
        for key, label in ((WHITELIST_CHANNEL, "whitelist requests"), (BOARD_CHANNEL, "the server board"),
                           (ALERT_CHANNEL, "alerts"), (MOD_CHANNEL, "moderator notes"),
                           (AUDIT_CHANNEL, "the audit log")):
            if settings.get(key):
                channel = as_id(settings[key])
                if channel is not None:
                    channels[channel] = label
        for channel_id, label in channels.items():
            if can_post(interaction, channel_id) is False:
                problems.append(f"I can't post in <#{channel_id}> ({label}). Give Portcullis View Channel and "
                                "Send Messages there.")
        whitelist_servers = [r for r in rows if await self.ctx.settings.get(WL_ADD, scope=r.shard_id)]
        if settings.get(WHITELIST_CHANNEL) and not whitelist_servers:
            problems.append("Whitelist requests are on, but no server has a whitelist add command "
                            "(`/setup server` → Game commands).")
        if whitelist_servers and not settings.get(WHITELIST_CHANNEL):
            problems.append("Some servers have a whitelist command, but whitelist requests are off. Pick a "
                            "channel for them above.")
        if settings.get(ALERT_ROLE) and not settings.get(ALERT_CHANNEL):
            problems.append("An alert role is set, but no alert channel, so nobody gets pinged.")
        guild = interaction.guild
        link_role = as_id(settings.get(LINK_ROLE))
        if link_role and guild is not None and hasattr(guild, "get_role"):
            role = guild.get_role(link_role)
            if role is None:
                problems.append("The link role no longer exists. Pick another one.")
            else:
                problem = role_problem(role, guild, self.ctx.access, handed_out=True)
                me = getattr(guild, "me", None)
                if problem:
                    problems.append(f"Link role: {problem}")
                elif me is not None and not me.guild_permissions.manage_roles:
                    problems.append("Portcullis needs the Manage Roles permission to hand out the link role.")
        if not self.ctx.live.reachable:
            problems.append("Portcullis can't reach AMP right now. `/admin diagnostics` shows why.")
        return problems

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

    async def set_board_channel(self, interaction: discord.Interaction, channel_id: int | None) -> None:
        if self.ctx.board is not None:
            await self.ctx.board.move(channel_id, by=interaction.user.id)
        await self.ctx.audit.record("setup.bot.board_channel", actor_id=interaction.user.id,
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

    @discord.ui.button(label="Quick commands", style=discord.ButtonStyle.primary, row=3)
    async def quick(self, interaction: discord.Interaction, button: discord.ui.Button[ServerPanel]) -> None:
        current = quick_commands(await self.module.ctx.settings.get(QUICK_KEY, scope=self.row.shard_id))
        await interaction.response.send_modal(QuickModal(self, current))

    @discord.ui.button(label="Stop all relays", style=discord.ButtonStyle.danger, row=3)
    async def stop_relays(self, interaction: discord.Interaction, button: discord.ui.Button[ServerPanel]) -> None:
        for kind in RELAY_KINDS:
            await self.module.set_relay(interaction, self.row, kind, None)
        await self.refresh(interaction, "Stopped every relay for this server.")

    @discord.ui.button(label="Moderation commands", style=discord.ButtonStyle.primary, row=4)
    async def moderation(self, interaction: discord.Interaction, button: discord.ui.Button[ServerPanel]) -> None:
        values = await self.module.ctx.settings.all(scope=self.row.shard_id)
        await interaction.response.send_modal(ModerationModal(self, values))

    @discord.ui.button(label="Options", style=discord.ButtonStyle.primary, row=4)
    async def options(self, interaction: discord.Interaction, button: discord.ui.Button[ServerPanel]) -> None:
        values = await self.module.ctx.settings.all(scope=self.row.shard_id)
        await interaction.response.send_modal(ServerOptionsModal(self, values))

    @discord.ui.button(label="Server operators (owners)", style=discord.ButtonStyle.secondary, row=4)
    async def operators(self, interaction: discord.Interaction, button: discord.ui.Button[ServerPanel]) -> None:
        if not self.module.ctx.access.is_owner(interaction.user):
            await reply(interaction, "Only the bot's owners (PORTCULLIS_OWNER_IDS) can hand out operator roles.")
            return
        await reply(interaction, f"Roles that may do everything on **{escape(self.row.name)}**, and nothing "
                                 "elsewhere. Pick none to clear.", view=OperatorsPanel(self))


class ModerationModal(discord.ui.Modal, title="Moderation commands"):
    def __init__(self, panel: ServerPanel, values: dict[str, Any]) -> None:
        super().__init__()
        self.panel = panel
        self.inputs: dict[str, discord.ui.TextInput[ModerationModal]] = {}
        hints = {KICK: "kick {name} {reason}", BAN: "ban {name} {reason}", UNBAN: "unban {name}"}
        for key, label in MOD_KEYS.items():
            field: discord.ui.TextInput[ModerationModal] = discord.ui.TextInput(
                label=label, default=str(values.get(key) or ""), placeholder=hints[key], required=False,
                max_length=200)
            self.inputs[key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        problem = await self.panel.module.save_mod_commands(interaction, self.panel.row,
                                                            {k: v.value for k, v in self.inputs.items()})
        await self.panel.refresh(interaction, problem or "Saved.")


class ServerOptionsModal(discord.ui.Modal, title="Server options"):
    def __init__(self, panel: ServerPanel, values: dict[str, Any]) -> None:
        super().__init__()
        self.panel = panel

        def field(label: str, default: object, placeholder: str, length: int) -> discord.ui.TextInput[Any]:
            item: discord.ui.TextInput[Any] = discord.ui.TextInput(
                label=label, default="" if default is None else str(default), placeholder=placeholder,
                required=False, max_length=length)
            self.add_item(item)
            return item

        self.address = field("Address players connect to", values.get(CONNECT_KEY), "play.example.com:27015", 260)
        self.can_start = field("Can any member start it? (yes or no)", "yes" if values.get(START_KEY) else "no",
                               "no", 3)
        self.welcome = field("Post first joins in events? (yes or no)",
                             "yes" if values.get(WELCOME_KEY, True) else "no", "yes", 3)
        self.cpu = field("Alert when CPU stays above (%)", values.get(CPU_KEY), "empty = off", 4)
        self.memory = field("Alert when memory stays above (%)", values.get(MEMORY_KEY), "empty = off", 4)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        problem = await self.panel.module.save_options(interaction, self.panel.row, self.address.value,
                                                       self.can_start.value, self.welcome.value, self.cpu.value,
                                                       self.memory.value)
        await self.panel.refresh(interaction, problem or "Saved.")


class OwnerView(discord.ui.View):
    """Only owners may use it: these panels hand out operator powers."""

    def __init__(self, module: SetupModule) -> None:
        super().__init__(timeout=300)
        self.module = module

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.module.ctx.access.is_owner(interaction.user):
            return True
        await reply(interaction, "Only the bot's owners can change this.")
        return False


class OperatorsPanel(OwnerView):
    def __init__(self, panel: ServerPanel) -> None:
        super().__init__(panel.module)
        self.panel = panel

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Server operator roles…", min_values=0, max_values=10)
    async def roles(self, interaction: discord.Interaction, select: discord.ui.RoleSelect[OperatorsPanel]) -> None:
        note = await self.module.set_server_operators(interaction, self.panel.row, list(select.values))
        await interaction.response.edit_message(content=note, view=self)


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


class QuickModal(discord.ui.Modal, title="Quick commands"):
    def __init__(self, panel: ServerPanel, current: list[dict[str, str]]) -> None:
        super().__init__()
        self.panel = panel
        self.lines: discord.ui.TextInput[QuickModal] = discord.ui.TextInput(
            label=f"One per line: Label = command (up to {QUICK_LIMIT})", style=discord.TextStyle.paragraph,
            default="\n".join(f"{q['label']} = {q['command']}" for q in current),
            placeholder="Save world = save\nAnnounce restart = say Restart in 5 minutes", required=False,
            max_length=1200)
        self.add_item(self.lines)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        problem = await self.panel.module.save_quick(interaction, self.panel.row, self.lines.value)
        await self.panel.refresh(interaction, problem or "Saved.")


class BotPanel(AdminView):
    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="Channel for whitelist requests…",
                       channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=0)
    async def whitelist_channel(self, interaction: discord.Interaction,
                                select: discord.ui.ChannelSelect[BotPanel]) -> None:
        channel_id = select.values[0].id
        if can_post(interaction, channel_id) is False:
            await interaction.response.edit_message(content=f"I can't post in <#{channel_id}>.",
                                                    embed=await self.module.bot_embed(), view=self)
            return
        await self.module.set_whitelist_channel(interaction, channel_id)
        await interaction.response.edit_message(content="Saved.", embed=await self.module.bot_embed(), view=self)

    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="Channel for the live server board…",
                       channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=1)
    async def board_channel(self, interaction: discord.Interaction,
                            select: discord.ui.ChannelSelect[BotPanel]) -> None:
        channel_id = select.values[0].id
        if can_post(interaction, channel_id) is False:
            await interaction.response.edit_message(content=f"I can't post in <#{channel_id}>.",
                                                    embed=await self.module.bot_embed(), view=self)
            return
        await interaction.response.defer()
        await self.module.set_board_channel(interaction, channel_id)
        await interaction.edit_original_response(content=f"The server board is now in <#{channel_id}>.",
                                                 embed=await self.module.bot_embed(), view=self)

    @discord.ui.button(label="Turn whitelist requests off", style=discord.ButtonStyle.danger, row=2)
    async def whitelist_off(self, interaction: discord.Interaction, button: discord.ui.Button[BotPanel]) -> None:
        await self.module.set_whitelist_channel(interaction, None)
        await interaction.response.edit_message(content="Whitelist requests are off.",
                                                embed=await self.module.bot_embed(), view=self)

    @discord.ui.button(label="Turn the server board off", style=discord.ButtonStyle.danger, row=2)
    async def board_off(self, interaction: discord.Interaction, button: discord.ui.Button[BotPanel]) -> None:
        await self.module.set_board_channel(interaction, None)
        await interaction.response.edit_message(content="The server board is off. You can delete its old message.",
                                                embed=await self.module.bot_embed(), view=self)

    @discord.ui.button(label="Alert and log channels", style=discord.ButtonStyle.primary, row=3)
    async def channels(self, interaction: discord.Interaction, button: discord.ui.Button[BotPanel]) -> None:
        await reply(interaction, "Pick a channel for each, or turn one off.", view=ChannelsPanel(self.module))

    @discord.ui.button(label="Roles", style=discord.ButtonStyle.primary, row=3)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button[BotPanel]) -> None:
        await reply(interaction, "Pick a role for each, or clear one.", view=RolesPanel(self.module))

    @discord.ui.button(label="Options", style=discord.ButtonStyle.primary, row=3)
    async def options(self, interaction: discord.Interaction, button: discord.ui.Button[BotPanel]) -> None:
        settings = await self.module.ctx.settings.all()
        await interaction.response.send_modal(BotOptionsModal(self, settings))

    @discord.ui.button(label="Who can do what (owners)", style=discord.ButtonStyle.secondary, row=3)
    async def grants(self, interaction: discord.Interaction, button: discord.ui.Button[BotPanel]) -> None:
        if not self.module.ctx.access.is_owner(interaction.user):
            await reply(interaction, "Only the bot's owners (PORTCULLIS_OWNER_IDS) can hand out operator powers.")
            return
        await reply(interaction, "Pick a task, then the roles that may do it (on every server). Full operator "
                                 "roles from `.env` can always do everything.", view=GrantsPanel(self.module))

    @discord.ui.button(label="Check setup", style=discord.ButtonStyle.success, row=4)
    async def check(self, interaction: discord.Interaction, button: discord.ui.Button[BotPanel]) -> None:
        problems = await self.module.check_setup(interaction)
        await self.module.ctx.audit.record("setup.bot.check", actor_id=interaction.user.id,
                                           detail={"problems": len(problems)})
        if not problems:
            await reply(interaction, "✅ Nothing missing that I can see.")
            return
        await reply(interaction, "\n".join(f"- {p}" for p in problems)[:2000])


class ChannelsPanel(AdminView):
    def __init__(self, module: SetupModule) -> None:
        super().__init__(module)
        for row, (key, label) in enumerate(BOT_CHANNELS.items()):
            select: discord.ui.ChannelSelect[ChannelsPanel] = discord.ui.ChannelSelect(
                placeholder=f"{label}…", channel_types=[discord.ChannelType.text], min_values=1, max_values=1,
                row=row)
            select.callback = self._picked(key, select)  # type: ignore[method-assign]
            self.add_item(select)
            off: discord.ui.Button[ChannelsPanel] = discord.ui.Button(
                label=f"Turn off: {label.split(' (')[0].lower()}", style=discord.ButtonStyle.danger, row=3)
            off.callback = self._off(key)  # type: ignore[method-assign]
            self.add_item(off)

    def _picked(self, key: str, select: discord.ui.ChannelSelect[ChannelsPanel]):  # noqa: ANN202
        async def picked(interaction: discord.Interaction) -> None:
            channel_id = select.values[0].id
            if can_post(interaction, channel_id) is False:
                await interaction.response.edit_message(content=f"I can't post in <#{channel_id}>.", view=self)
                return
            await self.module.set_bot_channel(interaction, key, channel_id)
            await interaction.response.edit_message(content=f"{BOT_CHANNELS[key]}: <#{channel_id}>.", view=self)
        return picked

    def _off(self, key: str):  # noqa: ANN202
        async def off(interaction: discord.Interaction) -> None:
            await self.module.set_bot_channel(interaction, key, None)
            await interaction.response.edit_message(content=f"{BOT_CHANNELS[key]}: off.", view=self)
        return off


class RolesPanel(AdminView):
    def __init__(self, module: SetupModule) -> None:
        super().__init__(module)
        for row, (key, label) in enumerate(BOT_ROLES.items()):
            select: discord.ui.RoleSelect[RolesPanel] = discord.ui.RoleSelect(
                placeholder=f"{label}…", min_values=0, max_values=1, row=row)
            select.callback = self._picked(key, select)  # type: ignore[method-assign]
            self.add_item(select)

    def _picked(self, key: str, select: discord.ui.RoleSelect[RolesPanel]):  # noqa: ANN202
        async def picked(interaction: discord.Interaction) -> None:
            role = select.values[0] if select.values else None
            note = await self.module.set_bot_role(interaction, key, role)
            await interaction.response.edit_message(content=f"{BOT_ROLES[key]}: {note}", view=self)
        return picked


class BotOptionsModal(discord.ui.Modal, title="Portcullis options"):
    def __init__(self, panel: BotPanel, settings: dict[str, Any]) -> None:
        super().__init__()
        self.panel = panel
        self.timezone: discord.ui.TextInput[BotOptionsModal] = discord.ui.TextInput(
            label="Time zone for /event times", default=str(settings.get(TIMEZONE) or ""), placeholder="UTC",
            required=False, max_length=64)
        self.backup_days: discord.ui.TextInput[BotOptionsModal] = discord.ui.TextInput(
            label="Warn when the newest backup is older (days)", default=str(settings.get(BACKUP_DAYS) or ""),
            placeholder="empty = off", required=False, max_length=3)
        self.rules: discord.ui.TextInput[BotOptionsModal] = discord.ui.TextInput(
            label="Name rules: words to flag in player names", style=discord.TextStyle.paragraph,
            default="\n".join(name_rules(settings.get(NAME_RULES))), placeholder="One word per line",
            required=False, max_length=2000)
        for item in (self.timezone, self.backup_days, self.rules):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        problem = await self.panel.module.save_bot_options(interaction, self.timezone.value, self.backup_days.value,
                                                           self.rules.value)
        await interaction.response.edit_message(content=problem or "Saved.", embed=await self.panel.module.bot_embed(),
                                                view=self.panel)


class GrantsPanel(OwnerView):
    def __init__(self, module: SetupModule) -> None:
        super().__init__(module)
        self.group: str | None = None
        choose: discord.ui.Select[GrantsPanel] = discord.ui.Select(
            placeholder="Which task?", options=[discord.SelectOption(label=label, value=key)
                                                 for key, label in GROUPS.items()], row=0)
        choose.callback = self._chose(choose)  # type: ignore[method-assign]
        self.add_item(choose)

    def _chose(self, choose: discord.ui.Select[GrantsPanel]):  # noqa: ANN202
        async def chose(interaction: discord.Interaction) -> None:
            self.group = choose.values[0]
            await interaction.response.edit_message(content=f"Now pick the roles for: {GROUPS[self.group]}. "
                                                            "Pick none to clear.", view=self)
        return chose

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Roles for that task…", min_values=0, max_values=10,
                       row=1)
    async def roles(self, interaction: discord.Interaction, select: discord.ui.RoleSelect[GrantsPanel]) -> None:
        note = await self.module.set_grant(interaction, self.group or "", list(select.values))
        await interaction.response.edit_message(content=note, view=self)
