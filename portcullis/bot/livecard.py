# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live status cards: one message per server in a channel, edited in place.

Each card is ``cards.status_embed`` with the footer "Updates every N minutes" and Panel and Support
link buttons when those links are set. One loop edits every card, paced by ``refresh.Pacer``: every
2 to 5 minutes, 5 while nobody is online, longer after a Discord 429 or a failed edit. A state
change (a server going down, say) may pull the next pass forward, but never closer than a minute to
the last one. Edits go to a partial message, so a pass costs one request per card and no fetch.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

import discord

from portcullis.bot.base import MUTED, NONE, Ctx, as_id
from portcullis.bot.cards import PANEL_URL, SUPPORT_URL, link_buttons, next_restart, status_embed
from portcullis.bot.refresh import EVERY_KEY, Pacer, clamp_every
from portcullis.bot.server import CONNECT_KEY
from portcullis.core.guard import PanelUnavailable
from portcullis.core.types import Schedule, ServerId, ServerState, ServerStatus
from portcullis.db.repos import ServerRow

log = logging.getLogger(__name__)

CARD_KEY = "live_card"  # server setting: {"channel": id, "message": id}
STATUS_TIMEOUT = 8.0
SCHEDULES_EVERY = 900.0  # schedules rarely change; read them at most every 15 minutes per server
MAX_CARDS = 25
STOPPED_FOOTER = "Portcullis is stopped, so this card is not updating"


def stored_card(value: object) -> tuple[int, int | None] | None:
    if not isinstance(value, dict):
        return None
    channel = as_id(value.get("channel"))
    if channel is None:
        return None
    return channel, as_id(value.get("message"))


def mark_stale(embed: discord.Embed, at: datetime) -> discord.Embed:
    """Grey the card and say the data is old, so nobody reads a frozen status as current."""
    embed.colour = MUTED
    note = (f"The panel isn't answering. This is what it said {discord.utils.format_dt(at, 'R')}; "
            "the card updates by itself once it answers again.")
    embed.description = f"{embed.description}\n\n{note}" if embed.description else note
    return embed


def retry_after(exc: BaseException) -> float | None:
    value = getattr(exc, "retry_after", None)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


class LiveCards:
    def __init__(self, ctx: Ctx, *, pacer: Pacer | None = None) -> None:
        self.ctx = ctx
        self.pacer = pacer or Pacer()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._schedules: dict[str, tuple[float, list[Schedule]]] = {}
        self._states: dict[str, ServerState] = {}
        self._shown: dict[str, tuple[int, int, discord.Embed]] = {}  # shard -> channel, message, last embed

    # ---------------------------------------------------------------- placing and removing

    async def place(self, row: ServerRow, channel_id: int, *, by: int | None = None) -> bool:
        """Post a new card for ``row`` in ``channel_id`` (replacing any older one's record)."""
        await self.ctx.settings.set(CARD_KEY, {"channel": channel_id}, scope=row.shard_id, by=by)
        return await self.refresh_one(row)

    async def remove(self, row: ServerRow) -> None:
        await self.ctx.settings.delete(CARD_KEY, scope=row.shard_id)

    async def rows(self) -> list[tuple[ServerRow, int, int | None]]:
        out = []
        for row in await self.ctx.servers.list():
            found = stored_card(await self.ctx.settings.get(CARD_KEY, scope=row.shard_id))
            if found is not None:
                out.append((row, *found))
        return out[:MAX_CARDS]

    # ---------------------------------------------------------------- one card

    async def _restart(self, shard_id: str) -> Schedule | None:
        now = time.monotonic()
        cached = self._schedules.get(shard_id)
        if cached is None or now - cached[0] > SCHEDULES_EVERY:
            try:
                items = await asyncio.wait_for(self.ctx.port.list_schedules(ServerId(shard_id)), STATUS_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - no schedule permission: the field is just left out
                log.debug("schedules for %s unavailable: %s", shard_id, exc)
                items = cached[1] if cached is not None else []  # keep the last list through a panel outage
            cached = (now, items)
            self._schedules[shard_id] = cached
        return next_restart(cached[1], datetime.now(UTC))

    async def build(self, row: ServerRow) -> tuple[discord.Embed, discord.ui.View | None]:
        stale_at: datetime | None = None
        try:
            status = await asyncio.wait_for(self.ctx.port.get_status(ServerId(row.shard_id)), STATUS_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - the card says AMP didn't answer instead of going stale silently
            if isinstance(exc, PanelUnavailable):
                log.debug("live card of %s: panel paused", row.name)  # the guard logged the outage once
            else:
                log.warning("status for the live card of %s failed: %s", row.name, exc)
            cached = self._cached(row.shard_id)
            if cached is not None:
                status, stale_at = cached
            else:
                status = ServerStatus(id=ServerId(row.shard_id), state=ServerState.UNKNOWN,
                                      message="AMP didn't answer the last check.")
        if stale_at is None:
            self._states[row.shard_id] = status.state
        address = await self.ctx.settings.get(CONNECT_KEY, scope=row.shard_id)
        embed = status_embed(row, status, address if isinstance(address, str) else None,
                             restart=await self._restart(row.shard_id), every=self.pacer.every,
                             going_down=self.ctx.offline_expected(row.shard_id), emojis=self.ctx.emojis)
        if stale_at is not None:
            mark_stale(embed, stale_at)
        view = link_buttons(await self.ctx.settings.get(PANEL_URL), await self.ctx.settings.get(SUPPORT_URL))
        return embed, view

    def _cached(self, shard_id: str) -> tuple[ServerStatus, datetime] | None:
        """The last status the panel gave, if the port keeps one (``core.guard.GuardedPort`` does)."""
        lookup = getattr(self.ctx.port, "cached_status", None)
        if not callable(lookup):
            return None
        found = lookup(shard_id)
        return found if isinstance(found, tuple) and len(found) == 2 else None

    async def refresh_one(self, row: ServerRow) -> bool:
        """Edit (or post) one card. Returns False on any failure; a 429 raises so the pass stops."""
        found = stored_card(await self.ctx.settings.get(CARD_KEY, scope=row.shard_id))
        if found is None or self.ctx.client is None:
            return False
        channel_id, message_id = found
        embed, view = await self.build(row)
        extra: dict[str, Any] = {"embed": embed, "allowed_mentions": NONE}
        if view is not None:
            extra["view"] = view
        try:
            channel = await self.ctx.channel(channel_id)
            if message_id is not None:
                try:
                    message = channel.get_partial_message(message_id)
                    await message.edit(**({"view": None} | extra))
                    self._shown[row.shard_id] = (channel_id, message_id, embed)
                    return True
                except discord.NotFound:
                    pass  # someone deleted the card: post a new one
            posted = await channel.send(**extra)
        except discord.HTTPException as exc:
            if exc.status == 429:
                raise
            log.warning("could not update the live card of %s in channel %s: %s", row.name, channel_id, exc)
            return False
        except Exception as exc:  # noqa: BLE001 - a gone channel must not stop the other cards
            log.warning("could not update the live card of %s in channel %s: %s", row.name, channel_id, exc)
            return False
        await self.ctx.settings.set(CARD_KEY, {"channel": channel_id, "message": posted.id}, scope=row.shard_id)
        self._shown[row.shard_id] = (channel_id, posted.id, embed)
        return True

    async def mark_stopped(self) -> int:
        """Grey every card this run edited and say it has stopped updating. Returns how many changed.

        Uses the embed already in memory, so it costs one edit per card and no reads. The next start
        refreshes every card at once, which replaces this footer. Errors are logged, never raised; the
        caller wraps this in a short timeout so a slow Discord can't hold up the exit.
        """
        client = self.ctx.client
        if client is None or not self._shown:
            return 0

        async def grey(channel_id: int, message_id: int, embed: discord.Embed) -> bool:
            stopped = embed.copy()
            stopped.colour = MUTED
            stopped.set_footer(text=STOPPED_FOOTER)
            try:
                await client.get_partial_messageable(channel_id).get_partial_message(message_id).edit(embed=stopped)
                return True
            except Exception as exc:  # noqa: BLE001 - a gone card or a 429: leave it as it is
                log.debug("could not mark the live card %s as stopped: %s", message_id, exc)
                return False

        done = await asyncio.gather(*(grey(*shown) for shown in self._shown.values()))
        return sum(done)

    # ---------------------------------------------------------------- the loop

    async def refresh_all(self) -> None:
        """One pass over every card, then tell the pacer how it went."""
        self.pacer.every = clamp_every(await self.ctx.settings.get(EVERY_KEY))
        rows = await self.rows()
        if not rows:
            return
        failed = 0
        for row, _channel, _message in rows:
            try:
                ok = await self.refresh_one(row)
            except discord.HTTPException as exc:  # only 429 gets here
                wait = self.pacer.rate_limited(retry_after(exc))
                log.warning("Discord rate-limited the live cards; next try in %.0f s", wait)
                return
            failed += not ok
        if failed == len(rows):
            wait = self.pacer.failed("no card could be updated")
            log.warning("no live card could be updated; next try in %.0f s", wait)
        else:
            self.pacer.edited()

    def idle(self) -> bool:
        """Nobody online on any server shown on a card."""
        live = self.ctx.live
        return not any(live.players.get(ServerId(sid)) for sid in self._states)

    def nudge(self) -> None:
        """A server changed state: refresh soon, if the pacer allows."""
        changed = any(self.ctx.live.states.get(ServerId(sid)) not in (None, state)
                      for sid, state in self._states.items())
        if changed and self.pacer.early_ok():
            self._wake.set()

    async def run(self) -> None:
        while True:
            try:
                await self.refresh_all()
            except Exception:  # noqa: BLE001 - one bad pass (a locked database) must not end the cards
                log.exception("live card pass failed")
                self.pacer.failed("internal error")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.pacer.delay(idle=self.idle()))

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="portcullis-live-cards")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
