# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is happening right now, built from relay events: server states, who is online, and the
player history behind ``/player``. Also verifies ``/link`` codes said in game chat.

All of these are relay listeners (``Relay.listeners``), so they see every followed server.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from portcullis.core.relay import ChatLine
from portcullis.core.types import (
    PlayerJoined,
    PlayerLeft,
    ServerEvent,
    ServerId,
    ServerState,
    ServerStatus,
    StateChanged,
)
from portcullis.db.database import now_iso
from portcullis.db.features import LinkCode, LinkCodeRepo, PlayerRepo
from portcullis.db.repos import UserRepo

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Summary:
    servers: int
    online: int
    players: int
    reachable: bool = True

    def text(self) -> str:
        """The bot's profile status, e.g. ``2/4 servers online · 5 players``."""
        if not self.reachable:
            return "Can't reach AMP"
        if self.servers == 0:
            return "No servers yet"
        players = f"{self.players} player" + ("" if self.players == 1 else "s")
        return f"{self.online}/{self.servers} servers online · {players}"


class LiveState:
    """Current state and player names per server. ``on_change`` runs after anything visible changes."""

    def __init__(self, on_change: Callable[[], Awaitable[None]] | None = None) -> None:
        self.states: dict[ServerId, ServerState] = {}
        self.players: dict[ServerId, set[str]] = {}
        self.reachable = True
        self.on_change = on_change

    def summary(self) -> Summary:
        online = sum(1 for s in self.states.values() if s is ServerState.RUNNING)
        players = sum(len(p) for sid, p in self.players.items() if self.states.get(sid) is ServerState.RUNNING)
        return Summary(servers=len(self.states), online=online, players=players, reachable=self.reachable)

    async def seed(self, statuses: Iterable[ServerStatus], known: Iterable[ServerId]) -> None:
        """Replace everything with a fresh poll. Servers in ``known`` without a status are Unknown."""
        self.states = {sid: ServerState.UNKNOWN for sid in known}
        self.players = {}
        for status in statuses:
            self.states[status.id] = status.state
            self.players[status.id] = {p.name for p in status.players}
        await self._changed()

    async def set_reachable(self, reachable: bool) -> None:
        if reachable != self.reachable:
            self.reachable = reachable
            await self._changed()

    async def __call__(self, event: ServerEvent) -> None:
        if event.server not in self.states:
            return  # hidden or unknown: not part of the summary
        before = self.summary()
        if isinstance(event, StateChanged):
            self.states[event.server] = event.state
            if event.state is not ServerState.RUNNING:
                self.players.pop(event.server, None)
        elif isinstance(event, PlayerJoined):
            self.players.setdefault(event.server, set()).add(event.player.name)
        elif isinstance(event, PlayerLeft):
            self.players.get(event.server, set()).discard(event.player.name)
        else:
            return
        if self.summary() != before:
            await self._changed()

    async def _changed(self) -> None:
        if self.on_change is not None:
            await self.on_change()


class PlayerTracker:
    """Writes join and leave into ``player_sessions`` for ``/player lookup`` and ``/player top``."""

    def __init__(self, players: PlayerRepo) -> None:
        self.players = players

    async def __call__(self, event: ServerEvent) -> None:
        if isinstance(event, PlayerJoined):
            await self.players.joined(event.server, event.player.name, event.player.id,
                                      at=event.at.isoformat(timespec="seconds"))
        elif isinstance(event, PlayerLeft):
            await self.players.left(event.server, event.player.name, at=event.at.isoformat(timespec="seconds"))
        elif isinstance(event, StateChanged) and event.state in (ServerState.STOPPED, ServerState.FAILED,
                                                                 ServerState.SLEEPING):
            await self.players.close_all(event.server, at=now_iso())


class LinkVerifier:
    """Turns a ``/link`` code said in game chat into a verified link."""

    def __init__(self, codes: LinkCodeRepo, users: UserRepo,
                 on_linked: Callable[[LinkCode], Awaitable[None]] | None = None) -> None:
        self.codes = codes
        self.users = users
        self.on_linked = on_linked

    async def __call__(self, event: ServerEvent) -> None:
        if not isinstance(event, ChatLine):
            return
        code = await self.codes.match(event.server, event.player, event.text)
        if code is None:
            return
        try:
            await self.users.add_link(code.discord_id, code.platform, event.player, shard_id=code.shard_id)
        except LookupError:
            log.warning("link code from %s matched, but the name is already linked to another member", event.player)
            return
        log.info("linked Discord user %s to %s on %s", code.discord_id, event.player, code.platform)
        if self.on_linked is not None:
            await self.on_linked(code)
