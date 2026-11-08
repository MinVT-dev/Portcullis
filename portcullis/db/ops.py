# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repositories for the whitelist record, the watchlist and the events calendar (migration 0004), and
admin-defined alert rules (migration 0006)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from portcullis.db.database import Database, now_iso


@dataclass(frozen=True)
class WhitelistEntry:
    shard_id: str
    ingame_name: str
    discord_id: int | None
    added_by: int | None
    added_at: str


class WhitelistEntryRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, shard_id: str, name: str, *, by: int | None, discord_id: int | None = None) -> None:
        await self.db.conn.execute(
            "INSERT INTO whitelist_entries (shard_id, ingame_name, discord_id, added_by, added_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(shard_id, ingame_name) DO UPDATE SET "
            "discord_id = COALESCE(excluded.discord_id, whitelist_entries.discord_id), "
            "added_by = excluded.added_by, added_at = excluded.added_at",
            (shard_id.lower(), name, discord_id, by, now_iso()),
        )

    async def remove(self, shard_id: str, name: str) -> bool:
        cur = await self.db.conn.execute(
            "DELETE FROM whitelist_entries WHERE shard_id = ? AND ingame_name = ?", (shard_id.lower(), name))
        return cur.rowcount > 0

    async def for_server(self, shard_id: str) -> list[WhitelistEntry]:
        async with self.db.conn.execute(
            "SELECT * FROM whitelist_entries WHERE shard_id = ? ORDER BY ingame_name", (shard_id.lower(),)
        ) as cur:
            return [WhitelistEntry(r["shard_id"], r["ingame_name"], r["discord_id"], r["added_by"], r["added_at"])
                    for r in await cur.fetchall()]


@dataclass(frozen=True)
class WatchEntry:
    ingame_name: str
    note: str | None
    added_by: int
    added_at: str


class WatchlistRepo:
    LIMIT = 200

    def __init__(self, db: Database) -> None:
        self.db = db

    async def toggle(self, name: str, *, by: int, note: str | None = None) -> bool | None:
        """Add or remove a name. True = now watched, False = removed, None = the list is full."""
        cur = await self.db.conn.execute("DELETE FROM watchlist WHERE ingame_name = ?", (name,))
        if cur.rowcount:
            return False
        if len(await self.all()) >= self.LIMIT:
            return None
        await self.db.conn.execute(
            "INSERT INTO watchlist (ingame_name, note, added_by, added_at) VALUES (?, ?, ?, ?)",
            (name, note, by, now_iso()))
        return True

    async def get(self, name: str) -> WatchEntry | None:
        async with self.db.conn.execute("SELECT * FROM watchlist WHERE ingame_name = ?", (name,)) as cur:
            r = await cur.fetchone()
        return WatchEntry(r["ingame_name"], r["note"], r["added_by"], r["added_at"]) if r else None

    async def all(self) -> list[WatchEntry]:
        async with self.db.conn.execute("SELECT * FROM watchlist ORDER BY ingame_name") as cur:
            return [WatchEntry(r["ingame_name"], r["note"], r["added_by"], r["added_at"])
                    for r in await cur.fetchall()]


@dataclass(frozen=True)
class Event:
    id: int
    title: str
    description: str | None
    shard_id: str | None
    starts_at: str
    channel_id: int
    message_id: int | None
    created_by: int
    reminded: bool
    cancelled: bool


class EventRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r: Any) -> Event:
        return Event(r["id"], r["title"], r["description"], r["shard_id"], r["starts_at"], r["channel_id"],
                     r["message_id"], r["created_by"], bool(r["reminded"]), bool(r["cancelled"]))

    async def create(self, title: str, description: str | None, shard_id: str | None, starts_at: str,
                     channel_id: int, created_by: int) -> Event:
        cur = await self.db.conn.execute(
            "INSERT INTO events (title, description, shard_id, starts_at, channel_id, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, description, shard_id.lower() if shard_id else None, starts_at, channel_id, created_by,
             now_iso()),
        )
        found = await self.get(int(cur.lastrowid or 0))
        assert found is not None
        return found

    async def get(self, event_id: int) -> Event | None:
        async with self.db.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)) as cur:
            r = await cur.fetchone()
        return self._row(r) if r else None

    async def set_message(self, event_id: int, message_id: int) -> None:
        await self.db.conn.execute("UPDATE events SET message_id = ? WHERE id = ?", (message_id, event_id))

    async def upcoming(self, *, limit: int = 15) -> list[Event]:
        async with self.db.conn.execute(
            "SELECT * FROM events WHERE cancelled = 0 AND starts_at >= ? ORDER BY starts_at LIMIT ?",
            (now_iso(), limit),
        ) as cur:
            return [self._row(r) for r in await cur.fetchall()]

    async def due_reminders(self, before: str) -> list[Event]:
        """Events that start between now and ``before`` and haven't had their reminder."""
        async with self.db.conn.execute(
            "SELECT * FROM events WHERE cancelled = 0 AND reminded = 0 AND starts_at <= ? AND starts_at >= ?",
            (before, now_iso()),
        ) as cur:
            return [self._row(r) for r in await cur.fetchall()]

    async def mark_reminded(self, event_id: int) -> bool:
        cur = await self.db.conn.execute("UPDATE events SET reminded = 1 WHERE id = ? AND reminded = 0", (event_id,))
        return cur.rowcount == 1

    async def cancel(self, event_id: int) -> bool:
        cur = await self.db.conn.execute("UPDATE events SET cancelled = 1 WHERE id = ? AND cancelled = 0",
                                         (event_id,))
        return cur.rowcount == 1

    async def toggle_rsvp(self, event_id: int, discord_id: int) -> bool:
        """True if the member is now going, False if they took it back."""
        cur = await self.db.conn.execute("DELETE FROM event_rsvps WHERE event_id = ? AND discord_id = ?",
                                         (event_id, discord_id))
        if cur.rowcount:
            return False
        await self.db.conn.execute("INSERT INTO event_rsvps (event_id, discord_id, created_at) VALUES (?, ?, ?)",
                                   (event_id, discord_id, now_iso()))
        return True

    async def going(self, event_id: int) -> list[int]:
        async with self.db.conn.execute(
            "SELECT discord_id FROM event_rsvps WHERE event_id = ? ORDER BY created_at", (event_id,)
        ) as cur:
            return [int(r["discord_id"]) for r in await cur.fetchall()]


@dataclass(frozen=True)
class AlertRule:
    id: int
    shard_id: str | None
    metric: str
    threshold: int
    minutes: int
    channel_id: int | None
    role_id: int | None
    muted_until: str | None
    created_by: int
    created_at: str
    last_fired_at: str | None

    def muted(self, now: datetime) -> bool:
        return self.muted_until is not None and datetime.fromisoformat(self.muted_until) > now


class AlertRuleRepo:
    LIMIT = 25  # /alert list picks rules from one select, which holds 25

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r: Any) -> AlertRule:
        return AlertRule(r["id"], r["shard_id"], r["metric"], r["threshold"], r["minutes"], r["channel_id"],
                         r["role_id"], r["muted_until"], r["created_by"], r["created_at"], r["last_fired_at"])

    async def add(self, *, metric: str, threshold: int, minutes: int, by: int, shard_id: str | None = None,
                  channel_id: int | None = None, role_id: int | None = None) -> int | None:
        """The new rule's id, or None when the list is full."""
        if len(await self.all()) >= self.LIMIT:
            return None
        cur = await self.db.conn.execute(
            "INSERT INTO alert_rules (shard_id, metric, threshold, minutes, channel_id, role_id, created_by, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (shard_id.lower() if shard_id else None, metric, threshold, minutes, channel_id, role_id, by, now_iso()))
        return int(cur.lastrowid or 0)

    async def get(self, rule_id: int) -> AlertRule | None:
        async with self.db.conn.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)) as cur:
            r = await cur.fetchone()
        return self._row(r) if r else None

    async def all(self) -> list[AlertRule]:
        async with self.db.conn.execute("SELECT * FROM alert_rules ORDER BY id") as cur:
            return [self._row(r) for r in await cur.fetchall()]

    async def delete(self, rule_id: int) -> bool:
        cur = await self.db.conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
        return cur.rowcount > 0

    async def mute(self, rule_id: int, until: datetime | None) -> None:
        await self.db.conn.execute("UPDATE alert_rules SET muted_until = ? WHERE id = ?",
                                   (until.isoformat() if until else None, rule_id))

    async def fired(self, rule_id: int) -> None:
        await self.db.conn.execute("UPDATE alert_rules SET last_fired_at = ? WHERE id = ?", (now_iso(), rule_id))
