# Setting up Portcullis

This guide covers what exists today:

- the **Discord bot**: server control, live relays, whitelist, players, polls, events, backups, schedules, alerts, setup panels and the audit log (section 8);
- the AMP 3 client, [`portcullis-api`](../packages/portcullis-api/README.md), which you can also use on its own;
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
| `AMP_PASSWORD` | recorder, bot | **Leave this empty.** Store it in the OS keyring instead (6.1). The recorder asks for it when it starts if it finds none. |
| `DISCORD_TOKEN` | bot | **Leave this empty** too, and store the token from section 8.1 in the keyring (6.1). |
| `DISCORD_GUILD_ID` | bot | Your Discord server's id (section 8.2). The bot only works there. |
| `PORTCULLIS_OWNER_IDS` | bot | Your own Discord user id. Owners can use every command. |
| `PORTCULLIS_OPERATOR_ROLE_IDS` | bot | Ids of the Discord roles allowed to start, stop and restart servers, comma separated. |
| `PORTCULLIS_DB` | bot | Optional; the default is `data/portcullis.db`. |
| `PORTCULLIS_LOG_LEVEL` | recorder, bot | Optional: `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `AMP_PASSWORD_FILE`, `DISCORD_TOKEN_FILE` | bot | Optional: a file holding the secret, for Docker or systemd secrets (6.1). |
| `PORTCULLIS_PLAINTEXT_SECRETS` | bot | Optional: `warn` (default) logs a warning at every start while a secret is in `.env`; `refuse` stops the bot instead; `allow` says nothing. |
| `PORTCULLIS_KEYRING` | bot | Optional: `off` skips the OS keyring, for a machine where it would prompt or hang. |
| `PORTCULLIS_LOG_FILE` | bot | Optional; the default is `data/logs/portcullis.log`. `off` logs to the console only. |
| `PORTCULLIS_LOG_FORMAT` | bot | Optional: `text` (default) or `json` for the log file. |
| `PORTCULLIS_LOG_CONSOLE` | bot | Optional: `text` (default) or `json` for the console, for Docker or systemd. |
| `PORTCULLIS_LOG_MAX_MB`, `PORTCULLIS_LOG_KEEP` | bot | Optional: start a new log file every so many MB (default 5) and keep that many old ones (default 5). |
| `PORTCULLIS_BANNER` | bot | Optional: `off` hides the welcome banner at start-up. |
| `PORTCULLIS_STATE_FILE` | bot | Optional; the default is `data/state.json` (section 8.3). `off` turns restart recovery off. |
| `PORTCULLIS_PANEL_RETRIES` | bot | Optional: extra tries when the panel doesn't answer a read, 0 to 5 (default 2). Actions are never retried. |
| `PORTCULLIS_PANEL_TRIP_AFTER` | bot | Optional: failures in a row before the bot pauses calls to the panel, 1 to 20 (default 3). |
| `PORTCULLIS_METRICS` | bot | Optional, **off by default**: `on` starts the health and metrics endpoint (section 8.3). |
| `PORTCULLIS_METRICS_HOST`, `PORTCULLIS_METRICS_PORT` | bot | Optional: where it listens. Default `127.0.0.1` and `9464`, so only this PC can reach it. |
| `PORTCULLIS_METRICS_TOKEN` | bot | Required if the host is anything but this PC; at least 16 characters. Callers send `Authorization: Bearer <token>`. |
| `PORTCULLIS_UPDATE_CHECK` | bot | Optional, **off by default**: `notify` checks once a day for a newer release and posts in the alert channel. It never installs anything. |
| `PORTCULLIS_UPDATE_REPO` | bot | Optional: the repository to check, default `MinVT-dev/Portcullis`. |

Real environment variables override `.env` values.

### 6.1 Secrets: the AMP password and the Discord token

Anyone who has these two can run your servers and speak as the bot, so they don't belong in a text file. Portcullis looks for each one in this order and uses the first it finds:

1. an environment variable (`AMP_PASSWORD`, `DISCORD_TOKEN`), for services and containers;
2. a file named by `AMP_PASSWORD_FILE` or `DISCORD_TOKEN_FILE` (Docker and systemd secrets);
3. the **OS keyring**: Windows Credential Manager, the macOS Keychain, or the Secret Service on Linux (GNOME Keyring, KWallet). This is the normal place;
4. `.env`, as plain text. This still works, so an existing install keeps running after an update, but the bot logs a warning at every start until you move the secret.

Store them in the keyring (the typing doesn't show, and you type each one twice):

```powershell
.\.venv\Scripts\python.exe -m portcullis secrets set AMP_PASSWORD
.\.venv\Scripts\python.exe -m portcullis secrets set DISCORD_TOKEN
.\.venv\Scripts\python.exe -m portcullis secrets status
```

`status` says where each secret comes from, never its value, and warns about an old copy left in `.env` or a `.env` other users can read.

**Moving an existing install.** If your secrets are in `.env` today, run `python -m portcullis secrets migrate`. It copies each one into the keyring, checks it reads back the same, and only then replaces the line in `.env` with a comment. Restart the bot afterwards. Copies of the old file (backups, a synced folder) still hold the old values; if that file was ever shared, rotate both secrets.

**Rotating a secret** (a leak, a new password, or just routine):

1. Make the new one first: reset the token on the Bot tab of the Discord developer portal, or change the bot user's password in AMP. The old one stops working at once.
2. `python -m portcullis secrets rotate DISCORD_TOKEN` (or `AMP_PASSWORD`). The old value is kept next to the new one.
3. Restart Portcullis. If the new value is wrong, `python -m portcullis secrets rollback DISCORD_TOKEN` puts the previous one back.

`secrets delete NAME` removes one from the keyring. With no keyring on the machine (a headless Linux server), use an environment variable or a `_FILE` readable only by the bot's user (`chmod 600`).

The secrets never appear in the bot's log, in Discord replies, embeds or the audit log: every log line and every error shown in Discord passes through a filter that replaces them with `<redacted>`.

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

The bot logs in to AMP, so run it on the PC that can reach the panel, like the recorder. `AMP_PASSWORD` and `DISCORD_TOKEN` must be stored (section 6.1).

```powershell
.\.venv\Scripts\python.exe -m portcullis
```

Linux or macOS: `.venv/bin/python -m portcullis`.

If a setting is missing it lists every problem and stops. Otherwise it logs in to AMP, then to Discord, registers its slash commands in your server (they appear at once; press Ctrl+R in Discord if not) and reads the server list from AMP every five minutes. Stop it with Ctrl+C. It creates `data\portcullis.db` on first start.

Ctrl+C (or Ctrl+Break, or SIGTERM from a service manager or `docker stop`) stops it cleanly: it greys out the live status cards, closes the Discord connection, leaves the AMP event streams, logs out of AMP and closes the database, then prints `Portcullis stopped` and exits with code 0. That takes a second or two, and each step is skipped after a few seconds if Discord or AMP don't answer. Press Ctrl+C a second time to stop at once. If you start it from a `.bat` file, Windows then asks `Terminate batch job (Y/N)?`; that question comes from Windows, not the bot, and either answer is fine.

**Check the settings first.** `python -m portcullis --check` reads every setting, prints the banner, logs in to the panel and checks the Discord token and server, then exits. It doesn't bring the bot online, register commands or write any file. Add `--offline` to check the settings alone. It exits with 0 when all is well, 1 when a login failed and 2 when a setting is wrong. Every setting problem is listed at once, and a `PORTCULLIS_` name that looks like a typo of a real one (`PORTCULLIS_LOG_LEVLE`) gets a warning.

**The banner.** At start-up the bot prints its version, the panel address and whether it answered, a summary of the settings (passwords and tokens are never shown), the optional features that are on, and how the last run ended. Consoles that can't draw box characters get a plain version, and `NO_COLOR` turns colour off. `PORTCULLIS_BANNER=off` hides it.

**When the panel is down.** A wrong AMP password still stops the bot at once. A panel that doesn't answer does not: the bot starts anyway and keeps trying to log in. While it runs, a read that gets no answer (a timeout, a refused connection, a 5xx) is tried up to `PORTCULLIS_PANEL_RETRIES` more times; a start, stop, console line or backup is never sent twice. After `PORTCULLIS_PANEL_TRIP_AFTER` failures in a row the bot pauses calls to the panel: commands answer at once that AMP can't be reached, the log says so once, and one test call goes out every 10 to 60 seconds until the panel answers. Live cards keep the last status the panel gave, greyed out and marked with when it was said, instead of switching to Unknown. The alert channel notice for a panel outage is unchanged (after 3 minutes, and again when it's back).

**Restart recovery.** `data\state.json` records when the bot started, a heartbeat every minute, and whether it stopped cleanly, plus the last status of every server. The next start's banner says "stopped cleanly 2 h ago (Ctrl+C)" or "ended without a clean stop (crash or power loss); last seen 5 min ago". If the panel is down at that start, the live cards show those saved statuses, marked stale. Live cards are edited in place after a restart (their message ids are in the database); a card someone deleted is posted again.

**Logs.** The console shows short lines, coloured by level when the console supports it. The full log goes to `data\logs\portcullis.log`, which starts a new file every 5 MB and keeps the last 5. `PORTCULLIS_LOG_FORMAT=json` writes one JSON object per line (`time` in UTC, `level`, `logger`, `message`, and `error` with the traceback). Passwords and tokens are masked in every log.

**Health and metrics (off by default).** With `PORTCULLIS_METRICS=on` the bot serves `http://127.0.0.1:9464/healthz` (JSON; status 200 while Discord is connected and the panel answers, 503 otherwise) and `/metrics` (Prometheus text: Discord connection and latency, panel reachability, request, retry and outage counters, servers by state, players online). Only this PC can reach it unless you set `PORTCULLIS_METRICS_HOST`, and then `PORTCULLIS_METRICS_TOKEN` is required. Neither page shows names, ids, addresses or secrets. If the port is taken the bot logs it and runs on without the endpoint.

**Updating.** With `PORTCULLIS_UPDATE_CHECK=notify` the bot asks GitHub once a day for the latest release and, when it's newer than the running version, posts once in the alert channel with the link. It never downloads or installs anything: an update can bring a database migration or new settings to read first, and releases aren't signed yet, so there is nothing to verify a download against. To update:

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m portcullis --check
```

Then stop the bot with Ctrl+C and start it again. The database is backed up to a `.bak` file before any schema upgrade.

### 8.4 Commands

Three levels: **everyone** in the server; **operators** (members with a role in `PORTCULLIS_OPERATOR_ROLE_IDS`); **admins** (members with **Manage Server**). Owners (`PORTCULLIS_OWNER_IDS`) can do everything. Owners can also give extra roles a single task (for example moderators who may whitelist but not stop servers), or a role that may do everything on one server only (8.6). Replies are only visible to you unless the table says otherwise.

| Command | Who | What it does |
|---|---|---|
| `/players [server]` | everyone | Who is online on each server, with the count and the player limit when AMP knows it. Admins choose per server whether members see names, only the count, or nothing (`/setup players`). Some games don't report players to AMP; those show 0 with a note. |
| `/server list [players]` | everyone | Every server with its state and player count. With `players`, also names everyone online. |
| `/server status <server>` | everyone | State, players, CPU, memory and how long it has been up. Operators also get Start, Stop and Restart buttons, plus the server's quick commands (8.6). If the server allows it, members get a Start button while it is stopped. Shows the address players connect to, when one is set. |
| `/server diagnostics <server>` | operators | CPU, memory and disk as bars against their limits, how long it has been up, and AMP's last error message. AMP 3 has no TPS figure, so none is shown. The disk figure hasn't been seen on a real panel. |
| `/server banner [server] [theme] [public]` | everyone | A picture with a card per server (up to 10): state in words, players, CPU, memory, uptime and address. Dark or light. Only you see it, unless an operator sets `public`. |
| `/server power <server> <action>` | operators | Start, stop, restart, or restart with a warning (says so in game chat 5 minutes and 1 minute before, with a Cancel button). Also update the game, sleep and wake, which haven't been seen working on a real panel. Every action asks first, privately: a card names the server, the action and how many players are on, and only you can answer it. It expires after a minute. Your roles are checked again when you confirm. The result is public, so the channel sees who did it. After an update, Portcullis follows the server and posts how it ended (finished, failed with AMP's reason, or still running after 14 minutes). There is no **kill**: AMP 3 has no route for it, so use stop, or kill the server in the AMP panel. |
| `/server power-all <action> [tag]` | operators | Starts or stops every server you may run, or only those with an AMP tag. Asks first. |
| `/server send <server> <text> [as]` | operators | Sends one console command, or a chat message if the server has a chat command set (8.6). Only the command word is logged, never its arguments. Commands on the server's blocked list (8.6, Console rules) are refused for everyone but owners. |
| `/server console <server> [lines]` | operators | The latest console lines (20 by default, up to 50) from AMP's own history, without needing a console relay. AMP's own lines can be mixed in. Lines matching the server's hide rules are left out. |
| `/backups <server>` | operators | Lists the server's backups. Operators get a **Make a backup** button; members with Manage Server and an operator role (or the backups power) also get pickers to **restore** or **delete** one. All three ask first; restoring asks you to type the server's name, then follows AMP until it finishes and says whether AMP's PreRestore safety copy appeared. The first backup has to be made in AMP, because the bot copies the settings of an existing one. Making a backup works on a real panel; restoring and deleting haven't been seen working yet (docs/RESTORE_TEST.md). |
| `/schedule list <server>` | operators | AMP's schedules on that server, with **Run now**, **Turn on**, **Turn off**, **Run history** (the last 10 runs, with how long each took and any error) and, for admins, **Delete** (asks first). Create them in AMP's panel. History and delete haven't been seen working on a real panel. |
| `/whitelist request <server> [name]` | everyone | Asks the operators to whitelist you. Without `name`, your linked name is used. |
| `/whitelist add\|remove <server> <name>` | operators | Runs the server's whitelist command (8.6). |
| `/whitelist list <server>` | operators | Names Portcullis has whitelisted on that server. Names added in game aren't known to it. |
| `/link [server] [name]` | everyone | Without options: your linked names. With both: gives you a code to type in game chat, which proves the name is yours. Only for games whose chat Portcullis can read. |
| `/notify [server]` | everyone | Get a direct message when a server comes online; run it again to stop. Without `server`: the servers you follow. Up to 25 servers, at most one message per server every 5 minutes. |
| `/player lookup <name>` | everyone | When a player was last seen, total play time and servers. |
| `/player top [server]` | everyone | Most play time. |
| `/player id <value>` | everyone | A Steam ID in all three formats (SteamID64, `STEAM_0:Y:Z`, `[U:1:N]`) from any of them or a `/profiles/` link, worked out offline. Or a Minecraft name's UUID, or a UUID's name, looked up at Mojang (one lookup every 3 seconds per member, cached for an hour). |
| `/player moderate <server> <name> <kick\|ban\|unban> [reason]` | operators | Runs the server's moderation command (8.6). The reason goes into the command, so it has the same character limits as names. |
| `/player watch <name> [note]` | operators | Adds a name to the watchlist, or takes it off. When it joins any server, the moderator channel gets a note. Up to 200 names. |
| `/player watchlist` | operators | The watched names and their notes. |
| `/poll create <question> <options> [hours]` | operators | Posts a poll with a button per answer (separate answers with `;`). Each member has one vote and can change it. It closes after `hours`, or with its Close button. |
| `/poll list` | operators | Open polls, with links. |
| `/event create <title> <when> [details] [server]` | operators | Posts an event with Going and Cancel buttons. `when` takes `in 2h 30m`, `20:00`, `tomorrow 20:00` or `2026-12-24 20:00`, in the time zone set in `/setup bot`. Everyone who pressed Going is pinged 30 minutes before. |
| `/event list` | everyone | Upcoming events. |
| `/user <member>`, or right-click a member → **Apps → Portcullis user** | operators | Linked names, flags and recent actions, with buttons to mark them as a donator, block them from whitelist requests, or remove their links. |
| `/setup start` | admins | The first-run wizard: five steps (AMP, alerts, live status cards, Panel and Support links, brand emojis), each marked done or not. Each pick saves at once. |
| `/setup live <server> [channel]` | admins | Posts a live status card for one server in a channel, or with no channel, removes its card (8.6). |
| `/setup relay`, `/setup server <server>`, `/setup bot` | admins | Relay channels, per-server settings, and bot-wide settings (8.6). |
| `/setup safety [confirm] [staff_channel] [staff_role] [denied_alerts] [clear]` | admins | Whether starting a server asks first too (it does by default; stop, restart and update always ask), and where blocked attempts are reported (8.6, Safety). With no options, shows the current settings. |
| `/setup players <show> [server]` | admins | What `/players` shows members: names (the default), only the count, or nothing. Staff always see names. Leave out `server` to set the default. |
| `/alert create <metric> [threshold] [minutes] [server] [channel] [role]` | admins | Your own alert: CPU or memory at or above a percentage, more or fewer players than a number, or offline, for `minutes` (default 5, 0 = at once). Leave out `server` to watch every server, and `channel` to post in the alert channel. It fires once, pings only `role`, and posts again when it clears. Up to 25 rules. These are Portcullis's own checks, once a minute, not AMP triggers. |
| `/alert list` | admins | Your alert rules, with **Mute 1 hour**, **Unmute** and **Delete**. |
| `/graph <server> [metric] [period] [share]` | everyone | A graph of players or uptime over the last day, week or month, from the history Portcullis records. CPU and memory graphs are for operators. Only you see it unless you set `share`. See [MONITORING.md](MONITORING.md). |
| `/monitor alerts [server] [channel] [dm_add] [dm_remove] [reset_channel] [update_notices]` | admins | Where alerts go: a channel and DMs, bot-wide or for one server. |
| `/monitor mute <server> <minutes>` | operators | Silences one server's alerts for up to a week. `0` turns them back on. |
| `/monitor thresholds <server> [cpu] [memory] [disk] [gap] [minutes]` | admins | CPU, memory and disk limits for one server, with a gap before an alert clears. |
| `/monitor weekly [enabled] [channel] [day] [hour] [preview]` | admins | The weekly summary (off by default): uptime, peak players, starts, crashes and alerts per server. |
| `/monitor history [days]` | admins | How many days of history to keep for graphs (30 by default, 0 stops recording). |
| `/monitor test [server]` | admins | Sends a test alert to the channel and DMs it would use. |
| `/audit [member] [server] [action] [result] [export] [stats]` | admins | Who did what through Portcullis, refused attempts included. `export` sends it all as a JSON file. `stats` shows counts for the last 30 days instead: most common actions, most active members, and results. |
| `/admin diagnostics` | admins | Checks the connection to AMP and every server, and shows the relays and the bot's status. A server line with a 403 names the AMP permission the bot lacks. It also lists what the bot's AMP account may do, and warns if it is a full admin account. |
| `/admin export` / `/admin import <file>` | admins | Download all Portcullis settings as a file, and restore them. Gatekeeper V3 databases can't be imported yet. |
| `/admin bot <status\|sync\|stop>` | owners | Status (uptime, Discord latency, versions, database), re-sync the slash commands to this Discord server, or stop the bot (asks first; a service manager or AMP starts it again). |
| `/health` | everyone | One line each for AMP, Discord, the servers, the live status cards, the brand emojis and the database, with ✅ ⚠️ ❌. Details are in `/admin diagnostics`. |
| `/help [v3]` | everyone | Lists the commands you can use. With `v3`, says where a Gatekeeper V3 command went. |

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
- **Name and visibility**: the name shown in Discord, a short prefix, whether members can see the server at all, and whether only donators (marked on `/user`) may make whitelist requests for it;
- **Game commands**: the console commands Portcullis sends for that game, with `{name}` or `{message}` where the player name or text goes. Examples: `whitelist add {name}`, `whitelist remove {name}`, `say {message}`. Each game has its own; check its console help. Whitelist commands and `/server send ... as:chat` stay off for a server until these are filled in.

`/setup bot` sets the channel where whitelist requests appear, with Approve and Deny buttons for operators. Until a channel is set, whitelist requests are off. The requester gets a direct message with the decision, if they allow messages from server members.

- **Moderation commands**: the kick, ban and unban commands for `/player moderate`, with `{name}` and `{reason}`, for example `kick {name} {reason}`. Each stays off until filled in.
- **Options**: the address players connect to (shown on `/server status`), whether any member may start the server while it's stopped (once every 2 minutes, logged), whether first joins are posted in the events channel (on by default; they start once Portcullis has a day of join history), and CPU and memory alert thresholds in percent (an alert when it stays above for 5 minutes).
- **Console rules**: **hidden console lines**, one rule per line, kept out of the console relay and `/server console`. A rule is text matched anywhere in a line, ignoring case, or a regular expression after `re:` (for example `re:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}` hides IP addresses). Up to 25 rules, of which up to 10 `re:` rules. Because a player can put any text in a console line, patterns that could freeze the bot are refused: a repeated part that itself repeats or has alternatives (`(a+)+`, `(a|aa)+`), back-references, and more than two parts that repeat without a small limit (write `\d{1,3}`, not `\d+`). Only the first 300 characters of a line are checked against `re:` rules. And **blocked console commands**: command words such as `op stop ban-ip` that `/server send` refuses for everyone but owners. The check also catches `minecraft:op` and `execute ... run op`, but not aliases a game or plugin adds, so treat it as a guard against mistakes, not a lock. Quick commands aren't checked, because only admins set them.
- **Server operators (owners)**: roles that may do everything on this server and nothing elsewhere. Only owners can set them.
- **Quick commands**: up to 5 one-click console commands, one per line as `Label = command`, for example `Save world = save`. They show as buttons under `/server status` for operators, and each press is logged with its label.

`/setup bot` also has a channel picker for the **live server board**: one message that Portcullis keeps editing with every server's state and player count (hidden servers left out). It updates at most every 2 minutes, and waits longer after a Discord rate limit. If someone deletes the message, the next update posts a new one. **Turn the server board off** stops it; delete the old message yourself.

**Live status cards** are one message per server, in a channel you pick in `/setup start` or with `/setup live`. Each card shows:
- the state in words, players, how long the server has been up, and the next restart;
- CPU and memory, the connect address, and who is online;
- the game's logo.

Portcullis edits the cards in place. It edits them every 2 to 5 minutes (3 by default, your choice in `/setup start`), and every 5 minutes while nobody is playing. After a Discord rate limit or a failed edit, it waits twice as long each time, up to 15 minutes. When a server changes state, the cards may update sooner, but never within a minute of the last edit. The footer says how often a card updates. Up to 25 cards. If someone deletes a card, the next pass posts a new one.

Two notes:
- **Next restart.** AMP doesn't mark what a schedule is for, so the card shows the soonest enabled schedule with "restart" in its name.
- **Buttons.** **Panel** and **Support** link buttons under the cards are optional (Links in `/setup start`). A link button can't carry a game address, so the address is shown in the card to copy instead.

The rest of `/setup bot`:

- **Daily summary and count channel**: a channel for a daily summary (yesterday's players, new players and peak) and play-time milestones, and a channel, voice channels included, renamed to the number online (needs Manage Channels, and lags a few minutes).
- **Alert and log channels**: clear a picker to turn that channel off. An **alerts** channel (a server crashes, stops without `/server power`, stays above its CPU or memory threshold, or hasn't been backed up for the set number of days; also when AMP can't be reached for 3 minutes and when it's back), a **moderator notes** channel (watchlist and name-rule joins) and an **audit log** channel: one line per admin action as it happens, such as `✅ ok · server.power.stop · Survival · by @Min · confirmed` or `⛔ denied · server.power.stop · Survival · by @Guest`. Each line has the result (ok, denied or failed), the action, the server, who asked, and for a failure the reason with any secret masked. At most 20 lines a minute; the rest are counted and stay in `/audit`.
- **Roles**: a role pinged on alerts; a role whose members' whitelist requests are approved at once if they ask for a name they have linked; and a role given to members who link a name. The last two can't be `@everyone`, a role with admin-type permissions, or a role above the bot's own; the bot needs **Manage Roles** to hand one out.
- **Whitelist rules**: whether `/whitelist request` only takes names the member has proved with `/link`; whether donators asking for their linked name are approved at once, like the auto-approve role; and an optional message (up to 500 characters) added to what an approved member is told.
- **Options**: the time zone for `/event` times (such as `Europe/Amsterdam`), the backup age in days that triggers an alert, name rules: words that flag a joining player's name to moderators (look-alike letters such as `4` for `a` are caught too), and the live server board style: `embed` (text, the default) or `image` (the same picture as `/server banner`).
- **Who can do what (owners)**: extra roles per task (power, console, whitelist, moderation, polls, events, backups). Only owners can change this, and `/admin import` skips it unless an owner runs the import.
- **Check setup** lists what is missing: no relay, a game command not filled in, a channel the bot can't post in, and similar.

**Safety** (`/setup safety`). Power actions ask first, and only the member who asked can confirm (8.4). When someone without the right role tries a power action, a backup restore or a backup delete, they only see "You need an operator role for that." Staff get a **Blocked attempt** card in the staff channel naming the member, the action and the server, and the staff role is pinged if one is set. One card per member and action every 10 minutes; repeats are counted on the next card. Nothing is reported until a staff channel is set. The full history is in `/audit`.

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
| `packages\portcullis-api\` | The AMP 3 client as its own package (MIT): login and renewal, API calls, the live event hub, a fake panel for tests. |
| `packages\portcullis-api\src\portcullis_api\spec\` | The pinned AMP 3 API spec the client is generated from. |
| `portcullis\amp\` | The adapter between the bot and `portcullis-api`. |
| `portcullis\core\` | The bot's own types, live state, the event relay and console rules, independent of AMP and Discord. |
| `portcullis\bot\` | The Discord side: slash commands, buttons and panels. It never talks to AMP directly. |
| `portcullis\assets\` | The game art registry and fallback images. |
| `portcullis\db\` | The database code and its migrations. The bot keeps its file at `PORTCULLIS_DB` (default `data\portcullis.db`) and saves a `.bak` copy before each schema upgrade. |
| `data\` | The bot's database, `state.json` (restart recovery) and `logs\`. Git ignores it. |
| `portcullis\ops\` | Start-up checks and banner, logs, restart recovery, the metrics endpoint and the update check. |
| `tools\` | The live-event recorder and the game art tools. Spec-diff and the client generator are in `packages\portcullis-api\codegen\`. |
| `docs\` | The design ([DESIGN.md](DESIGN.md)), developer notes ([DEVELOPING.md](DEVELOPING.md)) and API notes. |

## 11. Known gaps

- **In-memory restart warnings.** A warned restart counts down inside the bot; restarting Portcullis during the countdown cancels it.
- **Not every planned command exists.** Creating schedules is still to come (see 8.4); create them in AMP's panel.
- **Unverified AMP actions.** Update, sleep, wake, restoring and deleting backups (making one works on a real panel), and the schedule buttons (including run history and delete) use routes from the API spec that haven't run on a real panel.
- **Play time starts now.** `/player` only knows what the bot has seen since it started recording. Sessions still open when the bot stops are closed at its next start, so a long restart can count a little extra time.
- **Leaves are not verified.** AMP's player-left event hasn't been captured yet for any game; if it never arrives, a player's session ends when the server stops.
- **Partly tested against a real AMP 3.** Login, the live hub, the server list, power, the console and listing backups have run on a real 3.0.0.6 panel. Everything else is only tested against a fake AMP built from the API docs. What is left to check is in the [real-panel checklist](BETA_CHECKLIST.md).
- **Player events depend on the game.** Join and chat are verified for Unturned only (AMP's own `UserJoined` event, plus a chat pattern on the console); leave has not been captured yet. Arma Reforger gives no player names or chat through AMP. Other games need a capture with a player in game before their chat can be relayed.
- **AMP preview builds.** Portcullis was built against AMP 3.0.0.6, which expires on 7 Dec 2026, and its AMP client was regenerated on 13 Nov 2026 from the spec a newer live panel served. A newer build may change the API; run spec-diff on it first.
- **Game licences (EULA).** When a server starts without anyone waiting for it (on boot, from a schedule, after an update) and its game has a licence that isn't accepted yet, AMP holds it. `/server status` shows **Needs licence**, the alert channel says so, and an admin can accept or decline it from the status buttons. Accepting is recorded by AMP under the bot's account and by Portcullis under the person who pressed it. This follows the API spec and hasn't run on a real panel yet.
- **Passkeys.** If the panel's passkey policy is set to Required, the bot refuses to log in and says why: a bot can't use a passkey. Keep the policy at Optional or Encouraged. Portcullis reads the expiry date from AMP once a day and, from 14 days before, posts a warning in the alert channel (pinging the alert role in the last 3 days). `/admin diagnostics` shows the date too. Reading that date has not run against a real panel yet.
- **Channels that are gone.** If a relay, alert or log channel is deleted or closed to the bot, Portcullis stops trying it for a while (30 seconds, doubling up to 15 minutes for relays; 10 minutes for the channel lookup) and logs a warning. Lines relayed in that time are dropped. Pick a new channel in `/setup`.
- **Alerts, limits, update status, graphs and the weekly summary** ([MONITORING.md](MONITORING.md)) are tested against a fake Discord and a fake AMP only. Game updates can't be seen as "available": AMP 3 doesn't expose that.
- **Live cards and brand emojis.** These are tested against a fake Discord only. They haven't run on a real Discord server yet.
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
| `The panel at ... isn't answering ... Starting anyway` | The panel was down or slow at start-up. The bot keeps trying; check `AMP_URL` with `python -m portcullis --check`. |
| `The panel stopped answering` in the log | Calls to the panel are paused until it answers again; the log says when it does. Live cards show the last known status, greyed. |
| `The metrics endpoint can't listen on ...` | Another program uses that port. Set `PORTCULLIS_METRICS_PORT` to a free one. |
| A setting `isn't a setting Portcullis knows` | A typo in `.env`. Use the name the warning suggests. |
| `Discord refused the bot token` | The token was reset or mistyped. Reset it on the Bot tab and paste the new one. |
| The slash commands don't appear | Check the bot is in the server set in `DISCORD_GUILD_ID` (the log warns if not), then press Ctrl+R in Discord. Commands only exist in that server. |
| `Portcullis only works in the Discord server it was set up for` | The command was used in another server. Use it in the one from `DISCORD_GUILD_ID`. |
| The profile shows no status | It appears a few seconds after the bot logs in. It is shown as a custom status, under the bot's name in the member list. |
| A whitelist approval says AMP didn't take the command | Check the server's whitelist command in `/setup server`, and that the game is running. The request stays approved; add the name by hand. |
| Nothing appears in a relay channel | Check the bot can see and post in it, that the server is running, and that something is happening (console lines, a player joining). For chat, see section 8.5. Warnings in the bot's log say if a post failed. |
| PowerShell: "running scripts is disabled" | Only happens if you use `Activate.ps1`. Use `.\.venv\Scripts\python.exe` as shown, or see step 3. |
| `py` not found | Python isn't installed, or wasn't added to PATH. Reinstall it with "Add python.exe to PATH" ticked. |
