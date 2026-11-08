# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Portcullis does when a player joins: first-join welcomes, watchlist notes and name checks.

``JoinWatch`` is a relay listener. It must run before ``PlayerTracker``, which records the join:
a first join is one with no earlier session for that name on that server.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from portcullis.bot.base import MOD_CHANNEL, NONE, Ctx, code
from portcullis.core.relay import escape
from portcullis.core.types import PlayerJoined, ServerEvent

log = logging.getLogger(__name__)

WELCOME_KEY = "first_join_welcome"  # per server, default on: post first joins in the events channel
NAME_RULES = "name_rules"  # bot: words that shouldn't appear in player names
RULES_LIMIT, RULE_LENGTH = 100, 40
# A new install has no history, so every join would look like a first one for a while.
SETTLE = timedelta(days=1)
_LEET = str.maketrans("0134578@$!|", "oieastbasii")


def name_rules(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [w for w in value if isinstance(w, str) and w][:RULES_LIMIT]


def parse_rules(text: str) -> list[str]:
    words = []
    for raw in re.split(r"[\n,]", text):
        word = raw.strip().lower()
        if word and word not in words:
            words.append(word[:RULE_LENGTH])
    return words[:RULES_LIMIT]


def rule_hit(name: str, rules: list[str]) -> str | None:
    """The first rule the name breaks. Also catches simple letter swaps (h4ck3r) and separators (h.a.c.k)."""
    lowered = name.lower()
    squashed = re.sub(r"[^a-z0-9]", "", lowered.translate(_LEET))
    for rule in rules:
        plain = re.sub(r"[^a-z0-9]", "", rule.translate(_LEET))
        if rule in lowered or (plain and plain in squashed):
            return rule
    return None


class JoinWatch:
    def __init__(self, ctx: Ctx, *, settle: timedelta = SETTLE) -> None:
        self.ctx = ctx
        self.settle = settle

    async def __call__(self, event: ServerEvent) -> None:
        if not isinstance(event, PlayerJoined):
            return
        row = await self.ctx.servers.get(event.server)
        if row is None:
            return
        name = event.player.name
        where = f"**{escape(row.name)}**"
        watched = await self.ctx.watchlist.get(name)
        if watched is not None:
            note = f" Note: {escape(watched.note)}" if watched.note else ""
            await self.ctx.post(MOD_CHANNEL, f"👀 **{escape(name)}** (on the watchlist) joined {where}.{note}")
        rule = rule_hit(name, name_rules(await self.ctx.settings.get(NAME_RULES)))
        if rule is not None:
            await self.ctx.post(MOD_CHANNEL, f"🚩 **{escape(name)}** joined {where}, and the name matches the "
                                             f"name rule {code(rule)}.")
        await self.welcome(row.shard_id, row.name, row.event_channel, name)

    async def welcome(self, server: str, server_name: str, channel_id: int | None, name: str) -> None:
        if not channel_id or not await self.ctx.settings.get(WELCOME_KEY, True, scope=server):
            return
        if await self.ctx.players.seen_on(server, name):
            return
        since = await self.ctx.players.tracked_since(server)
        if since is None or datetime.now(UTC) - datetime.fromisoformat(since) < self.settle:
            return
        try:
            channel = await self.ctx.channel(channel_id)
            await channel.send(f"👋 **{escape(name)}** joined **{escape(server_name)}** for the first time.",
                               allowed_mentions=NONE)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not post a first-join welcome in %s: %s", channel_id, exc)
