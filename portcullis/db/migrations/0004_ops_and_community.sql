-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Server tags, Portcullis's own whitelist record, the watchlist and the events calendar.

-- AMP's tags for the shard, as a JSON list. Used by /server power-all.
ALTER TABLE servers ADD COLUMN tags TEXT NOT NULL DEFAULT '[]';

-- Names Portcullis put on a server's whitelist (approved requests and /whitelist add).
-- It can't see the game's own whitelist file, so names added in game are missing here.
CREATE TABLE whitelist_entries (
    shard_id     TEXT NOT NULL,
    ingame_name  TEXT NOT NULL COLLATE NOCASE,
    discord_id   INTEGER,                -- the member it was for, when it came from a request
    added_by     INTEGER,                -- 0 when Portcullis approved it by itself
    added_at     TEXT NOT NULL,
    PRIMARY KEY (shard_id, ingame_name)
);
INSERT OR IGNORE INTO whitelist_entries (shard_id, ingame_name, discord_id, added_by, added_at)
    SELECT shard_id, ingame_name, discord_id, decided_by, COALESCE(decided_at, created_at)
    FROM whitelist_requests WHERE status = 'approved';

-- Player names moderators want to hear about when they join.
CREATE TABLE watchlist (
    ingame_name  TEXT PRIMARY KEY COLLATE NOCASE,
    note         TEXT,
    added_by     INTEGER NOT NULL,
    added_at     TEXT NOT NULL
);

CREATE TABLE events (
    id           INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT,
    shard_id     TEXT,                   -- the server it's on, if any
    starts_at    TEXT NOT NULL,          -- ISO 8601 UTC
    channel_id   INTEGER NOT NULL,
    message_id   INTEGER,
    created_by   INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    reminded     INTEGER NOT NULL DEFAULT 0,
    cancelled    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX events_upcoming ON events(cancelled, starts_at);

CREATE TABLE event_rsvps (
    event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    discord_id   INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (event_id, discord_id)
);
