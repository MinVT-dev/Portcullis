# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/server list|status|power|send|console`` and ``/backups``."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from portcullis.assets import game_art
from portcullis.bot.backups import BackupView
from portcullis.bot.banners import MAX_CARDS, THEMES, card_from, render_banner
from portcullis.bot.base import (
    NONE,
    Ctx,
    Module,
    card,
    code,
    fill,
    reply,
    short_error,
    state_text,
    valid_line,
    valid_template,
)
from portcullis.core.console_rules import DENY_KEY, HIDE_KEY, blocked_word, hider, stored_denylist, stored_rules
from portcullis.core.relay import console_safe, escape
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
    ServerState.AWAITING_LICENCE: 0xE67E22,
    ServerState.UNKNOWN: 0x7F8C8D,
}
POWER_ACTIONS = (PowerAction.START, PowerAction.STOP, PowerAction.RESTART)
# AMP has routes for these but none has been run on a real panel yet, so they're single-server only and
# update and sleep always ask first.
UNVERIFIED_ACTIONS = {PowerAction.UPDATE: "Update the game (unverified)", PowerAction.SLEEP: "Sleep (unverified)",
                      PowerAction.WAKE: "Wake from sleep (unverified)"}
ASK_FIRST = {PowerAction.UPDATE: "Update it", PowerAction.SLEEP: "Put it to sleep"}
# Actions that take the server down on purpose, so the offline alert stays quiet.
GOES_DOWN = (PowerAction.STOP, PowerAction.RESTART, PowerAction.UPDATE, PowerAction.SLEEP)
VERBS = {"start": "Starting", "stop": "Stopping", "restart": "Restarting", "update": "Updating",
         "sleep": "Putting to sleep", "wake": "Waking"}
STATUS_TIMEOUT = 8.0
QUICK_TIMEOUT = 2.0  # for an AMP read that happens before the first reply: Discord allows 3 seconds in all
SAY_KEY = "say"  # per-server setting: console command template for in-game chat, e.g. "say {message}"
QUICK_KEY = "quick_commands"  # per-server setting: [{"label": "Save world", "command": "save"}, ...]
QUICK_LIMIT = 5
CONSOLE_DEFAULT, CONSOLE_MAX = 20, 50
CONSOLE_CHARS = 1800  # leaves room for the heading and code fences in Discord's 2000-character limit
CONNECT_KEY = "connect_address"  # per-server setting: the address players type, e.g. play.example.com:27015
START_KEY = "members_can_start"  # per-server setting: any member may start it (never stop it)
START_COOLDOWN = 120.0  # seconds between member starts of one server
WARNED = "restart_warned"
# Warnings as (seconds before the restart, text for players).
WARN_STEPS = ((300, "5 minutes"), (60, "1 minute"))
WARN_TEXT = "Server restart in {when}."
_ADDRESS = re.compile(r"^[A-Za-z0-9.\-]{1,253}(:\d{1,5})?$")


def valid_address(text: str) -> bool:
    return bool(_ADDRESS.match(text))


def quick_commands(value: object) -> list[dict[str, str]]:
    """The stored quick commands, ignoring anything malformed."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:QUICK_LIMIT]:
        if isinstance(item, dict) and isinstance(item.get("label"), str) and item["label"].strip() \
                and valid_template(item.get("command")):  # the list can come from /admin import
            out.append({"label": item["label"][:40], "command": item["command"]})
    return out


def console_block(lines: list[str]) -> str:
    """The newest lines that fit in one message, oldest first, inside a code block."""
    kept: list[str] = []
    size = 0
    for text in reversed(lines):
        safe = console_safe(text)
        if size + len(safe) + 1 > CONSOLE_CHARS:
            break
        kept.append(safe)
        size += len(safe) + 1
    return "```\n" + "\n".join(reversed(kept)) + "\n```"


def status_embed(row: ServerRow, status: ServerStatus, address: str | None = None) -> discord.Embed:
    embed = card(title=row.name, colour=STATE_COLOURS.get(status.state, 0x7F8C8D))
    embed.add_field(name="State", value=state_text(status.state))
    count = f"{status.player_count}" + (f" / {status.max_players}" if status.max_players else "")
    embed.add_field(name="Players", value=count)
    if status.cpu_percent is not None:
        embed.add_field(name="CPU", value=f"{status.cpu_percent:.0f}%")
    if status.memory_mb is not None:
        limit = f" / {status.memory_limit_mb} MB" if status.memory_limit_mb else " MB"
        embed.add_field(name="Memory", value=f"{status.memory_mb}{limit}")
    if status.started_at is not None and status.state is ServerState.RUNNING:
        embed.add_field(name="Up since", value=discord.utils.format_dt(status.started_at, "R"))
    if status.players:
        names = ", ".join(escape(p.name) for p in status.players)
        embed.add_field(name="Online now", value=names if len(names) <= 1000 else names[:999] + "…", inline=False)
    if address:
        embed.add_field(name="Address", value=code(address))
    if status.message:
        embed.add_field(name="Message", value=escape(status.message)[:1000], inline=False)
    art = game_art(row.template, name=row.amp_name)
    if art.thumbnail_url:
        embed.set_thumbnail(url=art.thumbnail_url)
    embed.set_footer(text=f"{art.name} · server id {row.shard_id}")
    return embed


class PowerButtons(discord.ui.View):
    """Start/Stop/Restart under a status reply, plus the server's quick commands. Moderators only."""

    def __init__(self, module: ServerModule, row: ServerRow, quick: list[dict[str, str]] | None = None, *,
                 start_only: bool = False) -> None:
        super().__init__(timeout=600)
        self.module = module
        self.row = row
        buttons = ((PowerAction.START, discord.ButtonStyle.success),
                   (PowerAction.STOP, discord.ButtonStyle.danger),
                   (PowerAction.RESTART, discord.ButtonStyle.secondary))
        for action, style in buttons[:1] if start_only else buttons:
            button: discord.ui.Button[PowerButtons] = discord.ui.Button(label=action.value.capitalize(), style=style)
            button.callback = self._handler(action)  # type: ignore[method-assign]
            self.add_item(button)
        for item in quick or []:
            quick_button: discord.ui.Button[PowerButtons] = discord.ui.Button(
                label=item["label"][:80], style=discord.ButtonStyle.primary, row=1)
            quick_button.callback = self._quick(item)  # type: ignore[method-assign]
            self.add_item(quick_button)

    def _quick(self, item: dict[str, str]):  # noqa: ANN202
        async def press(interaction: discord.Interaction) -> None:
            await self.module.run_quick(interaction, self.row, item)
        return press

    def _handler(self, action: PowerAction):  # noqa: ANN202
        async def press(interaction: discord.Interaction) -> None:
            await self.module.do_power(interaction, self.row, action)
        return press


class Confirm(discord.ui.View):
    """Yes/No under an ephemeral question. Only the person who asked can answer."""

    def __init__(self, owner_id: int, on_yes: Callable[[discord.Interaction], Awaitable[None]],
                 yes_label: str) -> None:
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.on_yes = on_yes
        yes: discord.ui.Button[Confirm] = discord.ui.Button(label=yes_label, style=discord.ButtonStyle.danger)
        yes.callback = self._yes  # type: ignore[method-assign]
        no: discord.ui.Button[Confirm] = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        no.callback = self._no  # type: ignore[method-assign]
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    async def _yes(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self.on_yes(interaction)

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was changed.", view=None)


class LicenceButtons(discord.ui.View):
    """Under the status of a server waiting for its game licence (EULA): accept (admins, after a
    confirmation) or decline (admins and anyone who may power the server)."""

    def __init__(self, module: ServerModule, row: ServerRow, *, can_accept: bool, can_decline: bool) -> None:
        super().__init__(timeout=600)
        self.module = module
        self.row = row
        if can_accept:
            accept: discord.ui.Button[LicenceButtons] = discord.ui.Button(
                label="Accept the licence…", style=discord.ButtonStyle.primary)
            accept.callback = self._press(True)  # type: ignore[method-assign]
            self.add_item(accept)
        if can_decline:
            decline: discord.ui.Button[LicenceButtons] = discord.ui.Button(
                label="Decline", style=discord.ButtonStyle.secondary)
            decline.callback = self._press(False)  # type: ignore[method-assign]
            self.add_item(decline)

    def _press(self, accept: bool):  # noqa: ANN202
        async def press(interaction: discord.Interaction) -> None:
            await self.module.answer_licence(interaction, self.row, accept)
        return press


class CancelRestart(discord.ui.View):
    def __init__(self, module: ServerModule, row: ServerRow) -> None:
        super().__init__(timeout=max(s for s, _ in WARN_STEPS) + 120)
        self.module = module
        self.row = row

    @discord.ui.button(label="Cancel the restart", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button[CancelRestart]) -> None:
        await self.module.cancel_restart(interaction, self.row)


class ServerModule(Module):
    def __init__(self, ctx: Ctx) -> None:
        super().__init__(ctx)
        self.restarts: dict[str, asyncio.Task[None]] = {}
        self.member_starts: dict[str, float] = {}
        self.restart_pending: set[str] = set()  # warned restarts between "asked" and "counting down"
        self.sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def players_online(self, row: ServerRow, timeout: float = STATUS_TIMEOUT) -> int | None:
        """How many players are on, or None if AMP couldn't say (in time)."""
        try:
            status = await asyncio.wait_for(self.ctx.port.get_status(row.shard_id), timeout)
        except Exception:  # noqa: BLE001
            return None
        return status.player_count if status.state is ServerState.RUNNING else 0

    async def server_list(self, interaction: discord.Interaction, players: bool = False) -> None:
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
            running = status.state is ServerState.RUNNING
            count = f" · {status.player_count} online" if running else ""
            line = f"{state_text(status.state)} · **{escape(row.name)}**{count}"
            if players and running and status.players:
                line += "\n　" + ", ".join(escape(p.name) for p in status.players)
            return line

        lines = await asyncio.gather(*(one(r) for r in rows))
        await reply(interaction, embed=card(title="Servers", description="\n".join(lines)[:4000]))

    async def server_banner(self, interaction: discord.Interaction, server: str | None = None, theme: str = "dark",
                            public: bool = False) -> None:
        """A status image of one server, or of every server the caller can see."""
        if not await self.in_guild(interaction):
            return
        if server:
            row = await self.server(interaction, server)
            if row is None:
                return
            rows = [row]
        else:
            rows = await self.ctx.servers.list(include_hidden=False)
        note = None
        if public and not await self.ctx.allowed(interaction.user, "server.banner",
                                                 rows[0].shard_id if server else None):
            public, note = False, "Only operators can post the banner for everyone, so here it is just for you."
        await interaction.response.defer(ephemeral=not public, thinking=True)

        async def one(row: ServerRow) -> ServerStatus:
            try:
                return await asyncio.wait_for(self.ctx.port.get_status(row.shard_id), STATUS_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - shown as Unknown on the banner
                log.warning("status for %s failed: %s", row.name, exc)
                return ServerStatus(id=row.shard_id, state=ServerState.UNKNOWN)

        shown = rows[:MAX_CARDS]
        statuses = await asyncio.gather(*(one(r) for r in shown))
        addresses = [await self.ctx.settings.get(CONNECT_KEY, scope=r.shard_id) for r in shown]
        cards = [card_from(r.name, s, a if isinstance(a, str) else None) for r, s, a in zip(shown, statuses, addresses,
                                                                                             strict=True)]
        png = await asyncio.to_thread(render_banner, cards, theme=theme if theme in THEMES else "dark")
        if len(rows) > MAX_CARDS:
            extra = f"Showing the first {MAX_CARDS} of {len(rows)} servers."
            note = f"{note} {extra}" if note else extra
        await reply(interaction, note, ephemeral=not public, file=discord.File(io.BytesIO(png), "servers.png"))

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
        extra = {}
        user = interaction.user
        can_power = await self.ctx.allowed(user, "server.power", row.shard_id)
        can_console = await self.ctx.allowed(user, "server.quick", row.shard_id)
        quick = quick_commands(await self.ctx.settings.get(QUICK_KEY, scope=row.shard_id)) if can_console else []
        if status.state is ServerState.AWAITING_LICENCE:
            can_accept = self.ctx.access.is_admin(user)
            if can_accept or can_power:
                extra["view"] = LicenceButtons(self, row, can_accept=can_accept, can_decline=True)
        elif can_power or quick:
            extra["view"] = PowerButtons(self, row, quick)
        elif status.state is ServerState.STOPPED and await self.ctx.settings.get(START_KEY, False, scope=row.shard_id):
            extra["view"] = PowerButtons(self, row, start_only=True)
        address = await self.ctx.settings.get(CONNECT_KEY, scope=row.shard_id)
        await reply(interaction, embed=status_embed(row, status, address), **extra)

    async def server_power(self, interaction: discord.Interaction, server: str, action: str) -> None:
        if not await self.in_guild(interaction):
            return
        power = next((a for a in (*POWER_ACTIONS, *UNVERIFIED_ACTIONS) if a.value == action), None)
        if power is None and action != WARNED:
            await reply(interaction, "Choose one of the actions in the list.")
            return
        row = await self.server(interaction, server)
        if row is None:
            return
        if power is None:
            await self.warned_restart(interaction, row)
        else:
            await self.do_power(interaction, row, power)

    async def do_power(self, interaction: discord.Interaction, row: ServerRow, power: PowerAction,
                       confirmed: bool = False) -> None:
        node = f"server.power.{power.value}"
        on_demand = False
        if not await self.ctx.allowed(interaction.user, node, row.shard_id):
            if power is PowerAction.START and await self.ctx.settings.get(START_KEY, False, scope=row.shard_id):
                on_demand = True
            elif not await self.require_mod(interaction, node, row.shard_id):
                return
        if on_demand:
            last = self.member_starts.get(row.shard_id)
            if last is not None and time.monotonic() - last < START_COOLDOWN:
                await reply(interaction, f"Someone started {escape(row.name)} a moment ago. Give it a minute.")
                return
            self.member_starts[row.shard_id] = time.monotonic()
        if power is PowerAction.STOP and not confirmed:
            online = await self.players_online(row, QUICK_TIMEOUT)
            if online != 0:
                who = "someone may be playing (AMP didn't say)" if online is None else \
                    f"{online} player{'s are' if online != 1 else ' is'} online"
                await reply(interaction, f"Stop **{escape(row.name)}**? {who}.",
                            view=Confirm(interaction.user.id,
                                         lambda i: self.do_power(i, row, power, confirmed=True), "Stop it"))
                return
        if power in ASK_FIRST and not confirmed:
            await reply(interaction, f"{UNVERIFIED_ACTIONS[power].split(' (')[0]}: **{escape(row.name)}**? "
                                     "Portcullis hasn't seen this work on a real panel yet.",
                        view=Confirm(interaction.user.id,
                                     lambda i: self.do_power(i, row, power, confirmed=True), ASK_FIRST[power]))
            return
        await interaction.response.defer(thinking=True)  # public, so everyone sees who did it
        if power in GOES_DOWN:
            self.ctx.expect_offline(row.shard_id)
        try:
            await self.ctx.port.power(row.shard_id, power)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s on %s failed: %s", node, row.name, exc)
            await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, result="failed",
                                        detail={"error": short_error(exc)})
            await reply(interaction, f"AMP didn't {power.value} {escape(row.name)}: {escape(short_error(exc))}",
                        ephemeral=False)
            return
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id,
                                    detail={"member_start": True} if on_demand else None)
        await reply(interaction, f"{VERBS[power.value]} **{escape(row.name)}** (asked by {interaction.user.mention}).",
                    ephemeral=False)

    # ---------------------------------------------------------------- game licence (EULA)

    async def answer_licence(self, interaction: discord.Interaction, row: ServerRow, accept: bool,
                             confirmed: bool = False) -> None:
        """Answer AMP's licence prompt. Accepting is an act on the server owner's behalf that AMP
        records in the game's settings, so it needs Manage Server and an explicit confirmation."""
        node = "server.licence.accept" if accept else "server.licence.decline"
        if accept:
            if not await self.require_admin(interaction, node):
                return
        elif not self.ctx.access.is_admin(interaction.user) and \
                not await self.require_mod(interaction, node, row.shard_id):
            return
        if accept and not confirmed:
            game = game_art(row.template, name=row.amp_name).name
            await reply(interaction,
                        f"Accept the licence (EULA) of **{escape(game)}** for **{escape(row.name)}**? Read the "
                        "game's licence first: you accept it for whoever runs this server. AMP records the "
                        "acceptance in the game's own settings under the bot's account, and Portcullis records "
                        "that you asked. The server is then stopped; start it when you're ready.",
                        view=Confirm(interaction.user.id,
                                     lambda i: self.answer_licence(i, row, True, confirmed=True), "I accept it"))
            return
        await interaction.response.defer(thinking=True)  # public, so everyone sees who answered
        try:
            await self.ctx.port.answer_licence(row.shard_id, accept)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s on %s failed: %s", node, row.name, exc)
            await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, result="failed",
                                        detail={"error": short_error(exc)})
            await reply(interaction, f"AMP didn't take the answer for {escape(row.name)}: "
                                     f"{escape(short_error(exc))}", ephemeral=False)
            return
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id)
        done = "accepted the game licence for" if accept else "declined the game licence for"
        after = " It is stopped now; start it with `/server power`." if accept else \
            " It stays stopped, and the next start asks again."
        await reply(interaction, f"{interaction.user.mention} {done} **{escape(row.name)}**.{after}",
                    ephemeral=False)

    # ---------------------------------------------------------------- restart with warning

    async def warned_restart(self, interaction: discord.Interaction, row: ServerRow) -> None:
        node = "server.power.restart"
        if not await self.require_mod(interaction, node, row.shard_id):
            return
        template = await self.ctx.settings.get(SAY_KEY, scope=row.shard_id)
        if not template:
            await reply(interaction, f"{escape(row.name)} has no chat command, so players can't be warned. An admin "
                                     "can add one in `/setup server` (for example `say {message}`), or use a "
                                     "plain restart.")
            return
        task = self.restarts.get(row.shard_id)
        if (task is not None and not task.done()) or row.shard_id in self.restart_pending:
            await reply(interaction, f"A restart of {escape(row.name)} is already counting down.")
            return
        # Claimed before the first await below, so two members asking at once can't start two countdowns.
        self.restart_pending.add(row.shard_id)
        try:
            if await self.players_online(row, QUICK_TIMEOUT) == 0:
                await self.do_power(interaction, row, PowerAction.RESTART)
                return
            await self.ctx.audit.record("server.power.restart.warned", actor_id=interaction.user.id,
                                        shard_id=row.shard_id)
            first = WARN_STEPS[0][1]
            await reply(interaction, f"Restarting **{escape(row.name)}** in {first} (asked by "
                                     f"{interaction.user.mention}). Players get a warning in game chat.",
                        ephemeral=False, view=CancelRestart(self, row))
            self.restarts[row.shard_id] = asyncio.create_task(self._count_down(interaction, row, template),
                                                              name=f"portcullis-restart-{row.shard_id[:8]}")
        finally:
            self.restart_pending.discard(row.shard_id)

    async def _count_down(self, interaction: discord.Interaction, row: ServerRow, template: str) -> None:
        try:
            for index, (seconds, label) in enumerate(WARN_STEPS):
                try:
                    await self.ctx.port.send_console(row.shard_id, fill(template, message=WARN_TEXT.format(when=label)))
                except Exception as exc:  # noqa: BLE001 - a missed warning shouldn't stop the restart
                    log.warning("restart warning on %s failed: %s", row.name, exc)
                following = WARN_STEPS[index + 1][0] if index + 1 < len(WARN_STEPS) else 0
                await self.sleep(seconds - following)
            self.ctx.expect_offline(row.shard_id)
            try:
                await self.ctx.port.power(row.shard_id, PowerAction.RESTART)
            except Exception as exc:  # noqa: BLE001
                await self.ctx.audit.record("server.power.restart", actor_id=interaction.user.id,
                                            shard_id=row.shard_id, result="failed", detail={"error": short_error(exc)})
                await self._update(interaction, f"AMP didn't restart **{escape(row.name)}**: "
                                                f"{escape(short_error(exc))}")
                return
            await self.ctx.audit.record("server.power.restart", actor_id=interaction.user.id, shard_id=row.shard_id)
            await self._update(interaction, f"Restarted **{escape(row.name)}** after warning players "
                                            f"(asked by {interaction.user.mention}).")
        finally:
            if self.restarts.get(row.shard_id) is asyncio.current_task():  # never a newer countdown's entry
                self.restarts.pop(row.shard_id, None)

    @staticmethod
    async def _update(interaction: discord.Interaction, text: str) -> None:
        try:
            await interaction.edit_original_response(content=text, view=None, allowed_mentions=NONE)
        except Exception as exc:  # noqa: BLE001 - the message may be gone; the restart still happened
            log.info("could not update the restart message: %s", exc)

    async def cancel_restart(self, interaction: discord.Interaction, row: ServerRow) -> None:
        if not await self.require_mod(interaction, "server.power.restart", row.shard_id):
            return
        task = self.restarts.pop(row.shard_id, None)
        if task is None or task.done():
            await reply(interaction, "That restart already happened or was cancelled.")
            return
        task.cancel()
        await self.ctx.audit.record("server.power.restart.cancelled", actor_id=interaction.user.id,
                                    shard_id=row.shard_id)
        # Answer the button first: Discord gives 3 seconds, and AMP may take longer than that.
        await interaction.response.edit_message(
            content=f"Restart of **{escape(row.name)}** cancelled by {interaction.user.mention}.", view=None,
            allowed_mentions=NONE)
        template = await self.ctx.settings.get(SAY_KEY, scope=row.shard_id)
        if template:
            try:
                await self.ctx.port.send_console(row.shard_id, fill(template, message="The restart is cancelled."))
            except Exception as exc:  # noqa: BLE001
                log.info("could not tell players the restart is off: %s", exc)

    # ---------------------------------------------------------------- power all

    async def power_all(self, interaction: discord.Interaction, action: str, tag: str | None = None) -> None:
        if not await self.in_guild(interaction):
            return
        power = next((a for a in POWER_ACTIONS if a.value == action), None)
        if power is None:
            await reply(interaction, "Choose start, stop or restart.")
            return
        node = f"server.power.{power.value}"
        wanted = tag.strip().lower() if tag else None
        targets = []
        for row in await self.ctx.servers.list():
            if wanted and wanted not in (t.lower() for t in row.tags):
                continue
            if not await self.ctx.allowed(interaction.user, node, row.shard_id):
                continue
            if row.hidden and not await self.ctx.sees_hidden(interaction.user, row.shard_id):
                continue  # a role granted "power" everywhere still doesn't get to see hidden servers
            state = self.ctx.live.states.get(row.shard_id, ServerState.UNKNOWN)
            if (power is PowerAction.START and state is ServerState.RUNNING) or \
                    (power is not PowerAction.START and state is ServerState.STOPPED):
                continue
            targets.append(row)
        if not targets:
            await self.ctx.audit.record(f"{node}.all", actor_id=interaction.user.id, result="denied",
                                        target=wanted)
            where = f" tagged {escape(wanted)}" if wanted else ""
            await reply(interaction, f"No servers{where} to {power.value} that you're allowed to.")
            return
        players = sum(len(self.ctx.live.players.get(r.shard_id, ())) for r in targets)
        names = ", ".join(f"**{escape(r.name)}**" for r in targets)
        online = f" {players} player{'s are' if players != 1 else ' is'} online." if players else ""
        await reply(interaction, f"{power.value.capitalize()} {len(targets)} server(s): {names}?{online}",
                    view=Confirm(interaction.user.id, lambda i: self._power_many(i, targets, power),
                                 f"{power.value.capitalize()} {len(targets)}"))

    async def _power_many(self, interaction: discord.Interaction, rows: list[ServerRow], power: PowerAction) -> None:
        await interaction.response.edit_message(content=f"Working on {len(rows)} server(s)…", view=None)
        node = f"server.power.{power.value}"
        done, failed = [], []
        for row in rows:
            if power in (PowerAction.STOP, PowerAction.RESTART):
                self.ctx.expect_offline(row.shard_id)
            try:
                await self.ctx.port.power(row.shard_id, power)
            except Exception as exc:  # noqa: BLE001 - keep going; report the failures together
                failed.append(f"{escape(row.name)} ({escape(short_error(exc))[:100]})")
                await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id,
                                            result="failed", detail={"error": short_error(exc), "all": True})
                continue
            done.append(f"**{escape(row.name)}**")
            await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id,
                                        detail={"all": True})
        text = f"{VERBS[power.value]} {', '.join(done) or 'nothing'} (asked by {interaction.user.mention})."
        if failed:
            text += "\nAMP refused: " + "; ".join(failed)
        await interaction.followup.send(text[:2000], allowed_mentions=NONE)

    async def close(self) -> None:
        for task in list(self.restarts.values()):
            task.cancel()
        await asyncio.gather(*self.restarts.values(), return_exceptions=True)
        self.restarts.clear()

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
            word = blocked_word(text, stored_denylist(await self.ctx.settings.get(DENY_KEY, scope=row.shard_id)))
            if word and not self.ctx.access.is_owner(interaction.user):
                await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id,
                                            target=word, result="denied")
                await reply(interaction, f"`{word}` is blocked on {escape(row.name)}. An admin can change the "
                                         "blocked commands in `/setup server` → Console rules.")
                return
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

    async def run_quick(self, interaction: discord.Interaction, row: ServerRow, item: dict[str, str]) -> None:
        """One of the admin-defined quick commands, pressed under ``/server status``."""
        node = "server.quick"
        if not await self.require_mod(interaction, node, row.shard_id):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.ctx.port.send_console(row.shard_id, item["command"])
        except Exception as exc:  # noqa: BLE001
            await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id,
                                        target=item["label"][:40], result="failed",
                                        detail={"error": short_error(exc)})
            await reply(interaction, f"AMP didn't take {escape(item['label'])}: {escape(short_error(exc))}")
            return
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id,
                                    target=item["label"][:40])
        await reply(interaction, f"Ran **{escape(item['label'])}** on **{escape(row.name)}**.")

    async def server_console(self, interaction: discord.Interaction, server: str,
                             lines: int = CONSOLE_DEFAULT) -> None:
        """The last console lines from AMP's own history, so a relay channel isn't needed to peek."""
        if not await self.in_guild(interaction):
            return
        row = await self.server(interaction, server)
        if row is None or not await self.require_mod(interaction, "server.console.read", row.shard_id):
            return
        lines = max(1, min(lines, CONSOLE_MAX))
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            history = await asyncio.wait_for(self.ctx.port.recent_console(row.shard_id, limit=lines), STATUS_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            await reply(interaction, f"Couldn't read the console of {escape(row.name)}: {escape(short_error(exc))}")
            return
        hide = hider(stored_rules(await self.ctx.settings.get(HIDE_KEY, scope=row.shard_id)))
        history = [t for t in history if t.strip() and not (hide and hide(t))]
        if not history:
            await reply(interaction, f"{escape(row.name)} has no recent console output.")
            return
        await self.ctx.audit.record("server.console.read", actor_id=interaction.user.id, shard_id=row.shard_id)
        await reply(interaction, f"Last console lines of **{escape(row.name)}**:\n{console_block(history)}")

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
        embed = card(title=f"Backups of {row.name}", description="\n".join(lines) + more)
        embed.set_footer(text="Making, restoring and deleting are not yet seen working on a real panel.")
        can_make = await self.ctx.allowed(interaction.user, "backup.create", row.shard_id)
        can_restore = self.ctx.access.is_admin(interaction.user) and \
            await self.ctx.allowed(interaction.user, "backup.restore", row.shard_id)
        can_delete = self.ctx.access.is_admin(interaction.user) and \
            await self.ctx.allowed(interaction.user, "backup.delete", row.shard_id)
        view = BackupView(self, row, items, can_make=can_make, can_restore=can_restore, can_delete=can_delete) \
            if can_make or can_restore or can_delete else None
        await reply(interaction, embed=embed, **({"view": view} if view is not None else {}))

    def backups_make(self, row: ServerRow):  # noqa: ANN201
        async def make(interaction: discord.Interaction) -> None:
            name = f"Discord {time.strftime('%Y-%m-%d %H:%M', time.gmtime())}"
            await self._backup_job(interaction, row, "backup.create", f"Making a backup of {escape(row.name)}.",
                                   self.ctx.port.create_backup(row.shard_id, name), {"name": name})
        return make

    def backups_restore(self, row: ServerRow, backup_id: str, name: str):  # noqa: ANN201
        async def restore(interaction: discord.Interaction) -> None:
            if not self.ctx.access.is_admin(interaction.user):
                await reply(interaction, "Restoring needs the Manage Server permission.")
                return
            self.ctx.expect_offline(row.shard_id)
            await self._backup_job(interaction, row, "backup.restore",
                                   f"Restoring {escape(row.name)} to **{escape(name)}**.",
                                   self.ctx.port.restore_backup(row.shard_id, backup_id), {"backup": name})
        return restore

    def backups_delete(self, row: ServerRow, backup_id: str, name: str):  # noqa: ANN201
        async def delete(interaction: discord.Interaction) -> None:
            if not self.ctx.access.is_admin(interaction.user):
                await reply(interaction, "Deleting backups needs the Manage Server permission.")
                return
            await self._backup_job(interaction, row, "backup.delete",
                                   f"Deleting the backup **{escape(name)}** of {escape(row.name)}.",
                                   self.ctx.port.delete_backup(row.shard_id, backup_id), {"backup": name})
        return delete

    async def _backup_job(self, interaction: discord.Interaction, row: ServerRow, node: str, done: str,
                          call: Awaitable[None], detail: dict[str, str]) -> None:
        await interaction.response.defer(thinking=True)
        try:
            await call
        except Exception as exc:  # noqa: BLE001
            log.warning("%s on %s failed: %s", node, row.name, exc)
            await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, result="failed",
                                        detail={**detail, "error": short_error(exc)})
            await reply(interaction, f"AMP didn't do that for {escape(row.name)}: {escape(short_error(exc))}",
                        ephemeral=False)
            return
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, detail=detail)
        await reply(interaction, f"{done} Asked by {interaction.user.mention}. AMP finishes it in the "
                                 "background; check `/backups` in a minute.", ephemeral=False)

    def register(self, tree: app_commands.CommandTree) -> None:
        group = app_commands.Group(name="server", description="Game servers", guild_only=True)
        auto = self.autocomplete_server

        @group.command(name="list", description="List the game servers and whether they are online")
        @app_commands.describe(players="Also name everyone who is online")
        async def server_list(interaction: discord.Interaction, players: bool = False) -> None:
            await self.server_list(interaction, players)

        @group.command(name="status", description="Show one server's state and who is online")
        @app_commands.describe(server="The game server")
        @app_commands.autocomplete(server=auto)
        async def server_status(interaction: discord.Interaction, server: str) -> None:
            await self.server_status(interaction, server)

        @group.command(name="banner", description="A status picture of one server or all of them")
        @app_commands.describe(server="One game server (leave out for all)", theme="Dark or light",
                               public="Post it for everyone in this channel (operators)")
        @app_commands.autocomplete(server=auto)
        @app_commands.choices(theme=[app_commands.Choice(name="Dark", value="dark"),
                                     app_commands.Choice(name="Light", value="light")])
        async def server_banner(interaction: discord.Interaction, server: str | None = None, theme: str = "dark",
                                public: bool = False) -> None:
            await self.server_banner(interaction, server, theme, public)

        @group.command(name="power", description="Start, stop or restart a server (operators)")
        @app_commands.describe(server="The game server", action="What to do")
        @app_commands.autocomplete(server=auto)
        @app_commands.choices(action=[app_commands.Choice(name=a.value.capitalize(), value=a.value)
                                      for a in POWER_ACTIONS] +
                                     [app_commands.Choice(name="Restart with a warning in game chat", value=WARNED)] +
                                     [app_commands.Choice(name=label, value=a.value)
                                      for a, label in UNVERIFIED_ACTIONS.items()])
        async def server_power(interaction: discord.Interaction, server: str, action: str) -> None:
            await self.server_power(interaction, server, action)

        @group.command(name="power-all", description="Start, stop or restart every server, or every server "
                                                     "with an AMP tag (operators)")
        @app_commands.describe(action="What to do", tag="Only servers with this tag in AMP")
        @app_commands.choices(action=[app_commands.Choice(name=a.value.capitalize(), value=a.value)
                                      for a in POWER_ACTIONS])
        async def power_all(interaction: discord.Interaction, action: str, tag: str | None = None) -> None:
            await self.power_all(interaction, action, tag)

        @group.command(name="send", description="Send a console command or a chat message to a server (operators)")
        @app_commands.describe(server="The game server", text="One line to send",
                               as_="Console command (default) or in-game chat")
        @app_commands.rename(as_="as")
        @app_commands.autocomplete(server=auto)
        @app_commands.choices(as_=[app_commands.Choice(name="Console command", value="console"),
                                   app_commands.Choice(name="In-game chat", value="chat")])
        async def server_send(interaction: discord.Interaction, server: str, text: str, as_: str = "console") -> None:
            await self.server_send(interaction, server, text, as_)

        @group.command(name="console", description="Show a server's latest console lines (operators)")
        @app_commands.describe(server="The game server", lines=f"How many lines (1 to {CONSOLE_MAX})")
        @app_commands.autocomplete(server=auto)
        async def server_console(interaction: discord.Interaction, server: str,
                                 lines: app_commands.Range[int, 1, CONSOLE_MAX] = CONSOLE_DEFAULT) -> None:
            await self.server_console(interaction, server, lines)

        @app_commands.command(name="backups", description="List, make and restore backups (operators)")
        @app_commands.guild_only()
        @app_commands.describe(server="The game server")
        @app_commands.autocomplete(server=auto)
        async def backups(interaction: discord.Interaction, server: str) -> None:
            await self.backups(interaction, server)

        tree.add_command(group)
        tree.add_command(backups)
