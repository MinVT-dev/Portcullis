# SPDX-License-Identifier: MIT
"""Downloading files, zips and snapshots from a shard (``amp.downloads``).

AMP 3 hands out downloads through single-use tickets, so a browser can open them by navigation:

* ``POST /api/shards/{id}/files/zip-ticket`` with the paths, then ``GET .../files/zip?ticket=``. The zip
  is only reachable this way (the old ``POST .../files/zip`` is gone).
* ``POST .../files/download-ticket`` with one path, then ``GET .../files/download?ticket=``. The same
  ``GET`` also takes a Bearer header with ``?path=``; that read is not audited by AMP, the ticket one is.
* ``POST .../snapshots/{snapshotId}/download-ticket``, then ``GET .../snapshots/{snapshotId}/download``.

A ticket is bound to the session that asked for it, is accepted once and lasts sixty seconds, so these
helpers always fetch a fresh one and spend it straight away. The generated methods
(``amp.files_backups.post_shards_files_zip_ticket`` and friends) stay available for anything else.

Bodies are read in chunks. Pass ``dest`` to stream to a file instead of holding the bytes in memory, and
``max_bytes`` to stop a download that is bigger than you expected.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import TYPE_CHECKING
from uuid import UUID

from portcullis_api import models as m

if TYPE_CHECKING:
    from portcullis_api.api.files_backups import FilesBackupsApi
    from portcullis_api.transport import AmpTransport

Dest = str | os.PathLike[str] | None


class Downloads:
    def __init__(self, transport: AmpTransport, files: FilesBackupsApi) -> None:
        self._t = transport
        self._files = files

    async def file(self, shard_id: UUID | str, path: str, *, dest: Dest = None, max_bytes: int | None = None,
                   timeout: float | None = None) -> bytes | int:
        """One file, by its path relative to the shard's working directory. Needs ``files.read``.

        Returns the bytes, or the byte count when ``dest`` is given. AMP may refuse a file type
        (``MissingCapability``) unless the account holds ``files.bypassrestrictions``.
        """
        ticket = await self._files.post_shards_files_download_ticket(shard_id, m.PathBody(path=path))
        return await self._t.download("/api/shards/{shardId}/files/download", path_params={"shardId": shard_id},
                                      params={"ticket": ticket.ticket}, dest=dest, bearer=False,
                                      max_bytes=max_bytes, timeout=timeout)

    async def zip(self, shard_id: UUID | str, paths: Iterable[str], *, dest: Dest = None,
                  max_bytes: int | None = None, timeout: float | None = None) -> bytes | int:
        """A zip of files and folders. Needs ``files.read``; denied subfolders and blocked file types
        are left out of the archive rather than failing it. Entry names are relative to the parent of
        the first path."""
        wanted = [p for p in paths if p]
        if not wanted:
            raise ValueError("name at least one path to zip")
        ticket = await self._files.post_shards_files_zip_ticket(shard_id, m.PathsBody(paths=wanted))
        return await self._t.download("/api/shards/{shardId}/files/zip", path_params={"shardId": shard_id},
                                      params={"ticket": ticket.ticket}, dest=dest, bearer=False,
                                      max_bytes=max_bytes, timeout=timeout)

    async def snapshot(self, shard_id: UUID | str, snapshot_id: UUID | str, *, dest: Dest = None,
                       max_bytes: int | None = None, timeout: float | None = None) -> bytes | int:
        """A snapshot (backup) as a zip, for restoring by hand. Needs the snapshot read permission.
        AMP answers 409 or 503 (``Conflict``, ``Unreachable``) while the snapshot store is busy."""
        ticket = await self._files.post_shards_snapshots_download_ticket(shard_id, snapshot_id)
        return await self._t.download("/api/shards/{shardId}/snapshots/{snapshotId}/download",
                                      path_params={"shardId": shard_id, "snapshotId": snapshot_id},
                                      params={"ticket": ticket.ticket}, dest=dest, bearer=False,
                                      max_bytes=max_bytes, timeout=timeout)
