-- SPDX-License-Identifier: AGPL-3.0-or-later
-- First schema (DESIGN.md Phase 0.3). Servers are keyed by AMP 3 shard UUID.
-- Discord ids are stored as INTEGER (64-bit snowflakes fit in SQLite's signed 64-bit integers).
-- Times are ISO 8601 UTC text, e.g. 2026-11-07T14:00:00+00:00.

CREATE TABLE servers (
    shard_id        TEXT PRIMARY KEY,            -- lower-case UUID
    amp_name        TEXT NOT NULL,               -- name as AMP reports it, refreshed by the registry
    display_name    TEXT,                        -- override set in /setup; NULL means use amp_name
    template        TEXT,
    hidden          INTEGER NOT NULL DEFAULT 0,  -- hidden from member-facing lists
    console_channel INTEGER,
    chat_channel    INTEGER,
    event_channel   INTEGER,
    discord_role    INTEGER,                     -- role pinged or granted for this server
    prefix          TEXT,                        -- short tag shown in relayed chat
    gone_at         TEXT,                        -- set when the shard disappears from AMP; kept for history
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE users (
    discord_id   INTEGER PRIMARY KEY,
    donator      INTEGER NOT NULL DEFAULT 0,
    banned       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- An in-game identity a Discord member has proved they own (/link).
CREATE TABLE links (
    id           INTEGER PRIMARY KEY,
    discord_id   INTEGER NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    platform     TEXT NOT NULL,      -- e.g. minecraft, steam, generic
    ingame_name  TEXT NOT NULL,
    ingame_id    TEXT,               -- Minecraft UUID, SteamID64, ... when known
    shard_id     TEXT REFERENCES servers(shard_id) ON DELETE SET NULL,  -- where it was verified
    verified_at  TEXT NOT NULL,
    UNIQUE (platform, ingame_name COLLATE NOCASE)
);
CREATE INDEX links_by_user ON links(discord_id);

-- Settings as JSON values. scope is 'bot' for global ones or a shard_id for per-server ones.
CREATE TABLE settings (
    scope       TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,       -- JSON
    updated_at  TEXT NOT NULL,
    updated_by  INTEGER,             -- Discord id of whoever changed it, NULL for the bot itself
    PRIMARY KEY (scope, key)
);

-- Who did what from Discord. Never stores console command arguments that may hold secrets
-- (the caller redacts before writing).
CREATE TABLE audit (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL,
    actor_id    INTEGER,             -- Discord id; NULL for the bot or a schedule
    action      TEXT NOT NULL,       -- permission-node style, e.g. server.power.start
    shard_id    TEXT,                -- no FK: audit rows outlive servers
    target      TEXT,                -- free text: a player name, a backup id, a setting key
    result      TEXT NOT NULL DEFAULT 'ok',  -- ok, denied, failed
    detail      TEXT                 -- JSON
);
CREATE INDEX audit_by_time ON audit(at);
CREATE INDEX audit_by_actor ON audit(actor_id, at);
CREATE INDEX audit_by_shard ON audit(shard_id, at);
