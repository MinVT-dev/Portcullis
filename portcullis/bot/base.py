# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every command module shares: who may do what, the services they use, and reply helpers."""

from __future__ import annotations

import logging
import re
import time
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
from portcullis.db.ops import AlertRuleRepo, EventRepo, WatchlistRepo, WhitelistEntryRepo
from portcullis.db.repos import AuditRepo, ServerRepo, ServerRow, SettingsRepo, UserRepo

log = logging.getLogger(__name__)

# Player names go into console commands (whitelist), so anything that could end or chain a command is refused.
_NAME = re.compile(r"^[^\x00-\x1f\x7f;|&`\"'\\$<>{}]{1,48}$")
_LINE = re.compile(r"^[^\x00-\x1f\x7f]{1,500}$")
_REASON = re.compile(r"^[^\x00-\x1f\x7f;|&`\"'\\$<>{}]{1,100}$")


# What a granted role may do. Full operators (PORTCULLIS_OPERATOR_ROLE_IDS) may do all of it.
GROUPS = {
    "power": "Start, stop and restart",
    "console": "Console commands, quick commands, console view",
    "whitelist": "Whitelist decisions and changes",
    "moderation": "Kick and ban, watchlist, /user",
    "polls": "Polls",
    "events": "Events calendar",
    "backups": "Backup list",
}
# Audit node prefix -> group. The first match wins.
NODE_GROUPS = (
    ("server.power", "power"), ("server.send", "console"), ("server.console", "console"),
    ("server.quick", "console"), ("whitelist", "whitelist"), ("player.moderate", "moderation"),
    ("watch", "moderation"), ("user", "moderation"), ("poll", "polls"), ("event", "events"),
    ("backup", "backups"), ("schedule", "backups"),
)
ROLE_GRANTS = "role_grants"  # bot scope: {group: [role ids]}
SERVER_OPERATORS = "operator_roles"  # server scope: [role ids] that may do everything on that server
# Only owners may change these, here and through /admin import: they hand out operator powers.
OWNER_ONLY_KEYS = frozenset({ROLE_GRANTS, SERVER_OPERATORS})

# Bot-wide channels and roles set in /setup bot.
ALERT_CHANNEL, ALERT_ROLE = "alert_channel", "alert_role"  # outages, crashes, high load, old backups
MOD_CHANNEL = "mod_channel"  # watchlist and name-check notes, never pinged
AUDIT_CHANNEL = "audit_channel"  # a live copy of the audit log
SUMMARY_CHANNEL = "summary_channel"  # daily summary and play-time milestones
COUNT_CHANNEL = "count_channel"  # a channel renamed to "N online"


def group_for(node: str) -> str | None:
    for prefix, group in NODE_GROUPS:
        if node == prefix or node.startswith(prefix + "."):
            return group
    return None


def role_ids(user: Any) -> set[int]:
    return {r.id for r in getattr(user, "roles", ())}


def as_id(value: object) -> int | None:
    """A stored Discord id, or None if it's missing or malformed (settings can come from an import)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def id_list(value: object) -> list[int]:
    """A stored list of Discord ids, ignoring anything malformed."""
    if not isinstance(value, list):
        return []
    return [int(v) for v in value if isinstance(v, int) and not isinstance(v, bool)]


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name.strip())) and name.strip() == name


# A role that Portcullis hands out or pings must not carry any of these.
RISKY_PERMISSIONS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
                     "manage_messages", "kick_members", "ban_members", "moderate_members", "mention_everyone")


def role_problem(role: Any, guild: Any, access: Access, *, handed_out: bool) -> str | None:
    """Why ``role`` can't be used for this, or None. ``handed_out``: Portcullis gives it to members."""
    if role.is_default():
        return "@everyone can't be used here."
    if not handed_out:
        return None
    if getattr(role, "managed", False):
        return "That role belongs to a bot or integration, so it can't be handed out."
    risky = [p for p in RISKY_PERMISSIONS if getattr(role.permissions, p, False)]
    if risky:
        return f"That role has moderator permissions ({', '.join(risky)}). Pick a role without them."
    if role.id in access.operator_role_ids:
        return "That's an operator role. Pick a role without operator powers."
    me = getattr(guild, "me", None)
    if me is not None and getattr(me, "top_role", None) is not None and role >= me.top_role:
        return "That role is above Portcullis's own role, so Discord won't let it hand the role out."
    return None


def valid_reason(text: str) -> bool:
    """A kick or ban reason. It goes into a console command, so it gets the same limits as names."""
    return bool(_REASON.match(text))


def valid_line(text: str) -> bool:
    return bool(_LINE.match(text))


TEMPLATE_MAX = 200


def valid_template(value: object) -> bool:
    """A stored console command template or quick command: one line, no control characters, up to 200."""
    return isinstance(value, str) and len(value) <= TEMPLATE_MAX and valid_line(value)


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
    expected_offline: dict[str, float] = field(default_factory=dict)
    # Channel id -> when to ask Discord about it again. A deleted or closed channel is otherwise fetched
    # (and refused) on every relay batch, alert and audit entry, which Discord counts against the bot.
    missing_channels: dict[int, float] = field(default_factory=dict)

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
        self.whitelisted = WhitelistEntryRepo(self.db)
        self.watchlist = WatchlistRepo(self.db)
        self.events = EventRepo(self.db)
        self.alert_rules = AlertRuleRepo(self.db)

    async def allowed(self, user: Any, node: str, shard_id: str | None = None) -> bool:
        """Full operators, a role granted the node's group, or one of the server's own operator roles."""
        if self.access.is_mod(user):
            return True
        roles = role_ids(user)
        if not roles:
            return False
        group = group_for(node)
        if group is not None:
            grants = await self.settings.get(ROLE_GRANTS, {})
            if isinstance(grants, dict) and roles & set(id_list(grants.get(group))):
                return True
        if shard_id is not None:
            return bool(roles & set(id_list(await self.settings.get(SERVER_OPERATORS, scope=shard_id))))
        return False

    async def is_staff(self, user: Any) -> bool:
        """Anyone with some operator power, anywhere. Decides what /help lists."""
        if self.access.is_mod(user):
            return True
        roles = role_ids(user)
        if not roles:
            return False
        grants = await self.settings.get(ROLE_GRANTS, {})
        if isinstance(grants, dict) and any(roles & set(id_list(v)) for v in grants.values()):
            return True
        for row in await self.servers.list():
            if roles & set(id_list(await self.settings.get(SERVER_OPERATORS, scope=row.shard_id))):
                return True
        return False

    async def sees_hidden(self, user: Any, shard_id: str) -> bool:
        if self.access.is_mod(user):
            return True
        return bool(role_ids(user) & set(id_list(await self.settings.get(SERVER_OPERATORS, scope=shard_id))))

    def expect_offline(self, shard_id: str) -> None:
        """A stop or restart Portcullis asked for: the offline alert stays quiet for it."""
        self.expected_offline[shard_id.lower()] = time.monotonic()

    def offline_expected(self, shard_id: str, within: float = 600.0) -> bool:
        at = self.expected_offline.get(shard_id.lower())
        return at is not None and time.monotonic() - at < within

    async def post(self, key: str, content: str | None = None, *, embed: discord.Embed | None = None,
                   ping_role: int | None = None, view: discord.ui.View | None = None) -> bool:
        """Post in the bot-wide channel stored under ``key``. Only ``ping_role`` can be mentioned."""
        channel_id = as_id(await self.settings.get(key))
        if channel_id is None:
            return False
        return await self.post_to(channel_id, content, embed=embed, ping_role=ping_role, view=view, label=key)

    async def post_to(self, channel_id: int, content: str | None = None, *, embed: discord.Embed | None = None,
                      ping_role: int | None = None, view: discord.ui.View | None = None,
                      label: str = "channel") -> bool:
        """Post in one channel. Only ``ping_role`` can be mentioned, and never @everyone."""
        if self.client is None:
            return False
        mentions = NONE
        if ping_role == self.guild_id:
            ping_role = None  # the @everyone role: never ping it
        if ping_role:
            content = f"<@&{ping_role}> {content or ''}".rstrip()
            mentions = discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(id=ping_role)])
        extra: dict[str, Any] = {"embed": embed} if embed is not None else {}
        if view is not None:
            extra["view"] = view
        try:
            channel = await self.channel(channel_id)
            await channel.send(content, allowed_mentions=mentions, **extra)
        except Exception as exc:  # noqa: BLE001 - a missing channel must not break the caller
            log.warning("could not post in the %s (%s): %s", label, channel_id, exc)
            return False
        return True

    async def relay_changed(self) -> None:
        if self.on_relay_changed is not None:
            await self.on_relay_changed()

    async def channel(self, channel_id: int) -> Any:
        if self.client is None:
            raise RuntimeError("no Discord client")
        cached = self.client.get_channel(channel_id)
        if cached is not None:
            self.missing_channels.pop(channel_id, None)
            return cached
        until = self.missing_channels.get(channel_id)
        if until is not None and time.monotonic() < until:
            raise ChannelUnavailable(channel_id)
        try:
            found = await self.client.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden):
            self.missing_channels[channel_id] = time.monotonic() + MISSING_CHANNEL_RETRY
            raise
        self.missing_channels.pop(channel_id, None)
        return found


MISSING_CHANNEL_RETRY = 600.0  # seconds before a channel Discord refused is looked up again


class ChannelUnavailable(Exception):
    """Discord said this channel is gone or closed to the bot a moment ago, so it wasn't asked again."""

    def __init__(self, channel_id: int) -> None:
        super().__init__(f"channel {channel_id} was missing or closed to the bot a moment ago; "
                         f"checking again within {MISSING_CHANNEL_RETRY / 60:.0f} minutes")
        self.channel_id = channel_id


def code(text: str) -> str:
    """``text`` as inline code. Markdown escapes would show up literally there, so only the
    backtick (which would end the span) and mentions are neutralised."""
    return "`" + text.replace("`", "ˋ").replace("@", "@​") + "`"


INFO, GOOD, WARN, BAD = 0x5865F2, 0x2ECC71, 0xF1C40F, 0xE74C3C  # embed colours; text always says it too


def card(**kwargs: Any) -> discord.Embed:
    """An embed in the house style: blurple unless the caller picks a colour."""
    kwargs.setdefault("colour", INFO)
    return discord.Embed(**kwargs)


def problem_text(ref: str) -> str:
    """What a member sees when a command broke on our side. The ref matches a line in the bot's log."""
    return (f"Something went wrong on my side, and I may not have finished. Try again in a minute. "
            f"If it keeps happening, tell an admin and give them this code: `{ref}`.")


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
    # A new message has no view or embed to clear. discord.py posts the message and only then calls
    # view.is_finished(), so view=None would fail after the member already saw the reply.
    for key in ("view", "embed"):
        if key in kwargs and kwargs[key] is None:
            del kwargs[key]
    if interaction.response.is_done():
        await interaction.followup.send(content, **kwargs)
    else:
        await interaction.response.send_message(content, **kwargs)


async def thinking(interaction: discord.Interaction, *, ephemeral: bool = True) -> None:
    """Show "thinking…" unless the interaction was already answered. Discord drops an interaction that
    gets no answer within 3 seconds, so call this before anything that waits on AMP."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)


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
        if await self.ctx.allowed(interaction.user, node, shard_id):
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
        if row is None or (row.hidden and not await self.ctx.sees_hidden(interaction.user, row.shard_id)):
            await reply(interaction, f"I don't know a server called {escape(name)}. "
                                     "Pick one from the list as you type.")
            return None
        return row

    async def autocomplete_server(self, interaction: discord.Interaction,
                                  current: str) -> list[app_commands.Choice[str]]:
        rows = await self.ctx.servers.list()
        needle = current.lower()
        out = []
        for r in rows:
            if needle in r.name.lower() and (not r.hidden or await self.ctx.sees_hidden(interaction.user, r.shard_id)):
                out.append(app_commands.Choice(name=r.name[:100], value=r.shard_id))
        return out[:25]

    async def autocomplete_player(self, interaction: discord.Interaction,
                                  current: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=n[:100], value=n[:100]) for n in await self.ctx.players.names(current)]
