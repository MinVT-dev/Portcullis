# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-memory ``AmpPort`` for core and command tests (DESIGN.md section 6, Phase 0.4).

It can list servers, change power state and emit console and player events, with no network.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from portcullis.core.types import (
    Backup,
    ConsoleLine,
    Player,
    PlayerJoined,
    PlayerLeft,
    PowerAction,
    Schedule,
    ScheduleRun,
    ServerEvent,
    ServerId,
    ServerInfo,
    ServerState,
    ServerStatus,
    StateChanged,
)


class FakeAmpError(Exception):
    pass


@dataclass
class FakeServer:
    info: ServerInfo
    state: ServerState = ServerState.STOPPED
    players: list[Player] = field(default_factory=list)
    console: list[str] = field(default_factory=list)
    backups: list[Backup] = field(default_factory=list)
    schedules: list[Schedule] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC)


class FakeAmpPort:
    def __init__(self) -> None:
        self.grants: list[str] = [">"]
        self.preview_expires: datetime | None = None
        self.servers: dict[ServerId, FakeServer] = {}
        self.sent: list[tuple[ServerId, str]] = []
        self.power_calls: list[tuple[ServerId, PowerAction]] = []
        self.backup_calls: list[tuple[str, ServerId, str]] = []
        self.schedule_calls: list[tuple[str, ServerId, str]] = []
        self.schedule_runs: dict[str, list[ScheduleRun]] = {}
        self.licence_answers: list[tuple[ServerId, bool]] = []
        self._subs: dict[ServerId, set[asyncio.Queue[ServerEvent]]] = {}

    # ------------------------------------------------------------------ setup helpers

    def add_server(self, sid: str, name: str, *, template: str = "minecraft",
                   state: ServerState = ServerState.STOPPED) -> ServerId:
        server_id = ServerId(sid)
        self.servers[server_id] = FakeServer(ServerInfo(server_id, name, template), state)
        return server_id

    def _get(self, server: ServerId) -> FakeServer:
        try:
            return self.servers[server]
        except KeyError:
            raise FakeAmpError(f"no such server {server}") from None

    def emit(self, event: ServerEvent) -> None:
        for queue in self._subs.get(event.server, ()):
            queue.put_nowait(event)

    def say(self, server: ServerId, text: str) -> None:
        self._get(server).console.append(text)
        self.emit(ConsoleLine(server=server, at=_now(), text=text))

    def join(self, server: ServerId, name: str) -> None:
        player = Player(name, joined_at=_now())
        self._get(server).players.append(player)
        self.emit(PlayerJoined(server=server, at=_now(), player=player))

    def leave(self, server: ServerId, name: str) -> None:
        srv = self._get(server)
        player = next((p for p in srv.players if p.name == name), Player(name))
        srv.players = [p for p in srv.players if p.name != name]
        self.emit(PlayerLeft(server=server, at=_now(), player=player))

    def _set_state(self, server: ServerId, state: ServerState) -> None:
        self._get(server).state = state
        self.emit(StateChanged(server=server, at=_now(), state=state))

    # ------------------------------------------------------------------ AmpPort

    async def list_servers(self) -> list[ServerInfo]:
        return [s.info for s in self.servers.values()]

    async def get_status(self, server: ServerId) -> ServerStatus:
        srv = self._get(server)
        return ServerStatus(id=server, state=srv.state, players=tuple(srv.players))

    async def power(self, server: ServerId, action: PowerAction) -> None:
        srv = self._get(server)
        self.power_calls.append((server, action))
        if action in (PowerAction.START, PowerAction.RESTART, PowerAction.WAKE):
            if action is PowerAction.RESTART:
                self._set_state(server, ServerState.BUSY)
            self._set_state(server, ServerState.RUNNING)
        elif action is PowerAction.STOP:
            for p in list(srv.players):
                self.leave(server, p.name)
            self._set_state(server, ServerState.STOPPED)
        elif action is PowerAction.SLEEP:
            self._set_state(server, ServerState.SLEEPING)
        elif action is PowerAction.UPDATE:
            self._set_state(server, ServerState.BUSY)
            self._set_state(server, ServerState.STOPPED)

    async def send_console(self, server: ServerId, line: str) -> None:
        self._get(server)
        self.sent.append((server, line))

    async def recent_console(self, server: ServerId, limit: int = 50) -> list[str]:
        return self._get(server).console[-limit:]

    async def events(self, server: ServerId) -> AsyncIterator[ServerEvent]:
        self._get(server)
        queue: asyncio.Queue[ServerEvent] = asyncio.Queue()
        self._subs.setdefault(server, set()).add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subs[server].discard(queue)

    async def list_backups(self, server: ServerId) -> list[Backup]:
        return list(self._get(server).backups)

    async def create_backup(self, server: ServerId, name: str) -> None:
        srv = self._get(server)
        if not srv.backups:
            raise FakeAmpError("there is no earlier backup to copy the settings from")
        self.backup_calls.append(("create", server, name))
        srv.backups.append(Backup(f"b{len(srv.backups)}", name, _now()))

    async def restore_backup(self, server: ServerId, backup_id: str) -> None:
        self._get(server)
        self.backup_calls.append(("restore", server, backup_id))

    async def delete_backup(self, server: ServerId, backup_id: str) -> None:
        srv = self._get(server)
        self.backup_calls.append(("delete", server, backup_id))
        srv.backups = [b for b in srv.backups if b.id != backup_id]

    async def list_schedules(self, server: ServerId) -> list[Schedule]:
        return list(self._get(server).schedules)

    async def run_schedule(self, server: ServerId, schedule_id: str) -> None:
        self._get(server)
        self.schedule_calls.append(("run", server, schedule_id))

    async def set_schedule_enabled(self, server: ServerId, schedule_id: str, enabled: bool) -> None:
        srv = self._get(server)
        self.schedule_calls.append(("enabled" if enabled else "disabled", server, schedule_id))
        srv.schedules = [Schedule(s.id, s.name, enabled, s.next_fire) if s.id == schedule_id else s
                         for s in srv.schedules]

    async def delete_schedule(self, server: ServerId, schedule_id: str) -> None:
        srv = self._get(server)
        self.schedule_calls.append(("delete", server, schedule_id))
        srv.schedules = [s for s in srv.schedules if s.id != schedule_id]

    async def schedule_history(self, server: ServerId, schedule_id: str, limit: int = 10) -> list[ScheduleRun]:
        self._get(server)
        return list(self.schedule_runs.get(schedule_id, []))[:limit]

    async def preview_expires_at(self) -> datetime | None:
        return self.preview_expires

    async def permissions(self) -> list[str]:
        return list(self.grants)

    async def answer_licence(self, server: ServerId, accept: bool) -> None:
        srv = self._get(server)
        self.licence_answers.append((server, accept))
        if srv.state is ServerState.AWAITING_LICENCE:
            self._set_state(server, ServerState.STOPPED)
