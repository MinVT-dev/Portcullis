# Real-panel checklist

What still has to be seen working on a real AMP 3 panel before a release. Everything else in Portcullis runs in tests against a fake AMP built from the pinned spec, which proves the code matches the spec, not that the spec matches the panel.

**State on 2026-11-11.** Built against AMP 3.0.0.6, a preview build that expires on 7 December 2026. The cloud test panel is gone, so these checks need a panel on your own network. An earlier version of this page asked for the API spec and the sign-in model; both are answered and written up in [amp3-api/](amp3-api/README.md) and [DESIGN.md](DESIGN.md) section 13.

## Already seen working

Checked on a real 3.0.0.6 panel on 2026-11-07 and 2026-11-08:

- Login with a username and password, token renewal, and single-use hub tickets.
- The shard hub in `direct` mode, with `JoinShard(tenantId, shardId)` and one connection per shard.
- The shapes of `Metrics`, `UserRoster`, `ShardStateChanged` and `ConsoleLine`.
- The server list, status, start, stop and restart, sending a console line, and listing backups.
- Making a backup from `/backups` (Min, 14 Nov 2026). Restoring and deleting are still to check.
- Unturned: `UserJoined`, and chat read from the console.

## Still to check, most important first

Tick each one on a real panel. For anything that fails, the bot's log line and the text Discord showed are enough; never paste a token, password or licence key.

### 1. A new AMP build (do this first after any panel update)

- [ ] Download the new spec and run spec-diff against the pinned one ([DEVELOPING.md](DEVELOPING.md)). A changed route or model is the most likely way for Portcullis to break, and the preview build expires on 7 December 2026.
- [ ] Log in, open `/server list` and `/server status` on one running server.
- [ ] Write the panel's build number into `packages/portcullis-api/src/portcullis_api/spec/PIN.json`. The client was regenerated on 2026-11-13 from the spec the live panel served, which names no build.

### 1a. New in the live spec of 2026-11-13

- [ ] A server waiting for its game licence (EULA): stop a Minecraft server, delete `eula.txt` or set it to false, and let a schedule or a reboot start it. `/server status` should say **Needs licence**, the alert channel should say so, and **Accept the licence…** (Manage Server) or **Decline** should answer it. The bot's AMP account needs `settings.write` to accept and `power.start` to decline.
- [ ] A sleeping server shows **Sleeping**, not Unknown or Offline. Portcullis reads "not running but accepting traffic" as asleep, which the spec implies but nobody has seen.
- [ ] `amp.downloads.file`, `.zip` and `.snapshot` from `portcullis_api` (not used by the bot yet): the download tickets work without a Bearer header.
- [ ] Making, restoring or deleting a backup while another backup job runs gives "AMP is busy with another backup job", not a raw 409.
- [ ] `GET /api/auth/capabilities` answers the bot's address with `passkeyPolicy` Optional or Encouraged; log in still works.

### 2. Actions labelled "unverified" in Discord

These use routes from the spec that have never run on a real panel. Try each on a server you can afford to lose.

- [ ] `/server power`: update, sleep and wake.
- [x] `/backups`: make a backup (works on Min's panel, 2026-11-14).
- [ ] `/backups`: restore one (follow docs/RESTORE_TEST.md on a throwaway server), delete one. Making a backup copies the kind and content values from an existing backup, because the spec doesn't document them; check the new backup looks like the one it copied.
- [ ] Schedules: run now, turn on and off, run history, delete.

### 3. Player events per game

- [ ] `UserLeft`: not captured for any game yet. Until it is, a player's session only ends when the server stops. Record a player leaving (`tools/record_events.py`).
- [ ] One capture with a player joining, chatting and leaving for each game you run other than Unturned. Arma Reforger is known to give no names or chat through AMP.
- [ ] What a command typed in the panel's console looks like on the hub, so relayed console output can tell it apart from game output.

### 4. Permissions

- [ ] Give the bot's AMP account a small role instead of an admin one, then run `/admin diagnostics`. So far the bot has mostly run with broad grants, so a missing grant has had little chance to show up.
- [ ] Confirm that a call the role lacks comes back as 403 and that Discord shows a clear message.

### 5. Long runs

- [ ] Leave the bot running for more than 8 hours, so the session token is renewed at least once, and check the relay still posts afterwards.
- [ ] Restart the AMP panel while the bot is running. The relay should come back by itself within a few minutes.
- [ ] Change the bot account's password in AMP while it runs. The log should say the login was refused, about once a minute and no faster.
- [ ] Run `python -m portcullis --check` on Windows. Expect the banner (box characters in Windows Terminal, plain ASCII in an old `cmd` window with code page 437), `✓ Panel` and `✓ Discord`, exit code 0, and no new files under `data\`.
- [ ] Start the bot with the panel stopped (or a wrong port in `AMP_URL`). Expect the bot to come online anyway, live cards greyed with "The panel isn't answering", one `The panel stopped answering` log line, and commands answering that AMP can't be reached. Start the panel: expect `The panel is answering again` and the cards back in colour within about a minute.
- [ ] Close the console window (not Ctrl+C) while the bot runs, then start it again. Expect the banner's last-run line to say "ended without a clean stop". After a Ctrl+C stop, expect "stopped cleanly ... (Ctrl+C)".
- [ ] With `PORTCULLIS_METRICS=on`, open `http://127.0.0.1:9464/healthz` and `/metrics` in a browser on the same PC. Expect JSON with `"status": "ok"` and Prometheus text.
- [ ] Stop the bot with Ctrl+C on Windows while it shows at least one live status card. Expect `Stopping Portcullis (Ctrl+C)`, then `Portcullis stopped (Ctrl+C)` within a few seconds, no traceback, the card greyed with "Portcullis is stopped, so this card is not updating", and `echo %ERRORLEVEL%` printing 0. Start it again: the card should update at once.

## What must never be shared

- Tokens, session cookies and `Authorization` headers.
- Passwords, TOTP secrets, passkeys and recovery codes.
- AMP licence keys.
- Game server, RCON and Steam passwords.
- Discord bot tokens and invite links.
- Public addresses of your real servers, and player names you may not share.

The recorder redacts these, but look through a capture before sending it. If something leaks, rotate it first and say so afterwards.

## Cards, emojis and setup (draft PR, 14 Nov 2026)

- [ ] `/setup start` places live status cards, and they edit in place every few minutes without a new message each time.
- [ ] A card's footer and the gap between edits match the interval chosen (2 to 5 minutes), and 5 minutes while nobody is online.
- [ ] Delete a card: the next pass posts a new one.
- [ ] Upload the eight emojis (docs/EMOJIS.md); **Check emojis** and `/health` report 8 of 8, and the cards show them.
- [ ] Rename one emoji in the portal: after **Check emojis**, that one falls back to its plain emoji and nothing else changes.
- [ ] `/server diagnostics` shows a disk figure (`GET /api/Shards/{id}/disk` hasn't run on a real panel).
- [ ] A schedule named with "restart" shows as **Next restart** on the card.

## Alerts, limits, graphs and weekly summary (draft PR, 14 Nov 2026)

- [ ] Capture one `Metrics` hub event with `tools/record_events.py` and check its keys (`cpuPercent`, `memoryMb`, `memoryLimitMb`, `activeUsers`). Portcullis reads them leniently, but limits and graphs depend on them.
- [ ] Set a low CPU limit (`/monitor thresholds server:X cpu:5 minutes:1`) on a busy server: one alert after a minute, one "back to normal" after it drops 10 points for 2 minutes.
- [ ] Kill a game process from the host so AMP reports Failed: the crash alert shows AMP's last error, and "back online" follows when AMP restarts it.
- [ ] Add yourself with `/monitor alerts dm_add:@you` and run `/monitor test`: one channel post and one DM.
- [ ] Restart the AMP host with several servers running: the offline alerts arrive as one grouped message.
- [ ] Update a game from AMP's own panel and from `/server power … update`: "is updating" (at least for the second), then "finished updating" with versions within 5 minutes. Note the status message AMP shows while it updates.
- [ ] Leave the bot running for a day, then `/graph metric:players` and `/graph metric:uptime`: the gaps match the times the bot was off.
- [ ] Turn on the weekly summary for today's weekday and the next hour; it posts once.

