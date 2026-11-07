# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn raw hub events into ``portcullis.core.types`` events.

PROVISIONAL. The payload shapes below are guesses from the REST twins (ConsoleLineDto,
ConnectedUser, ShardState); nobody has captured a live payload yet. The parser is deliberately
lenient: it accepts camelCase or PascalCase keys, a payload with or without a leading shard id
argument, and falls back to ``OtherEvent`` rather than raising. Replace the guesses with fixtures
from ``tools/record_events.py`` once Min has run it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping
from datetime import UTC
from typing import Any

from portcullis.amp.auth import parse_instant
from portcullis.amp.signalr import HubEvent
from portcullis.core.types import (
    ConsoleLine,
    OtherEvent,
    Player,
    PlayerJoined,
    PlayerLeft,
    ServerEvent,
    ServerId,
    ServerState,
    StateChanged,
)

log = logging.getLogger(__name__)

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def field_of(data: Any, *names: str) -> Any:
    """Read a key in camelCase, PascalCase or snake_case."""
    if not isinstance(data, Mapping):
        return None
    for name in names:
        for key in (name, name[:1].upper() + name[1:], re.sub(r"([A-Z])", r"_\1", name).lower()):
            if key in data and data[key] is not None:
                return data[key]
    return None


def to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def state_from(data: Any, *, suspended: bool = False) -> tuple[ServerState, int | None]:
    """Map a ShardState (model dump or event payload) to a ServerState.

    Uses AMP's own isBusy/isRunning/isStopped flags, because the numbers behind ApplicationState
    are not documented (20 and 25 were both seen on a running shard).
    """
    raw = to_int(field_of(data, "status"))
    if suspended:
        return ServerState.SUSPENDED, raw
    if field_of(data, "isBusy"):
        return ServerState.BUSY, raw
    if field_of(data, "isRunning"):
        return ServerState.RUNNING, raw
    if field_of(data, "isStopped"):
        return (ServerState.FAILED if field_of(data, "lastErrorCode") else ServerState.STOPPED), raw
    return ServerState.UNKNOWN, raw


def player_from(data: Any) -> Player | None:
    inner = field_of(data, "user", "player")
    if isinstance(inner, Mapping):
        data = inner
    name = field_of(data, "name", "userName", "username", "displayName")
    if not isinstance(name, str) or not name:
        return None
    joined = field_of(data, "joinedAt")
    return Player(name=name, id=(str(field_of(data, "id")) if field_of(data, "id") is not None else None),
                  joined_at=parse_instant(joined) if isinstance(joined, str) else None)


def split_arguments(event: HubEvent, joined: Collection[str]) -> tuple[str | None, Any]:
    """Find which shard an event belongs to, and its payload."""
    args = list(event.arguments)
    if len(args) >= 2 and isinstance(args[0], str) and _UUID.match(args[0]):
        return args[0].lower(), args[1]
    payload = args[0] if args else None
    sid = field_of(payload, "shardId")
    if isinstance(sid, str) and _UUID.match(sid):
        return sid.lower(), payload
    if len(joined) == 1:
        return next(iter(joined)).lower(), payload
    return None, payload


def parse_hub_event(event: HubEvent, joined: Collection[str]) -> ServerEvent | None:
    sid, payload = split_arguments(event, joined)
    if sid is None:
        log.debug("Can't tell which shard %s belongs to; dropped", event.target)
        return None
    server = ServerId(sid)
    at = event.received_at if event.received_at.tzinfo else event.received_at.replace(tzinfo=UTC)

    if event.target == "ConsoleLine":
        text = field_of(payload, "text")
        if isinstance(payload, str):
            text = payload
        if isinstance(text, str):
            stamp = field_of(payload, "timestamp")
            when = parse_instant(stamp) if isinstance(stamp, str) else None
            return ConsoleLine(server=server, at=when or at, text=text, seq=to_int(field_of(payload, "seq")),
                               kind=str(field_of(payload, "kind") or "line"))
    elif event.target in ("UserJoined", "UserLeft"):
        player = player_from(payload)
        if player is not None:
            cls = PlayerJoined if event.target == "UserJoined" else PlayerLeft
            return cls(server=server, at=at, player=player)
    elif event.target == "ShardStateChanged":
        state, raw = state_from(payload)
        return StateChanged(server=server, at=at, state=state, raw_status=raw)
    return OtherEvent(server=server, at=at, name=event.target, payload=payload)
