# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/setup safety`` and ``/setup players``: the settings behind confirmations, staff alerts and ``/players``.

Both join the ``/setup`` group that ``SetupModule`` creates, so ``Modules.all`` lists this module after it.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from portcullis.bot.base import Module, as_id, card, reply, role_problem
from portcullis.bot.guard import CONFIRM_KEY, CONFIRM_MODES, DENIED_ALERTS, STAFF_CHANNEL, STAFF_ROLE
from portcullis.bot.players import VISIBILITY, VISIBILITY_KEY
from portcullis.core.relay import escape

log = logging.getLogger(__name__)


class SafetyModule(Module):
    async def safety_embed(self) -> discord.Embed:
        get = self.ctx.settings.get
        mode = await get(CONFIRM_KEY, "all")
        channel = as_id(await get(STAFF_CHANNEL))
        role = as_id(await get(STAFF_ROLE))
        on = await get(DENIED_ALERTS, True)
        embed = card(title="Safety settings")
        embed.add_field(name="Confirmations", value=CONFIRM_MODES.get(mode, CONFIRM_MODES["all"]), inline=False)
        embed.add_field(name="Blocked attempts go to", value=f"<#{channel}>" if channel else "Nowhere yet",
                        inline=True)
        embed.add_field(name="Ping", value=f"<@&{role}>" if role else "Nobody", inline=True)
        embed.add_field(name="Reports", value="On" if on else "Off", inline=True)
        return embed

    async def setup_safety(self, interaction: discord.Interaction, confirm: str | None = None,
                           staff_channel: discord.TextChannel | None = None, staff_role: discord.Role | None = None,
                           denied_alerts: bool | None = None, clear: bool = False) -> None:
        node = "setup.safety"
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, node):
            return
        by = interaction.user.id
        changed = []
        if confirm is not None and confirm not in CONFIRM_MODES:
            await reply(interaction, "Choose one of the confirmation options in the list.")
            return
        if staff_role is not None:
            problem = role_problem(staff_role, interaction.guild, self.ctx.access, handed_out=False)
            if problem:
                await reply(interaction, problem)
                return
        if clear:
            for key in (STAFF_CHANNEL, STAFF_ROLE):
                await self.ctx.settings.delete(key)
            changed.append("staff channel and role cleared")
        if confirm is not None:
            await self.ctx.settings.set(CONFIRM_KEY, confirm, by=by)
            changed.append(f"confirm={confirm}")
        if staff_channel is not None:
            await self.ctx.settings.set(STAFF_CHANNEL, staff_channel.id, by=by)
            changed.append(f"staff_channel={staff_channel.id}")
        if staff_role is not None:
            await self.ctx.settings.set(STAFF_ROLE, staff_role.id, by=by)
            changed.append(f"staff_role={staff_role.id}")
        if denied_alerts is not None:
            await self.ctx.settings.set(DENIED_ALERTS, denied_alerts, by=by)
            changed.append(f"denied_alerts={denied_alerts}")
        if changed:
            await self.ctx.audit.record(node, actor_id=by, target=", ".join(changed)[:200])
        await reply(interaction, "Saved." if changed else None, embed=await self.safety_embed())

    async def setup_players(self, interaction: discord.Interaction, show: str,
                            server: str | None = None) -> None:
        node = "setup.players"
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, node):
            return
        if show not in VISIBILITY:
            await reply(interaction, "Choose one of the options in the list.")
            return
        shard_id = None
        if server:
            row = await self.server(interaction, server)
            if row is None:
                return
            shard_id = row.shard_id
            await self.ctx.settings.set(VISIBILITY_KEY, show, scope=shard_id, by=interaction.user.id)
            where = f"**{escape(row.name)}**"
        else:
            await self.ctx.settings.set(VISIBILITY_KEY, show, by=interaction.user.id)
            where = "every server without its own setting"
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=shard_id, target=show)
        await reply(interaction, f"`/players` for {where}: {VISIBILITY[show]}.")

    def register(self, tree: app_commands.CommandTree) -> None:
        group = tree.get_command("setup")
        if not isinstance(group, app_commands.Group):
            log.warning("no /setup group to add /setup safety and /setup players to")
            return

        @group.command(name="safety", description="Confirmations and where blocked attempts are reported")
        @app_commands.describe(confirm="When to ask before a power action", staff_channel="Where blocked "
                               "attempts are reported", staff_role="Who is pinged about them",
                               denied_alerts="Report blocked attempts", clear="Forget the staff channel and role")
        @app_commands.choices(confirm=[app_commands.Choice(name=label, value=key)
                                       for key, label in CONFIRM_MODES.items()])
        async def setup_safety(interaction: discord.Interaction, confirm: str | None = None,
                               staff_channel: discord.TextChannel | None = None,
                               staff_role: discord.Role | None = None, denied_alerts: bool | None = None,
                               clear: bool = False) -> None:
            await self.setup_safety(interaction, confirm, staff_channel, staff_role, denied_alerts, clear)

        @group.command(name="players", description="Who may see the names in /players")
        @app_commands.describe(show="What members see", server="One server (leave out for the default)")
        @app_commands.choices(show=[app_commands.Choice(name=label, value=key) for key, label in VISIBILITY.items()])
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def setup_players(interaction: discord.Interaction, show: str, server: str | None = None) -> None:
            await self.setup_players(interaction, show, server)
