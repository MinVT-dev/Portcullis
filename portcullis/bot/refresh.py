# SPDX-License-Identifier: AGPL-3.0-or-later
"""How often live messages (status cards, the server board) are edited, and how they back off.

Discord allows a message to be edited often, but every edit counts against the bot's rate limits,
and a bot that keeps hitting them gets 429s and, if it ignores those, a temporary ban. So live
messages are edited every 2 to 5 minutes (``MIN_EVERY`` to ``MAX_EVERY``, an admin picks in
``/setup start``), at the slow end while nobody is playing, and back off further after a 429 or a
failed edit. The pacing is plain arithmetic on a clock so the tests can check it without waiting.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

MIN_EVERY = 120.0
MAX_EVERY = 300.0
DEFAULT_EVERY = 180.0
IDLE_EVERY = MAX_EVERY  # nobody online on any card's server
BACKOFF_MAX = 900.0  # never wait more than 15 minutes, so a card can't go stale for hours
MIN_GAP = 60.0  # a state change may pull the next edit forward, but never closer than this to the last one
EVERY_KEY = "live_every"  # bot setting: seconds between edits


def clamp_every(value: object) -> float:
    """A stored interval, held to 2 to 5 minutes. Anything unreadable is the default."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return DEFAULT_EVERY
    return min(MAX_EVERY, max(MIN_EVERY, float(value)))


def minutes(seconds: float) -> str:
    whole = round(seconds / 60)
    return f"{whole} minute" + ("" if whole == 1 else "s")


class Pacer:
    """When the next edit may go out."""

    def __init__(self, every: float = DEFAULT_EVERY, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.every = clamp_every(every)
        self.clock = clock
        self.failures = 0
        self.blocked_until = 0.0  # set by a 429 or a failure; nothing goes out before it
        self.last_edit: float | None = None
        self.last_problem: str | None = None

    def delay(self, *, idle: bool = False) -> float:
        """Seconds to wait before the next pass."""
        base = IDLE_EVERY if idle else self.every
        if self.last_edit is not None:
            base = max(0.0, self.last_edit + base - self.clock())
        return max(base, self.blocked_until - self.clock(), 0.0)

    def early_ok(self) -> bool:
        """May a state change pull the next pass forward now?"""
        now = self.clock()
        if now < self.blocked_until:
            return False
        return self.last_edit is None or now - self.last_edit >= MIN_GAP

    def edited(self) -> None:
        self.last_edit = self.clock()
        self.failures = 0
        self.blocked_until = 0.0
        self.last_problem = None

    def rate_limited(self, retry_after: float | None = None) -> float:
        """Discord answered 429. Wait what it asked, or double the interval each time, whichever is longer."""
        return self._back_off("Discord rate limit", retry_after)

    def failed(self, reason: str = "edit failed") -> float:
        """An edit failed some other way (Discord 5xx, a timeout). Same doubling, without a hint."""
        return self._back_off(reason, None)

    def _back_off(self, reason: str, retry_after: float | None) -> float:
        self.failures += 1
        wait = min(BACKOFF_MAX, self.every * 2 ** (self.failures - 1))
        if retry_after is not None and math.isfinite(retry_after) and retry_after > 0:
            wait = max(wait, min(retry_after, BACKOFF_MAX))
        self.blocked_until = self.clock() + wait
        self.last_problem = reason
        return wait

    @property
    def backing_off(self) -> bool:
        return self.clock() < self.blocked_until
