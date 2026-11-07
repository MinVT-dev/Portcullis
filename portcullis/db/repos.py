# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repositories: the only code that writes SQL outside migrations."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from portcullis.core.types import ServerId, ServerInfo
from portcullis.db.database import Database, now_iso

BOT_SCOPE = "bot"


@dataclass(frozen=True)
class ServerRow:
    shard_id: ServerId
    amp_name: str
    display_name: str | None
    template: str | None
    hidden: bool
    console_channel: int | None
    chat_channel: int | None
    event_channel: int | None
    discord_role: int | None
    prefix: str | None
    gone_at: str | None

    @property
    def name(self) -> str:
        return self.display_name or self.amp_name


class ServerRepo:
    # Columns an admin may change from /setup. amp_name, template and gone_at follow AMP.
    EDITABLE = frozenset({"display_name", "hidden", "console_channel", "chat_channel", "event_channel",
                          "discord_role", "prefix"})

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r: Any) -> ServerRow:
        return ServerRow(
            shard_id=ServerId(r["shard_id"]), amp_name=r["amp_name"], display_name=r["display_name"],
            template=r["template"], hidden=bool(r["hidden"]), console_channel=r["console_channel"],
            chat_channel=r["chat_channel"], event_channel=r["event_channel"], discord_role=r["discord_role"],
            prefix=r["prefix"], gone_at=r["gone_at"],
        )

    async def sync(self, servers: Iterable[ServerInfo]) -> tuple[list[ServerId], list[ServerId]]:
        """Make the table match what AMP reports. Returns (new ids, ids that disappeared).

        Rows for shards that vanished are kept with ``gone_at`` set, so their settings come back if
        the shard does (for example after a restore), and audit history still has a name.
        """
        conn = self.db.conn
        stamp = now_iso()
        seen = {s.id.lower(): s for s in servers}
        async with conn.execute("SELECT shard_id, gone_at FROM servers") as cur:
            existing = {r["shard_id"]: r["gone_at"] for r in await cur.fetchall()}
        new = [ServerId(sid) for sid in seen if sid not in existing]
        gone = [ServerId(sid) for sid, gone_at in existing.items() if sid not in seen and gone_at is None]
        await conn.execute("BEGIN")
        try:
            for sid, info in seen.items():
                await conn.execute(
                    "INSERT INTO servers (shard_id, amp_name, template, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(shard_id) DO UPDATE SET amp_name = excluded.amp_name, "
                    "template = excluded.template, gone_at = NULL, updated_at = excluded.updated_at",
                    (sid, info.name, info.template, stamp, stamp),
                )
            for sid in gone:
                await conn.execute("UPDATE servers SET gone_at = ?, updated_at = ? WHERE shard_id = ?",
                                   (stamp, stamp, sid))
            await conn.execute("COMMIT")
        except BaseException:
            await conn.execute("ROLLBACK")
            raise
        return new, gone

    async def get(self, shard_id: str) -> ServerRow | None:
        async with self.db.conn.execute("SELECT * FROM servers WHERE shard_id = ?", (shard_id.lower(),)) as cur:
            row = await cur.fetchone()
        return self._row(row) if row else None

    async def list(self, *, include_gone: bool = False, include_hidden: bool = True) -> list[ServerRow]:
        sql = "SELECT * FROM servers WHERE 1=1"
        if not include_gone:
            sql += " AND gone_at IS NULL"
        if not include_hidden:
            sql += " AND hidden = 0"
        async with self.db.conn.execute(sql + " ORDER BY COALESCE(display_name, amp_name) COLLATE NOCASE") as cur:
            return [self._row(r) for r in await cur.fetchall()]

    async def find(self, name_or_id: str) -> ServerRow | None:
        """Look a server up the way a member types it: shard id, display name or AMP name."""
        row = await self.get(name_or_id)
        if row:
            return row
        async with self.db.conn.execute(
            "SELECT * FROM servers WHERE gone_at IS NULL AND (display_name = ? COLLATE NOCASE "
            "OR amp_name = ? COLLATE NOCASE) ORDER BY display_name IS NULL LIMIT 1",
            (name_or_id, name_or_id),
        ) as cur:
            found = await cur.fetchone()
        return self._row(found) if found else None

    async def update(self, shard_id: str, **fields: Any) -> ServerRow:
        bad = set(fields) - self.EDITABLE
        if bad:
            raise ValueError(f"not editable: {', '.join(sorted(bad))}")
        if not fields:
            row = await self.get(shard_id)
            if row is None:
                raise KeyError(shard_id)
            return row
        assignments = ", ".join(f"{k} = ?" for k in fields)  # keys are from the fixed EDITABLE set
        values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        cur = await self.db.conn.execute(
            f"UPDATE servers SET {assignments}, updated_at = ? WHERE shard_id = ?",
            (*values, now_iso(), shard_id.lower()),
        )
        if cur.rowcount == 0:
            raise KeyError(shard_id)
        row = await self.get(shard_id)
        assert row is not None
        return row


class SettingsRepo:
    """JSON values per scope: ``"bot"`` for global settings, a shard id for per-server ones."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, key: str, default: Any = None, *, scope: str = BOT_SCOPE) -> Any:
        async with self.db.conn.execute("SELECT value FROM settings WHERE scope = ? AND key = ?",
                                        (scope.lower(), key)) as cur:
            row = await cur.fetchone()
        return json.loads(row["value"]) if row else default

    async def set(self, key: str, value: Any, *, scope: str = BOT_SCOPE, by: int | None = None) -> None:
        await self.db.conn.execute(
            "INSERT INTO settings (scope, key, value, updated_at, updated_by) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(scope, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
            "updated_by = excluded.updated_by",
            (scope.lower(), key, json.dumps(value), now_iso(), by),
        )

    async def delete(self, key: str, *, scope: str = BOT_SCOPE) -> bool:
        cur = await self.db.conn.execute("DELETE FROM settings WHERE scope = ? AND key = ?", (scope.lower(), key))
        return cur.rowcount > 0

    async def all(self, *, scope: str = BOT_SCOPE) -> dict[str, Any]:
        async with self.db.conn.execute("SELECT key, value FROM settings WHERE scope = ? ORDER BY key",
                                        (scope.lower(),)) as cur:
            return {r["key"]: json.loads(r["value"]) for r in await cur.fetchall()}


@dataclass(frozen=True)
class LinkRow:
    id: int
    discord_id: int
    platform: str
    ingame_name: str
    ingame_id: str | None
    shard_id: str | None
    verified_at: str


class UserRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure(self, discord_id: int) -> None:
        stamp = now_iso()
        await self.db.conn.execute(
            "INSERT INTO users (discord_id, created_at, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(discord_id) DO NOTHING", (discord_id, stamp, stamp))

    async def set_flags(self, discord_id: int, *, donator: bool | None = None, banned: bool | None = None) -> None:
        await self.ensure(discord_id)
        if donator is not None:
            await self.db.conn.execute("UPDATE users SET donator = ?, updated_at = ? WHERE discord_id = ?",
                                       (int(donator), now_iso(), discord_id))
        if banned is not None:
            await self.db.conn.execute("UPDATE users SET banned = ?, updated_at = ? WHERE discord_id = ?",
                                       (int(banned), now_iso(), discord_id))

    async def flags(self, discord_id: int) -> dict[str, bool]:
        async with self.db.conn.execute("SELECT donator, banned FROM users WHERE discord_id = ?",
                                        (discord_id,)) as cur:
            row = await cur.fetchone()
        return {"donator": bool(row["donator"]), "banned": bool(row["banned"])} if row else \
            {"donator": False, "banned": False}

    @staticmethod
    def _link(r: Any) -> LinkRow:
        return LinkRow(r["id"], r["discord_id"], r["platform"], r["ingame_name"], r["ingame_id"], r["shard_id"],
                       r["verified_at"])

    async def add_link(self, discord_id: int, platform: str, ingame_name: str, *, ingame_id: str | None = None,
                       shard_id: str | None = None) -> LinkRow:
        """Record a verified identity. Raises ``LookupError`` if another member already owns it."""
        existing = await self.find_link(platform, ingame_name)
        if existing and existing.discord_id != discord_id:
            raise LookupError(f"{ingame_name} on {platform} is already linked to another member")
        await self.ensure(discord_id)
        await self.db.conn.execute(
            "INSERT INTO links (discord_id, platform, ingame_name, ingame_id, shard_id, verified_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(platform, ingame_name) DO UPDATE SET "
            "ingame_id = COALESCE(excluded.ingame_id, links.ingame_id), shard_id = excluded.shard_id, "
            "verified_at = excluded.verified_at",
            (discord_id, platform, ingame_name, ingame_id, shard_id.lower() if shard_id else None, now_iso()),
        )
        found = await self.find_link(platform, ingame_name)
        assert found is not None
        return found

    async def find_link(self, platform: str, ingame_name: str) -> LinkRow | None:
        async with self.db.conn.execute(
            "SELECT * FROM links WHERE platform = ? AND ingame_name = ? COLLATE NOCASE", (platform, ingame_name)
        ) as cur:
            row = await cur.fetchone()
        return self._link(row) if row else None

    async def links_for(self, discord_id: int) -> list[LinkRow]:
        async with self.db.conn.execute("SELECT * FROM links WHERE discord_id = ? ORDER BY platform, ingame_name",
                                        (discord_id,)) as cur:
            return [self._link(r) for r in await cur.fetchall()]

    async def remove_links(self, discord_id: int, platform: str | None = None) -> int:
        if platform is None:
            cur = await self.db.conn.execute("DELETE FROM links WHERE discord_id = ?", (discord_id,))
        else:
            cur = await self.db.conn.execute("DELETE FROM links WHERE discord_id = ? AND platform = ?",
                                             (discord_id, platform))
        return cur.rowcount


@dataclass(frozen=True)
class AuditEntry:
    id: int
    at: str
    actor_id: int | None
    action: str
    shard_id: str | None
    target: str | None
    result: str
    detail: Any


class AuditRepo:
    RESULTS = ("ok", "denied", "failed")

    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, action: str, *, actor_id: int | None = None, shard_id: str | None = None,
                     target: str | None = None, result: str = "ok", detail: Any = None) -> int:
        if result not in self.RESULTS:
            raise ValueError(f"result must be one of {self.RESULTS}")
        cur = await self.db.conn.execute(
            "INSERT INTO audit (at, actor_id, action, shard_id, target, result, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), actor_id, action, shard_id.lower() if shard_id else None, target, result,
             json.dumps(detail) if detail is not None else None),
        )
        return int(cur.lastrowid or 0)

    async def query(self, *, actor_id: int | None = None, shard_id: str | None = None,
                    action_prefix: str | None = None, result: str | None = None, since: str | None = None,
                    limit: int = 50) -> list[AuditEntry]:
        clauses, args = [], []
        if actor_id is not None:
            clauses.append("actor_id = ?")
            args.append(actor_id)
        if shard_id is not None:
            clauses.append("shard_id = ?")
            args.append(shard_id.lower())
        if action_prefix:
            clauses.append("(action = ? OR action LIKE ? ESCAPE '\\')")
            escaped = action_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args += [action_prefix, escaped + ".%"]
        if result:
            clauses.append("result = ?")
            args.append(result)
        if since:
            clauses.append("at >= ?")
            args.append(since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(max(1, min(limit, 1000)))
        async with self.db.conn.execute(f"SELECT * FROM audit{where} ORDER BY id DESC LIMIT ?", args) as cur:
            rows = await cur.fetchall()
        return [AuditEntry(r["id"], r["at"], r["actor_id"], r["action"], r["shard_id"], r["target"], r["result"],
                           json.loads(r["detail"]) if r["detail"] else None) for r in rows]
