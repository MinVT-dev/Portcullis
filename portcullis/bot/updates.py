# SPDX-License-Identifier: AGPL-3.0-or-later
"""Game update status: updating, finished and failed, posted through the alert router.

What AMP 3 lets Portcullis see, and how each state is found:

* **Updating.** An update started through Portcullis (``server.power.update`` in the audit log), or a
  state change to Busy whose ``statusMessage`` mentions updating, downloading or installing. The status
  message is free text from AMP's agent and its wording has not been captured yet, so an update started
  from AMP's own panel may only show up when it finishes.
* **Finished.** ``installedVersion`` or ``installedAt`` on the server changes between two reads of the
  server list. Portcullis already reads that list every 5 minutes, so this costs no extra calls. The last
  value seen is stored, so an update that ran while Portcullis was off is still reported once.
* **Failed.** The server goes to Failed while an update is in progress.
* **Update available: not detectable.** AMP 3 has no field or event for it. Templates carry an
  ``updateAvailableRegex`` that AMP applies to console output itself, but the result isn't exposed.

While an update runs, the offline alert stays quiet (an update stops the server on purpose).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Iterable
from typing import Any

from portcullis.bot.alerting import Alert, AlertRouter
from portcullis.bot.base import Ctx, code
from portcullis.core.relay import escape
from portcullis.core.types import ServerEvent, ServerInfo, ServerState, StateChanged
from portcullis.db.repos import AuditEntry

log = logging.getLogger(__name__)

SEEN_KEY = "installed_seen"  # server scope: {"version": str|None, "at": str|None}
NOTICES_KEY = "update_notices"  # bot scope: false turns update posts off (default on)
UPDATING_WORDS = re.compile(r"(?i)\b(updat\w*|download\w*|install\w*)")
GIVE_UP = 7200.0  # an update with no result after 2 hours is forgotten without a post


class Updates:
    def __init__(self, ctx: Ctx, router: AlertRouter, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.ctx = ctx
        self.router = router
        self.clock = clock
        self.updating: dict[str, float] = {}
        self.tasks: set[asyncio.Task[None]] = set()

    async def _enabled(self) -> bool:
        return await self.ctx.settings.get(NOTICES_KEY, True) is not False

    async def _name(self, server: str) -> str:
        row = await self.ctx.servers.get(server)
        return escape(row.name) if row else server

    async def started(self, server: str, how: str) -> None:
        server = server.lower()
        if server in self.updating and self.clock() - self.updating[server] < GIVE_UP:
            return
        self.updating[server] = self.clock()
        self.ctx.expect_offline(server)
        if await self._enabled():
            await self.router.send(Alert(kind="update", server=server, severity="info", ping=False,
                                         title="Updating", text=f"⬇️ **{await self._name(server)}** is updating "
                                                                f"({how})."))

    def on_audit(self, entry: AuditEntry) -> None:
        """``AuditRepo.listeners`` hook: an update someone started through Portcullis."""
        if entry.action == "server.power.update" and entry.result == "ok" and entry.shard_id:
            task = asyncio.create_task(self.started(entry.shard_id, "started through Portcullis"),
                                       name="portcullis-update-started")
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def __call__(self, event: ServerEvent) -> None:
        if not isinstance(event, StateChanged):
            return
        server = event.server.lower()
        if event.state is ServerState.BUSY and event.message and UPDATING_WORDS.search(event.message):
            await self.started(server, f"AMP says: {event.message[:100]}")
            return
        since = self.updating.get(server)
        if since is None:
            return
        if self.clock() - since > GIVE_UP:
            self.updating.pop(server, None)
            return
        if event.state is ServerState.FAILED:
            self.updating.pop(server, None)
            fields = (("AMP says", event.message[:300]),) if event.message else ()
            await self.router.send(Alert(kind="update_failed", server=server, severity="bad", title="Update failed",
                                         text=f"❌ **{await self._name(server)}**: the game update failed.",
                                         fields=fields))
        elif event.state is not ServerState.BUSY:
            self.ctx.expect_offline(server)  # the stop at the end of an update is still expected

    async def seen(self, servers: Iterable[ServerInfo]) -> None:
        """Called after each read of AMP's server list. A new installed version or time means an update finished."""
        for info in servers:
            server = info.id.lower()
            now: dict[str, Any] = {"version": info.installed_version, "at": info.installed_at}
            if now["version"] is None and now["at"] is None:
                continue
            before = await self.ctx.settings.get(SEEN_KEY, scope=server)
            if before == now:
                continue
            await self.ctx.settings.set(SEEN_KEY, now, scope=server)
            if not isinstance(before, dict):
                continue  # first sighting: nothing to compare with
            self.updating.pop(server, None)
            if not await self._enabled():
                continue
            old, new = before.get("version"), now["version"]
            change = f"{code(str(old))} → {code(str(new))}" if old and new and old != new else \
                (code(str(new)) if new else "version not reported")
            await self.router.send(Alert(kind="update_done", server=server, severity="good", ping=False,
                                         cooldown=False, title="Update finished",
                                         text=f"✅ **{escape(info.name)}** finished updating: {change}."))

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
