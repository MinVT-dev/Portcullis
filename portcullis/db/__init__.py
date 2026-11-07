# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite storage: one connection, numbered SQL migrations and small repositories.

Migrations live in ``portcullis/db/migrations/NNNN_name.sql`` and run in order, each in its own
transaction together with the ``schema_version`` bump, so a failed migration leaves the database
exactly as it was. Before upgrading an existing database, a copy is saved next to it as
``<name>.v<old version>.bak``.
"""

from __future__ import annotations

from portcullis.db.database import Database, DatabaseTooNew, MigrationError
from portcullis.db.repos import AuditEntry, AuditRepo, LinkRow, ServerRepo, ServerRow, SettingsRepo, UserRepo

__all__ = [
    "AuditEntry",
    "AuditRepo",
    "Database",
    "DatabaseTooNew",
    "LinkRow",
    "MigrationError",
    "ServerRepo",
    "ServerRow",
    "SettingsRepo",
    "UserRepo",
]
