# SPDX-License-Identifier: AGPL-3.0-or-later
"""The small interface core code uses to reach AMP (DESIGN.md section 6, rule 2).

The real implementation is ``portcullis.amp.port.AmpServerPort``; tests use
``tests.fakes.amp.FakeAmpPort``. Methods are added here as features are ported, never more.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from portcullis.core.types import Backup, PowerAction, ServerEvent, ServerId, ServerInfo, ServerStatus


@runtime_checkable
class AmpPort(Protocol):
    async def list_servers(self) -> list[ServerInfo]: ...

    async def get_status(self, server: ServerId) -> ServerStatus: ...

    async def power(self, server: ServerId, action: PowerAction) -> None: ...

    async def send_console(self, server: ServerId, line: str) -> None: ...

    async def recent_console(self, server: ServerId, limit: int = 50) -> list[str]: ...

    def events(self, server: ServerId) -> AsyncIterator[ServerEvent]: ...

    async def list_backups(self, server: ServerId) -> list[Backup]: ...

    # create_backup waits: TakeBody needs SnapshotKind and SnapshotContent numbers that the spec
    # doesn't list. Read them off a snapshot taken in the panel first.
