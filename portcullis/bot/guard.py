# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guards around actions that take a server up or down.

* ``Confirm`` and ``ask_power``: a Yes/Cancel step that names the server and the action. Only the
  member who asked can answer it, and it expires.
* ``StaffAlerts``: when a member without the right role tries one of these actions, staff hear about it
  in their own channel. The member only sees a plain refusal.

Settings (bot scope, set with ``/setup safety``):

* ``confirm_power``: ``all`` (default) asks before start, stop, restart and update. ``down`` skips
  the question for start, which is easy to undo.
* ``staff_channel`` and ``staff_role``: where denied attempts are reported, and who is pinged.
* ``denied_alerts``: ``True`` (default) reports denied attempts once a staff channel is set.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import discord

from portcullis.bot.base import NONE, Ctx, as_id, card, reply
from portcullis.core.relay import escape
from portcullis.core.types import PowerAction
from portcullis.db.repos import AuditEntry

log = logging.getLogger(__name__)

CONFIRM_KEY = "confirm_power"
CONFIRM_MODES = {"all": "Ask before start, stop, restart and update",
                 "down": "Ask before stop, restart and update only"}
STAFF_CHANNEL, STAFF_ROLE, DENIED_ALERTS = "staff_channel", "staff_role", "denied_alerts"

CONFIRM_SECONDS = 60
# Audit nodes that count as gated actions: a denied one is reported to staff.
GATED = ("server.power", "backup.restore", "backup.delete")
# One report per member and action within this window. Repeats are counted into the next one.
ALERT_WINDOW = 600.0
VERB = {"start": "Start", "stop": "Stop", "restart": "Restart", "update": "Update", "sleep": "Put to sleep",
        "wake": "Wake"}


def action_label(node: str) -> str:
    """``server.power.stop.all`` -> ``Stop (every server)``: what staff read in a denied report."""
    parts = node.split(".")
    if parts[:2] == ["server", "power"] and len(parts) >= 3:
        text = VERB.get(parts[2], parts[2])
        if parts[2] == "restart" and parts[-1] == "warned":
            text = "Restart with a warning"
        if parts[-1] == "all":
            text += " (every server)"
        return text
    return {"backup.restore": "Restore a backup", "backup.delete": "Delete a backup"}.get(node, node)


async def needs_confirm(ctx: Ctx, power: PowerAction) -> bool:
    if power in (PowerAction.START, PowerAction.WAKE):
        return await ctx.settings.get(CONFIRM_KEY, "all") != "down"
    return True


class Confirm(discord.ui.View):
    """Yes/Cancel under an ephemeral question. Only the person who asked can answer.

    With ``origin`` (the interaction that asked), the question is replaced by "Timed out" when it
    expires, so nobody presses a stale button later and wonders why nothing happens.
    """

    def __init__(self, owner_id: int, on_yes: Callable[[discord.Interaction], Awaitable[None]],
                 yes_label: str, *, timeout: float = 120, origin: discord.Interaction | None = None) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.on_yes = on_yes
        self.origin = origin
        self.answered = False
        yes: discord.ui.Button[Confirm] = discord.ui.Button(label=yes_label[:80], style=discord.ButtonStyle.danger)
        yes.callback = self._yes  # type: ignore[method-assign]
        no: discord.ui.Button[Confirm] = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        no.callback = self._no  # type: ignore[method-assign]
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await reply(interaction, "Only the person who asked can answer this.")
        return False

    async def _yes(self, interaction: discord.Interaction) -> None:
        if self.answered:  # a double click
            await reply(interaction, "That was already answered.")
            return
        self.answered = True
        self.stop()
        await self.on_yes(interaction)

    async def _no(self, interaction: discord.Interaction) -> None:
        if self.answered:
            await reply(interaction, "That was already answered.")
            return
        self.answered = True
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was changed.", embed=None, view=None)

    async def on_timeout(self) -> None:
        if self.origin is None or self.answered:
            return
        self.answered = True
        try:
            await self.origin.edit_original_response(content="Timed out. Nothing was changed.", embed=None,
                                                     view=None, allowed_mentions=NONE)
        except Exception as exc:  # noqa: BLE001 - the member may have dismissed it
            log.debug("could not mark a question as timed out: %s", exc)


def power_question(server: str, power: PowerAction, *, online: int | None, expires: datetime,
                   warned: bool = False) -> discord.Embed:
    """The confirmation card: which server, which action, who is playing, when the question ends."""
    verb = VERB.get(power.value, power.value.capitalize())
    going_up = power in (PowerAction.START, PowerAction.WAKE)
    embed = card(title=f"{verb} {server}?"[:256], kind="info" if going_up else "warn",
                 footer="Only you can answer this. The answer goes in the audit log.")
    embed.add_field(name="Server", value=escape(server)[:1024], inline=True)
    embed.add_field(name="Action", value="Restart with a warning" if warned else verb, inline=True)
    if not going_up:
        embed.add_field(name="Players online", value="AMP didn't say" if online is None else str(online),
                        inline=True)
    if power is PowerAction.UPDATE:
        embed.add_field(name="What happens", value="AMP stops the server and updates the game files. Portcullis "
                        "follows the update and posts the result here.", inline=False)
    if warned:
        embed.add_field(name="What happens", value="Players get a warning in game chat 5 minutes and 1 minute "
                        "before the restart. Anyone with power rights can cancel it.", inline=False)
    embed.add_field(name="Expires", value=discord.utils.format_dt(expires, "R"), inline=False)
    return embed


async def ask_power(interaction: discord.Interaction, server: str, power: PowerAction, online: int | None,
                    on_yes: Callable[[discord.Interaction], Awaitable[None]], *, warned: bool = False) -> None:
    expires = datetime.now(UTC) + timedelta(seconds=CONFIRM_SECONDS)
    label = f"{VERB.get(power.value, power.value.capitalize())} {server}"
    view = Confirm(interaction.user.id, on_yes, label, timeout=CONFIRM_SECONDS, origin=interaction)
    await reply(interaction, embed=power_question(server, power, online=online, expires=expires, warned=warned),
                view=view)


class StaffAlerts:
    """Reports denied attempts at gated actions in the staff channel (an ``AuditRepo`` listener)."""

    def __init__(self, ctx: Ctx, clock: Callable[[], float] = time.monotonic) -> None:
        self.ctx = ctx
        self.clock = clock
        self.last: dict[tuple[int, str], float] = {}
        self.held: dict[tuple[int, str], int] = {}  # attempts not reported yet, within the window
        self.tasks: set[asyncio.Task[None]] = set()

    def __call__(self, entry: AuditEntry) -> None:
        if entry.result != "denied" or entry.actor_id is None:
            return
        if not any(entry.action == g or entry.action.startswith(g + ".") for g in GATED):
            return
        key = (entry.actor_id, entry.action)
        now = self.clock()
        last = self.last.get(key)
        if last is not None and now - last < ALERT_WINDOW:
            self.held[key] = self.held.get(key, 0) + 1
            return
        self.last = {k: t for k, t in self.last.items() if now - t < ALERT_WINDOW}
        self.last[key] = now
        earlier = self.held.pop(key, 0)
        task = asyncio.create_task(self.post(entry, earlier), name="portcullis-staff-alert")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def post(self, entry: AuditEntry, earlier: int = 0) -> bool:
        try:
            return await self._post(entry, earlier)
        except Exception as exc:  # noqa: BLE001 - the denial already happened and is in the audit log
            log.warning("could not report a denied attempt: %s", exc)
            return False

    async def _post(self, entry: AuditEntry, earlier: int) -> bool:
        if not await self.ctx.settings.get(DENIED_ALERTS, True):
            return False
        channel_id = as_id(await self.ctx.settings.get(STAFF_CHANNEL))
        if channel_id is None:
            return False
        where = "Any server"
        if entry.shard_id:
            row = await self.ctx.servers.get(entry.shard_id)
            where = escape(row.name if row else entry.shard_id)
        embed = card(title="Blocked attempt", kind="warn",
                     description=f"<@{entry.actor_id}> tried something their roles don't allow. "
                                 "Nothing changed.",
                     footer="They only saw a plain refusal. Full history: /audit")
        embed.add_field(name="Member", value=f"<@{entry.actor_id}> (`{entry.actor_id}`)", inline=True)
        embed.add_field(name="Action", value=action_label(entry.action), inline=True)
        embed.add_field(name="Server", value=where[:1024], inline=True)
        if earlier:
            embed.add_field(name="Earlier tries", value=f"{earlier} more in the last {int(ALERT_WINDOW // 60)} "
                                                        "minutes", inline=False)
        role = as_id(await self.ctx.settings.get(STAFF_ROLE))
        return await self.ctx.post_to(channel_id, embed=embed, ping_role=role, label="staff channel")

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
