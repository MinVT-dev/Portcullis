# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every slash command, by module. See ``docs/SETUP.md`` section 8.4 for the list and who may use what.

Handlers are plain methods taking an interaction, so tests call them with a fake interaction;
``register`` only wires them into a ``CommandTree``.
"""

from __future__ import annotations

from discord import app_commands

from portcullis.bot.admin import AdminModule
from portcullis.bot.base import Access, Ctx, Module
from portcullis.bot.community import CommunityModule, WhitelistDecision
from portcullis.bot.polls import PollClose, PollModule, PollVote
from portcullis.bot.server import ServerModule
from portcullis.bot.setup import SetupModule

__all__ = ["Access", "Ctx", "Modules", "DYNAMIC_ITEMS"]

# Buttons that must keep working after a restart (their state is in the custom id).
DYNAMIC_ITEMS = (WhitelistDecision, PollVote, PollClose)


class Modules:
    def __init__(self, ctx: Ctx) -> None:
        self.server = ServerModule(ctx)
        self.setup = SetupModule(ctx)
        self.community = CommunityModule(ctx)
        self.polls = PollModule(ctx)
        self.admin = AdminModule(ctx)

    def all(self) -> list[Module]:
        return [self.server, self.setup, self.community, self.polls, self.admin]

    def register(self, tree: app_commands.CommandTree) -> None:
        for module in self.all():
            module.register(tree)
