# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repositories for server history and incidents (migration 0007), read by graphs and the weekly summary."""

from __future__ import annotations

from dataclasses import dataclass

from portcullis.db.database import Database


@dataclass(frozen=True)
class HistoryPoint:
    """One step of a graph: everything recorded between ``start`` and ``start + step``."""

    start: int  # Unix seconds
    samples: int
    up: int
    players_max: int
    players_avg: float
    cpu_max: float | None
    memory_max: float | None

    @property
    def uptime(self) -> float:
        return self.up / self.samples if self.samples else 0.0


@dataclass(frozen=True)
class HistoryStats:
    samples: int
    up: int
    players_max: int

    @property
    def uptime(self) -> float | None:
        """Share of samples that saw the server online, or None when nothing was recorded."""
        return self.up / self.samples if self.samples else None


class HistoryRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, shard_id: str, bucket: int, *, samples: int, up: int, players_max: int, players_sum: int,
                  cpu_max: float | None, memory_max: float | None) -> None:
        """Add a window's samples. Adding to a window that already has a row merges them (a restart mid-window)."""
        await self.db.conn.execute(
            "INSERT INTO server_history (shard_id, bucket, samples, up, players_max, players_sum, cpu_max, "
            "memory_max) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(shard_id, bucket) DO UPDATE SET "
            "samples = samples + excluded.samples, up = up + excluded.up, "
            "players_max = MAX(players_max, excluded.players_max), players_sum = players_sum + excluded.players_sum, "
            "cpu_max = CASE WHEN cpu_max IS NULL THEN excluded.cpu_max WHEN excluded.cpu_max IS NULL THEN cpu_max "
            "ELSE MAX(cpu_max, excluded.cpu_max) END, "
            "memory_max = CASE WHEN memory_max IS NULL THEN excluded.memory_max "
            "WHEN excluded.memory_max IS NULL THEN memory_max ELSE MAX(memory_max, excluded.memory_max) END",
            (shard_id.lower(), bucket, samples, up, players_max, players_sum, cpu_max, memory_max),
        )

    async def series(self, shard_id: str, since: int, until: int, step: int) -> list[HistoryPoint]:
        """Rows between ``since`` and ``until`` folded into ``step``-second points. Steps without data are left out."""
        async with self.db.conn.execute(
            "SELECT (bucket - ?) / ? AS n, SUM(samples) AS samples, SUM(up) AS up, MAX(players_max) AS pmax, "
            "SUM(players_sum) AS psum, MAX(cpu_max) AS cpu, MAX(memory_max) AS mem FROM server_history "
            "WHERE shard_id = ? AND bucket >= ? AND bucket < ? GROUP BY n ORDER BY n",
            (since, step, shard_id.lower(), since, until),
        ) as cur:
            rows = await cur.fetchall()
        return [HistoryPoint(start=since + r["n"] * step, samples=r["samples"], up=r["up"], players_max=r["pmax"],
                             players_avg=(r["psum"] / r["samples"]) if r["samples"] else 0.0,
                             cpu_max=r["cpu"], memory_max=r["mem"]) for r in rows]

    async def stats(self, shard_id: str, since: int, until: int) -> HistoryStats:
        async with self.db.conn.execute(
            "SELECT COALESCE(SUM(samples), 0) AS s, COALESCE(SUM(up), 0) AS u, COALESCE(MAX(players_max), 0) AS p "
            "FROM server_history WHERE shard_id = ? AND bucket >= ? AND bucket < ?",
            (shard_id.lower(), since, until),
        ) as cur:
            row = await cur.fetchone()
        return HistoryStats(samples=row["s"], up=row["u"], players_max=row["p"])

    async def prune(self, before: int) -> int:
        cur = await self.db.conn.execute("DELETE FROM server_history WHERE bucket < ?", (before,))
        return cur.rowcount


class IncidentRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, shard_id: str | None, kind: str, at: int, detail: str | None = None) -> None:
        await self.db.conn.execute(
            "INSERT INTO server_incidents (shard_id, kind, at, detail) VALUES (?, ?, ?, ?)",
            (shard_id.lower() if shard_id else None, kind, at, detail[:300] if detail else None),
        )

    async def counts(self, shard_id: str, since: int, until: int) -> dict[str, int]:
        async with self.db.conn.execute(
            "SELECT kind, COUNT(*) AS n FROM server_incidents WHERE shard_id = ? AND at >= ? AND at < ? GROUP BY kind",
            (shard_id.lower(), since, until),
        ) as cur:
            return {r["kind"]: r["n"] for r in await cur.fetchall()}

    async def prune(self, before: int) -> int:
        cur = await self.db.conn.execute("DELETE FROM server_incidents WHERE at < ?", (before,))
        return cur.rowcount
