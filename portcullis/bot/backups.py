# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making and restoring backups from ``/backups``, and ``/schedule list`` for AMP's own schedules.

None of this has run against a real panel. Making a backup copies the settings of the newest
existing one (the spec doesn't name the values), and AMP may pause or stop the server while it
captures. Restoring needs Manage Server, and AMP takes a backup of the current state first.
Schedules can be run, switched on and off, deleted and their run history read. Creating them isn't
here: the step and trigger formats are too involved to send blind.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from portcullis.bot.base import Module, card, reply, short_error
from portcullis.core.relay import escape
from portcullis.core.types import Backup, Schedule
from portcullis.db.repos import ServerRow

if TYPE_CHECKING:
    from portcullis.bot.server import ServerModule

log = logging.getLogger(__name__)

UNVERIFIED = "Not yet seen working on a real panel."
RUN_ICONS = {"Succeeded": "✅", "CompletedWithErrors": "⚠️", "Failed": "❌", "TimedOut": "⏱️", "Skipped": "⏭️",
             "Running": "🔄", "Queued": "🕒"}


class BackupView(discord.ui.View):
    """A Make button and a restore picker under the backup list."""

    def __init__(self, module: ServerModule, row: ServerRow, items: list[Backup], *, can_make: bool,
                 can_restore: bool, can_delete: bool = False) -> None:
        super().__init__(timeout=300)
        self.module = module
        self.row = row
        if can_make:
            make: discord.ui.Button[BackupView] = discord.ui.Button(
                label="Make a backup", style=discord.ButtonStyle.primary)
            make.callback = self._make  # type: ignore[method-assign]
            self.add_item(make)
        restorable = [b for b in items if b.status in (None, "Completed", "completed", "Ready", "ready")][:25]
        if can_restore and restorable:
            options = [discord.SelectOption(
                label=(b.name or b.id)[:90], value=b.id,
                description=b.created_at.strftime("%d %b %Y %H:%M") if b.created_at else None) for b in restorable]
            pick: discord.ui.Select[BackupView] = discord.ui.Select(placeholder="Restore one of these…",
                                                                    options=options, row=1)
            pick.callback = self._restore  # type: ignore[method-assign]
            self.add_item(pick)
        self.names = {b.id: b.name or b.id for b in items}
        self.kept = {b.id for b in items if b.sticky}
        if can_delete and items:
            options = [discord.SelectOption(
                label=(b.name or b.id)[:90], value=b.id,
                description=(b.created_at.strftime("%d %b %Y %H:%M") if b.created_at else "unknown time")
                + (" · kept" if b.sticky else "")) for b in items[:25]]
            drop: discord.ui.Select[BackupView] = discord.ui.Select(placeholder="Delete one of these…",
                                                                    options=options, row=2)
            drop.callback = self._delete  # type: ignore[method-assign]
            self.add_item(drop)

    async def _make(self, interaction: discord.Interaction) -> None:
        from portcullis.bot.server import Confirm
        await interaction.response.send_message(
            f"Make a backup of **{escape(self.row.name)}**? AMP may pause or stop the server while it saves. "
            f"{UNVERIFIED}", ephemeral=True,
            view=Confirm(interaction.user.id, self.module.backups_make(self.row), "Make it"))

    def _chosen(self, row: int) -> str:
        select = next(c for c in self.children if isinstance(c, discord.ui.Select) and c.row == row)
        return select.values[0]

    async def _restore(self, interaction: discord.Interaction) -> None:
        from portcullis.bot.server import Confirm
        backup_id = self._chosen(1)
        name = self.names.get(backup_id, backup_id)
        await interaction.response.send_message(
            f"Restore **{escape(self.row.name)}** to **{escape(name)}**? Everything since then is rolled back "
            f"and the server may stop. AMP saves the current state first. {UNVERIFIED}", ephemeral=True,
            view=Confirm(interaction.user.id, self.module.backups_restore(self.row, backup_id, name),
                         "Restore it"))


    async def _delete(self, interaction: discord.Interaction) -> None:
        from portcullis.bot.server import Confirm
        backup_id = self._chosen(2)
        name = self.names.get(backup_id, backup_id)
        kept = " It is marked to be kept in AMP." if backup_id in self.kept else ""
        await interaction.response.send_message(
            f"Delete the backup **{escape(name)}** of **{escape(self.row.name)}**? This can't be undone.{kept} "
            f"{UNVERIFIED}", ephemeral=True,
            view=Confirm(interaction.user.id, self.module.backups_delete(self.row, backup_id, name), "Delete it"))


class ScheduleView(discord.ui.View):
    def __init__(self, module: ScheduleModule, row: ServerRow, items: list[Schedule]) -> None:
        super().__init__(timeout=300)
        self.module = module
        self.row = row
        self.items = {s.id: s for s in items[:25]}
        options = [discord.SelectOption(label=(s.name or s.id)[:90], value=s.id,
                                        description="On" if s.enabled else "Off") for s in items[:25]]
        pick: discord.ui.Select[ScheduleView] = discord.ui.Select(placeholder="Pick a schedule…", options=options)
        pick.callback = self._picked  # type: ignore[method-assign]
        self.add_item(pick)
        self.chosen: str | None = None
        for label, action, style in (("Run now", "run", discord.ButtonStyle.primary),
                                     ("Turn on", "on", discord.ButtonStyle.success),
                                     ("Turn off", "off", discord.ButtonStyle.secondary),
                                     ("Run history", "history", discord.ButtonStyle.secondary),
                                     ("Delete", "delete", discord.ButtonStyle.danger)):
            button: discord.ui.Button[ScheduleView] = discord.ui.Button(label=label, style=style, row=1)
            button.callback = self._act(action)  # type: ignore[method-assign]
            self.add_item(button)

    async def _picked(self, interaction: discord.Interaction) -> None:
        select = next(c for c in self.children if isinstance(c, discord.ui.Select))
        self.chosen = select.values[0]
        await interaction.response.defer()

    def _act(self, action: str):  # noqa: ANN202
        async def act(interaction: discord.Interaction) -> None:
            if self.chosen is None:
                await reply(interaction, "Pick a schedule first.")
                return
            schedule = self.items[self.chosen]
            if action == "history":
                await self.module.history(interaction, self.row, schedule)
            elif action == "delete":
                await self.module.confirm_delete(interaction, self.row, schedule)
            else:
                await self.module.act(interaction, self.row, schedule, action)
        return act


class ScheduleModule(Module):
    async def list_schedules(self, interaction: discord.Interaction, server: str) -> None:
        if not await self.in_guild(interaction):
            return
        row = await self.server(interaction, server)
        if row is None or not await self.require_mod(interaction, "schedule.list", row.shard_id):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            items = await self.ctx.port.list_schedules(row.shard_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("listing schedules for %s failed: %s", row.name, exc)
            await reply(interaction, f"Couldn't read the schedules of {escape(row.name)}: "
                                     f"{escape(short_error(exc))}\nThe bot's AMP account may lack the schedule "
                                     "permission.")
            return
        if not items:
            await reply(interaction, f"{escape(row.name)} has no schedules. Create them in AMP's panel; "
                                     "this list can run and switch them.")
            return
        now = datetime.now(UTC)
        lines = []
        for s in items[:25]:
            when = f"next {discord.utils.format_dt(s.next_fire, 'R')}" if s.next_fire and s.next_fire > now else \
                ("no next run" if s.enabled else "off")
            lines.append(f"{'🟢' if s.enabled else '⚪'} **{escape(s.name)}**: {when}")
        embed = card(title=f"Schedules on {row.name}", description="\n".join(lines))
        embed.set_footer(text=UNVERIFIED)
        await reply(interaction, embed=embed, view=ScheduleView(self, row, items))

    async def act(self, interaction: discord.Interaction, row: ServerRow, schedule: Schedule, action: str) -> None:
        node = f"schedule.{action}"
        if not await self.require_mod(interaction, node, row.shard_id):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if action == "run":
                await self.ctx.port.run_schedule(row.shard_id, schedule.id)
            elif action == "delete":
                await self.ctx.port.delete_schedule(row.shard_id, schedule.id)
            else:
                await self.ctx.port.set_schedule_enabled(row.shard_id, schedule.id, action == "on")
        except Exception as exc:  # noqa: BLE001
            log.warning("%s on %s failed: %s", node, row.name, exc)
            await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id, result="failed",
                                        detail={"schedule": schedule.name, "error": short_error(exc)})
            await reply(interaction, f"AMP didn't take that: {escape(short_error(exc))}")
            return
        await self.ctx.audit.record(node, actor_id=interaction.user.id, shard_id=row.shard_id,
                                    detail={"schedule": schedule.name})
        done = {"run": "Started", "on": "Turned on", "off": "Turned off", "delete": "Deleted"}[action]
        await reply(interaction, f"{done}: **{escape(schedule.name)}** on {escape(row.name)}.")

    async def history(self, interaction: discord.Interaction, row: ServerRow, schedule: Schedule) -> None:
        if not await self.require_mod(interaction, "schedule.history", row.shard_id):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            runs = await self.ctx.port.schedule_history(row.shard_id, schedule.id, limit=10)
        except Exception as exc:  # noqa: BLE001
            await reply(interaction, f"Couldn't read the run history: {escape(short_error(exc))}")
            return
        if not runs:
            await reply(interaction, f"**{escape(schedule.name)}** hasn't run yet.")
            return
        lines = []
        for run in runs:
            at = discord.utils.format_dt(run.started_at, "f") if run.started_at else "not started"
            took = ""
            if run.started_at and run.finished_at:
                took = f" · took {max(0, int((run.finished_at - run.started_at).total_seconds()))}s"
            error = f"\n> {escape(run.error[:200])}" if run.error else ""
            status = run.status or "unknown"
            lines.append(f"{RUN_ICONS.get(status, '❔')} {at} · {escape(status)}{took}{error}")
        embed = card(title=f"Last runs of {schedule.name}", description="\n".join(lines)[:4000])
        embed.set_footer(text=UNVERIFIED)
        await reply(interaction, embed=embed)

    async def confirm_delete(self, interaction: discord.Interaction, row: ServerRow, schedule: Schedule) -> None:
        from portcullis.bot.server import Confirm
        if not self.ctx.access.is_admin(interaction.user):
            await self.ctx.audit.record("schedule.delete", actor_id=interaction.user.id, shard_id=row.shard_id,
                                        result="denied")
            await reply(interaction, "Deleting schedules needs the Manage Server permission.")
            return

        async def delete(confirmed: discord.Interaction) -> None:
            await self.act(confirmed, row, schedule, "delete")

        await interaction.response.send_message(
            f"Delete the schedule **{escape(schedule.name)}** from {escape(row.name)} in AMP? This can't be "
            f"undone. {UNVERIFIED}", ephemeral=True, view=Confirm(interaction.user.id, delete, "Delete it"))

    def register(self, tree: app_commands.CommandTree) -> None:
        group = app_commands.Group(name="schedule", description="AMP's schedules", guild_only=True)

        @group.command(name="list", description="A server's schedules: run, switch, history, delete (operators)")
        @app_commands.describe(server="The game server")
        @app_commands.autocomplete(server=self.autocomplete_server)
        async def schedule_list(interaction: discord.Interaction, server: str) -> None:
            await self.list_schedules(interaction, server)

        tree.add_command(group)
