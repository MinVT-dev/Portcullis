# Alerts, limits, graphs and the weekly summary

This page covers what Portcullis tells admins when something goes wrong, how to choose where it goes, and the history it keeps for graphs. Pictures in [images/monitoring](images/monitoring) are mock-ups drawn by the real code with made-up data (`tools/render_monitoring.py`).

None of this has run against a real Discord server or AMP panel yet. What to check is at the end of [BETA_CHECKLIST.md](BETA_CHECKLIST.md).

## What raises an alert

| Alert | When | Pings the alert role |
|---|---|---|
| Crashed | AMP reports the server as Failed. The alert shows AMP's last error when it gives one. | yes |
| Offline | A running server stops and nobody stopped it through Portcullis. A stop from AMP's own panel also counts. | yes |
| Needs licence | A start that nobody was waiting on hit the game's licence (EULA). Answer it from `/server status`. | yes |
| Back online | A server that raised one of the three alerts above is running again. Says how long it was down. | no |
| Crash loop | The third crash of one server within 15 minutes. Its alerts are then muted for an hour. | yes |
| High CPU, memory or disk | A limit you set (below) has held long enough. A second post says when it is back to normal. | yes |
| Updating, update finished, update failed | Game updates (below). Only a failed update pings. | failed only |
| AMP unreachable | Portcullis can't reach AMP for 3 minutes. A second post says when it is back. | yes |

Old backups and the AMP preview build's expiry still post in the alert channel as before.

## Where alerts go

`/monitor alerts` (admins) shows and changes it.

- **Channel.** Bot-wide, the alert channel from `/setup bot` or `/monitor alerts channel:`. A server can have its own: `/monitor alerts server:Survival channel:#survival-ops`. `reset_channel:True` puts it back on the bot-wide channel.
- **DMs.** `dm_add:@member` and `dm_remove:@member`, bot-wide or per server. Members on both lists get one DM, not two. Up to 10 members per list. DMs never ping anyone. If a member's DMs are closed, the channel still gets the alert.
- **Role.** Only the alert role from `/setup bot` is pinged, and only in a channel. `@everyone` is never pinged.
- **Update notices.** `/monitor alerts update_notices:False` turns the update posts off.

`/monitor test [server]` sends a test alert along the same path, so you can check the channel and the DMs.

## Keeping a bad night quiet

- **Cooldown.** One alert of a kind per server every 5 minutes.
- **Grouping.** Alerts that arrive within 10 seconds of each other go out as one message per channel or DM. An AMP host restart that takes every server down gives one message, not ten.
- **Crash loop.** The third crash in 15 minutes posts one "crash loop" alert and mutes that server for an hour.
- **Mute.** `/monitor mute <server> <minutes>` (operators and admins) silences one server for up to a week. `minutes:0` turns its alerts back on. Alerts about AMP itself are never muted.

## Limits (thresholds)

`/monitor thresholds <server> [cpu] [memory] [disk] [gap] [minutes]` (admins). Values are percentages; `0` turns one off. CPU and memory can also be set in `/setup server`, which uses the same settings.

- **CPU** is the server's CPU use. **Memory** is a percentage of the server's memory limit in AMP, so it needs a limit set there. **Disk** is a percentage of the server's disk budget in AMP, so it needs a budget.
- An alert goes out once a value stays at or above its limit for `minutes` (5 by default).
- It clears only once the value is `gap` points lower (10 by default) for 2 minutes. A value that hovers around the limit can't post alert, clear, alert.
- A server that stops starts over: no "back to normal" post, and no alert until it is high again for the full time.

**What it costs.** CPU and memory come from AMP's `Metrics` events, which Portcullis already receives for every server it follows, about every 3 to 5 seconds. Watching them makes no extra calls to AMP. Disk isn't in those events, so Portcullis reads AMP's disk scan (`GET /api/Shards/{id}/disk`) at most every 30 minutes per server, and only for servers with a disk limit.

`/alert create` still exists for rules on any server or player counts; `/monitor thresholds` is the per-server limit with hysteresis.

## Game update status

| Status | How Portcullis finds out |
|---|---|
| Updating | An update started through Portcullis (`/server power … update`), or AMP marks the server Busy with a status message that mentions updating, downloading or installing. |
| Finished | The server's installed version or install time changes between two reads of AMP's server list (every 5 minutes, a read Portcullis already makes). The post shows old → new version when AMP reports both. |
| Failed | The server goes to Failed while an update is in progress. AMP's message is shown. |
| Update available | **Can't be detected.** AMP 3 has no field or event for it. Each template has an "update available" pattern that AMP applies to the console itself, but the result isn't exposed. |

While an update runs, the offline alert stays quiet, since an update stops the server on purpose. The status message AMP sends during an update hasn't been captured yet, so an update started from AMP's own panel may only show up when it finishes. The last installed version is stored, so an update that ran while Portcullis was off is still reported once.

## History and graphs

Every minute, Portcullis notes for each server whether it is online, how many players are on, and the latest CPU and memory figure. It keeps one row per server per 5 minutes in its database. All of this comes from what it already receives from AMP, so recording costs no AMP calls.

- Hidden servers aren't recorded. Neither are minutes when AMP can't be reached or a server's state is unknown.
- Times when Portcullis wasn't running are gaps in the graphs, not zeros.
- `/monitor history [days]` (admins) sets how much is kept: 30 days by default, up to 90. `0` stops recording. Older rows are removed once a day. Thirty days is about 8,600 rows per server.

`/graph <server> [metric] [period] [share]`

- `metric`: `players` (most online at once), `uptime` (share of each step it was online; red is offline), `cpu` and `memory` (highest in each step). CPU and memory are for operators.
- `period`: `day` (30-minute steps), `week` (3-hour steps) or `month` (12-hour steps).
- Only you see the graph unless you set `share:True`. One graph per member every 10 seconds.
- Graphs are drawn with Pillow in the brand colours. Times are in the bot's time zone from `/setup bot`.

## Weekly summary

**Off by default.** `/monitor weekly enabled:True [channel] [day] [hour]` (admins) turns it on. It posts once a week, Monday 09:00 by default, in the bot's time zone. It goes to the chosen channel, else the summary channel, else the alert channel.

For each server it shows uptime, peak players, starts, crashes and alerts for the last 7 days, plus a graph of the busiest servers' players. Its footer says how much of the week Portcullis recorded, because hours when it was off can't be counted. `/monitor weekly preview:True` shows the last 7 days now, without posting.

## Settings

| Setting | Scope | Default | Changed with |
|---|---|---|---|
| `alert_channel`, `alert_role` | bot | none | `/setup bot`, `/monitor alerts channel:` |
| `alert_dm` | bot | nobody | `/monitor alerts dm_add:` |
| `alert_route` | server | bot's channel, no extra DMs | `/monitor alerts server:` |
| `alert_muted_until` | server | not muted | `/monitor mute`, a crash loop |
| `cpu_alert`, `memory_alert`, `disk_alert` | server | off | `/monitor thresholds`, `/setup server` (CPU, memory) |
| `alert_clear_gap`, `alert_sustain_minutes` | server | 10 points, 5 minutes | `/monitor thresholds` |
| `update_notices` | bot | on | `/monitor alerts update_notices:` |
| `history_days` | bot | 30 | `/monitor history` |
| `weekly_summary` | bot | off, Monday 09:00 | `/monitor weekly` |

All of them go into `/admin export` like every other setting.
