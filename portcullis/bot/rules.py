# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/alert create`` and ``/alert list``: admin-defined alert rules, checked by Portcullis once a minute.

These are Portcullis's own thresholds, not AMP triggers. They read only data seen working on a real
panel: a server's state (offline), CPU, memory and its player list. A rule fires once when its
condition has held for its minutes, then posts a "back to normal" note when it clears.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord import app_commands

from portcullis.bot.base import ALERT_CHANNEL, Module, card, reply, role_problem
from portcullis.bot.setup import can_post
from portcullis.core.relay import escape
from portcullis.core.types import ServerState, ServerStatus
from portcullis.db.ops import AlertRule, AlertRuleRepo

log = logging.getLogger(__name__)

METRICS = {
    "cpu": "CPU at or above {n}%",
    "memory": "memory at or above {n}% of its limit",
    "players_above": "more than {n} players online",
    "players_below": "fewer than {n} players online",
    "offline": "offline",
}
STATUS_TIMEOUT = 8.0
MUTE_FOR = timedelta(hours=1)


def describe(rule: AlertRule) -> str:
    return METRICS[rule.metric].format(n=rule.threshold)


def check_values(metric: str, threshold: int, minutes: int) -> str | None:
    """A problem with the numbers to show the admin, or None."""
    if metric not in METRICS:
        return "Pick a metric from the list."
    if metric in ("cpu", "memory") and not 1 <= threshold <= 100:
        return "CPU and memory thresholds are a percentage from 1 to 100."
    if metric in ("players_above", "players_below") and not 0 <= threshold <= 1000:
        return "Player thresholds go from 0 to 1000."
    if metric == "players_below" and threshold == 0:
        return "Fewer than 0 players can't happen. Use 1 or more."
    if not 0 <= minutes <= 1440:
        return "Minutes go from 0 to 1440 (one day)."
    return None


def condition(rule: AlertRule, state: ServerState | None, status: ServerStatus | None) -> bool | None:
    """Whether the rule's condition holds now. None when there isn't enough data to tell."""
    if rule.metric == "offline":
        if state is None or state in (ServerState.UNKNOWN, ServerState.BUSY):
            return None
        return state in (ServerState.STOPPED, ServerState.FAILED)
    if state is not None and state not in (ServerState.RUNNING, ServerState.UNKNOWN):
        return False
    if status is None:
        return None
    if status.state is not ServerState.RUNNING:
        return False
    if rule.metric == "cpu":
        return None if status.cpu_percent is None else status.cpu_percent >= rule.threshold
    if rule.metric == "memory":
        if status.memory_mb is None or not status.memory_limit_mb:
            return None
        return 100 * status.memory_mb / status.memory_limit_mb >= rule.threshold
    if rule.metric == "players_above":
        return len(status.players) > rule.threshold
    return len(status.players) < rule.threshold


class AlertRuleModule(Module):
    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.since: dict[tuple[int, str], float] = {}  # (rule, server) -> when the condition started holding
        self.fired: dict[tuple[int, str], bool] = {}  # episode handled; True if the alert was posted

    @property
    def repo(self) -> AlertRuleRepo:
        return self.ctx.alert_rules

    # ---------------------------------------------------------------- /alert create

    async def create(self, interaction: discord.Interaction, metric: str, threshold: int, minutes: int = 5,
                     server: str | None = None, channel: Any | None = None, role: Any | None = None) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "alert.create"):
            return
        problem = check_values(metric, threshold, minutes)
        if problem is not None:
            await reply(interaction, problem)
            return
        if metric == "offline":
            threshold = 0
        shard_id = None
        if server:
            row = await self.server(interaction, server)
            if row is None:
                return
            shard_id = row.shard_id
        if channel is not None and can_post(interaction, channel.id) is False:
            await reply(interaction, f"I can't post in {channel.mention}. Give Portcullis View Channel and "
                                     "Send Messages there, then try again.")
            return
        if role is not None:
            issue = role_problem(role, interaction.guild, self.ctx.access, handed_out=False)
            if issue:
                await reply(interaction, issue)
                return
        rule_id = await self.repo.add(metric=metric, threshold=threshold, minutes=minutes, by=interaction.user.id,
                                      shard_id=shard_id, channel_id=channel.id if channel else None,
                                      role_id=role.id if role else None)
        if rule_id is None:
            await reply(interaction, f"There are already {AlertRuleRepo.LIMIT} alert rules. Delete one first "
                                     "with `/alert list`.")
            return
        rule = await self.repo.get(rule_id)
        assert rule is not None
        await self.ctx.audit.record("alert.create", actor_id=interaction.user.id, shard_id=shard_id,
                                    target=str(rule_id), detail={"metric": metric, "threshold": threshold,
                                                                 "minutes": minutes})
        where = channel.mention if channel else "the alert channel"
        await reply(interaction, f"Alert {rule_id} saved: {await self.summary(rule)}. It posts in {where}.")

    async def summary(self, rule: AlertRule) -> str:
        if rule.shard_id:
            row = await self.ctx.servers.get(rule.shard_id)
            target = escape(row.name) if row else "a removed server"
        else:
            target = "any server"
        hold = f" for {rule.minutes} min" if rule.minutes else ""
        return f"{target} {describe(rule)}{hold}"

    # ---------------------------------------------------------------- /alert list

    async def list_rules(self, interaction: discord.Interaction) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "alert.view"):
            return
        await reply(interaction, **await self.listing())

    async def listing(self, note: str | None = None) -> dict[str, Any]:
        rules = await self.repo.all()
        if not rules:
            text = "No alert rules yet. Add one with `/alert create`."
            return {"content": f"{note}\n{text}" if note else text, "embed": None, "view": None}
        now = datetime.now(UTC)
        lines = []
        for rule in rules:
            where = f"<#{rule.channel_id}>" if rule.channel_id else "alert channel"
            ping = f", pings <@&{rule.role_id}>" if rule.role_id else ""
            muted = ""
            if rule.muted(now):
                assert rule.muted_until is not None
                muted = f" · muted until {discord.utils.format_dt(datetime.fromisoformat(rule.muted_until), 't')}"
            lines.append(f"**{rule.id}.** {await self.summary(rule)} → {where}{ping}{muted}")
        embed = card(title="Alert rules", description="\n".join(lines)[:4000])
        embed.set_footer(text="Pick a rule, then Mute for 1 hour, Unmute or Delete. Works for 5 minutes.")
        return {"content": note, "embed": embed, "view": RuleView(self, rules)}

    async def act(self, interaction: discord.Interaction, rule_id: int, action: str) -> None:
        if not self.ctx.access.is_admin(interaction.user):
            await self.ctx.audit.record(f"alert.{action}", actor_id=interaction.user.id, result="denied",
                                        target=str(rule_id))
            await reply(interaction, "You need the Manage Server permission for that.")
            return
        rule = await self.repo.get(rule_id)
        if rule is None:
            note = "That rule is already gone."
        elif action == "delete":
            await self.repo.delete(rule_id)
            self.forget(rule_id)
            await self.ctx.audit.record("alert.delete", actor_id=interaction.user.id, shard_id=rule.shard_id,
                                        target=str(rule_id))
            note = f"Deleted alert {rule_id}."
        else:
            until = datetime.now(UTC) + MUTE_FOR if action == "mute" else None
            await self.repo.mute(rule_id, until)
            await self.ctx.audit.record("alert.mute", actor_id=interaction.user.id, shard_id=rule.shard_id,
                                        target=str(rule_id), detail={"muted": until is not None})
            note = f"Muted alert {rule_id} for an hour." if until else f"Alert {rule_id} is no longer muted."
        await interaction.response.edit_message(**await self.listing(note))

    def forget(self, rule_id: int) -> None:
        for key in [k for k in (*self.since, *self.fired) if k[0] == rule_id]:
            self.since.pop(key, None)
            self.fired.pop(key, None)

    # ---------------------------------------------------------------- checking, once a minute

    async def tick(self, now: float | None = None) -> None:
        clock = time.monotonic() if now is None else now
        rules = await self.repo.all()
        live = {r.id for r in rules}
        for key in [k for k in (*self.since, *self.fired) if k[0] not in live]:
            self.since.pop(key, None)
            self.fired.pop(key, None)
        if not rules:
            return
        rows = {r.shard_id: r for r in await self.ctx.servers.list()}
        need_status = {sid for rule in rules if rule.metric != "offline"
                       for sid in ([rule.shard_id] if rule.shard_id else rows) if sid in rows}
        statuses = await self._statuses(need_status)
        wall = datetime.now(UTC)
        for rule in rules:
            for sid in [rule.shard_id] if rule.shard_id else list(rows):
                row = rows.get(sid)
                if row is None:
                    continue
                state = self.ctx.live.states.get(sid)
                status = statuses.get(sid)
                if state is None and status is not None:
                    state = status.state
                holds = condition(rule, state, status)
                if holds is not None:
                    await self._step(rule, sid, row.name, holds, clock, wall)

    async def _statuses(self, shard_ids: set[str]) -> dict[str, ServerStatus]:
        async def one(sid: str) -> tuple[str, ServerStatus | None]:
            if self.ctx.live.states.get(sid) not in (None, ServerState.RUNNING):
                return sid, None
            try:
                return sid, await asyncio.wait_for(self.ctx.port.get_status(sid), STATUS_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - the unreachable alert covers AMP being down
                log.debug("alert rule status for %s failed: %s", sid, exc)
                return sid, None

        found = await asyncio.gather(*(one(sid) for sid in shard_ids))
        return {sid: status for sid, status in found if status is not None}

    async def _step(self, rule: AlertRule, sid: str, name: str, holds: bool, clock: float, wall: datetime) -> None:
        key = (rule.id, sid)
        if not holds:
            self.since.pop(key, None)
            if self.fired.pop(key, False):
                await self._send(rule, f"✅ Alert {rule.id} cleared: **{escape(name)}** is no longer "
                                       f"{describe(rule)}.", ping=False)
            return
        started = self.since.setdefault(key, clock)
        if key in self.fired or clock - started < rule.minutes * 60:
            return
        if rule.muted(wall):
            return  # not marked fired, so it alerts on the first check after the mute ends
        hold = f" for {rule.minutes} min" if rule.minutes else ""
        self.fired[key] = await self._send(rule, f"🚨 Alert {rule.id}: **{escape(name)}** has been "
                                                 f"{describe(rule)}{hold}.", ping=True)
        await self.repo.fired(rule.id)

    async def _send(self, rule: AlertRule, text: str, *, ping: bool) -> bool:
        role = rule.role_id if ping else None
        if rule.channel_id:
            return await self.ctx.post_to(rule.channel_id, text, ping_role=role, label="alert rule channel")
        return await self.ctx.post(ALERT_CHANNEL, text, ping_role=role)

    # ---------------------------------------------------------------- registration

    def register(self, tree: app_commands.CommandTree) -> None:
        group = app_commands.Group(name="alert", description="Your own alert rules (admins)", guild_only=True,
                                   default_permissions=discord.Permissions(manage_guild=True))

        @group.command(name="create", description="Post an alert when a server crosses a limit for a while")
        @app_commands.describe(metric="What to watch", threshold="The limit (percent or players; ignored for offline)",
                               minutes="How long it must last before the alert (0 = at once)",
                               server="One server (leave out for every server)",
                               channel="Where to post (leave out for the alert channel)", role="A role to ping")
        @app_commands.choices(metric=[
            app_commands.Choice(name="CPU above (%)", value="cpu"),
            app_commands.Choice(name="Memory above (% of limit)", value="memory"),
            app_commands.Choice(name="More players than", value="players_above"),
            app_commands.Choice(name="Fewer players than (while running)", value="players_below"),
            app_commands.Choice(name="Offline", value="offline")])
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def alert_create(interaction: discord.Interaction, metric: str, threshold: int = 0,
                               minutes: app_commands.Range[int, 0, 1440] = 5, server: str | None = None,
                               channel: discord.TextChannel | None = None, role: discord.Role | None = None) -> None:
            await self.create(interaction, metric, threshold, minutes, server, channel, role)

        @group.command(name="list", description="Your alert rules, with Mute and Delete")
        async def alert_list(interaction: discord.Interaction) -> None:
            await self.list_rules(interaction)

        tree.add_command(group)


class RuleView(discord.ui.View):
    def __init__(self, module: AlertRuleModule, rules: list[AlertRule]) -> None:
        super().__init__(timeout=300)
        self.module = module
        self.chosen: int | None = None
        options = [discord.SelectOption(label=f"Alert {r.id}", value=str(r.id), description=describe(r)[:100])
                   for r in rules[:25]]
        pick: discord.ui.Select[RuleView] = discord.ui.Select(placeholder="Pick a rule…", options=options)
        pick.callback = self._picked  # type: ignore[method-assign]
        self.add_item(pick)
        for label, action, style in (("Mute 1 hour", "mute", discord.ButtonStyle.secondary),
                                     ("Unmute", "unmute", discord.ButtonStyle.secondary),
                                     ("Delete", "delete", discord.ButtonStyle.danger)):
            button: discord.ui.Button[RuleView] = discord.ui.Button(label=label, style=style, row=1)
            button.callback = self._act(action)  # type: ignore[method-assign]
            self.add_item(button)

    async def _picked(self, interaction: discord.Interaction) -> None:
        select = next(c for c in self.children if isinstance(c, discord.ui.Select))
        self.chosen = int(select.values[0])
        await interaction.response.defer()

    def _act(self, action: str):  # noqa: ANN202
        async def act(interaction: discord.Interaction) -> None:
            if self.chosen is None:
                await reply(interaction, "Pick a rule first.")
                return
            await self.module.act(interaction, self.chosen, action)
        return act
