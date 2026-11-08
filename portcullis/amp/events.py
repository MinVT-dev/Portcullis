# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn raw hub events into ``portcullis.core.types`` events.

Argument shapes seen live on AMP 3.0.0.6 (recorder capture, 2026-11-07):

* ``Metrics``: ``[shardId, sample]``, every ~3 s
* ``UserRoster``: ``[shardId, [users]]``, every ~10 s
* ``ShardStateChanged``: ``[tenantId, shardId, ShardState]``, every ~9 s even when nothing changed
* ``ConsoleLine``: ``[line]``, with NO shard id, and ``seq`` counts across all shards, so the
  shard comes from the connection (one connection per shard). ``streamId`` is ``main`` (the
  game), ``agent`` (AMP's shard agent) or ``system`` (``kind: marker`` lifecycle notes carrying a
  ``state``). Player chat has not been captured yet.
* ``Lifecycle``: ``[event]`` with ``shardId``, a numeric ``state`` and a ``message``.

``UserJoined`` and ``UserLeft`` have not been seen yet; ``UserRoster`` may be the only source.
The parser stays lenient: camelCase or PascalCase keys, optional leading tenant and shard id
arguments, and ``OtherEvent`` rather than an exception.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping
from datetime import UTC
from typing import Any

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
from portcullis_api.auth import parse_instant
from portcullis_api.signalr import HubEvent

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
    return Player(
        name=name,
        id=(str(field_of(data, "id")) if field_of(data, "id") is not None else None),
        joined_at=parse_instant(joined) if isinstance(joined, str) else None,
    )


def split_arguments(event: HubEvent, joined: Collection[str]) -> tuple[str | None, Any]:
    """Find which shard an event belongs to, and its payload."""
    args = list(event.arguments)
    # A leading tenant id (e.g. "_root") comes before the shard id on some events.
    if len(args) >= 3 and isinstance(args[0], str) and not _UUID.match(args[0]):
        args = args[1:]
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
            return ConsoleLine(
                server=server,
                at=when or at,
                text=text,
                seq=to_int(field_of(payload, "seq")),
                kind=str(field_of(payload, "kind") or "line"),
                stream=str(field_of(payload, "streamId") or "main"),
            )
    elif event.target in ("UserJoined", "UserLeft"):
        player = player_from(payload)
        if player is not None:
            cls = PlayerJoined if event.target == "UserJoined" else PlayerLeft
            return cls(server=server, at=at, player=player)
    elif event.target == "ShardStateChanged":
        state, raw = state_from(payload)
        return StateChanged(server=server, at=at, state=state, raw_status=raw)
    return OtherEvent(server=server, at=at, name=event.target, payload=payload)
