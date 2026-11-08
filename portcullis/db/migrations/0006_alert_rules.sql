-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Admin-defined alert rules (/alert): a metric, a threshold, how long it must hold, and where to post.
CREATE TABLE alert_rules (
    id             INTEGER PRIMARY KEY,
    shard_id       TEXT,                 -- NULL: every server
    metric         TEXT NOT NULL CHECK (metric IN ('cpu', 'memory', 'players_above', 'players_below', 'offline')),
    threshold      INTEGER NOT NULL,
    minutes        INTEGER NOT NULL CHECK (minutes BETWEEN 0 AND 1440),
    channel_id     INTEGER,              -- NULL: the bot's alert channel
    role_id        INTEGER,              -- pinged when the rule fires
    muted_until    TEXT,
    created_by     INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    last_fired_at  TEXT
);
