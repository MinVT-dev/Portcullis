-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Server history for graphs and the weekly summary (bot/monitor.py). One row per server per 5 minutes,
-- filled from what Portcullis already sees (hub events), so recording it costs no extra AMP calls.
-- Old rows are pruned daily; /monitor history sets how many days are kept.
CREATE TABLE server_history (
    shard_id     TEXT NOT NULL,
    bucket       INTEGER NOT NULL,        -- start of the 5-minute window, Unix seconds (UTC)
    samples      INTEGER NOT NULL,        -- one-minute samples taken in the window
    up           INTEGER NOT NULL,        -- of those, how many saw the server online
    players_max  INTEGER NOT NULL DEFAULT 0,
    players_sum  INTEGER NOT NULL DEFAULT 0,
    cpu_max      REAL,                    -- percent, NULL when AMP sent no metrics
    memory_max   REAL,                    -- percent of the memory limit, NULL without a limit
    PRIMARY KEY (shard_id, bucket)
);

-- Things that happened to a server: crashes, starts, alerts, updates. Counted by the weekly summary.
CREATE TABLE server_incidents (
    id        INTEGER PRIMARY KEY,
    shard_id  TEXT,                       -- NULL: Portcullis itself (AMP unreachable)
    kind      TEXT NOT NULL,
    at        INTEGER NOT NULL,           -- Unix seconds (UTC)
    detail    TEXT
);
CREATE INDEX server_incidents_at ON server_incidents (shard_id, at);
