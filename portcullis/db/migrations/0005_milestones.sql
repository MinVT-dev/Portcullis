-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Play-time milestones already announced, so each one is posted once.
CREATE TABLE milestones (
    player_name  TEXT NOT NULL COLLATE NOCASE,
    hours        INTEGER NOT NULL,
    reached_at   TEXT NOT NULL,
    PRIMARY KEY (player_name, hours)
);
