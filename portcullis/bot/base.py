# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every command module shares: who may do what, the services they use, and reply helpers."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import discord
from discord import app_commands

from portcullis.core.live import LiveState
from portcullis.core.port import AmpPort
from portcullis.core.relay import STATE_ICONS, escape
from portcullis.core.types import ServerState
from portcullis.db.database import Database
from portcullis.db.features import LinkCodeRepo, NotifyRepo, PlayerRepo, PollRepo, WhitelistRepo
from portcullis.db.repos import AuditRepo, ServerRepo, ServerRow, SettingsRepo, UserRepo

log = logging.getLogger(__name__)

# Player names go into console commands (whitelist), so anything that could end or chain a command is refused.
_NAME = re.compile(r"^[^\x00-\x1f\x7f;|&`\"'\\$<>{}]{1,48}$")
_LINE = re.compile(r"^[^\x00-\x1f\x7f]{1,500}$")


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name.strip())) and name.strip() == name


def valid_line(text: str) -> bool:
    return bool(_LINE.match(text))


def fill(template: str, **values: str) -> str:
    """``{name}``-style substitution without ``str.format`` (so templates can't reach attributes)."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


@dataclass(frozen=True)
class Access:
    """Owners can do everything. Moderators are members with an operator role (power, send, backups,
    whitelist decisions, polls, /user). Admins are members with Manage Server (/setup, /audit, /admin)."""

    owner_ids: frozenset[int] = frozenset()
    operator_role_ids: frozenset[int] = frozenset()

    def is_owner(self, user: Any) -> bool:
        return user.id in self.owner_ids

    def is_mod(self, user: Any) -> bool:
        roles = {r.id for r in getattr(user, "roles", ())}
        return self.is_owner(user) or bool(roles & self.operator_role_ids)

    def is_admin(self, user: Any) -> bool:
        perms = getattr(user, "guild_permissions", None)
        return self.is_owner(user) or bool(perms and perms.manage_guild)

    # names used by the first slice
    can_power = is_mod
    can_setup = is_admin


@dataclass
class Ctx:
    """The services command modules use. Built once by ``PortcullisBot``."""

    port: AmpPort
    db: Database
    access: Access
    guild_id: int
    live: LiveState = field(default_factory=LiveState)
    on_relay_changed: Callable[[], Awaitable[None]] | None = None
    has_chat: Callable[[str | None], bool] = lambda game: False
    client: discord.Client | None = None
    board: Any = None  # StatusBoard, set by Modules (it imports this module)

    def __post_init__(self) -> None:
        self.servers = ServerRepo(self.db)
        self.settings = SettingsRepo(self.db)
        self.users = UserRepo(self.db)
        self.audit = AuditRepo(self.db)
        self.players = PlayerRepo(self.db)
        self.whitelist = WhitelistRepo(self.db)
        self.links = LinkCodeRepo(self.db)
        self.polls = PollRepo(self.db)
        self.notify = NotifyRepo(self.db)

    async def relay_changed(self) -> None:
        if self.on_relay_changed is not None:
            await self.on_relay_changed()

    async def channel(self, channel_id: int) -> Any:
        if self.client is None:
            raise RuntimeError("no Discord client")
        return self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)


def short_error(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    return text if len(text) <= 300 else text[:299] + "…"


def state_text(state: ServerState) -> str:
    """Status is never colour-only (DESIGN.md section 7): icon plus a word."""
    return f"{STATE_ICONS.get(state, '❔')} {state.label}"


def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def when(iso: str | None) -> str:
    """A Discord timestamp that each reader sees in their own time zone."""
    if not iso:
        return "never"
    return discord.utils.format_dt(datetime.fromisoformat(iso), "R")


NONE = discord.AllowedMentions.none()


async def reply(interaction: discord.Interaction, content: str | None = None, *, ephemeral: bool = True,
                **kwargs: Any) -> None:
    kwargs = {"ephemeral": ephemeral, "allowed_mentions": NONE, **kwargs}
    if interaction.response.is_done():
        await interaction.followup.send(content, **kwargs)
    else:
        await interaction.response.send_message(content, **kwargs)


class Module:
    """Base for one command group. Subclasses add their commands in ``register``."""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def register(self, tree: app_commands.CommandTree) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # ------------------------------------------------------------------ checks shared by commands

    async def in_guild(self, interaction: discord.Interaction) -> bool:
        """Refuse use from any other Discord server the bot ended up in."""
        if interaction.guild_id == self.ctx.guild_id:
            return True
        await reply(interaction, "Portcullis only works in the Discord server it was set up for.")
        return False

    async def require_mod(self, interaction: discord.Interaction, node: str, shard_id: str | None = None) -> bool:
        if self.ctx.access.is_mod(interaction.user):
            return True
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=shard_id, result="denied")
        await reply(interaction, "You need an operator role for that.")
        return False

    async def require_admin(self, interaction: discord.Interaction, node: str) -> bool:
        if self.ctx.access.is_admin(interaction.user):
            return True
        await self.ctx.audit.record(node, actor_id=interaction.user.id, result="denied")
        await reply(interaction, "You need the Manage Server permission for that.")
        return False

    async def server(self, interaction: discord.Interaction, name: str) -> ServerRow | None:
        """Find a server the caller may see, or reply that it doesn't exist."""
        row = await self.ctx.servers.find(name)
        if row is None or (row.hidden and not self.ctx.access.is_mod(interaction.user)):
            await reply(interaction, f"I don't know a server called {escape(name)}. "
                                     "Pick one from the list as you type.")
            return None
        return row

    async def autocomplete_server(self, interaction: discord.Interaction,
                                  current: str) -> list[app_commands.Choice[str]]:
        rows = await self.ctx.servers.list(include_hidden=self.ctx.access.is_mod(interaction.user))
        needle = current.lower()
        return [app_commands.Choice(name=r.name[:100], value=r.shard_id)
                for r in rows if needle in r.name.lower()][:25]

    async def autocomplete_player(self, interaction: discord.Interaction,
                                  current: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=n[:100], value=n[:100]) for n in await self.ctx.players.names(current)]
