# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/audit``, ``/admin diagnostics|export|import`` and ``/help``."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as dist_version
from typing import Any

import discord
from discord import app_commands

from portcullis.bot.base import (
    BAD,
    OWNER_ONLY_KEYS,
    Module,
    card,
    code,
    duration,
    reply,
    short_error,
    state_text,
    thinking,
    valid_template,
    when,
)
from portcullis.bot.emojis import SET
from portcullis.bot.errors import plain_error
from portcullis.bot.refresh import minutes
from portcullis.bot.v3map import STATUS_LABELS, lookup, search
from portcullis.core.relay import escape
from portcullis.db.features import all_settings
from portcullis.db.repos import ServerRepo

log = logging.getLogger(__name__)


def version(dist: str) -> str:
    try:
        return dist_version(dist)
    except PackageNotFoundError:
        return "unknown"

# Settings that hold console command templates (the keys /setup server writes).
TEMPLATE_KEYS = frozenset({"whitelist_add", "whitelist_remove", "say", "kick", "ban", "unban"})
_SERVER_TEXT = {"display_name": 64, "prefix": 16}  # column -> longest value an import may set


def server_field_ok(key: str, value: object) -> bool:
    """Whether an imported server field has the type its column expects."""
    if value is None:
        return key != "hidden"
    if key == "hidden":
        return isinstance(value, bool) or value in (0, 1)
    if key in _SERVER_TEXT:
        return isinstance(value, str) and len(value) <= _SERVER_TEXT[key] and "\n" not in value
    return isinstance(value, int) and not isinstance(value, bool) and value > 0  # channel and role ids


STARTED = time.monotonic()
HEALTH_CACHE = 30.0  # /health is open to everyone, so AMP is asked at most this often
STATS_DAYS = 30
EXPORT_VERSION = 1
MAX_IMPORT_BYTES = 1_000_000

# (command, who, what) for /help. "who" is everyone, mod or admin.
HELP = [
    ("/players", "everyone", "Who is online on each server"),
    ("/server list", "everyone", "Every server and whether it's online (players: names who's on)"),
    ("/server status", "everyone", "One server's state, who is online and its address"),
    ("/server banner", "everyone", "A status picture of one server or all of them"),
    ("/player lookup", "everyone", "When a player was last seen, and their play time"),
    ("/player top", "everyone", "Who has played the most"),
    ("/player id", "everyone", "Steam ID formats, or a Minecraft name's UUID and back"),
    ("/whitelist request", "everyone", "Ask to be whitelisted on a server"),
    ("/link", "everyone", "Prove which in-game name is yours"),
    ("/notify", "everyone", "Get a DM when a server comes online"),
    ("/event list", "everyone", "Events coming up"),
    ("/health", "everyone", "Is Portcullis working: Discord, AMP, live cards, brand emojis"),
    ("/graph", "everyone", "Players or uptime over a day, week or month (CPU and memory: operators)"),
    ("/help", "everyone", "This list"),
    ("/server power", "mod", "Start, stop, restart, update, or restart with a warning (asks first)"),
    ("/server power-all", "mod", "Start, stop or restart every server, or every server with a tag"),
    ("/server send", "mod", "Send a console command or chat message"),
    ("/server console", "mod", "A server's latest console lines"),
    ("/server diagnostics", "mod", "CPU, memory and disk against their limits, and the last error"),
    ("/backups", "mod", "A server's backups, with Make a backup and (for admins) Restore and Delete"),
    ("/schedule list", "mod", "A server's AMP schedules: Run now, On and Off, run history, Delete (admins)"),
    ("/whitelist add · remove · list", "mod", "Change or see a server's whitelist"),
    ("/player moderate", "mod", "Kick, ban or unban a player"),
    ("/player watch · watchlist", "mod", "Get a quiet note when a player joins"),
    ("/poll create · list", "mod", "Community polls"),
    ("/event create", "mod", "Announce an event with a Going button and a reminder"),
    ("/user (or right-click → Apps → Portcullis user)", "mod", "A member's links and flags"),
    ("/monitor mute", "mod", "Silence one server's alerts for a while"),
    ("/setup start", "admin", "First-run setup: alerts, live status cards, links, emojis"),
    ("/setup relay · server · bot · live", "admin", "Relays, server settings, bot settings, live status cards"),
    ("/setup safety · players", "admin", "Confirmations, blocked-attempt reports, who sees /players"),
    ("/alert create · list", "admin", "Your own alerts: CPU, memory, player count or offline, held for N minutes"),
    ("/monitor alerts · thresholds · weekly · history · test", "admin",
     "Where alerts go (channel, DMs), limits, the weekly summary, graph history"),
    ("/audit", "admin", "Who did what through Portcullis (stats: counts for 30 days)"),
    ("/admin diagnostics · export · import", "admin", "Health check and settings backup"),
    ("/admin bot", "owner", "Bot status, re-sync the commands, or stop the bot"),
]



def _nats_match(grant: list[str], subject: list[str | None]) -> bool:
    """NATS-style match: ``*`` is one token, ``>`` is the rest (one or more). A ``None`` subject token is
    an id we don't know, so any single grant token fits it."""
    for i, token in enumerate(grant):
        if token == ">":
            return len(subject) > i
        if i >= len(subject):
            return False
        if token != "*" and subject[i] is not None and token != subject[i]:
            return False
    return len(grant) == len(subject)


def grant_covers(grant: str, node: str) -> bool:
    """Whether one AMP grant covers a per-server node such as ``power.start`` on some server.

    AMP 3 grants are dotted nodes with NATS wildcards (API_REFERENCE.md, permissions). Server nodes sit
    under ``spire.<id>.shard.<id>.``; a bare node or one under a single shard also counts."""
    parts = grant.split(".")
    tail = node.split(".")
    return (_nats_match(parts, ["spire", None, "shard", None, *tail])
            or _nats_match(parts, ["shard", None, *tail])
            or _nats_match(parts, list(tail)))

async def stop_soon(client: Any) -> None:
    """``/admin bot stop``: give Discord a second to show the reply, grey the live cards, then close.

    Closing makes ``bot.start`` return, and ``__main__`` then closes AMP and the database as for Ctrl+C.
    """
    await asyncio.sleep(1)
    mark = getattr(client, "mark_stopped", None)
    if mark is not None:
        try:
            await asyncio.wait_for(mark(), 3)
        except Exception as exc:  # noqa: BLE001 - cosmetic; the stop goes ahead
            log.debug("could not grey the live cards: %r", exc)
    await client.close()


class AdminModule(Module):
    _health_amp: tuple[float, tuple[str, str]] | None = None

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
        if access.is_owner(interaction.user):
            allowed.add("owner")
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
                    action: str | None = None, result: str | None = None, export: bool = False,
                    stats: bool = False) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "audit.view"):
            return
        shard_id = None
        if server:
            row = await self.server(interaction, server)
            if row is None:
                return
            shard_id = row.shard_id
        if stats:
            await self.audit_stats(interaction, shard_id, server)
            return
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

    async def audit_stats(self, interaction: discord.Interaction, shard_id: str | None, server: str | None) -> None:
        """V3's /audit stats: what happened most in the last 30 days."""
        since = (datetime.now(UTC) - timedelta(days=STATS_DAYS)).isoformat()
        counts = await self.ctx.audit.stats(since=since, shard_id=shard_id)
        if not counts["result"]:
            await reply(interaction, f"Nothing in the audit log for the last {STATS_DAYS} days.")
            return
        total = sum(n for _, n in counts["result"])
        title = f"Audit log, last {STATS_DAYS} days" + (f" on {server}" if shard_id and server else "")
        embed = card(title=title, description=f"{total} action(s) in total.")
        embed.add_field(name="Most common", value="\n".join(f"`{a}` · {n}" for a, n in counts["action"]),
                        inline=False)
        embed.add_field(name="Most active", value="\n".join(
            f"{f'<@{a}>' if a else 'Portcullis itself'} · {n}" for a, n in counts["actor"]), inline=False)
        embed.add_field(name="Results", value=" · ".join(f"{r}: {n}" for r, n in counts["result"]), inline=False)
        await reply(interaction, embed=embed)

    # ---------------------------------------------------------------- /admin bot (owners)

    async def bot_action(self, interaction: discord.Interaction, action: str) -> None:
        """V3's /bot utils ping, status, sync, restart and disconnect, for the bot's owners."""
        node = f"admin.bot.{action}"
        if not self.ctx.access.is_owner(interaction.user):
            await self.ctx.audit.record(node, actor_id=interaction.user.id, result="denied")
            await reply(interaction, "Only the bot's owners (PORTCULLIS_OWNER_IDS) can do that.")
            return
        client = self.ctx.client
        if action == "status":
            await reply(interaction, embed=await self.bot_status())
        elif action == "sync":
            tree = getattr(client, "tree", None)
            if tree is None:
                await reply(interaction, "There's no command tree to sync.")
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            guild = discord.Object(id=self.ctx.guild_id)
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
            await self.ctx.audit.record(node, actor_id=interaction.user.id, target=str(len(synced)))
            await reply(interaction, f"Synced {len(synced)} command(s) to this Discord server.")
        elif action == "stop":
            from portcullis.bot.server import Confirm

            async def stop(confirmed: discord.Interaction) -> None:
                await self.ctx.audit.record(node, actor_id=confirmed.user.id)
                await confirmed.response.edit_message(content="Stopping. If Portcullis runs as a service or in "
                                                              "AMP, it will start again by itself.", view=None)
                log.warning("stopped from Discord by %s", confirmed.user.id)
                if client is not None:
                    self._stopping = asyncio.create_task(stop_soon(client), name="portcullis-admin-stop")

            await reply(interaction, "Stop Portcullis? Relays, alerts and commands stop until it is started "
                                     "again.", view=Confirm(interaction.user.id, stop, "Stop it"))
        else:
            await reply(interaction, "Choose status, sync or stop.")

    async def bot_status(self) -> discord.Embed:
        import platform

        import portcullis_api

        client = self.ctx.client
        embed = card(title="Portcullis")
        embed.add_field(name="Running for", value=duration(time.monotonic() - STARTED))
        if client is not None:
            embed.add_field(name="Discord latency", value=f"{client.latency * 1000:.0f} ms")
        summary = self.ctx.live.summary()
        embed.add_field(name="AMP", value="reachable" if summary.reachable else "not reachable")
        embed.add_field(name="Servers", value=summary.text())
        embed.add_field(name="Versions", value=f"Portcullis {version('portcullis')} · portcullis-api "
                                               f"{portcullis_api.__version__} (AMP spec {portcullis_api.SPEC_BUILD}, "
                                               f"{portcullis_api.SPEC_DATE})"
                                               f" · discord.py {discord.__version__} · Python "
                                               f"{platform.python_version()}", inline=False)
        embed.add_field(name="Database", value=f"schema version {self.ctx.db.version}")
        return embed

    # ---------------------------------------------------------------- /health

    async def health(self, interaction: discord.Interaction) -> None:
        if not await self.in_guild(interaction):
            return
        await thinking(interaction)
        await reply(interaction, embed=await self.health_embed())

    async def health_embed(self) -> discord.Embed:
        """One line per part of Portcullis, each with a mark and a word, worst first decides the colour."""
        checks: list[tuple[str, str, str]] = []  # (mark, part, detail)
        now = time.monotonic()
        cached = self._health_amp
        if cached is None or now - cached[0] > HEALTH_CACHE:
            started = time.perf_counter()
            try:
                servers = await asyncio.wait_for(self.ctx.port.list_servers(), 5)
                amp = ("ok", f"answering, {len(servers)} server(s), {(time.perf_counter() - started) * 1000:.0f} ms")
            except Exception as exc:  # noqa: BLE001
                amp = ("bad", plain_error(exc))
            cached = self._health_amp = (now, amp)
        checks.append((cached[1][0], "AMP", cached[1][1]))
        client = self.ctx.client
        latency = getattr(client, "latency", float("nan")) if client is not None else float("nan")
        if latency == latency and latency != float("inf"):  # not NaN: connected
            checks.append(("ok" if latency < 0.5 else "warn", "Discord", f"connected, {latency * 1000:.0f} ms"))
        else:
            checks.append(("warn", "Discord", "still connecting"))
        checks.append(("ok", "Servers", self.ctx.live.summary().text()))
        cards = self.ctx.live_cards
        if cards is not None:
            placed = len(await cards.rows())
            pacer = cards.pacer
            if not placed:
                checks.append(("off", "Live status cards", "none yet (`/setup start` places them)"))
            elif pacer.backing_off:
                checks.append(("warn", "Live status cards", f"{placed} card(s), paused after {pacer.last_problem}; "
                                                             f"next try {minutes(pacer.delay())} from now"))
            else:
                checks.append(("ok", "Live status cards", f"{placed} card(s), every {minutes(pacer.every)}"))
        emojis = self.ctx.emojis
        found = len(SET) - len(emojis.missing())
        if emojis.error:
            checks.append(("warn", "Brand emojis", "couldn't read them from Discord, plain emojis in use"))
        elif found == len(SET):
            checks.append(("ok", "Brand emojis", f"all {found} found"))
        else:
            checks.append(("off", "Brand emojis", f"{found} of {len(SET)} uploaded, plain emojis for the rest "
                                                  "(docs/EMOJIS.md)"))
        checks.append(("ok", "Database", f"schema version {self.ctx.db.version}"))
        marks = {"ok": "✅", "warn": "⚠️", "bad": "❌", "off": "➖"}
        worst = "bad" if any(c[0] == "bad" for c in checks) else "warn" if any(c[0] == "warn" for c in checks) \
            else "good"
        embed = card(title="Health", kind=worst,
                     description="\n".join(f"{marks[m]} **{part}** · {detail}" for m, part, detail in checks))
        embed.set_footer(text=f"Running for {duration(now - STARTED)} · details for admins: /admin diagnostics")
        return embed

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
            embed.add_field(name="AMP", value=f"❌ {plain_error(exc)}\n{code(short_error(exc))}", inline=False)
            embed.colour = discord.Colour(BAD)
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
        try:
            expires = await asyncio.wait_for(self.ctx.port.preview_expires_at(), 8)
        except Exception:  # noqa: BLE001 - not every build has the route; the field is a courtesy
            expires = None
        if expires is not None:
            embed.add_field(name="AMP preview build", value=f"stops working {discord.utils.format_dt(expires, 'R')}")
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
        missing = [w for w in wanted if not any(grant_covers(g, w) for g in grants)]
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
            bad = sorted(k for k, v in clean.items() if not server_field_ok(k, v))
            for key in bad:
                del clean[key]
                problems.append(f"{shard_id}: skipped {key} (not a valid value)")
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
                if key in TEMPLATE_KEYS and not valid_template(value):
                    # These are sent to the game's console, so a file gets the same check as the /setup form.
                    problems.append(f"skipped {key} ({scope}): not a one-line command of up to 200 characters")
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
        await thinking(interaction)  # the download and the writes can take longer than Discord's 3 seconds
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

        @app_commands.command(name="health", description="Is Portcullis working? Discord, AMP, live cards, emojis")
        @app_commands.guild_only()
        async def health(interaction: discord.Interaction) -> None:
            await self.health(interaction)

        @app_commands.command(name="audit", description="Who did what through Portcullis (admins)")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.describe(member="Only this member", server="Only this server",
                               action="Only actions starting with this, e.g. server.power",
                               result="Only this result", export="Send everything that matches as a JSON file",
                               stats="Counts for the last 30 days instead of a list")
        @app_commands.autocomplete(server=self.autocomplete_server)
        @app_commands.choices(result=[app_commands.Choice(name=r, value=r) for r in ("ok", "denied", "failed")])
        async def audit(interaction: discord.Interaction, member: discord.Member | None = None,
                        server: str | None = None, action: str | None = None, result: str | None = None,
                        export: bool = False, stats: bool = False) -> None:
            await self.audit(interaction, member, server, action, result, export, stats)

        group = app_commands.Group(name="admin", description="Health check and settings backup (admins)",
                                   guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

        @group.command(name="bot", description="Bot status, re-sync the commands, or stop the bot (owners)")
        @app_commands.choices(action=[app_commands.Choice(name="Status", value="status"),
                                      app_commands.Choice(name="Sync commands", value="sync"),
                                      app_commands.Choice(name="Stop", value="stop")])
        async def bot_(interaction: discord.Interaction, action: str) -> None:
            await self.bot_action(interaction, action)

        @group.command(name="diagnostics", description="Check the connection to AMP and every server")
        async def diagnostics(interaction: discord.Interaction) -> None:
            await self.diagnostics(interaction)

        @group.command(name="export", description="Download Portcullis's settings as a file")
        async def export(interaction: discord.Interaction) -> None:
            await self.export(interaction)

        @group.command(name="import", description="Restore settings from an export file")
        async def import_(interaction: discord.Interaction, file: discord.Attachment) -> None:
            await self.import_(interaction, file)

        for command in (help_, health, audit, group):
            tree.add_command(command)
