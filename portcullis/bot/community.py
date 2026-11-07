# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/whitelist``, ``/link``, ``/player`` and ``/user`` (plus the right-click user menu)."""

from __future__ import annotations

import contextlib
import logging
import re
import secrets
from typing import Any

import discord
from discord import app_commands

from portcullis.bot.base import NONE, Ctx, Module, duration, fill, reply, short_error, valid_name, when
from portcullis.bot.setup import WHITELIST_CHANNEL, WL_ADD, WL_REMOVE
from portcullis.core.relay import escape
from portcullis.db.features import LinkCode, WhitelistRequest, links_by_name
from portcullis.db.repos import ServerRow

log = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I


def platform_of(row: ServerRow) -> str:
    """Links are per game: a name proven on one Unturned server counts on every Unturned server."""
    return (row.template or "generic").lower()


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def request_embed(req: WhitelistRequest, server_name: str, note: str | None = None) -> discord.Embed:
    colour = {"pending": 0xF1C40F, "approved": 0x2ECC71, "denied": 0xE74C3C}.get(req.status, 0x7F8C8D)
    embed = discord.Embed(title=f"Whitelist request for {server_name}", colour=colour)
    embed.add_field(name="Member", value=f"<@{req.discord_id}>")
    embed.add_field(name="In-game name", value=escape(req.ingame_name))
    status = req.status.capitalize()
    if req.decided_by:
        status += f" by <@{req.decided_by}>"
    embed.add_field(name="Status", value=status)
    if note:
        embed.add_field(name="Note", value=note[:1000], inline=False)
    embed.set_footer(text=f"Request {req.id}")
    return embed


class WhitelistDecision(discord.ui.DynamicItem[discord.ui.Button[Any]],
                        template=r"wl:(?P<action>approve|deny):(?P<id>\d+)"):
    """Approve/Deny on a request message. Works across bot restarts (the id is in the custom id)."""

    module: CommunityModule | None = None

    def __init__(self, action: str, request_id: int) -> None:
        style = discord.ButtonStyle.success if action == "approve" else discord.ButtonStyle.danger
        super().__init__(discord.ui.Button(label=action.capitalize(), style=style,
                                           custom_id=f"wl:{action}:{request_id}"))
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item[Any],
                             match: re.Match[str]) -> WhitelistDecision:
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.module is not None
        await self.module.decide(interaction, self.request_id, self.action)


def decision_view(request_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(WhitelistDecision("approve", request_id))
    view.add_item(WhitelistDecision("deny", request_id))
    return view


class CommunityModule(Module):
    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)
        WhitelistDecision.module = self

    # ---------------------------------------------------------------- /whitelist

    async def whitelist_request(self, interaction: discord.Interaction, server: str, name: str | None = None) -> None:
        if not await self.in_guild(interaction):
            return
        row = await self.server(interaction, server)
        if row is None:
            return
        user_id = interaction.user.id
        if (await self.ctx.users.flags(user_id))["banned"]:
            await reply(interaction, "You can't make whitelist requests. Ask a moderator if you think that's wrong.")
            return
        if not await self.ctx.settings.get(WL_ADD, scope=row.shard_id):
            await reply(interaction, f"{escape(row.name)} doesn't take whitelist requests through Portcullis.")
            return
        channel_id = await self.ctx.settings.get(WHITELIST_CHANNEL)
        if not channel_id:
            await reply(interaction, "Whitelist requests are off. An admin can turn them on in `/setup bot`.")
            return
        if name is None:
            linked = [lk for lk in await self.ctx.users.links_for(user_id) if lk.platform == platform_of(row)]
            if not linked:
                await reply(interaction, "Tell me your in-game name (the `name` option), or prove it first "
                                         "with `/link`.")
                return
            name = linked[0].ingame_name
        if not valid_name(name):
            await reply(interaction, "That in-game name has characters I can't send to the server.")
            return
        owner = await self.ctx.users.find_link(platform_of(row), name)
        if owner is not None and owner.discord_id != user_id:
            await reply(interaction, f"{escape(name)} is linked to another member.")
            return
        if await self.ctx.whitelist.pending_for(user_id, row.shard_id):
            await reply(interaction, f"You already have a request waiting for {escape(row.name)}.")
            return
        req = await self.ctx.whitelist.create(row.shard_id, user_id, name)
        try:
            channel = await self.ctx.channel(int(channel_id))
            await channel.send(embed=request_embed(req, row.name), view=decision_view(req.id), allowed_mentions=NONE)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not post whitelist request %s: %s", req.id, exc)
            await self.ctx.whitelist.decide(req.id, "denied", 0)  # 0: closed by the bot, never shown to anyone
            await reply(interaction, "I couldn't post your request for the moderators. Tell an admin to check "
                                     "the whitelist channel in `/setup bot`.")
            return
        await self.ctx.audit.record("whitelist.request", actor_id=user_id, shard_id=row.shard_id, target=name)
        await reply(interaction, f"Asked the moderators to whitelist **{escape(name)}** on **{escape(row.name)}**. "
                                 "I'll message you when they decide.")

    async def decide(self, interaction: discord.Interaction, request_id: int, action: str) -> None:
        node = f"whitelist.{action}"
        req = await self.ctx.whitelist.get(request_id)
        if req is None or not await self.require_mod(interaction, node, req.shard_id):
            if req is None:
                await reply(interaction, "That request no longer exists.")
            return
        row = await self.ctx.servers.get(req.shard_id)
        server_name = row.name if row else req.shard_id
        status = "approved" if action == "approve" else "denied"
        if not await self.ctx.whitelist.decide(request_id, status, interaction.user.id):
            await reply(interaction, "Someone already decided this request.")
            return
        note = None
        if status == "approved":
            template = await self.ctx.settings.get(WL_ADD, scope=req.shard_id)
            try:
                if not template:
                    raise RuntimeError("the server has no whitelist add command any more")
                await self.ctx.port.send_console(req.shard_id, fill(template, name=req.ingame_name))
            except Exception as exc:  # noqa: BLE001
                note = f"Approved, but AMP didn't take the command ({short_error(exc)}). Add the name by hand."
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=req.shard_id, target=req.ingame_name,
                                    result="failed" if note else "ok")
        updated = await self.ctx.whitelist.get(request_id)
        assert updated is not None
        await interaction.response.edit_message(embed=request_embed(updated, server_name, note), view=None)
        await self._dm(req.discord_id, f"Your whitelist request for **{escape(req.ingame_name)}** on "
                                       f"**{escape(server_name)}** was {status}.")

    async def _dm(self, user_id: int, text: str) -> None:
        if self.ctx.client is None:
            return
        with contextlib.suppress(Exception):  # members can block DMs; that's fine
            user = self.ctx.client.get_user(user_id) or await self.ctx.client.fetch_user(user_id)
            await user.send(text, allowed_mentions=NONE)

    async def whitelist_change(self, interaction: discord.Interaction, server: str, name: str, add: bool) -> None:
        if not await self.in_guild(interaction):
            return
        row = await self.server(interaction, server)
        node = "whitelist.add" if add else "whitelist.remove"
        if row is None or not await self.require_mod(interaction, node, row.shard_id):
            return
        if not valid_name(name):
            await reply(interaction, "That in-game name has characters I can't send to the server.")
            return
        template = await self.ctx.settings.get(WL_ADD if add else WL_REMOVE, scope=row.shard_id)
        if not template:
            await reply(interaction, f"{escape(row.name)} has no whitelist {'add' if add else 'remove'} command. "
                                     "An admin can set one in `/setup server`.")
            return
        try:
            await self.ctx.port.send_console(row.shard_id, fill(template, name=name))
        except Exception as exc:  # noqa: BLE001
            await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, target=name,
                                        result="failed")
            await reply(interaction, f"AMP didn't take the command: {escape(short_error(exc))}")
            return
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, target=name)
        verb = "Added" if add else "Removed"
        await reply(interaction, f"{verb} **{escape(name)}** {'to' if add else 'from'} the whitelist on "
                                 f"**{escape(row.name)}**.")

    # ---------------------------------------------------------------- /link

    async def link(self, interaction: discord.Interaction, server: str | None = None, name: str | None = None) -> None:
        if not await self.in_guild(interaction):
            return
        user_id = interaction.user.id
        if server is None and name is None:
            links = await self.ctx.users.links_for(user_id)
            pending = await self.ctx.links.pending(user_id)
            lines = [f"**{escape(lk.ingame_name)}** on {escape(lk.platform)}" for lk in links] or ["No linked names."]
            if pending:
                lines.append(f"Waiting for you to type `{pending.code}` in game as **{escape(pending.ingame_name)}**"
                             f" (expires {when(pending.expires_at)}).")
            lines.append("\nTo link a name: `/link server:<server> name:<your in-game name>`.")
            extra = {"view": UnlinkView(self, user_id)} if links else {}
            await reply(interaction, "\n".join(lines), **extra)
            return
        if server is None or name is None:
            await reply(interaction, "Give both the server and your in-game name.")
            return
        row = await self.server(interaction, server)
        if row is None:
            return
        if not self.ctx.has_chat(row.template):
            await reply(interaction, f"Linking works by typing a code in game chat, and Portcullis can't read chat "
                                     f"on {escape(row.name)} yet.")
            return
        if not valid_name(name):
            await reply(interaction, "That doesn't look like an in-game name.")
            return
        owner = await self.ctx.users.find_link(platform_of(row), name)
        if owner is not None and owner.discord_id != user_id:
            await reply(interaction, f"{escape(name)} is already linked to another member.")
            return
        code = await self.ctx.links.issue(user_id, new_code(), row.shard_id, platform_of(row), name)
        await reply(interaction, f"Join **{escape(row.name)}** as **{escape(name)}** and type `{code.code}` in chat "
                                 f"{when(code.expires_at)}. I'll message you when it's done.")

    async def on_linked(self, code: LinkCode) -> None:
        await self.ctx.audit.record("link.verified", actor_id=code.discord_id, shard_id=code.shard_id,
                                    target=code.ingame_name)
        await self._dm(code.discord_id, f"Linked: you are **{escape(code.ingame_name)}** in game.")

    # ---------------------------------------------------------------- /player

    async def player_lookup(self, interaction: discord.Interaction, name: str) -> None:
        if not await self.in_guild(interaction):
            return
        found = await self.ctx.players.lookup(name)
        if found is None:
            await reply(interaction, f"I haven't seen {escape(name)} on any server.")
            return
        names = {r.shard_id: r.name for r in await self.ctx.servers.list(include_gone=True)}
        embed = discord.Embed(title=found.name)
        if found.online_on:
            embed.add_field(name="Online now", value=", ".join(escape(names.get(s, s)) for s in found.online_on))
        else:
            embed.add_field(name="Last seen", value=when(found.last_seen))
        embed.add_field(name="First seen", value=when(found.first_seen))
        embed.add_field(name="Play time", value=f"{duration(found.seconds)} over {found.sessions} sessions")
        embed.add_field(name="Servers", value=", ".join(escape(names.get(s, s)) for s in found.servers)[:1000],
                        inline=False)
        members = await links_by_name(self.ctx.db, found.name)
        if members:
            embed.add_field(name="Discord", value=", ".join(f"<@{m}>" for m, _ in members), inline=False)
        await reply(interaction, embed=embed)

    async def player_top(self, interaction: discord.Interaction, server: str | None = None) -> None:
        if not await self.in_guild(interaction):
            return
        row = None
        if server:
            row = await self.server(interaction, server)
            if row is None:
                return
        ranking = await self.ctx.players.top(row.shard_id if row else None)
        if not ranking:
            await reply(interaction, "No play time recorded yet.")
            return
        lines = [f"{i}. **{escape(name)}** · {duration(secs)}" for i, (name, secs) in enumerate(ranking, 1)]
        title = f"Most played on {row.name}" if row else "Most played"
        await reply(interaction, embed=discord.Embed(title=title, description="\n".join(lines)))

    # ---------------------------------------------------------------- /user

    async def user_embed(self, member: Any) -> discord.Embed:
        flags = await self.ctx.users.flags(member.id)
        links = await self.ctx.users.links_for(member.id)
        embed = discord.Embed(title=getattr(member, "display_name", str(member.id)))
        embed.add_field(name="Member", value=f"<@{member.id}>")
        embed.add_field(name="Donator", value="Yes" if flags["donator"] else "No")
        embed.add_field(name="Blocked from requests", value="Yes" if flags["banned"] else "No")
        embed.add_field(name="Linked names",
                        value="\n".join(f"{escape(lk.ingame_name)} ({escape(lk.platform)})" for lk in links) or "none",
                        inline=False)
        recent = await self.ctx.audit.query(actor_id=member.id, limit=5)
        if recent:
            embed.add_field(name="Recent actions", value="\n".join(
                f"{when(e.at)} `{e.action}` {e.result}" for e in recent), inline=False)
        return embed

    async def user(self, interaction: discord.Interaction, member: Any) -> None:
        if not await self.in_guild(interaction) or not await self.require_mod(interaction, "user.view"):
            return
        await reply(interaction, embed=await self.user_embed(member), view=UserView(self, member))

    async def toggle_flag(self, interaction: discord.Interaction, member: Any, flag: str) -> None:
        current = (await self.ctx.users.flags(member.id))[flag]
        await self.ctx.users.set_flags(member.id, **{flag: not current})
        await self.ctx.audit.record(f"user.{flag}", actor_id=interaction.user.id, target=str(member.id),
                                    detail={"value": not current})

    # ---------------------------------------------------------------- registration

    def register(self, tree: app_commands.CommandTree) -> None:
        wl = app_commands.Group(name="whitelist", description="Get on a server's whitelist", guild_only=True)

        @wl.command(name="request", description="Ask the moderators to whitelist you on a server")
        @app_commands.describe(server="The game server", name="Your in-game name (leave out to use your linked name)")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def wl_request(interaction: discord.Interaction, server: str, name: str | None = None) -> None:
            await self.whitelist_request(interaction, server, name)

        @wl.command(name="add", description="Add a name to a server's whitelist (operators)")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def wl_add(interaction: discord.Interaction, server: str, name: str) -> None:
            await self.whitelist_change(interaction, server, name, add=True)

        @wl.command(name="remove", description="Remove a name from a server's whitelist (operators)")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def wl_remove(interaction: discord.Interaction, server: str, name: str) -> None:
            await self.whitelist_change(interaction, server, name, add=False)

        @app_commands.command(name="link", description="Prove which in-game name is yours, or see your links")
        @app_commands.guild_only()
        @app_commands.describe(server="A server you can join", name="Your in-game name")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def link(interaction: discord.Interaction, server: str | None = None, name: str | None = None) -> None:
            await self.link(interaction, server, name)

        player = app_commands.Group(name="player", description="Players on the game servers", guild_only=True)

        @player.command(name="lookup", description="When a player was last seen and how long they've played")
        @app_commands.autocomplete(name=self.autocomplete_player)
        async def player_lookup(interaction: discord.Interaction, name: str) -> None:
            await self.player_lookup(interaction, name)

        @player.command(name="top", description="Who has played the most")
        @app_commands.describe(server="Leave out for all servers")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def player_top(interaction: discord.Interaction, server: str | None = None) -> None:
            await self.player_top(interaction, server)

        @app_commands.command(name="user", description="A member's links and flags (operators)")
        @app_commands.guild_only()
        async def user(interaction: discord.Interaction, member: discord.Member) -> None:
            await self.user(interaction, member)

        @app_commands.context_menu(name="Portcullis user")
        @app_commands.guild_only()
        async def user_menu(interaction: discord.Interaction, member: discord.Member) -> None:
            await self.user(interaction, member)

        for command in (wl, link, player, user, user_menu):
            tree.add_command(command)


class UnlinkView(discord.ui.View):
    def __init__(self, module: CommunityModule, user_id: int) -> None:
        super().__init__(timeout=300)
        self.module = module
        self.user_id = user_id

    @discord.ui.button(label="Remove my links", style=discord.ButtonStyle.danger)
    async def unlink(self, interaction: discord.Interaction, button: discord.ui.Button[UnlinkView]) -> None:
        removed = await self.module.ctx.users.remove_links(self.user_id)
        await self.module.ctx.audit.record("link.remove", actor_id=self.user_id, detail={"removed": removed})
        await interaction.response.edit_message(content=f"Removed {removed} link(s).", view=None)


class UserView(discord.ui.View):
    def __init__(self, module: CommunityModule, member: Any) -> None:
        super().__init__(timeout=600)
        self.module = module
        self.member = member

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.module.ctx.access.is_mod(interaction.user):
            return True
        await reply(interaction, "You need an operator role for that.")
        return False

    async def _flip(self, interaction: discord.Interaction, flag: str) -> None:
        await self.module.toggle_flag(interaction, self.member, flag)
        await interaction.response.edit_message(embed=await self.module.user_embed(self.member), view=self)

    @discord.ui.button(label="Toggle donator", style=discord.ButtonStyle.secondary)
    async def donator(self, interaction: discord.Interaction, button: discord.ui.Button[UserView]) -> None:
        await self._flip(interaction, "donator")

    @discord.ui.button(label="Block or unblock requests", style=discord.ButtonStyle.danger)
    async def banned(self, interaction: discord.Interaction, button: discord.ui.Button[UserView]) -> None:
        await self._flip(interaction, "banned")

    @discord.ui.button(label="Remove their links", style=discord.ButtonStyle.danger)
    async def unlink(self, interaction: discord.Interaction, button: discord.ui.Button[UserView]) -> None:
        removed = await self.module.ctx.users.remove_links(self.member.id)
        await self.module.ctx.audit.record("user.unlink", actor_id=interaction.user.id, target=str(self.member.id),
                                           detail={"removed": removed})
        await interaction.response.edit_message(embed=await self.module.user_embed(self.member), view=self)
