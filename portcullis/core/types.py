# SPDX-License-Identifier: AGPL-3.0-or-later
"""Types the rest of Portcullis uses to talk about servers. AMP's generated models never leave
``portcullis.amp``; the adapter there maps them to these."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, NewType

# AMP 3 shard UUID, as a string. Stable for the life of the shard.
ServerId = NewType("ServerId", str)


class PowerAction(StrEnum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    SLEEP = "sleep"
    WAKE = "wake"
    UPDATE = "update"
    # No KILL: AMP 3.0.0.6 has a power.kill permission node but no kill route (DESIGN.md section 13).


class ServerState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    BUSY = "busy"  # starting, stopping, updating, installing...
    SLEEPING = "sleeping"
    FAILED = "failed"
    SUSPENDED = "suspended"
    # A start no person was waiting on (boot, schedule, after an update) met the game's licence (EULA),
    # and AMP holds the server until someone accepts or declines it (ShardState.awaitsLicence).
    AWAITING_LICENCE = "licence"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Text shown next to any colour, so status is never colour-only (DESIGN.md section 7)."""
        return {
            "running": "Online",
            "stopped": "Offline",
            "busy": "Busy",
            "sleeping": "Sleeping",
            "failed": "Failed",
            "suspended": "Suspended",
            "licence": "Needs licence",
            "unknown": "Unknown",
        }[self.value]


class TryLater(Exception):
    """AMP can't do it right now, but may in a while (another job holds the server, or storage is down).
    The message is written for the person who asked."""


@dataclass(frozen=True)
class ServerInfo:
    id: ServerId
    name: str
    template: str | None = None
    suspended: bool = False
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Player:
    name: str
    id: str | None = None
    joined_at: datetime | None = None


@dataclass(frozen=True)
class ServerStatus:
    id: ServerId
    state: ServerState
    players: tuple[Player, ...] = ()
    max_players: int | None = None
    cpu_percent: float | None = None
    memory_mb: int | None = None
    memory_limit_mb: int | None = None
    message: str | None = None
    started_at: datetime | None = None
    raw_status: int | None = None  # AMP's ApplicationState number, kept until its values are mapped

    @property
    def player_count(self) -> int:
        return len(self.players)


@dataclass(frozen=True)
class Schedule:
    id: str
    name: str
    enabled: bool
    next_fire: datetime | None = None


@dataclass(frozen=True)
class ScheduleRun:
    """One firing of a schedule, from AMP's run history."""

    started_at: datetime | None
    finished_at: datetime | None
    status: str | None
    error: str | None = None


@dataclass(frozen=True)
class Backup:
    id: str
    name: str | None
    created_at: datetime | None
    size_mb: int | None = None
    status: str | None = None
    sticky: bool = False


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class ServerEvent:
    server: ServerId
    at: datetime


@dataclass(frozen=True)
class ConsoleLine(ServerEvent):
    text: str
    seq: int | None = None
    kind: str = "line"  # "line", or "marker" for a lifecycle note such as a restart
    stream: str = "main"  # "main" is the game's own output; AMP also sends "agent" and "system"


@dataclass(frozen=True)
class PlayerJoined(ServerEvent):
    player: Player


@dataclass(frozen=True)
class PlayerLeft(ServerEvent):
    player: Player


@dataclass(frozen=True)
class StateChanged(ServerEvent):
    state: ServerState
    raw_status: int | None = None


@dataclass(frozen=True)
class OtherEvent(ServerEvent):
    """An event Portcullis doesn't model yet (Metrics, Lifecycle, Snapshot, ...)."""

    name: str
    payload: Any = field(default=None)
