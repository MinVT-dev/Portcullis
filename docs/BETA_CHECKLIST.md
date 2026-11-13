# Real-panel checklist

What still has to be seen working on a real AMP 3 panel before a release. Everything else in Portcullis runs in tests against a fake AMP built from the pinned spec, which proves the code matches the spec, not that the spec matches the panel.

**State on 2026-11-11.** Built against AMP 3.0.0.6, a preview build that expires on 7 December 2026. The cloud test panel is gone, so these checks need a panel on your own network. An earlier version of this page asked for the API spec and the sign-in model; both are answered and written up in [amp3-api/](amp3-api/README.md) and [DESIGN.md](DESIGN.md) section 13.

## Already seen working

Checked on a real 3.0.0.6 panel on 2026-11-07 and 2026-11-08:

- Login with a username and password, token renewal, and single-use hub tickets.
- The shard hub in `direct` mode, with `JoinShard(tenantId, shardId)` and one connection per shard.
- The shapes of `Metrics`, `UserRoster`, `ShardStateChanged` and `ConsoleLine`.
- The server list, status, start, stop and restart, sending a console line, and listing backups.
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
- [ ] `/backups`: make a backup, restore one, delete one. Making a backup copies the kind and content values from an existing backup, because the spec doesn't document them; check the new backup looks like the one it copied.
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

## What must never be shared

- Tokens, session cookies and `Authorization` headers.
- Passwords, TOTP secrets, passkeys and recovery codes.
- AMP licence keys.
- Game server, RCON and Steam passwords.
- Discord bot tokens and invite links.
- Public addresses of your real servers, and player names you may not share.

The recorder redacts these, but look through a capture before sending it. If something leaks, rotate it first and say so afterwards.
