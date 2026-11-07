-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Player sessions, whitelist requests, /link codes and polls. Times are ISO 8601 UTC text.

-- One row per time a player was seen on a server. left_at stays NULL while they are online.
CREATE TABLE player_sessions (
    id           INTEGER PRIMARY KEY,
    shard_id     TEXT NOT NULL,          -- no FK: history outlives servers
    player_name  TEXT NOT NULL,
    player_id    TEXT,                   -- SteamID64 or similar, when AMP reports it
    joined_at    TEXT NOT NULL,
    left_at      TEXT
);
CREATE INDEX sessions_by_name ON player_sessions(player_name COLLATE NOCASE, joined_at);
CREATE INDEX sessions_open ON player_sessions(shard_id, left_at);

CREATE TABLE whitelist_requests (
    id           INTEGER PRIMARY KEY,
    shard_id     TEXT NOT NULL,
    discord_id   INTEGER NOT NULL,
    ingame_name  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending, approved, denied
    created_at   TEXT NOT NULL,
    decided_by   INTEGER,
    decided_at   TEXT
);
CREATE INDEX whitelist_pending ON whitelist_requests(status, created_at);

-- A code a member has to say in game chat to prove they own a name (/link). One per member.
CREATE TABLE link_codes (
    discord_id   INTEGER PRIMARY KEY,
    code         TEXT NOT NULL,
    shard_id     TEXT NOT NULL,
    platform     TEXT NOT NULL,
    ingame_name  TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);

CREATE TABLE polls (
    id           INTEGER PRIMARY KEY,
    channel_id   INTEGER NOT NULL,
    message_id   INTEGER,
    question     TEXT NOT NULL,
    options      TEXT NOT NULL,          -- JSON list of strings
    created_by   INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    closes_at    TEXT,
    closed       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE poll_votes (
    poll_id      INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    discord_id   INTEGER NOT NULL,
    option       INTEGER NOT NULL,
    PRIMARY KEY (poll_id, discord_id)
);
