# SPDX-License-Identifier: AGPL-3.0-or-later
"""History, graphs and the ``/monitor`` settings.

``Monitor`` ties the monitoring pieces together:

* a relay listener that logs starts and crashes, and feeds ``Thresholds`` and ``Updates``;
* a once-a-minute sample of every server (online or not, players, CPU, memory) from what Portcullis
  already knows (``LiveState`` and the Metrics hub events), folded into 5-minute rows. Recording costs no
  AMP calls. Hidden servers and minutes when AMP's state is unknown are not recorded;
* ``/graph``: uptime and players for everyone, CPU and memory for operators;
* ``/monitor``: where alerts go, mutes, limits, update notices, the weekly summary and how long history is kept.
"""

from __future__ import annotations

import io
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord import app_commands

from portcullis.bot.alerting import ALERT_DM, DM_LIMIT, ROUTE_KEY, Alert, AlertRouter, stored_route
from portcullis.bot.alerts import CPU_KEY, MEMORY_KEY
from portcullis.bot.base import ALERT_CHANNEL, Module, card, id_list, reply, thinking
from portcullis.bot.events import TIMEZONE, zone
from portcullis.bot.graphs import GREEN, MUSTARD, ORANGE, Series, render
from portcullis.bot.setup import can_post
from portcullis.bot.thresholds import DEFAULT_GAP, DEFAULT_SUSTAIN, DISK_KEY, GAP_KEY, SUSTAIN_KEY, Thresholds
from portcullis.bot.updates import NOTICES_KEY, Updates
from portcullis.bot.weekly import DAYS, DEFAULT, WEEKLY_KEY, Weekly, stored_config
from portcullis.core.monitoring import HistoryBuffer, Window
from portcullis.core.relay import escape
from portcullis.core.types import ServerEvent, ServerInfo, ServerState, StateChanged
from portcullis.db.history import HistoryRepo, IncidentRepo

log = logging.getLogger(__name__)

HISTORY_DAYS = "history_days"  # bot scope: days of history kept; 0 stops recording
DEFAULT_DAYS, MAX_DAYS = 30, 90
PRUNED_KEY = "history_pruned_at"
FRESH = 120.0  # a metrics sample older than this isn't used for the minute's sample
PERIODS = {"day": (86400, 1800), "week": (7 * 86400, 3 * 3600), "month": (30 * 86400, 12 * 3600)}
METRICS = ("players", "uptime", "cpu", "memory")
OPERATOR_METRICS = ("cpu", "memory")
GRAPH_COOLDOWN = 10.0  # per member


def history_days(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_DAYS
    return min(MAX_DAYS, max(0, value))


class Monitor(Module):
    def __init__(self, ctx: Any, router: AlertRouter) -> None:
        super().__init__(ctx)
        self.router = router
        self.history = HistoryRepo(ctx.db)
        self.incidents = IncidentRepo(ctx.db)
        self.thresholds = Thresholds(ctx, router)
        self.updates = Updates(ctx, router)
        self.weekly = Weekly(ctx, self.history, self.incidents)
        self.buffer = HistoryBuffer()
        self.last: dict[str, ServerState] = {}
        self.graphed: dict[int, float] = {}
        router.on_sent.append(self._alert_sent)
        ctx.audit.listeners.append(self.updates.on_audit)

    # ---------------------------------------------------------------- recording

    async def _alert_sent(self, alert: Alert) -> None:
        if alert.server and alert.severity in ("bad", "warn"):
            await self.incidents.add(alert.server, "alert", int(time.time()), alert.kind)

    async def __call__(self, event: ServerEvent) -> None:
        if isinstance(event, StateChanged):
            server = event.server.lower()
            previous = self.last.get(server)
            self.last[server] = event.state
            if previous is not None and previous is not event.state:
                at = int(event.at.timestamp())
                if event.state is ServerState.RUNNING:
                    await self.incidents.add(server, "start", at)
                elif event.state is ServerState.FAILED:
                    await self.incidents.add(server, "crash", at, event.message)
        await self.thresholds(event)
        await self.updates(event)

    async def servers_seen(self, servers: list[ServerInfo]) -> None:
        """Called by the bot after each read of AMP's server list."""
        await self.updates.seen(servers)

    def sample(self, now: datetime) -> list[tuple[str, Window]]:
        """One sample per server with a known state. Returns windows that are now complete."""
        done: list[tuple[str, Window]] = []
        live = self.ctx.live
        for sid, state in live.states.items():
            if state is ServerState.UNKNOWN or not live.reachable:
                continue
            online = state is ServerState.RUNNING
            players = len(live.players.get(sid, ())) if online else 0
            metric = self.thresholds.latest.get(sid.lower())
            fresh = metric is not None and (now - metric.at).total_seconds() <= FRESH
            done += self.buffer.observe(sid.lower(), now, online=online, players=players,
                                        cpu=metric.cpu if fresh and online else None,
                                        memory=metric.memory_percent if fresh and online else None)
        return done + self.buffer.drain(now)

    async def write(self, windows: list[tuple[str, Window]]) -> None:
        for server, w in windows:
            await self.history.add(server, w.start, samples=w.samples, up=w.up, players_max=w.players_max,
                                   players_sum=w.players_sum, cpu_max=w.cpu_max, memory_max=w.memory_max)

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        days = history_days(await self.ctx.settings.get(HISTORY_DAYS))
        if days > 0:
            await self.write(self.sample(now))
            await self.prune(now, days)
        await self.thresholds.tick(now)
        await self.weekly.tick(now)

    async def prune(self, now: datetime, days: int) -> None:
        last = await self.ctx.settings.get(PRUNED_KEY)
        if isinstance(last, int) and 0 <= now.timestamp() - last < 86400:
            return
        await self.ctx.settings.set(PRUNED_KEY, int(now.timestamp()))
        before = int((now - timedelta(days=days)).timestamp())
        removed = await self.history.prune(before) + await self.incidents.prune(before)
        if removed:
            log.info("pruned %d history rows older than %d days", removed, days)

    async def close(self) -> None:
        try:
            await self.write(self.buffer.drain())
        except Exception as exc:  # noqa: BLE001 - shutting down; a few minutes of history is not worth an error
            log.debug("could not save the last history window: %s", exc)
        await self.updates.close()

    # ---------------------------------------------------------------- /graph

    async def graph_png(self, shard_id: str, name: str, metric: str, period: str,
                        now: datetime | None = None) -> tuple[bytes, list[str]]:
        """The graph image and a few facts to show beside it."""
        span, step = PERIODS[period]
        now = now or datetime.now(UTC)
        until = int(now.timestamp())
        until -= until % step
        until += step
        since = until - span
        tz = zone(await self.ctx.settings.get(TIMEZONE))
        points = await self.history.series(shard_id, since, until, step)
        stats = await self.history.stats(shard_id, since, until)
        facts = []
        if stats.uptime is not None:
            facts.append(f"{100 * stats.uptime:.1f}% online")
            facts.append(f"peak {stats.players_max} player{'s' if stats.players_max != 1 else ''}")
        label = {"day": "last 24 hours", "week": "last 7 days", "month": "last 30 days"}[period]
        if metric == "players":
            series = [Series("Players", ORANGE, [(p.start, p.players_max) for p in points])]
            png = render(f"{name}: players", f"Most online at once, {label}", series, since=since, until=until,
                         step=step, tz=tz, style="area")
        elif metric == "uptime":
            series = [Series("Online", GREEN, [(p.start, 100 * p.uptime) for p in points])]
            png = render(f"{name}: uptime", f"Share of each step it was online, {label}. Red: offline.", series,
                         since=since, until=until, step=step, tz=tz, style="bars", unit="%", y_max=100)
        else:
            colour = ORANGE if metric == "cpu" else MUSTARD
            values = [(p.start, p.cpu_max if metric == "cpu" else p.memory_max) for p in points]
            title = "CPU" if metric == "cpu" else "memory (of its limit)"
            png = render(f"{name}: {title}", f"Highest in each step, {label}", [Series(title, colour, values)],
                         since=since, until=until, step=step, tz=tz, unit="%", y_max=100)
        return png, facts

    async def graph(self, interaction: discord.Interaction, server: str, metric: str = "players",
                    period: str = "day", share: bool = False) -> None:
        if not await self.in_guild(interaction):
            return
        if metric not in METRICS or period not in PERIODS:
            await reply(interaction, "Pick a graph and a period from the lists.")
            return
        row = await self.server(interaction, server)
        if row is None:
            return
        if metric in OPERATOR_METRICS and not await self.require_mod(interaction, "server.diagnostics", row.shard_id):
            return
        now = time.monotonic()
        if now - self.graphed.get(interaction.user.id, -GRAPH_COOLDOWN) < GRAPH_COOLDOWN:
            await reply(interaction, "One graph every 10 seconds, please. Try again in a moment.")
            return
        self.graphed[interaction.user.id] = now
        await thinking(interaction, ephemeral=not share)
        png, facts = await self.graph_png(row.shard_id, row.name, metric, period)
        embed = card(title=f"{escape(row.name)}", description=" · ".join(facts) or None,
                     footer="Recorded by Portcullis every minute. Gaps are times it wasn't running.")
        embed.set_image(url="attachment://graph.png")
        await reply(interaction, embed=embed, file=discord.File(io.BytesIO(png), filename="graph.png"),
                    ephemeral=not share)

    # ---------------------------------------------------------------- /monitor

    async def alerts(self, interaction: discord.Interaction, server: str | None = None, channel: Any | None = None,
                     dm_add: Any | None = None, dm_remove: Any | None = None, reset_channel: bool = False,
                     update_notices: bool | None = None) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "monitor.alerts"):
            return
        scope = None
        if server:
            row = await self.server(interaction, server)
            if row is None:
                return
            scope = row.shard_id
        if channel is not None and can_post(interaction, channel.id) is False:
            await reply(interaction, f"I can't post in {channel.mention}. Give Portcullis View Channel and Send "
                                     "Messages there, then try again.")
            return
        changes = []
        by = interaction.user.id
        if scope is None:
            dms = id_list(await self.ctx.settings.get(ALERT_DM))
            if channel is not None:
                await self.ctx.settings.set(ALERT_CHANNEL, channel.id, by=by)
                changes.append(f"alerts go to {channel.mention}")
            dms, note = edit_dms(dms, dm_add, dm_remove)
            if note:
                await self.ctx.settings.set(ALERT_DM, dms, by=by)
                changes.append(note)
            if update_notices is not None:
                await self.ctx.settings.set(NOTICES_KEY, update_notices, by=by)
                changes.append("update notices " + ("on" if update_notices else "off"))
        else:
            route = stored_route(await self.ctx.settings.get(ROUTE_KEY, scope=scope))
            if channel is not None:
                route.channel = channel.id
                changes.append(f"this server's alerts go to {channel.mention}")
            elif reset_channel:
                route.channel = None
                changes.append("this server's alerts use the bot's alert channel")
            route.dms, note = edit_dms(route.dms, dm_add, dm_remove)
            if note:
                changes.append(note)
            if changes:
                await self.ctx.settings.set(ROUTE_KEY, {"channel": route.channel, "dm": route.dms}, scope=scope, by=by)
        if changes:
            await self.ctx.audit.record("monitor.alerts", actor_id=by, shard_id=scope, detail={"changes": changes})
        summary = await self.describe(scope)
        head = ("Saved: " + "; ".join(changes) + ".\n\n") if changes else ""
        await reply(interaction, embed=card(title="Alert settings", description=head + summary))

    async def describe(self, scope: str | None) -> str:
        route = await self.router.route(scope)
        where = f"<#{route.channel}>" if route.channel else "nowhere (set a channel)"
        dms = ", ".join(f"<@{u}>" for u in route.dms) or "nobody"
        lines = [f"**Channel:** {where}", f"**DMs:** {dms}"]
        if scope is None:
            notices = await self.ctx.settings.get(NOTICES_KEY, True) is not False
            lines.append(f"**Update notices:** {'on' if notices else 'off'}")
        else:
            until = await self.router.muted_until(scope)
            if until is not None:
                lines.append(f"**Muted until** <t:{int(until.timestamp())}:f>")
            keys = (("CPU", CPU_KEY), ("memory", MEMORY_KEY), ("disk", DISK_KEY))
            limits = [f"{label} {value}%" for label, key in keys
                      if (value := await self.ctx.settings.get(key, scope=scope))]
            lines.append("**Limits:** " + (", ".join(limits) if limits else "none"))
        return "\n".join(lines)

    async def mute(self, interaction: discord.Interaction, server: str, minutes: int) -> None:
        if not await self.in_guild(interaction):
            return
        row = await self.server(interaction, server)
        if row is None:
            return
        if not self.ctx.access.is_admin(interaction.user) and \
                not await self.require_mod(interaction, "monitor.mute", row.shard_id):
            return
        if not 0 <= minutes <= 10080:
            await reply(interaction, "Minutes go from 0 (unmute) to 10080 (a week).")
            return
        until = await self.router.mute(row.shard_id, minutes, by=interaction.user.id)
        await self.ctx.audit.record("monitor.mute", actor_id=interaction.user.id, shard_id=row.shard_id,
                                    target=str(minutes))
        if until is None:
            await reply(interaction, f"Alerts for **{escape(row.name)}** are on again.")
        else:
            await reply(interaction, f"Alerts for **{escape(row.name)}** are muted until "
                                     f"<t:{int(until.timestamp())}:f>. `/monitor mute minutes:0` turns them back on.")

    async def limits(self, interaction: discord.Interaction, server: str, cpu: int | None = None,
                     memory: int | None = None, disk: int | None = None, gap: int | None = None,
                     minutes: int | None = None) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "monitor.thresholds"):
            return
        row = await self.server(interaction, server)
        if row is None:
            return
        for value in (cpu, memory, disk):
            if value is not None and not 0 <= value <= 100:
                await reply(interaction, "Limits are a percentage from 1 to 100, or 0 to turn one off.")
                return
        if gap is not None and not 1 <= gap <= 50:
            await reply(interaction, "The gap goes from 1 to 50 points.")
            return
        if minutes is not None and not 0 <= minutes <= 120:
            await reply(interaction, "Minutes go from 0 to 120.")
            return
        by, scope = interaction.user.id, row.shard_id
        changed = {}
        for key, value in ((CPU_KEY, cpu), (MEMORY_KEY, memory), (DISK_KEY, disk)):
            if value == 0:
                await self.ctx.settings.delete(key, scope=scope)
                changed[key] = None
            elif value is not None:
                await self.ctx.settings.set(key, value, scope=scope, by=by)
                changed[key] = value
        for key, value in ((GAP_KEY, gap), (SUSTAIN_KEY, minutes)):
            if value is not None:
                await self.ctx.settings.set(key, value, scope=scope, by=by)
                changed[key] = value
        if changed:
            self.thresholds.forget(scope)
            await self.ctx.audit.record("monitor.thresholds", actor_id=by, shard_id=scope, detail=changed)
        values = [await self.ctx.settings.get(k, scope=scope) for k in (CPU_KEY, MEMORY_KEY, DISK_KEY)]
        gap_now = await self.ctx.settings.get(GAP_KEY, DEFAULT_GAP, scope=scope)
        held = await self.ctx.settings.get(SUSTAIN_KEY, DEFAULT_SUSTAIN, scope=scope)
        shown = [f"**{label}:** {f'{v}%' if v else 'off'}" for label, v in zip(("CPU", "Memory", "Disk"), values,
                                                                            strict=True)]
        text = "\n".join(shown) + (f"\n\nAn alert goes out after a value stays at or above its limit for {held} "
                                   f"minute(s), and clears once it is {gap_now} points lower for 2 minutes. Disk "
                                   "is checked every 30 minutes and needs a disk budget set in AMP.")
        await reply(interaction, embed=card(title=f"Limits for {escape(row.name)}", description=text))

    async def weekly_settings(self, interaction: discord.Interaction, enabled: bool | None = None,
                              channel: Any | None = None, day: int | None = None, hour: int | None = None,
                              preview: bool = False) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "monitor.weekly"):
            return
        if channel is not None and can_post(interaction, channel.id) is False:
            await reply(interaction, f"I can't post in {channel.mention}. Give Portcullis View Channel and Send "
                                     "Messages there, then try again.")
            return
        stored = await self.ctx.settings.get(WEEKLY_KEY, DEFAULT)
        data = dict(DEFAULT) | (stored if isinstance(stored, dict) else {})
        for key, value in (("enabled", enabled), ("day", day), ("hour", hour)):
            if value is not None:
                data[key] = value
        if channel is not None:
            data["channel"] = channel.id
        if (enabled, channel, day, hour) != (None, None, None, None):
            await self.ctx.settings.set(WEEKLY_KEY, data, by=interaction.user.id)
            await self.ctx.audit.record("monitor.weekly", actor_id=interaction.user.id, detail=data)
        cfg = stored_config(data)
        where = f"<#{cfg.channel}>" if cfg.channel else "the summary channel (or the alert channel)"
        state = (f"On: every {DAYS[cfg.day]} at {cfg.hour:02d}:00 (bot time zone) in {where}." if cfg.enabled
                 else "Off. `/monitor weekly enabled:True` turns it on.")
        if preview:
            await thinking(interaction)
            embed, png = await self.weekly.build(datetime.now(UTC))
            extra: dict[str, Any] = {}
            if png is not None:
                embed.set_image(url="attachment://week.png")
                extra["file"] = discord.File(io.BytesIO(png), filename="week.png")
            await reply(interaction, f"Weekly summary: {state} A preview of the last 7 days:", embed=embed, **extra)
            return
        await reply(interaction, f"Weekly summary: {state}")

    async def history_settings(self, interaction: discord.Interaction, days: int | None = None) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "monitor.history"):
            return
        if days is not None:
            if not 0 <= days <= MAX_DAYS:
                await reply(interaction, f"Days go from 0 (stop recording) to {MAX_DAYS}.")
                return
            await self.ctx.settings.set(HISTORY_DAYS, days, by=interaction.user.id)
            await self.ctx.settings.delete(PRUNED_KEY)  # prune at the next tick with the new limit
            await self.ctx.audit.record("monitor.history", actor_id=interaction.user.id, target=str(days))
        kept = history_days(await self.ctx.settings.get(HISTORY_DAYS))
        text = (f"Portcullis keeps {kept} days of history for graphs and the weekly summary." if kept
                else "History recording is off. Graphs and the weekly summary have nothing new to show.")
        await reply(interaction, text)

    async def test(self, interaction: discord.Interaction, server: str | None = None) -> None:
        if not await self.in_guild(interaction) or not await self.require_admin(interaction, "monitor.test"):
            return
        scope = None
        name = "Portcullis"
        if server:
            row = await self.server(interaction, server)
            if row is None:
                return
            scope, name = row.shard_id, escape(row.name)
        await thinking(interaction)
        sent = await self.router.deliver([Alert(
            kind="test", server=scope, severity="info", ping=False, title="Test alert",
            text=f"🔔 Test alert for **{name}**, sent by {interaction.user.mention}. Real alerts look like this.",
            fields=(("Why", "Someone ran /monitor test"),))])
        await reply(interaction, "Sent. Check the channel and your DMs." if sent else
                    "Nothing went out: no alert channel or DM is set, or Discord refused them. See `/monitor alerts`.")

    # ---------------------------------------------------------------- registration

    def register(self, tree: app_commands.CommandTree) -> None:
        metric_choices = [app_commands.Choice(name=n, value=n) for n in METRICS]
        period_choices = [app_commands.Choice(name=n, value=n) for n in PERIODS]

        @app_commands.command(name="graph",
                              description="Uptime or player graph for a server (CPU and memory: operators)")
        @app_commands.guild_only()
        @app_commands.describe(server="The game server", metric="What to draw", period="How far back",
                               share="Post it for everyone in this channel")
        @app_commands.choices(metric=metric_choices, period=period_choices)
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def graph(interaction: discord.Interaction, server: str, metric: str = "players", period: str = "day",
                        share: bool = False) -> None:
            await self.graph(interaction, server, metric, period, share)

        group = app_commands.Group(name="monitor", guild_only=True,
                                   description="Alerts, limits, history and the weekly summary (admins)")

        @group.command(name="alerts", description="Where alerts go: a channel and DMs, bot-wide or per server")
        @app_commands.describe(server="One server's own settings; leave out for bot-wide",
                               channel="Post alerts here", dm_add="Also DM this member", dm_remove="Stop DMing them",
                               reset_channel="This server goes back to the bot's alert channel",
                               update_notices="Post game update status (bot-wide)")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def alerts(interaction: discord.Interaction, server: str | None = None,
                         channel: discord.TextChannel | None = None, dm_add: discord.Member | None = None,
                         dm_remove: discord.Member | None = None, reset_channel: bool = False,
                         update_notices: bool | None = None) -> None:
            await self.alerts(interaction, server, channel, dm_add, dm_remove, reset_channel, update_notices)

        @group.command(name="mute", description="Silence one server's alerts for a while (operators)")
        @app_commands.describe(server="The game server", minutes="How long; 0 turns alerts back on")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def mute(interaction: discord.Interaction, server: str, minutes: int) -> None:
            await self.mute(interaction, server, minutes)

        @group.command(name="thresholds", description="CPU, memory and disk limits for one server (0 turns one off)")
        @app_commands.describe(server="The game server", cpu="CPU %", memory="Memory % of its limit",
                               disk="Disk % of its budget", gap="Points under the limit before it clears (10)",
                               minutes="Minutes over the limit before an alert (5)")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def thresholds(interaction: discord.Interaction, server: str, cpu: int | None = None,
                             memory: int | None = None, disk: int | None = None, gap: int | None = None,
                             minutes: int | None = None) -> None:
            await self.limits(interaction, server, cpu, memory, disk, gap, minutes)

        @group.command(name="weekly", description="The weekly summary: uptime, peak players, crashes (off by default)")
        @app_commands.describe(enabled="Turn it on or off", channel="Post it here", day="Day it posts",
                               hour="Hour it posts (0-23, bot time zone)", preview="Show the last 7 days now")
        @app_commands.choices(day=[app_commands.Choice(name=d, value=i) for i, d in enumerate(DAYS)])
        async def weekly(interaction: discord.Interaction, enabled: bool | None = None,
                         channel: discord.TextChannel | None = None, day: int | None = None,
                         hour: app_commands.Range[int, 0, 23] | None = None, preview: bool = False) -> None:
            await self.weekly_settings(interaction, enabled, channel, day, hour, preview)

        @group.command(name="history", description="How many days of history to keep for graphs (0 stops it)")
        async def history(interaction: discord.Interaction, days: app_commands.Range[int, 0, 90] | None = None) -> None:
            await self.history_settings(interaction, days)

        @group.command(name="test", description="Send a test alert to check the channel and DMs")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def test(interaction: discord.Interaction, server: str | None = None) -> None:
            await self.test(interaction, server)

        tree.add_command(graph)
        tree.add_command(group)


def edit_dms(dms: list[int], add: Any | None, remove: Any | None) -> tuple[list[int], str | None]:
    """Add or remove one member from a DM list. Returns the list and what changed, if anything."""
    note = None
    if add is not None and getattr(add, "bot", False):
        return dms, "bots can't get DMs, so that member wasn't added"
    if add is not None and add.id not in dms:
        if len(dms) >= DM_LIMIT:
            return dms, f"the DM list is full ({DM_LIMIT}); nobody was added"
        dms = [*dms, add.id]
        note = f"<@{add.id}> gets alert DMs"
    if remove is not None and remove.id in dms:
        dms = [d for d in dms if d != remove.id]
        note = (note + "; " if note else "") + f"<@{remove.id}> no longer gets alert DMs"
    return dms, note
