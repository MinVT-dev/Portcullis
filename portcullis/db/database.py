# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    pass


class DatabaseTooNew(MigrationError):
    """The file was written by a newer Portcullis. Refuse rather than guess."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _NAME.match(path.name)
        if not match:
            raise MigrationError(f"migration file name {path.name!r} must look like 0001_name.sql")
        found.append(Migration(int(match.group(1)), path.stem, path.read_text(encoding="utf-8")))
    for expected, migration in enumerate(found, start=1):
        if migration.version != expected:
            raise MigrationError(f"migrations must be numbered 1, 2, 3... without gaps; found "
                                 f"{migration.name} where {expected:04d} was expected")
    return found


class Database:
    def __init__(self, conn: aiosqlite.Connection, path: Path | None) -> None:
        self.conn = conn
        self.path = path
        self.version = 0

    @classmethod
    async def open(cls, path: Path | str, *, migrations_dir: Path = MIGRATIONS_DIR) -> Database:
        """Open (creating if needed) and migrate. Use ``":memory:"`` for tests."""
        memory = str(path) == ":memory:"
        file_path = None if memory else Path(path)
        if file_path is not None:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(":memory:" if memory else str(file_path), isolation_level=None)
        conn.row_factory = aiosqlite.Row
        db = cls(conn, file_path)
        try:
            await conn.execute("PRAGMA foreign_keys = ON")
            if not memory:
                await conn.execute("PRAGMA journal_mode = WAL")
            await db.migrate(load_migrations(migrations_dir))
        except BaseException:
            await conn.close()
            raise
        return db

    async def current_version(self) -> int:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        async with self.conn.execute("SELECT MAX(version) FROM schema_version") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    async def migrate(self, migrations: list[Migration]) -> None:
        current = await self.current_version()
        latest = migrations[-1].version if migrations else 0
        if current > latest:
            raise DatabaseTooNew(
                f"the database is at schema version {current}, but this Portcullis only knows up to "
                f"{latest}. Update Portcullis, or restore a backup made by this version."
            )
        pending = [m for m in migrations if m.version > current]
        if pending and current > 0 and self.path is not None:
            backup = self.path.with_name(f"{self.path.name}.v{current}.bak")
            if not backup.exists():
                await self.conn.execute("VACUUM INTO ?", (str(backup),))
                log.info("Saved a copy of the database before upgrading: %s", backup)
        for migration in pending:
            script = (
                f"BEGIN;\n{migration.sql}\n;\n"
                f"INSERT INTO schema_version (version, applied_at) VALUES ({migration.version}, '{now_iso()}');\n"
                "COMMIT;"
            )
            try:
                await self.conn.executescript(script)
            except Exception as exc:
                if self.conn.in_transaction:
                    await self.conn.execute("ROLLBACK")
                raise MigrationError(f"migration {migration.name} failed and was rolled back: {exc}") from exc
            log.info("Applied database migration %s", migration.name)
        self.version = max(current, latest)

    async def close(self) -> None:
        await self.conn.close()

    async def __aenter__(self) -> Database:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
