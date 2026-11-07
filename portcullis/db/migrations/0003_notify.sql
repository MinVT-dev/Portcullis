-- SPDX-License-Identifier: AGPL-3.0-or-later
-- /notify: members who want a DM when a server comes online.

CREATE TABLE notify_subscriptions (
    discord_id   INTEGER NOT NULL,
    shard_id     TEXT NOT NULL,          -- no FK: a server that disappears simply never notifies
    created_at   TEXT NOT NULL,
    PRIMARY KEY (discord_id, shard_id)
);
CREATE INDEX notify_by_server ON notify_subscriptions(shard_id);
