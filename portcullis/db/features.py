# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repositories for players, whitelist requests, /link codes and polls (migration 0002)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from portcullis.db.database import Database, now_iso


def _seconds(start: str, end: str | None, now: datetime) -> float:
    begin = datetime.fromisoformat(start)
    finish = datetime.fromisoformat(end) if end else now
    return max(0.0, (finish - begin).total_seconds())


@dataclass(frozen=True)
class PlayerSummary:
    name: str
    player_id: str | None
    first_seen: str
    last_seen: str | None  # None while online
    online_on: tuple[str, ...]  # shard ids where a session is open
    servers: tuple[str, ...]
    seconds: float
    sessions: int


class PlayerRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def joined(self, shard_id: str, name: str, player_id: str | None = None, *, at: str | None = None) -> None:
        sid = shard_id.lower()
        async with self.db.conn.execute(
            "SELECT 1 FROM player_sessions WHERE shard_id = ? AND player_name = ? AND left_at IS NULL",
            (sid, name),
        ) as cur:
            if await cur.fetchone():
                return  # already online (AMP can report a join twice)
        await self.db.conn.execute(
            "INSERT INTO player_sessions (shard_id, player_name, player_id, joined_at) VALUES (?, ?, ?, ?)",
            (sid, name, player_id, at or now_iso()),
        )

    async def left(self, shard_id: str, name: str, *, at: str | None = None) -> None:
        await self.db.conn.execute(
            "UPDATE player_sessions SET left_at = ? WHERE shard_id = ? AND player_name = ? AND left_at IS NULL",
            (at or now_iso(), shard_id.lower(), name),
        )

    async def close_all(self, shard_id: str | None = None, *, at: str | None = None) -> int:
        """End open sessions: when a server stops, or at start-up for sessions the bot never saw end."""
        if shard_id is None:
            cur = await self.db.conn.execute("UPDATE player_sessions SET left_at = ? WHERE left_at IS NULL",
                                             (at or now_iso(),))
        else:
            cur = await self.db.conn.execute(
                "UPDATE player_sessions SET left_at = ? WHERE shard_id = ? AND left_at IS NULL",
                (at or now_iso(), shard_id.lower()))
        return cur.rowcount

    async def seen_on(self, shard_id: str, name: str) -> bool:
        async with self.db.conn.execute(
            "SELECT 1 FROM player_sessions WHERE shard_id = ? AND player_name = ? COLLATE NOCASE LIMIT 1",
            (shard_id.lower(), name),
        ) as cur:
            return await cur.fetchone() is not None

    async def tracked_since(self, shard_id: str) -> str | None:
        """When Portcullis first saw anyone on this server, or None if it never has."""
        async with self.db.conn.execute(
            "SELECT MIN(joined_at) AS first FROM player_sessions WHERE shard_id = ?", (shard_id.lower(),)
        ) as cur:
            row = await cur.fetchone()
        return row["first"] if row else None

    async def online(self, shard_id: str) -> list[str]:
        async with self.db.conn.execute(
            "SELECT player_name FROM player_sessions WHERE shard_id = ? AND left_at IS NULL ORDER BY joined_at",
            (shard_id.lower(),),
        ) as cur:
            return [r["player_name"] for r in await cur.fetchall()]

    async def lookup(self, name: str) -> PlayerSummary | None:
        async with self.db.conn.execute(
            "SELECT * FROM player_sessions WHERE player_name = ? COLLATE NOCASE ORDER BY joined_at", (name,)
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return None
        now = datetime.now(UTC)
        open_rows = [r for r in rows if r["left_at"] is None]
        ended = [r["left_at"] for r in rows if r["left_at"]]
        return PlayerSummary(
            name=rows[-1]["player_name"],
            player_id=next((r["player_id"] for r in reversed(rows) if r["player_id"]), None),
            first_seen=rows[0]["joined_at"],
            last_seen=None if open_rows else max(ended),
            online_on=tuple(dict.fromkeys(r["shard_id"] for r in open_rows)),
            servers=tuple(dict.fromkeys(r["shard_id"] for r in rows)),
            seconds=sum(_seconds(r["joined_at"], r["left_at"], now) for r in rows),
            sessions=len(rows),
        )

    async def top(self, shard_id: str | None = None, *, limit: int = 10) -> list[tuple[str, float]]:
        sql = "SELECT player_name, joined_at, left_at FROM player_sessions"
        args: tuple[Any, ...] = ()
        if shard_id:
            sql += " WHERE shard_id = ?"
            args = (shard_id.lower(),)
        async with self.db.conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        now = datetime.now(UTC)
        totals: dict[str, float] = {}
        names: dict[str, str] = {}
        for r in rows:
            key = r["player_name"].lower()
            names[key] = r["player_name"]
            totals[key] = totals.get(key, 0.0) + _seconds(r["joined_at"], r["left_at"], now)
        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [(names[k], secs) for k, secs in ranked]

    async def day(self, shard_id: str, since: datetime, until: datetime) -> tuple[int, int, int]:
        """(players, new players, peak at once) on one server between ``since`` and ``until``."""
        start, end = since.isoformat(timespec="seconds"), until.isoformat(timespec="seconds")
        async with self.db.conn.execute(
            "SELECT player_name, joined_at, left_at FROM player_sessions WHERE shard_id = ? AND joined_at < ? "
            "AND (left_at IS NULL OR left_at > ?)", (shard_id.lower(), end, start),
        ) as cur:
            rows = await cur.fetchall()
        async with self.db.conn.execute(
            "SELECT COUNT(*) AS n FROM (SELECT player_name FROM player_sessions WHERE shard_id = ? "
            "GROUP BY player_name COLLATE NOCASE HAVING MIN(joined_at) >= ? AND MIN(joined_at) < ?)",
            (shard_id.lower(), start, end),
        ) as cur:
            new = (await cur.fetchone())["n"]
        # Sweep the joins and leaves in time order; a leave sorts before a join at the same moment.
        edges = sorted([(max(r["joined_at"], start), 1) for r in rows] +
                       [(r["left_at"], -1) for r in rows if r["left_at"] and r["left_at"] < end])
        peak = online = 0
        for _, step in edges:
            online += step
            peak = max(peak, online)
        return len({r["player_name"].lower() for r in rows}), int(new), peak

    async def online_everywhere(self) -> list[str]:
        async with self.db.conn.execute(
            "SELECT DISTINCT player_name FROM player_sessions WHERE left_at IS NULL") as cur:
            return [r["player_name"] for r in await cur.fetchall()]

    async def milestone_reached(self, name: str, hours: int) -> bool:
        """Record a milestone. False if it was already announced."""
        cur = await self.db.conn.execute(
            "INSERT OR IGNORE INTO milestones (player_name, hours, reached_at) VALUES (?, ?, ?)",
            (name, hours, now_iso()))
        return cur.rowcount == 1

    async def names(self, prefix: str = "", *, limit: int = 25) -> list[str]:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        async with self.db.conn.execute(
            "SELECT player_name FROM player_sessions WHERE player_name LIKE ? ESCAPE '\\' "
            "GROUP BY player_name COLLATE NOCASE ORDER BY MAX(joined_at) DESC LIMIT ?",
            (f"%{escaped}%", limit),
        ) as cur:
            return [r["player_name"] for r in await cur.fetchall()]


@dataclass(frozen=True)
class WhitelistRequest:
    id: int
    shard_id: str
    discord_id: int
    ingame_name: str
    status: str
    created_at: str
    decided_by: int | None
    decided_at: str | None


class WhitelistRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r: Any) -> WhitelistRequest:
        return WhitelistRequest(r["id"], r["shard_id"], r["discord_id"], r["ingame_name"], r["status"],
                                r["created_at"], r["decided_by"], r["decided_at"])

    async def create(self, shard_id: str, discord_id: int, ingame_name: str) -> WhitelistRequest:
        cur = await self.db.conn.execute(
            "INSERT INTO whitelist_requests (shard_id, discord_id, ingame_name, created_at) VALUES (?, ?, ?, ?)",
            (shard_id.lower(), discord_id, ingame_name, now_iso()),
        )
        found = await self.get(int(cur.lastrowid or 0))
        assert found is not None
        return found

    async def get(self, request_id: int) -> WhitelistRequest | None:
        async with self.db.conn.execute("SELECT * FROM whitelist_requests WHERE id = ?", (request_id,)) as cur:
            row = await cur.fetchone()
        return self._row(row) if row else None

    async def pending_for(self, discord_id: int, shard_id: str) -> WhitelistRequest | None:
        async with self.db.conn.execute(
            "SELECT * FROM whitelist_requests WHERE discord_id = ? AND shard_id = ? AND status = 'pending'",
            (discord_id, shard_id.lower()),
        ) as cur:
            row = await cur.fetchone()
        return self._row(row) if row else None

    async def decide(self, request_id: int, status: str, by: int) -> bool:
        """Move a pending request to approved or denied. False if someone else got there first."""
        if status not in ("approved", "denied"):
            raise ValueError(status)
        cur = await self.db.conn.execute(
            "UPDATE whitelist_requests SET status = ?, decided_by = ?, decided_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, by, now_iso(), request_id),
        )
        return cur.rowcount == 1


@dataclass(frozen=True)
class LinkCode:
    discord_id: int
    code: str
    shard_id: str
    platform: str
    ingame_name: str
    expires_at: str


class LinkCodeRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def issue(self, discord_id: int, code: str, shard_id: str, platform: str, ingame_name: str,
                    ttl: timedelta = timedelta(minutes=10)) -> LinkCode:
        expires = (datetime.now(UTC) + ttl).isoformat(timespec="seconds")
        await self.db.conn.execute(
            "INSERT INTO link_codes (discord_id, code, shard_id, platform, ingame_name, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(discord_id) DO UPDATE SET code = excluded.code, "
            "shard_id = excluded.shard_id, platform = excluded.platform, ingame_name = excluded.ingame_name, "
            "expires_at = excluded.expires_at",
            (discord_id, code, shard_id.lower(), platform, ingame_name, expires),
        )
        return LinkCode(discord_id, code, shard_id.lower(), platform, ingame_name, expires)

    async def match(self, shard_id: str, ingame_name: str, text: str) -> LinkCode | None:
        """The live code this player on this server just said, if any. Expired codes are removed."""
        now = now_iso()
        await self.db.conn.execute("DELETE FROM link_codes WHERE expires_at < ?", (now,))
        async with self.db.conn.execute(
            "SELECT * FROM link_codes WHERE shard_id = ? AND ingame_name = ? COLLATE NOCASE",
            (shard_id.lower(), ingame_name),
        ) as cur:
            rows = await cur.fetchall()
        for r in rows:
            if r["code"] in text.upper():
                await self.db.conn.execute("DELETE FROM link_codes WHERE discord_id = ?", (r["discord_id"],))
                return LinkCode(r["discord_id"], r["code"], r["shard_id"], r["platform"], r["ingame_name"],
                                r["expires_at"])
        return None

    async def pending(self, discord_id: int) -> LinkCode | None:
        async with self.db.conn.execute(
            "SELECT * FROM link_codes WHERE discord_id = ? AND expires_at >= ?", (discord_id, now_iso())
        ) as cur:
            r = await cur.fetchone()
        return LinkCode(r["discord_id"], r["code"], r["shard_id"], r["platform"], r["ingame_name"],
                        r["expires_at"]) if r else None


@dataclass(frozen=True)
class Poll:
    id: int
    channel_id: int
    message_id: int | None
    question: str
    options: tuple[str, ...]
    created_by: int
    created_at: str
    closes_at: str | None
    closed: bool


class PollRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(r: Any) -> Poll:
        return Poll(r["id"], r["channel_id"], r["message_id"], r["question"], tuple(json.loads(r["options"])),
                    r["created_by"], r["created_at"], r["closes_at"], bool(r["closed"]))

    async def create(self, channel_id: int, question: str, options: list[str], created_by: int,
                     closes_at: str | None) -> Poll:
        cur = await self.db.conn.execute(
            "INSERT INTO polls (channel_id, question, options, created_by, created_at, closes_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (channel_id, question, json.dumps(options), created_by, now_iso(), closes_at),
        )
        found = await self.get(int(cur.lastrowid or 0))
        assert found is not None
        return found

    async def set_message(self, poll_id: int, message_id: int) -> None:
        await self.db.conn.execute("UPDATE polls SET message_id = ? WHERE id = ?", (message_id, poll_id))

    async def get(self, poll_id: int) -> Poll | None:
        async with self.db.conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)) as cur:
            r = await cur.fetchone()
        return self._row(r) if r else None

    async def open_polls(self) -> list[Poll]:
        async with self.db.conn.execute("SELECT * FROM polls WHERE closed = 0 ORDER BY id") as cur:
            return [self._row(r) for r in await cur.fetchall()]

    async def due(self) -> list[Poll]:
        async with self.db.conn.execute(
            "SELECT * FROM polls WHERE closed = 0 AND closes_at IS NOT NULL AND closes_at <= ?", (now_iso(),)
        ) as cur:
            return [self._row(r) for r in await cur.fetchall()]

    async def vote(self, poll_id: int, discord_id: int, option: int) -> None:
        await self.db.conn.execute(
            "INSERT INTO poll_votes (poll_id, discord_id, option) VALUES (?, ?, ?) "
            "ON CONFLICT(poll_id, discord_id) DO UPDATE SET option = excluded.option",
            (poll_id, discord_id, option),
        )

    async def tally(self, poll_id: int) -> dict[int, int]:
        async with self.db.conn.execute(
            "SELECT option, COUNT(*) AS n FROM poll_votes WHERE poll_id = ? GROUP BY option", (poll_id,)
        ) as cur:
            return {r["option"]: r["n"] for r in await cur.fetchall()}

    async def close(self, poll_id: int) -> bool:
        cur = await self.db.conn.execute("UPDATE polls SET closed = 1 WHERE id = ? AND closed = 0", (poll_id,))
        return cur.rowcount == 1


async def links_by_name(db: Database, ingame_name: str) -> list[tuple[int, str]]:
    """(discord id, platform) for every member linked to this in-game name, on any game."""
    async with db.conn.execute("SELECT discord_id, platform FROM links WHERE ingame_name = ? COLLATE NOCASE",
                               (ingame_name,)) as cur:
        return [(r["discord_id"], r["platform"]) for r in await cur.fetchall()]


async def all_settings(db: Database) -> dict[str, dict[str, Any]]:
    """Every setting, by scope (``bot`` or a shard id). Used by /admin export."""
    out: dict[str, dict[str, Any]] = {}
    async with db.conn.execute("SELECT scope, key, value FROM settings ORDER BY scope, key") as cur:
        for r in await cur.fetchall():
            out.setdefault(r["scope"], {})[r["key"]] = json.loads(r["value"])
    return out


class NotifyRepo:
    """Who wants a DM when a server comes online (``/notify``)."""

    LIMIT = 25  # servers per member

    def __init__(self, db: Database) -> None:
        self.db = db

    async def toggle(self, discord_id: int, shard_id: str) -> bool | None:
        """Subscribe or unsubscribe. True = now subscribed, False = removed, None = at the limit."""
        sid = shard_id.lower()
        cur = await self.db.conn.execute(
            "DELETE FROM notify_subscriptions WHERE discord_id = ? AND shard_id = ?", (discord_id, sid))
        if cur.rowcount:
            return False
        if len(await self.for_member(discord_id)) >= self.LIMIT:
            return None
        await self.db.conn.execute(
            "INSERT INTO notify_subscriptions (discord_id, shard_id, created_at) VALUES (?, ?, ?)",
            (discord_id, sid, now_iso()))
        return True

    async def for_member(self, discord_id: int) -> list[str]:
        async with self.db.conn.execute(
            "SELECT shard_id FROM notify_subscriptions WHERE discord_id = ? ORDER BY created_at", (discord_id,)
        ) as cur:
            return [r["shard_id"] for r in await cur.fetchall()]

    async def for_server(self, shard_id: str) -> list[int]:
        async with self.db.conn.execute(
            "SELECT discord_id FROM notify_subscriptions WHERE shard_id = ? ORDER BY created_at", (shard_id.lower(),)
        ) as cur:
            return [int(r["discord_id"]) for r in await cur.fetchall()]
