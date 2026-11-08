# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/audit``, ``/admin diagnostics|export|import`` and ``/help``."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from typing import Any

import discord
from discord import app_commands

from portcullis.bot.base import OWNER_ONLY_KEYS, Module, card, code, reply, short_error, state_text, when
from portcullis.bot.v3map import STATUS_LABELS, lookup, search
from portcullis.core.relay import escape
from portcullis.db.features import all_settings
from portcullis.db.repos import ServerRepo

log = logging.getLogger(__name__)

EXPORT_VERSION = 1
MAX_IMPORT_BYTES = 1_000_000

# (command, who, what) for /help. "who" is everyone, mod or admin.
HELP = [
    ("/server list", "everyone", "Every server and whether it's online (players: names who's on)"),
    ("/server status", "everyone", "One server's state, who is online and its address"),
    ("/server banner", "everyone", "A status picture of one server or all of them"),
    ("/player lookup", "everyone", "When a player was last seen, and their play time"),
    ("/player top", "everyone", "Who has played the most"),
    ("/whitelist request", "everyone", "Ask to be whitelisted on a server"),
    ("/link", "everyone", "Prove which in-game name is yours"),
    ("/notify", "everyone", "Get a DM when a server comes online"),
    ("/event list", "everyone", "Events coming up"),
    ("/help", "everyone", "This list"),
    ("/server power", "mod", "Start, stop, restart, or restart with a warning in game chat"),
    ("/server power-all", "mod", "Start, stop or restart every server, or every server with a tag"),
    ("/server send", "mod", "Send a console command or chat message"),
    ("/server console", "mod", "A server's latest console lines"),
    ("/backups", "mod", "A server's backups, with Make a backup and (for admins) Restore"),
    ("/schedule list", "mod", "A server's AMP schedules, with Run now, On and Off"),
    ("/whitelist add · remove · list", "mod", "Change or see a server's whitelist"),
    ("/player moderate", "mod", "Kick, ban or unban a player"),
    ("/player watch · watchlist", "mod", "Get a quiet note when a player joins"),
    ("/poll create · list", "mod", "Community polls"),
    ("/event create", "mod", "Announce an event with a Going button and a reminder"),
    ("/user (or right-click → Apps → Portcullis user)", "mod", "A member's links and flags"),
    ("/setup relay · server · bot", "admin", "Relays, server settings, bot settings"),
    ("/alert create · list", "admin", "Your own alerts: CPU, memory, player count or offline, held for N minutes"),
    ("/audit", "admin", "Who did what through Portcullis"),
    ("/admin diagnostics · export · import", "admin", "Health check and settings backup"),
]


class AdminModule(Module):
    # ---------------------------------------------------------------- /help

    async def help(self, interaction: discord.Interaction, v3: str | None = None) -> None:
        if v3:
            await self.help_v3(interaction, v3)
            return
        access = self.ctx.access
        allowed = {"everyone"}
        if await self.ctx.is_staff(interaction.user):
            allowed.add("mod")
        if access.is_admin(interaction.user):
            allowed.add("admin")
        lines = [f"`{cmd}` · {what}" for cmd, who, what in HELP if who in allowed]
        embed = card(title="Portcullis", description="\n".join(lines))
        embed.set_footer(text="Only the commands you can use are listed.")
        await reply(interaction, embed=embed)

    async def help_v3(self, interaction: discord.Interaction, name: str) -> None:
        """Where a Gatekeeper V3 command went (docs/V3_PARITY.md)."""
        found = lookup(name)
        if found is None:
            await reply(interaction, f"Gatekeeper V3 had no command called {code(name[:100])}. "
                                     "Pick one from the list as you type.")
            return
        title = "Where it went" if found.status in ("same", "moved") else STATUS_LABELS[found.status]
        embed = card(title=f"{found.v3} in Portcullis")
        embed.add_field(name=title, value=found.home, inline=False)
        embed.set_footer(text="Every V3 command is listed in docs/V3_PARITY.md.")
        await reply(interaction, embed=embed)

    async def autocomplete_v3(self, interaction: discord.Interaction,
                              current: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=n, value=n) for n in search(current)]

    # ---------------------------------------------------------------- /audit

    async def audit(self, interaction: discord.Interaction, member: Any | None = None, server: str | None = None,
                    action: str | None = None, result: str | None = None, export: bool = False) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "audit.view"):
            return
        shard_id = None
        if server:
            row = await self.server(interaction, server)
            if row is None:
                return
            shard_id = row.shard_id
        entries = await self.ctx.audit.query(actor_id=member.id if member else None, shard_id=shard_id,
                                             action_prefix=action, result=result, limit=1000 if export else 25)
        if export:
            data = [e.__dict__ for e in entries]
            file = discord.File(io.BytesIO(json.dumps(data, indent=2).encode()), filename="portcullis-audit.json")
            await reply(interaction, f"{len(entries)} entries.", file=file)
            return
        if not entries:
            await reply(interaction, "Nothing matches.")
            return
        names = {r.shard_id: r.name for r in await self.ctx.servers.list(include_gone=True)}
        lines = []
        for e in entries:
            who = f"<@{e.actor_id}>" if e.actor_id else "bot"
            where = f" · {escape(names.get(e.shard_id, e.shard_id))}" if e.shard_id else ""
            what = f" · {escape(e.target)}" if e.target else ""
            mark = "" if e.result == "ok" else f" · **{e.result}**"
            lines.append(f"{when(e.at)} {who} `{e.action}`{where}{what}{mark}")
        await reply(interaction, embed=card(title="Audit log", description="\n".join(lines)[:4000]))

    # ---------------------------------------------------------------- /admin diagnostics

    async def diagnostics(self, interaction: discord.Interaction) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "admin.diagnostics"):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = card(title="Diagnostics")
        started = time.perf_counter()
        try:
            servers = await asyncio.wait_for(self.ctx.port.list_servers(), 10)
            embed.add_field(name="AMP", value=f"✅ reachable, {len(servers)} server(s), "
                                              f"{(time.perf_counter() - started) * 1000:.0f} ms", inline=False)
        except Exception as exc:  # noqa: BLE001
            embed.add_field(name="AMP", value=f"❌ {escape(short_error(exc))}", inline=False)
            servers = []
        lines = []
        for info in servers[:20]:
            try:
                status = await asyncio.wait_for(self.ctx.port.get_status(info.id), 8)
                lines.append(f"{state_text(status.state)} · {escape(info.name)}")
            except Exception as exc:  # noqa: BLE001 - a 403 here names the missing AMP permission
                lines.append(f"❌ {escape(info.name)} · {escape(short_error(exc))[:150]}")
        if lines:
            embed.add_field(name="Servers", value="\n".join(lines)[:1024], inline=False)
        embed.add_field(name="AMP account permissions", value=await self.permission_text(), inline=False)
        rows = await self.ctx.servers.list()
        relays = sum(1 for r in rows if r.console_channel or r.event_channel or r.chat_channel)
        embed.add_field(name="Relays", value=f"{relays} server(s) with a relay channel")
        dropped = getattr(getattr(self.ctx.port, "hub", None), "dropped", None)
        if dropped is not None:
            embed.add_field(name="Events dropped", value=str(dropped))
        embed.add_field(name="Profile status", value=self.ctx.live.summary().text())
        if self.ctx.client is not None and self.ctx.client.latency == self.ctx.client.latency:  # not NaN
            embed.add_field(name="Discord latency", value=f"{self.ctx.client.latency * 1000:.0f} ms")
        embed.add_field(name="Database", value=f"schema version {self.ctx.db.version}")
        await reply(interaction, embed=embed)

    async def permission_text(self) -> str:
        """What the bot's AMP account may do, from ``GET /api/me/permissions``."""
        try:
            grants = await asyncio.wait_for(self.ctx.port.permissions(), 8)
        except Exception as exc:  # noqa: BLE001
            return f"❌ couldn't read them: {escape(short_error(exc))[:200]}"
        if ">" in grants:
            return ("⚠️ Everything (an admin account). It works, but if the bot's password leaks, so does the "
                    "whole panel. docs/SETUP.md lists a smaller role.")
        if not grants:
            return "❌ None. Give the bot's AMP account a role (docs/SETUP.md)."
        wanted = ("view", "power.start", "power.stop", "power.restart", "console.read", "console.send")
        text = " ".join(grants)
        missing = [w for w in wanted if w not in text]
        shown = ", ".join(code(g) for g in grants[:20]) + (f" and {len(grants) - 20} more"
                                                            if len(grants) > 20 else "")
        if missing:
            shown += ("\nNot found in that list: " + ", ".join(code(m) for m in missing) +
                      ". AMP's grant format isn't fully mapped yet, so check the role in AMP before changing it.")
        return shown[:1024]

    # ---------------------------------------------------------------- /admin export, import

    async def export_data(self) -> dict[str, Any]:
        rows = await self.ctx.servers.list(include_gone=True)
        return {
            "portcullis_export": EXPORT_VERSION,
            "servers": {r.shard_id: {k: getattr(r, k) for k in sorted(ServerRepo.EDITABLE)} for r in rows},
            "settings": await all_settings(self.ctx.db),
        }

    async def export(self, interaction: discord.Interaction) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "admin.export"):
            return
        data = json.dumps(await self.export_data(), indent=2).encode()
        await self.ctx.audit.record("admin.export", actor_id=interaction.user.id)
        await reply(interaction, "Portcullis settings. Keep this file: `/admin import` restores it.",
                    file=discord.File(io.BytesIO(data), filename="portcullis-settings.json"))

    async def import_data(self, data: Any, by: int, *, owner: bool = False) -> tuple[int, int, list[str]]:
        """Apply an export. Returns (servers updated, settings written, problems).
        Operator role settings are only imported for owners: a file mustn't hand out powers."""
        if not isinstance(data, dict) or data.get("portcullis_export") != EXPORT_VERSION:
            return 0, 0, ["This isn't a Portcullis settings export (Gatekeeper V3 databases can't be imported yet)."]
        problems: list[str] = []
        updated = written = 0
        for shard_id, fields in (data.get("servers") or {}).items():
            if not isinstance(fields, dict) or await self.ctx.servers.get(shard_id) is None:
                problems.append(f"skipped unknown server {shard_id}")
                continue
            clean = {k: v for k, v in fields.items() if k in ServerRepo.EDITABLE}
            try:
                await self.ctx.servers.update(shard_id, **clean)
                updated += 1
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{shard_id}: {short_error(exc)}")
        for scope, values in (data.get("settings") or {}).items():
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                if key in OWNER_ONLY_KEYS and not owner:
                    problems.append(f"skipped {key} ({scope}): only owners can import operator roles")
                    continue
                await self.ctx.settings.set(str(key), value, scope=str(scope), by=by)
                written += 1
        return updated, written, problems

    async def import_(self, interaction: discord.Interaction, file: discord.Attachment) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "admin.import"):
            return
        if file.size > MAX_IMPORT_BYTES:
            await reply(interaction, "That file is too big for a settings export.")
            return
        try:
            data = json.loads(await file.read())
        except ValueError:
            await reply(interaction, "That file isn't JSON.")
            return
        updated, written, problems = await self.import_data(data, interaction.user.id,
                                                            owner=self.ctx.access.is_owner(interaction.user))
        await self.ctx.audit.record("admin.import", actor_id=interaction.user.id,
                                    detail={"servers": updated, "settings": written, "problems": problems[:20]})
        await self.ctx.relay_changed()
        text = f"Imported {updated} server(s) and {written} setting(s)."
        if problems:
            text += "\n" + "\n".join(f"- {escape(p)}" for p in problems[:10])
        await reply(interaction, text)

    def register(self, tree: app_commands.CommandTree) -> None:
        @app_commands.command(name="help", description="What Portcullis can do for you")
        @app_commands.guild_only()
        @app_commands.describe(v3="A Gatekeeper V3 command, to see where it is now")
        @app_commands.autocomplete(v3=self.autocomplete_v3)
        async def help_(interaction: discord.Interaction, v3: str | None = None) -> None:
            await self.help(interaction, v3)

        @app_commands.command(name="audit", description="Who did what through Portcullis (admins)")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.describe(member="Only this member", server="Only this server",
                               action="Only actions starting with this, e.g. server.power",
                               result="Only this result", export="Send everything that matches as a JSON file")
        @app_commands.autocomplete(server=self.autocomplete_server)
        @app_commands.choices(result=[app_commands.Choice(name=r, value=r) for r in ("ok", "denied", "failed")])
        async def audit(interaction: discord.Interaction, member: discord.Member | None = None,
                        server: str | None = None, action: str | None = None, result: str | None = None,
                        export: bool = False) -> None:
            await self.audit(interaction, member, server, action, result, export)

        group = app_commands.Group(name="admin", description="Health check and settings backup (admins)",
                                   guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

        @group.command(name="diagnostics", description="Check the connection to AMP and every server")
        async def diagnostics(interaction: discord.Interaction) -> None:
            await self.diagnostics(interaction)

        @group.command(name="export", description="Download Portcullis's settings as a file")
        async def export(interaction: discord.Interaction) -> None:
            await self.export(interaction)

        @group.command(name="import", description="Restore settings from an export file")
        async def import_(interaction: discord.Interaction, file: discord.Attachment) -> None:
            await self.import_(interaction, file)

        for command in (help_, audit, group):
            tree.add_command(command)
