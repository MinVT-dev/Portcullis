# Portcullis: ideas list

Every feature idea we have, grouped by what it is for. Each idea says whether it can be built now.

**Status key**

- ✅ **Built**: in the bot today.
- 🟢 **Ready**: buildable now. It uses only AMP data that has worked on a real panel, or no AMP data at all.
- 🟡 **Needs checking**: depends on AMP 3 data that hasn't been seen working yet. The note says what to capture first.
- ⚪ **Maybe not**: possible, but probably not worth it, or risky. The note says why.

**What counts as verified** (7 Nov 2026, AMP 3.0.0.6): the server list, a server's state, player roster, CPU and memory; start, stop and restart; console lines and player joins over the live event stream (joins seen on Unturned); console history; sending a console command; listing backups. Not yet seen working: player **leaves**, a real **crash** (the Failed state), the **scheduler** payloads, **making** backups, the **update** action, AMP's **analytics** endpoints and outbound **webhooks**. Chat only works for games with a chat pattern (Unturned, once #17 is merged).

The design budget is about 25 slash commands (DESIGN.md, Appendix B), so most new ideas below are buttons, panel fields or options on an existing command, not new commands.

## 1. Server operations

| Idea | What it does | Status |
|---|---|---|
| `/server list`, `/server status` | States, players, CPU, memory, "up since". | ✅ Built |
| `/server power` and status buttons | Start, stop and restart, publicly logged. | ✅ Built |
| `/server send` | One console command or chat line. | ✅ Built |
| `/server console` | The latest console lines without a relay channel. | ✅ Built |
| Quick commands | Up to 5 admin-defined console buttons per server ("Save world"). | ✅ Built |
| Confirm before stop | A "Stop Survival? 3 players are online" confirmation when players are on. | 🟢 Ready |
| Restart with warning | Says "restarting in 5 minutes" in game chat at 5 and 1 minutes, then restarts. Uses the server's `say` command. | 🟢 Ready |
| Start on demand | Members (not only operators) may start a stopped server, but not stop it. A per-server setting. | 🟢 Ready |
| Power all | Start or stop every server, or every server with a tag, in one go. | 🟢 Ready |
| Update game button | Runs AMP's update action from status. | 🟡 The update route is in the spec but hasn't been run. |
| Kill a stuck server | Force stop. | ⚪ AMP 3.0.0.6 has a permission for it but no route. |
| Sleep and wake | Some games can sleep when empty. | 🟡 Routes exist; no game has been seen sleeping. |

## 2. Monitoring and alerts

| Idea | What it does | Status |
|---|---|---|
| Profile status | "2/4 servers online · 5 players" on the bot's profile. | ✅ Built |
| Live server board | One self-updating message with every server's state. | ✅ Built |
| `/notify` | Members get a DM when a server comes online. | ✅ Built |
| Offline alert with role ping | Pings a chosen role when a server goes offline without anyone using `/server power`. | 🟢 Ready (state changes are verified). A stop from AMP's own panel would also alert. |
| Crash alert | Pings a role when a server enters the Failed state. | 🟡 Failed is mapped from AMP's error code, but no real crash has been captured. |
| AMP unreachable alert | Posts in an admin channel when Portcullis loses AMP for more than a few minutes, and when it's back. | 🟢 Ready |
| High CPU or memory alert | Posts when a server stays above a threshold for N minutes. | 🟢 Ready (metrics come from AMP's state). |
| Player count in a channel name | A voice channel named "Survival: 5 online". | ⚪ Discord allows 2 renames per 10 minutes, so it lags. |
| Daily summary | One post a day: uptime, peak players, new players. | 🟡 Play time needs leaves; peak and new players are fine. |
| `/alert` thresholds | Admin-defined alerts from AMP's own triggers. | 🟡 Waits on the scheduler payloads. |

## 3. Players and community

| Idea | What it does | Status |
|---|---|---|
| `/player lookup`, `/player top` | Last seen, play time, most played. | ✅ Built (play time is approximate until leaves are seen). |
| `/link` | Prove an in-game name is yours by typing a code in game chat. | ✅ Built (needs a chat pattern for the game). |
| `/poll` | Community polls with buttons. | ✅ Built |
| First-join welcome | "Bob joined Survival for the first time" in the events channel. | 🟢 Ready (joins are verified). |
| Milestones | "Alice reached 100 hours" posts. | 🟡 Needs leaves for real play time. |
| Who's online, everywhere | A `/server list` option that names every online player. | 🟢 Ready |
| Chat bridge, Discord to game | Messages in the chat channel are sent into the game with `say`. | 🟡 Needs the Message Content intent (privileged) and a chat pattern per game. `/server send as:chat` already covers it. |
| Events calendar | Operators schedule a game night; members RSVP with a button; a reminder goes out. Discord-only. | 🟢 Ready |
| Giveaways | A button to enter, a random winner at the end. Discord-only. | ⚪ Not about game servers; plenty of bots do it. |
| Vote to restart | A poll that restarts the server if it passes. | ⚪ Overlaps `/poll` and `/server power`; easy to abuse. |

## 4. Moderation and access

| Idea | What it does | Status |
|---|---|---|
| Whitelist requests, add and remove | With Approve and Deny buttons. | ✅ Built |
| `/user` flags | Donator, blocked from requests, remove links. | ✅ Built |
| Kick and ban | One command with an action option, with per-game command templates like the whitelist ones. | 🟢 Ready (same console path as the whitelist). Each game needs its commands filled in. |
| Whitelist list | Who is whitelisted on a server, from the requests Portcullis approved. | 🟢 Ready (Portcullis's own records, not the game's file). |
| Auto-approve linked members | Whitelist requests from members with a verified link and a chosen role are approved without a moderator. | 🟢 Ready |
| Role on link | Give members a Discord role when they link a name. | 🟢 Ready, but the bot would need the Manage Roles permission. |
| Watchlist | Moderators mark a player name; a quiet post when that name joins. | 🟢 Ready (joins are verified). |
| Name checks on join | Flag joins with names that break the rules (a word list). | 🟢 Ready |

## 5. Backups and schedules

| Idea | What it does | Status |
|---|---|---|
| `/backups` list | Recent backups with size and date. | ✅ Built |
| Make a backup | A button on `/backups`. | 🟡 AMP's API doesn't say which snapshot type to send. |
| Restore a backup | With a confirmation and its own permission. | 🟡 Restore route not tried; destructive. |
| `/schedule` | Create and list schedules on AMP's own scheduler. | 🟡 Scheduler payloads not checked on a real panel. |
| Schedule run history | Buttons on `/schedule list`. | 🟡 Same as above. |
| Backup reminder | Warns admins when a server's newest backup is older than N days. | 🟢 Ready (the backup list is verified). |

## 6. Setup, admin and safety

| Idea | What it does | Status |
|---|---|---|
| `/setup` panels | Relays, names, game commands, quick commands, whitelist channel, board. | ✅ Built |
| `/audit`, `/admin diagnostics`, export and import | Who did what, health check, settings backup. | ✅ Built |
| Per-command operator roles | "Moderators can whitelist but not stop servers": separate roles per action. | 🟢 Ready |
| Per-server operator roles | A role that can only run its own server. | 🟢 Ready |
| Setup check | One button in `/setup bot` that lists what's missing: no relay, no whitelist command, a channel the bot can't post in. | 🟢 Ready |
| Audit log channel | Every power and console action also posted to an admin channel as it happens. | 🟢 Ready |
| Gatekeeper V3 import | Bring old links and settings over. | 🟡 Needs a V3 database to map. |
| AMP permission view | What the bot's AMP account may do per server, in diagnostics. | 🟢 Ready (`/api/me/permissions` was verified). |
| Manage AMP users from Discord | Create or change AMP accounts. | ⚪ AMP's panel does it, and a bug here is a security problem. |

## 7. Per-game extras

| Idea | What it does | Status |
|---|---|---|
| Unturned chat and `/link` | Chat relay and link codes from Unturned's console. | 🟡 Built in PR #17, not merged yet. |
| More chat patterns | Minecraft, Valheim, Palworld and others. | 🟡 Each needs a real console capture of chat. |
| Game art | Thumbnails on `/server status`. | ✅ Built |
| Connect button | The server address, or a Steam `steam://connect/…` link, on status. | 🟢 Ready if an admin types the address; AMP's port data isn't mapped. |
| Map, mods and version | Current map, mod list and game version on status. | 🟡 Per game; nothing captured yet. |
| Arma Reforger players | Joins and leaves for Reforger. | ⚪ AMP gets no player data from that game. |

## Suggested next batch

The most useful **Ready** ideas, in order:

1. **Restart with warning**, so players get a heads-up instead of a sudden kick.
2. **Offline alert with role ping**, noting that panel stops also alert.
3. **AMP unreachable alert** in an admin channel.
4. **First-join welcome** and **watchlist**, both from verified join events.
5. **Confirm before stop** when players are online.
6. **Setup check** button.
7. **Kick and ban** through per-game command templates.
