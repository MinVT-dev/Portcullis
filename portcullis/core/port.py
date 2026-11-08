# SPDX-License-Identifier: AGPL-3.0-or-later
"""The small interface core code uses to reach AMP (DESIGN.md section 6, rule 2).

The real implementation is ``portcullis.amp.port.AmpServerPort``; tests use
``tests.fakes.amp.FakeAmpPort``. Methods are added here as features are ported, never more.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from portcullis.core.types import (
    Backup,
    PowerAction,
    Schedule,
    ScheduleRun,
    ServerEvent,
    ServerId,
    ServerInfo,
    ServerStatus,
)


@runtime_checkable
class AmpPort(Protocol):
    async def list_servers(self) -> list[ServerInfo]: ...

    async def get_status(self, server: ServerId) -> ServerStatus: ...

    async def power(self, server: ServerId, action: PowerAction) -> None: ...

    async def send_console(self, server: ServerId, line: str) -> None: ...

    async def recent_console(self, server: ServerId, limit: int = 50) -> list[str]: ...

    def events(self, server: ServerId) -> AsyncIterator[ServerEvent]: ...

    async def list_backups(self, server: ServerId) -> list[Backup]: ...

    async def create_backup(self, server: ServerId, name: str) -> None:
        """Copies the kind and content of the newest existing backup. Raises if there is none to copy."""
        ...

    async def restore_backup(self, server: ServerId, backup_id: str) -> None: ...

    async def delete_backup(self, server: ServerId, backup_id: str) -> None: ...

    async def list_schedules(self, server: ServerId) -> list[Schedule]: ...

    async def run_schedule(self, server: ServerId, schedule_id: str) -> None: ...

    async def set_schedule_enabled(self, server: ServerId, schedule_id: str, enabled: bool) -> None: ...

    async def delete_schedule(self, server: ServerId, schedule_id: str) -> None: ...

    async def schedule_history(self, server: ServerId, schedule_id: str, limit: int = 10) -> list[ScheduleRun]: ...

    async def permissions(self) -> list[str]: ...

    # create_backup waits: TakeBody needs SnapshotKind and SnapshotContent numbers that the spec
    # doesn't list. Read them off a snapshot taken in the panel first.
