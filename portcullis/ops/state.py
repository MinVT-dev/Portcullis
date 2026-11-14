# SPDX-License-Identifier: AGPL-3.0-or-later
"""Restart recovery: a small JSON file next to the database (``PORTCULLIS_STATE_FILE``).

It holds two things the database doesn't:

* **How the last run ended.** ``started_at`` and ``seen_at`` (a heartbeat every minute) are written
  while running; ``stopped_at`` and ``stop_reason`` on a clean stop. A file with no ``stopped_at``
  means the last run crashed or the machine lost power; the boot banner says so, with when the bot
  was last seen alive.
* **The last status of every server**, so after a restart with the panel down the live cards show
  that data, marked stale, instead of "Unknown".

Live card message ids are already in the database (``live_card`` per server), so a restart edits
the same messages, and a deleted message is replaced with a new one.

The file is written to a temporary name and moved into place, so a crash mid-write leaves the old
file whole. A missing or broken file is treated as a first run, never as an error.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FORMAT = 1


def now() -> datetime:
    return datetime.now(UTC)


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


@dataclass(frozen=True)
class LastRun:
    started_at: datetime | None
    seen_at: datetime | None
    stopped_at: datetime | None
    stop_reason: str | None
    version: str | None

    @property
    def clean(self) -> bool:
        return self.stopped_at is not None


def ago(when: datetime, at: datetime | None = None) -> str:
    seconds = max(0, int(((at or now()) - when).total_seconds()))
    if seconds < 90:
        return f"{seconds} s ago"
    if seconds < 5400:
        return f"{round(seconds / 60)} min ago"
    if seconds < 172800:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} days ago"


def describe(last: LastRun | None, at: datetime | None = None) -> str:
    """One line for the boot banner."""
    if last is None:
        return "first start (no earlier run recorded)"
    if last.stopped_at is not None:
        reason = f" ({last.stop_reason})" if last.stop_reason else ""
        return f"stopped cleanly {ago(last.stopped_at, at)}{reason}"
    seen = last.seen_at or last.started_at
    if seen is None:
        return "ended without a clean stop"
    return f"ended without a clean stop (crash or power loss); last seen {ago(seen, at)}"


class StateFile:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.data: dict[str, Any] = {}
        self.last_run: LastRun | None = None

    def load(self) -> dict[str, Any]:
        """Read the file left by the last run. Returns the saved statuses (possibly empty)."""
        if self.path is None or not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("ignoring the state file %s, which can't be read (%s); starting fresh", self.path, exc)
            return {}
        if not isinstance(data, dict) or data.get("version") != FORMAT:
            return {}
        run = data.get("run") if isinstance(data.get("run"), dict) else {}
        self.last_run = LastRun(
            started_at=parse_time(run.get("started_at")),
            seen_at=parse_time(run.get("seen_at")),
            stopped_at=parse_time(run.get("stopped_at")),
            stop_reason=run.get("stop_reason") if isinstance(run.get("stop_reason"), str) else None,
            version=run.get("version") if isinstance(run.get("version"), str) else None,
        )
        statuses = data.get("statuses")
        return statuses if isinstance(statuses, dict) else {}

    def started(self, version: str, statuses: dict[str, Any] | None = None) -> None:
        stamp = now().isoformat()
        self.data = {"version": FORMAT, "run": {"started_at": stamp, "seen_at": stamp, "stopped_at": None,
                                                 "stop_reason": None, "version": version, "pid": os.getpid()},
                     "statuses": statuses or {}}
        self.save()

    def heartbeat(self, statuses: dict[str, Any] | None = None) -> None:
        if not self.data:
            return
        self.data["run"]["seen_at"] = now().isoformat()
        if statuses is not None:
            self.data["statuses"] = statuses
        self.save()

    def stopped(self, reason: str, statuses: dict[str, Any] | None = None) -> None:
        if not self.data:
            return
        self.data["run"]["stopped_at"] = now().isoformat()
        self.data["run"]["stop_reason"] = reason
        self.heartbeat(statuses)

    def save(self) -> bool:
        if self.path is None:
            return False
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)  # atomic on Windows and Linux
            return True
        except OSError as exc:  # an antivirus holding the file on Windows, a full disk: try next time
            log.debug("could not save the state file %s: %s", self.path, exc)
            return False
