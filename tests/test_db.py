# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migrations and repositories (DESIGN.md Phase 0.3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from portcullis.core.types import ServerId, ServerInfo
from portcullis.db import AuditRepo, Database, DatabaseTooNew, MigrationError, ServerRepo, SettingsRepo, UserRepo
from portcullis.db.database import MIGRATIONS_DIR, load_migrations

A = "7D4B6A52-9A7E-4E0E-9A38-1D1F0D3C2B11"
B = "aaaaaaaa-9a7e-4e0e-9a38-1d1f0d3c2b11"


@pytest.fixture
async def db():
    database = await Database.open(":memory:")
    yield database
    await database.close()


CURRENT = len(load_migrations())  # the schema version this build creates
NEXT = f"{CURRENT + 1:04d}"


def copy_migrations(tmp_path: Path, *extra: tuple[str, str]) -> Path:
    out = tmp_path / "migrations"
    out.mkdir()
    for m in MIGRATIONS_DIR.glob("*.sql"):
        (out / m.name).write_text(m.read_text())
    for name, sql in extra:
        (out / name).write_text(sql)
    return out


# --------------------------------------------------------------------------- migrations


async def test_fresh_database_gets_every_migration(tmp_path: Path) -> None:
    path = tmp_path / "data" / "portcullis.db"
    async with await Database.open(path) as d:
        assert d.version == len(load_migrations())
    async with await Database.open(path) as d:  # reopening is a no-op
        async with d.conn.execute("SELECT COUNT(*) FROM schema_version") as cur:
            assert (await cur.fetchone())[0] == len(load_migrations())
    assert not list(path.parent.glob("*.bak"))  # nothing to back up on first creation


async def test_upgrade_keeps_data_and_saves_a_backup(tmp_path: Path) -> None:
    path = tmp_path / "portcullis.db"
    async with await Database.open(path) as d:
        await SettingsRepo(d).set("timezone", "Europe/Amsterdam")
    migrations = copy_migrations(tmp_path, (f"{NEXT}_add_colour.sql", "ALTER TABLE servers ADD COLUMN colour TEXT;"))
    async with await Database.open(path, migrations_dir=migrations) as d:
        assert d.version == CURRENT + 1
        assert await SettingsRepo(d).get("timezone") == "Europe/Amsterdam"
        async with d.conn.execute("SELECT colour FROM servers") as cur:
            assert await cur.fetchall() == []
    backup = tmp_path / f"portcullis.db.v{CURRENT}.bak"
    assert backup.exists()
    with sqlite3.connect(backup) as old:
        assert old.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == CURRENT


async def test_failed_migration_rolls_back_completely(tmp_path: Path) -> None:
    path = tmp_path / "portcullis.db"
    async with await Database.open(path):
        pass
    bad = copy_migrations(tmp_path, (f"{NEXT}_broken.sql", "CREATE TABLE half_done (x INTEGER);\nNOT SQL AT ALL;"))
    with pytest.raises(MigrationError, match=f"{NEXT}_broken"):
        await Database.open(path, migrations_dir=bad)
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == CURRENT
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "half_done" not in tables


async def test_refuses_a_newer_database(tmp_path: Path) -> None:
    path = tmp_path / "portcullis.db"
    newer = copy_migrations(tmp_path, (f"{NEXT}_future.sql", "CREATE TABLE future (x INTEGER);"))
    async with await Database.open(path, migrations_dir=newer):
        pass
    with pytest.raises(DatabaseTooNew, match=f"schema version {CURRENT + 1}"):
        await Database.open(path)


def test_migration_numbering_is_checked(tmp_path: Path) -> None:
    skipped = f"{CURRENT + 2:04d}_skipped.sql"
    gap = copy_migrations(tmp_path, (skipped, "SELECT 1;"))
    with pytest.raises(MigrationError, match="without gaps"):
        load_migrations(gap)
    (gap / skipped).unlink()
    (gap / "2_badname.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="must look like"):
        load_migrations(gap)


# --------------------------------------------------------------------------- servers


async def test_server_sync_tracks_new_and_gone(db: Database) -> None:
    repo = ServerRepo(db)
    new, gone = await repo.sync([ServerInfo(ServerId(A), "Survival", "minecraft"),
                                 ServerInfo(ServerId(B), "Valheim", "valheim")])
    assert sorted(new) == sorted([A.lower(), B]) and gone == []
    await repo.update(A, display_name="SMP", console_channel=123, hidden=True)

    new, gone = await repo.sync([ServerInfo(ServerId(B), "Valheim", "valheim")])
    assert new == [] and gone == [A.lower()]
    assert [s.shard_id for s in await repo.list()] == [B]
    assert (await repo.get(A)).gone_at is not None

    await repo.sync([ServerInfo(ServerId(A), "Survival 2", "minecraft"), ServerInfo(ServerId(B), "Valheim")])
    back = await repo.get(A)
    assert back.gone_at is None and back.amp_name == "Survival 2"
    assert back.name == "SMP" and back.console_channel == 123 and back.hidden  # settings survived


async def test_server_find_and_update_guards(db: Database) -> None:
    repo = ServerRepo(db)
    await repo.sync([ServerInfo(ServerId(A), "Survival")])
    assert (await repo.find("survival")).shard_id == A.lower()
    assert (await repo.find(A)).shard_id == A.lower()
    await repo.update(A, display_name="SMP")
    assert (await repo.find("smp")).shard_id == A.lower()
    assert await repo.find("nope") is None
    with pytest.raises(ValueError, match="amp_name"):
        await repo.update(A, amp_name="hacked")
    with pytest.raises(KeyError):
        await repo.update(B, prefix="x")


# --------------------------------------------------------------------------- settings, users, audit


async def test_settings_are_scoped_json(db: Database) -> None:
    s = SettingsRepo(db)
    await s.set("relay", {"rate": 5, "denylist": ["op"]}, by=42)
    await s.set("relay", {"rate": 1}, scope=A)
    assert await s.get("relay") == {"rate": 5, "denylist": ["op"]}
    assert await s.get("relay", scope=A.lower()) == {"rate": 1}
    assert await s.get("missing", "default") == "default"
    assert await s.all(scope=A) == {"relay": {"rate": 1}}
    assert await s.delete("relay") and not await s.delete("relay")


async def test_links_are_unique_per_identity(db: Database) -> None:
    users = UserRepo(db)
    await users.add_link(1, "minecraft", "Steve", ingame_id="uuid-1")
    again = await users.add_link(1, "minecraft", "steve")  # same person, different case: refresh
    assert again.ingame_id == "uuid-1"
    assert len(await users.links_for(1)) == 1
    with pytest.raises(LookupError):
        await users.add_link(2, "minecraft", "STEVE")
    await users.add_link(2, "steam", "Steve")  # same name on another platform is fine
    assert (await users.find_link("minecraft", "sTeVe")).discord_id == 1
    await users.set_flags(1, donator=True)
    assert await users.flags(1) == {"donator": True, "banned": False}
    assert await users.remove_links(1) == 1


async def test_audit_query_filters(db: Database) -> None:
    audit = AuditRepo(db)
    await audit.record("server.power.start", actor_id=1, shard_id=A)
    await audit.record("server.power.stop", actor_id=2, shard_id=A, result="denied")
    await audit.record("server.powerful", actor_id=1)
    await audit.record("setup.console.channel", actor_id=1, detail={"channel": 5})
    assert [e.action for e in await audit.query(action_prefix="server.power")] == \
        ["server.power.stop", "server.power.start"]
    assert len(await audit.query(actor_id=1)) == 3
    assert [e.result for e in await audit.query(shard_id=A.lower(), result="denied")] == ["denied"]
    assert (await audit.query(action_prefix="setup"))[0].detail == {"channel": 5}
    with pytest.raises(ValueError):
        await audit.record("x", result="maybe")
