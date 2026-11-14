# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every slash command, by module. See ``docs/SETUP.md`` section 8.4 for the list and who may use what.

Handlers are plain methods taking an interaction, so tests call them with a fake interaction;
``register`` only wires them into a ``CommandTree``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from discord import app_commands

from portcullis.bot.admin import AdminModule
from portcullis.bot.alerts import Alerts, AuditFeed
from portcullis.bot.backups import ScheduleModule
from portcullis.bot.base import Access, Ctx, Module
from portcullis.bot.board import StatusBoard
from portcullis.bot.community import CommunityModule, WhitelistDecision
from portcullis.bot.digest import Digest
from portcullis.bot.events import EventCancel, EventGoing, EventModule
from portcullis.bot.joins import JoinWatch
from portcullis.bot.livecard import LiveCards
from portcullis.bot.notify import NotifyModule
from portcullis.bot.polls import PollClose, PollModule, PollVote
from portcullis.bot.rules import AlertRuleModule
from portcullis.bot.server import ServerModule
from portcullis.bot.setup import SetupModule

__all__ = ["Access", "Ctx", "Modules", "DYNAMIC_ITEMS"]

log = logging.getLogger(__name__)

# Buttons that must keep working after a restart (their state is in the custom id).
DYNAMIC_ITEMS = (WhitelistDecision, PollVote, PollClose, EventGoing, EventCancel)


class Modules:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        if ctx.board is None:
            ctx.board = StatusBoard(ctx)
        if ctx.live_cards is None:
            ctx.live_cards = LiveCards(ctx)
        self.server = ServerModule(ctx)
        self.setup = SetupModule(ctx)
        self.community = CommunityModule(ctx)
        self.polls = PollModule(ctx)
        self.admin = AdminModule(ctx)
        self.notify = NotifyModule(ctx)
        self.events = EventModule(ctx)
        self.schedules = ScheduleModule(ctx)
        self.alerts = Alerts(ctx)
        self.rules = AlertRuleModule(ctx)
        self.joins = JoinWatch(ctx)
        self.digest = Digest(ctx)
        self.audit_feed = AuditFeed(ctx)
        ctx.audit.listeners.append(self.audit_feed)

    def all(self) -> list[Module]:
        return [self.server, self.setup, self.community, self.polls, self.notify, self.events, self.schedules,
                self.rules, self.admin]

    async def tick(self) -> None:
        """Once a minute: event reminders, load and backup alerts, alert rules, the daily summary and milestones."""
        for name, job in (("events", self.events.tick), ("alerts", self.alerts.tick),
                          ("alert rules", self.rules.tick), ("digest", self.digest.tick)):
            try:
                await job()
            except Exception:  # one failing job must not stop the others
                log.exception("the %s job failed", name)

    async def close(self) -> None:
        """Stop the background work the modules own. One failing step never skips the others."""
        steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("server countdowns and restore watchers", self.server.close),
            ("audit feed", self.audit_feed.close),
        ]
        if self.ctx.live_cards is not None:
            steps.insert(0, ("live cards", self.ctx.live_cards.close))
        for name, step in steps:
            try:
                await step()
            except Exception:  # noqa: BLE001 - shutting down: log it and keep going
                log.exception("could not stop the %s cleanly", name)

    def register(self, tree: app_commands.CommandTree) -> None:
        for module in self.all():
            module.register(tree)
