# Setting up Portcullis

This guide covers what exists today:

- the **Discord bot**, with a first set of slash commands (server list and status, start/stop/restart) and a relay of live console lines, player joins and leaves, and state changes into Discord channels (section 8);
- the AMP 3 client library, with its tests;
- the **event recorder**, which logs in to your AMP 3 panel and saves live server events, so the bot's event handling can be built from real data;
- the **spec-diff tool**, which tells you what changed in a new AMP build's API.

Windows (PowerShell) comes first, because that is where the recorder runs. Linux and macOS commands follow each step where they differ.

## 1. Prerequisites

- **Python 3.11 or newer.** Check with `py -3 --version` (Windows) or `python3 --version`. Install it from python.org and tick "Add python.exe to PATH".
- **Git**, and access to the private `MinVT-dev/Portcullis` repository.
- **An AMP 3 panel** that this PC can reach in a browser. The panel can sit on a private home address; nothing needs to be opened to the internet.

## 2. Get the code

```powershell
cd $HOME\Documents\Claude
git clone https://github.com/MinVT-dev/Portcullis.git
cd Portcullis
```

To update later, run `git pull` in the same folder.

## 3. Create the Python environment and install

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Linux or macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

This guide calls `.venv\Scripts\python.exe` directly, so you never need to "activate" the environment. If you'd rather activate it (`.\.venv\Scripts\Activate.ps1`) and PowerShell refuses with "running scripts is disabled", run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or just keep using the full path.

## 4. Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Every test should pass. The tests never touch your real AMP: they run against a fake AMP built in-process from the API docs.

## 5. Create the AMP 3 bot user

Use a dedicated account for the bot, never your own admin account.

1. In the AMP 3 panel, create a user, for example `portcullis`, with a strong password.
2. **Don't** turn on two-factor authentication or a passkey for it, and make sure it isn't required to change its password at the next login. A bot can't complete any of those, and login will fail.
3. Give it a role with these permissions on the server (shard) you want to test with:

   | Permission | Needed for |
   |---|---|
   | `view` | Seeing the shard at all, and its state. Required. |
   | `console.read` | Console history and live console lines. Required for the recorder. |
   | `analytics.users` | The online player list. Optional: without it, events still record but the player snapshot shows an error. |

   Grant it on one test server first. Add more servers later.

These are the only permissions the recorder needs. The bot also needs to start, stop and restart servers. For now it runs with an admin AMP account; a limited role with exactly the bot's permissions is planned, and this guide will list them once they are checked against a real panel.

## 6. Fill in `.env`

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

`.env` is ignored by git, so it is never committed. Never paste its contents into chat or an issue.

| Key | Used by | What to put |
|---|---|---|
| `AMP_URL` | recorder | The panel address with its port, as your browser shows it, e.g. `http://192.168.1.10:8443`. Use `https://` if the panel is behind HTTPS. No path such as `/api`. |
| `AMP_USERNAME` | recorder | The bot user from step 5, e.g. `portcullis`. |
| `AMP_PASSWORD` | recorder, bot | For the recorder alone, **leave this empty**: it asks for the password when it starts, and the typing doesn't show. The bot can't ask, so to run the bot put the password here. |
| `DISCORD_TOKEN` | bot | The bot token from section 8.1. |
| `DISCORD_GUILD_ID` | bot | Your Discord server's id (section 8.2). The bot only works there. |
| `PORTCULLIS_OWNER_IDS` | bot | Your own Discord user id. Owners can use every command. |
| `PORTCULLIS_OPERATOR_ROLE_IDS` | bot | Ids of the Discord roles allowed to start, stop and restart servers, comma separated. |
| `PORTCULLIS_DB` | bot | Optional; the default is `data/portcullis.db`. |
| `PORTCULLIS_LOG_LEVEL` | recorder, bot | Optional: `DEBUG`, `INFO`, `WARNING` or `ERROR`. |

Real environment variables override `.env` values.

## 7. Record live events

```powershell
.\.venv\Scripts\python.exe -m tools.record_events --duration 900
```

What happens:

1. It asks for the bot user's password.
2. It prints `Writing to captures\<time>` and the shards it will record, each as `Name (id): state`.
3. It reads the panel's own JavaScript and saves every place it calls `JoinShard` to `panel_hub_calls.txt`, so the real call can be compared with ours. Skip this with `--no-inspect`.
4. It tries up to three ways of connecting to the live event hub and keeps the first that works (see "Connection mode" below).
5. It joins each shard the way the panel does, `JoinShard(tenantId, shardId)`. A shard that refuses is logged and skipped, and the other shards are still recorded. If every shard refuses, it stops after 3 identical failures with exit code 3 instead of retrying forever.
6. It prints `Recording. ...`, and then each event as it arrives. `Metrics` (every ~3 s) is never printed, and `UserRoster` and `ShardStateChanged` (repeated every ~10 s) are printed only when they change. Every event is still saved to `events.jsonl`.

While it records, do the things you want captured: join the game, chat, run a console command from the panel, leave. If you can, restart the server once, or unplug the PC's network for a few seconds, to test reconnecting. It stops after `--duration` seconds, or when you press Ctrl+C.

Useful options:

| Option | What it does |
|---|---|
| `--shard <id>` | Record only this shard. Repeat for more. Default: every shard the bot can see. Pick a **running** server. |
| `--join-attempts <n>` | Give up after this many identical join failures. Default 3. |
| `--no-inspect` | Don't read the panel's JavaScript. |
| `--duration <seconds>` | Stop after this long. Default: until Ctrl+C. |
| `--mode auto\|direct\|negotiate\|negotiate-reuse` | Force one connection mode. Default `auto`. |
| `--extra-hub tasks` / `--extra-hub spire` | Also record the tasks or spire hub. |
| `--quiet` | Don't print events as they arrive. |
| `--verbose` | Debug logging (still redacted). |
| `--keep-host`, `--keep-ips` | Don't replace the panel address or IPv4 addresses in the capture. Leave these off if you'll share the capture. |

### What it writes

Each run makes a new folder, `captures\<UTC time>\`. Git ignores `captures\`.

| File | Contents |
|---|---|
| `summary.txt` | Read this first: event counts by type, the connection mode that worked, connects, hub tickets used, logins and renewals. |
| `events.jsonl` | One line per hub event, with its raw payload. |
| `lifecycle.jsonl` | Connects, disconnects, reconnects, which modes failed and why, and every `JoinShard` call with its arguments and the server's full error. |
| `panel_hub_calls.txt` | Snippets of the panel's own JavaScript around `JoinShard`/`LeaveShard`. CubeCoders' code: fine to send to me, don't commit it. |
| `snapshot.json` | The REST view at the start (health, the bot's permissions, and each shard's state, users and recent console), to compare with the events. |

What is removed before anything is written: the session token, every hub ticket, the password, SignalR connection ids, anything shaped like a JWT, and by default the panel address and every IPv4 address. **Player names are kept.** Read `events.jsonl` before sharing it, and never commit a capture.

### Connection mode

It isn't known yet whether AMP 3 counts the hub's negotiate request and its WebSocket connection as one use of a single-use ticket or two. With `--mode auto`, the recorder tries `direct`, then `negotiate`, then `negotiate-reuse`, and records which worked in `summary.txt`. The bot will use that one.

## 8. Run the Discord bot

### 8.1 Create the Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and sign in. Click **New Application**, name it `Portcullis`, accept the terms and click **Create**.
2. Optional: on **General Information**, upload `docs/branding/png/portcullis-icon.png` as the app icon.
3. Open the **Bot** tab:
   - Click **Reset Token**, confirm, and copy the token into `DISCORD_TOKEN` in `.env`. Discord shows it only once. Treat it like a password: never paste it into chat, an issue or a commit. If it leaks, reset it here and the old one stops working.
   - Turn **Public Bot** off, so only you can add it to a server.
   - Leave all three **Privileged Gateway Intents** off (Presence, Server Members, Message Content). Portcullis uses slash commands only and asks Discord for nothing else.
4. Open the **Installation** tab and set **Install Link** to **None**. You'll use the URL from the next step instead.

### 8.2 Invite the bot and collect ids

1. On **General Information**, copy the **Application ID**.
2. Open this address in a browser, with your application id in place of `APP_ID`:

   ```
   https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot+applications.commands&permissions=3072
   ```

   `permissions=3072` is **View Channels** plus **Send Messages**, and nothing else: no admin, no managing roles or messages. Pick your server and click **Authorize**. (The same URL can be built on the **OAuth2** tab: scopes `bot` and `applications.commands`, permissions View Channels and Send Messages.)
3. In Discord, turn on **User Settings → Advanced → Developer Mode**. Then:
   - right-click your server's icon and **Copy Server ID** into `DISCORD_GUILD_ID`;
   - right-click your own name and **Copy User ID** into `PORTCULLIS_OWNER_IDS`;
   - in **Server Settings → Roles**, right-click each role that may start and stop servers and **Copy Role ID** into `PORTCULLIS_OPERATOR_ROLE_IDS`, separated by commas.

### 8.3 Start it

The bot logs in to AMP, so run it on the PC that can reach the panel, like the recorder. `AMP_PASSWORD` must be filled in (section 6).

```powershell
.\.venv\Scripts\python.exe -m portcullis
```

Linux or macOS: `.venv/bin/python -m portcullis`.

If a setting is missing it lists every problem and stops. Otherwise it logs in to AMP, then to Discord, registers its slash commands in your server (they appear at once; press Ctrl+R in Discord if not) and reads the server list from AMP every five minutes. Stop it with Ctrl+C. It creates `data\portcullis.db` on first start.

### 8.4 Commands

Three levels: **everyone** in the server; **operators** (members with a role in `PORTCULLIS_OPERATOR_ROLE_IDS`); **admins** (members with **Manage Server**). Owners (`PORTCULLIS_OWNER_IDS`) can do everything. Owners can also give extra roles a single task (for example moderators who may whitelist but not stop servers), or a role that may do everything on one server only (8.6). Replies are only visible to you unless the table says otherwise.

| Command | Who | What it does |
|---|---|---|
| `/server list [players]` | everyone | Every server with its state and player count. With `players`, also names everyone online. |
| `/server status <server>` | everyone | State, players, CPU, memory and how long it has been up. Operators also get Start, Stop and Restart buttons, plus the server's quick commands (8.6). If the server allows it, members get a Start button while it is stopped. Shows the address players connect to, when one is set. |
| `/server banner [server] [theme] [public]` | everyone | A picture with a card per server (up to 10): state in words, players, CPU, memory, uptime and address. Dark or light. Only you see it, unless an operator sets `public`. |
| `/server power <server> <action>` | operators | Start, stop, restart, or restart with a warning (says so in game chat 5 minutes and 1 minute before, with a Cancel button). Also update the game, sleep and wake, which ask first (update and sleep) and haven't been seen working on a real panel. Stopping or restarting asks first when players are online. The reply is public, so the channel sees who did it. |
| `/server power-all <action> [tag]` | operators | Starts or stops every server you may run, or only those with an AMP tag. Asks first. |
| `/server send <server> <text> [as]` | operators | Sends one console command, or a chat message if the server has a chat command set (8.6). Only the command word is logged, never its arguments. |
| `/server console <server> [lines]` | operators | The latest console lines (20 by default, up to 50) from AMP's own history, without needing a console relay. AMP's own lines can be mixed in. |
| `/backups <server>` | operators | Lists the server's backups. Operators get a **Make a backup** button; admins also get a picker to **restore** one. Both ask first. The first backup has to be made in AMP, because the bot copies the settings of an existing one. Neither has been seen working on a real panel. |
| `/schedule list <server>` | operators | AMP's schedules on that server, with **Run now**, **Turn on** and **Turn off**. Create them in AMP's panel. |
| `/whitelist request <server> [name]` | everyone | Asks the operators to whitelist you. Without `name`, your linked name is used. |
| `/whitelist add\|remove <server> <name>` | operators | Runs the server's whitelist command (8.6). |
| `/whitelist list <server>` | operators | Names Portcullis has whitelisted on that server. Names added in game aren't known to it. |
| `/link [server] [name]` | everyone | Without options: your linked names. With both: gives you a code to type in game chat, which proves the name is yours. Only for games whose chat Portcullis can read. |
| `/notify [server]` | everyone | Get a direct message when a server comes online; run it again to stop. Without `server`: the servers you follow. Up to 25 servers, at most one message per server every 5 minutes. |
| `/player lookup <name>` | everyone | When a player was last seen, total play time and servers. |
| `/player top [server]` | everyone | Most play time. |
| `/player moderate <server> <name> <kick\|ban\|unban> [reason]` | operators | Runs the server's moderation command (8.6). The reason goes into the command, so it has the same character limits as names. |
| `/player watch <name> [note]` | operators | Adds a name to the watchlist, or takes it off. When it joins any server, the moderator channel gets a note. Up to 200 names. |
| `/player watchlist` | operators | The watched names and their notes. |
| `/poll create <question> <options> [hours]` | operators | Posts a poll with a button per answer (separate answers with `;`). Each member has one vote and can change it. It closes after `hours`, or with its Close button. |
| `/poll list` | operators | Open polls, with links. |
| `/event create <title> <when> [details] [server]` | operators | Posts an event with Going and Cancel buttons. `when` takes `in 2h 30m`, `20:00`, `tomorrow 20:00` or `2026-12-24 20:00`, in the time zone set in `/setup bot`. Everyone who pressed Going is pinged 30 minutes before. |
| `/event list` | everyone | Upcoming events. |
| `/user <member>`, or right-click a member → **Apps → Portcullis user** | operators | Linked names, flags and recent actions, with buttons to mark them as a donator, block them from whitelist requests, or remove their links. |
| `/setup relay`, `/setup server <server>`, `/setup bot` | admins | Relay channels, per-server settings, and bot-wide settings (8.6). |
| `/alert create <metric> [threshold] [minutes] [server] [channel] [role]` | admins | Your own alert: CPU or memory at or above a percentage, more or fewer players than a number, or offline, for `minutes` (default 5, 0 = at once). Leave out `server` to watch every server, and `channel` to post in the alert channel. It fires once, pings only `role`, and posts again when it clears. Up to 50 rules. These are Portcullis's own checks, once a minute, not AMP triggers. |
| `/alert list` | admins | Your alert rules, with **Mute 1 hour**, **Unmute** and **Delete**. |
| `/audit [member] [server] [action] [result] [export]` | admins | Who did what through Portcullis, refused attempts included. `export` sends it all as a JSON file. |
| `/admin diagnostics` | admins | Checks the connection to AMP and every server, and shows the relays and the bot's status. A server line with a 403 names the AMP permission the bot lacks. It also lists what the bot's AMP account may do, and warns if it is a full admin account. |
| `/admin export` / `/admin import <file>` | admins | Download all Portcullis settings as a file, and restore them. Gatekeeper V3 databases can't be imported yet. |
| `/help` | everyone | Lists the commands you can use. |

Not built: creating schedules (the scheduler's step and trigger formats haven't been checked on a real panel), a Discord-to-game chat bridge (it needs a privileged Discord intent, and `/server send as:chat` covers it), and giveaways, vote-to-restart and managing AMP accounts from Discord (not game-server features, or too easy to abuse).

**Coming from Gatekeeper V3?** `/help v3:<old command>` says where any of V3's 117 commands went, and [V3_PARITY.md](V3_PARITY.md) lists them all.

Discord's **Server Settings → Integrations → Portcullis** can narrow further who sees each command. It can't widen the bot's own checks.

**Profile status.** The bot's profile shows a live summary such as `2/4 servers online · 5 players`, updated as servers start and stop and players come and go (at most every 15 seconds). Hidden servers aren't counted. If AMP can't be reached, the bot shows as idle with `Can't reach AMP`.

### 8.5 Relays

Run `/setup relay` once per server and kind. One channel can take several kinds, or several servers. Give the bot **View Channel** and **Send Messages** in that channel first; the command checks and tells you if it can't post there.

- **Console** posts the game's own output in code blocks, a batch every couple of seconds. AMP's own `agent` and `system` lines are left out. If the game prints faster than Discord accepts, the oldest lines are skipped and the next message says how many.
- **Events** posts players joining and leaving, and real state changes (Online, Offline, Busy...). Join and leave need AMP to track players for that game; Arma Reforger, for one, gives none.
- **Chat** is **off for every game** until Portcullis knows how that game writes chat to its console. AMP 3 has no chat event, so chat is read from console lines with a per-game pattern taken from a real capture. Setting a chat channel for a game without a pattern is allowed, and the bot says nothing will appear yet.

Mentions never ping: everything the bot posts has `@everyone`, `@here`, user and role mentions turned off, and player names and chat are escaped so they can't format text or hide links.

### 8.6 Server and bot settings

`/setup server <server>` opens a panel for one server:

- three channel pickers for the console, events and chat relays, and a **Stop all relays** button;
- **Name and visibility**: the name shown in Discord, a short prefix, and whether members can see the server at all;
- **Game commands**: the console commands Portcullis sends for that game, with `{name}` or `{message}` where the player name or text goes. Examples: `whitelist add {name}`, `whitelist remove {name}`, `say {message}`. Each game has its own; check its console help. Whitelist commands and `/server send ... as:chat` stay off for a server until these are filled in.

`/setup bot` sets the channel where whitelist requests appear, with Approve and Deny buttons for operators. Until a channel is set, whitelist requests are off. The requester gets a direct message with the decision, if they allow messages from server members.

- **Moderation commands**: the kick, ban and unban commands for `/player moderate`, with `{name}` and `{reason}`, for example `kick {name} {reason}`. Each stays off until filled in.
- **Options**: the address players connect to (shown on `/server status`), whether any member may start the server while it's stopped (once every 2 minutes, logged), whether first joins are posted in the events channel (on by default; they start once Portcullis has a day of join history), and CPU and memory alert thresholds in percent (an alert when it stays above for 5 minutes).
- **Server operators (owners)**: roles that may do everything on this server and nothing elsewhere. Only owners can set them.
- **Quick commands**: up to 5 one-click console commands, one per line as `Label = command`, for example `Save world = save`. They show as buttons under `/server status` for operators, and each press is logged with its label.

`/setup bot` also has a channel picker for the **live server board**: one message that Portcullis keeps editing with every server's state and player count (hidden servers left out). It updates at most every 30 seconds. If someone deletes the message, the next update posts a new one. **Turn the server board off** stops it; delete the old message yourself.

The rest of `/setup bot`:

- **Daily summary and count channel**: a channel for a daily summary (yesterday's players, new players and peak) and play-time milestones, and a channel, voice channels included, renamed to the number online (needs Manage Channels, and lags a few minutes).
- **Alert and log channels**: an **alerts** channel (a server crashes, stops without `/server power`, stays above its CPU or memory threshold, or hasn't been backed up for the set number of days; also when AMP can't be reached for 3 minutes and when it's back), a **moderator notes** channel (watchlist and name-rule joins) and an **audit log** channel (every action as it happens, at most 20 posts a minute).
- **Roles**: a role pinged on alerts; a role whose members' whitelist requests are approved at once if they ask for a name they have linked; and a role given to members who link a name. The last two can't be `@everyone`, a role with admin-type permissions, or a role above the bot's own; the bot needs **Manage Roles** to hand one out.
- **Options**: the time zone for `/event` times (such as `Europe/Amsterdam`), the backup age in days that triggers an alert, name rules: words that flag a joining player's name to moderators (look-alike letters such as `4` for `a` are caught too), and the live server board style: `embed` (text, the default) or `image` (the same picture as `/server banner`).
- **Who can do what (owners)**: extra roles per task (power, console, whitelist, moderation, polls, events, backups). Only owners can change this, and `/admin import` skips it unless an owner runs the import.
- **Check setup** lists what is missing: no relay, a game command not filled in, a channel the bot can't post in, and similar.

Player names are checked before they go into a console command: names with line breaks or characters such as `;`, `|`, `&`, quotes or `$` are refused, so a name can't add a second command.

## 9. Check a new AMP build's API (spec-diff)

When AMP ships a new build, copy its `data/versions/<version>/webapi/webapi.json` off the AMP host and run:

```powershell
cd packages\portcullis-api
..\..\.venv\Scripts\python.exe -m codegen.spec_diff path\to\webapi.json
cd ..\..
```

It lists breaking, changed and added API operations against the spec Portcullis is built on. Exit code 1 means something breaking. Add `--markdown` for a table you can paste into an issue. Updating the pinned spec is covered in [DEVELOPING.md](DEVELOPING.md#when-amp-ships-a-new-build).

## 10. Where things live

| Path | What it is |
|---|---|
| `.env` | Your settings. Git ignores it. |
| `captures\` | Recorder output. Git ignores it. |
| `portcullis\amp\` | The AMP 3 client: login and renewal, API calls, the live event hub. |
| `portcullis\amp\spec\` | The pinned AMP 3 API spec the client is generated from. |
| `portcullis\core\` | The bot's own types and the event relay, independent of AMP and Discord. |
| `portcullis\bot\` | The Discord side: slash commands and the client. It never talks to AMP directly. |
| `portcullis\db\` | The database code and its migrations. The bot keeps its file at `PORTCULLIS_DB` (default `data\portcullis.db`) and saves a `.bak` copy before each schema upgrade. |
| `data\` | The bot's database. Git ignores it. |
| `tools\` | The recorder, spec-diff and the client generator. |
| `docs\` | The design ([DESIGN.md](DESIGN.md)), developer notes ([DEVELOPING.md](DEVELOPING.md)) and API notes. |

## 11. Known gaps

- **In-memory restart warnings.** A warned restart counts down inside the bot; restarting Portcullis during the countdown cancels it.
- **Not every planned command exists.** Creating schedules and deleting backups are still to come (see 8.4).
- **Unverified AMP actions.** Update, sleep, wake, making and restoring backups, and the schedule buttons use routes from the API spec that haven't run on a real panel.
- **Play time starts now.** `/player` only knows what the bot has seen since it started recording. Sessions still open when the bot stops are closed at its next start, so a long restart can count a little extra time.
- **Leaves are not verified.** AMP's player-left event hasn't been captured yet for any game; if it never arrives, a player's session ends when the server stops.
- **Untested against a real AMP 3.** Everything has been tested against a fake AMP built from the API docs. Not yet checked against a real panel: whether login and the live hub work as the docs say, which connection mode works, whether joining a shard's events succeeds, what the event payloads really look like, and whether the generated models accept real responses. The recorder exists to answer these questions.
- **Player events depend on the game.** Join and chat are verified for Unturned only (AMP's own `UserJoined` event, plus a chat pattern on the console); leave has not been captured yet. Arma Reforger gives no player names or chat through AMP. Other games need a capture with a player in game before their chat can be relayed.
- **One AMP preview build.** Portcullis is built against AMP 3.0.0.6, which expires on 7 Dec 2026. A newer build may change the API; run spec-diff on it first.
- **Licence.** The pinned spec and generated client contain CubeCoders' text. That's fine while the repo is private. See [DEVELOPING.md](DEVELOPING.md#licence-note) before it goes public.

## 12. Troubleshooting

| What you see | Likely cause and fix |
|---|---|
| `Set AMP_URL and AMP_USERNAME in .env` | `.env` is missing, or not in the folder you ran the command from. Run it from the `Portcullis` folder. |
| `Login failed: ... second factor, a passkey ...` (`LoginNotSupported`) | The bot user has 2FA or a passkey, or must change its password. Turn those off for that user in the panel, then log in once in a browser to clear any forced password change. |
| `Login failed` with a 401 (`AuthFailed`) | Wrong username or password. Check the user exists and try logging in with it in a browser. |
| `RateLimited` (429) | Too many failed logins. Wait a few minutes before trying again. |
| `Unreachable`, or connection refused or timed out | Wrong `AMP_URL`, wrong port, or the PC can't reach the panel. Open the same address in a browser on this PC. Check `http` against `https`. |
| `The bot account can't see any shards` | The bot's role lacks `view` on any shard. Fix the role in the panel. |
| `MissingCapability` (403) in `snapshot.json` | The role lacks that permission, usually `analytics.users` for the player list. Optional for recording. |
| `No connection mode worked` | All three ways of connecting to the event hub failed. Open `lifecycle.jsonl` in that capture folder: each `probe-failed` line says why. Send me those lines (they hold no secrets). |
| Repeated 401s while recording | The session expired and renewal failed. The client logs in again by itself; if it keeps happening, check whether the user's password changed or the user was disabled. |
| `Stopped: gave up after 3 identical join failures` | The hub connection works but AMP refuses `JoinShard`. Try `--shard <id>` with one running server the bot has `view` and `console.read` on. If it still fails, send `lifecycle.jsonl` and `panel_hub_calls.txt`. |
| `Hub task stopped: ...` | The live connection gave up. `lifecycle.jsonl` has the reason; send that line. |
| `DISCORD_TOKEN is missing` or `DISCORD_GUILD_ID is missing` | Fill them in from section 8. |
| `Discord refused the bot token` | The token was reset or mistyped. Reset it on the Bot tab and paste the new one. |
| The slash commands don't appear | Check the bot is in the server set in `DISCORD_GUILD_ID` (the log warns if not), then press Ctrl+R in Discord. Commands only exist in that server. |
| `Portcullis only works in the Discord server it was set up for` | The command was used in another server. Use it in the one from `DISCORD_GUILD_ID`. |
| The profile shows no status | It appears a few seconds after the bot logs in. It is shown as a custom status, under the bot's name in the member list. |
| A whitelist approval says AMP didn't take the command | Check the server's whitelist command in `/setup server`, and that the game is running. The request stays approved; add the name by hand. |
| Nothing appears in a relay channel | Check the bot can see and post in it, that the server is running, and that something is happening (console lines, a player joining). For chat, see section 8.5. Warnings in the bot's log say if a post failed. |
| PowerShell: "running scripts is disabled" | Only happens if you use `Activate.ps1`. Use `.\.venv\Scripts\python.exe` as shown, or see step 3. |
| `py` not found | Python isn't installed, or wasn't added to PATH. Reinstall it with "Add python.exe to PATH" ticked. |
