# SPDX-License-Identifier: MIT
"""``AmpClient``: one object that holds the HTTP session, the token manager, the typed API groups and
a factory for hub connections.

    async with AmpClient(url, Credentials(user, password)) as amp:
        shards = await amp.shards.get_shards()
        await amp.shards.post_shards_console(shard_id, {"command": "say hi"})
        data = await amp.downloads.file(shard_id, "server.properties")
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import aiohttp

from portcullis_api.auth import Credentials, TokenManager
from portcullis_api.downloads import Downloads
from portcullis_api.redact import Redactor
from portcullis_api.signalr import HubConnection, Mode
from portcullis_api.transport import AmpTransport

if TYPE_CHECKING:
    from portcullis_api import api


class AmpClient:
    # Generated API groups, attached in __init__ (see portcullis_api/api/__init__.py for the full list).
    shards: api.ShardsApi
    scheduling: api.SchedulingApi
    files_backups: api.FilesBackupsApi
    my_account: api.MyAccountApi
    authentication_setup: api.AuthenticationSetupApi
    system_health: api.SystemHealthApi
    templates: api.TemplatesApi
    users_access: api.UsersAccessApi

    def __init__(
        self,
        base_url: str,
        credentials: Credentials,
        *,
        session: aiohttp.ClientSession | None = None,
        redactor: Redactor | None = None,
        refresh_margin: timedelta = timedelta(minutes=30),
        timeout: float = 30.0,
        keep_session_renewed: bool = True,
        check_capabilities: bool = True,
    ) -> None:
        from portcullis_api.api import GROUPS  # generated; imported lazily to keep start-up cheap

        self.base_url = base_url.rstrip("/")
        self._own_session = session is None
        self.session = session or aiohttp.ClientSession()
        self.redactor = redactor or Redactor()
        self.auth = TokenManager(self.base_url, self.session, credentials, redactor=self.redactor,
                                 refresh_margin=refresh_margin, timeout=timeout,
                                 check_capabilities=check_capabilities)
        self.transport = AmpTransport(self.base_url, self.session, self.auth, timeout=timeout)
        for name, cls in GROUPS.items():
            setattr(self, name, cls(self.transport))
        # Ticket-based downloads (files, zips, snapshots); see portcullis_api.downloads.
        self.downloads = Downloads(self.transport, self.files_backups)
        self._keep_renewed = keep_session_renewed
        self._renewal: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.auth.token()  # log in now so bad credentials fail fast
        if self._keep_renewed and self._renewal is None:
            self._renewal = asyncio.create_task(self.auth.run_renewal(), name="amp-session-renewal")

    async def close(self) -> None:
        if self._renewal is not None:
            self._renewal.cancel()
            try:
                await self._renewal
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutting down
                pass
            self._renewal = None
        await self.auth.logout()
        if self._own_session:
            await self.session.close()

    async def __aenter__(self) -> AmpClient:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def hub(self, name: str = "shard", *, mode: Mode = "direct", **kwargs: Any) -> HubConnection:
        """A hub connection that fetches a fresh single-use ticket for every connect."""
        return HubConnection(self.base_url, name, self.session, self.auth.hub_ticket, mode=mode,
                             redactor=self.redactor, **kwargs)
