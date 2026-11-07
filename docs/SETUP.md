# Setting up Portcullis

This guide covers what exists today. Portcullis is not a working Discord bot yet: there is no Discord code. What you can set up and run now is:

- the AMP 3 client library, with its tests;
- the **event recorder**, which logs in to your AMP 3 panel and saves live server events, so the bot's event handling can be built from real data;
- the **spec-diff tool**, which tells you what changed in a new AMP build's API.

The database layer exists as code and tests only. Nothing creates or uses a database file yet.

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

These are the only permissions the recorder needs. The bot's later features (start/stop, console commands, backups) will need more, and this guide will list them when that code exists.

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
| `AMP_PASSWORD` | recorder | **Leave this empty.** The recorder asks for the password when it starts, and the typing doesn't show. |
| `DISCORD_TOKEN` | the bot (not built yet) | Leave empty for now. |
| `PORTCULLIS_OWNER_IDS` | the bot (not built yet) | Leave empty for now. |
| `PORTCULLIS_DB` | the bot (not built yet) | Optional; the default will be `data/portcullis.db`. |
| `PORTCULLIS_LOG_LEVEL` | the bot (not built yet) | Optional: `DEBUG`, `INFO`, `WARNING` or `ERROR`. |

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

## 8. Check a new AMP build's API (spec-diff)

When AMP ships a new build, copy its `data/versions/<version>/webapi/webapi.json` off the AMP host and run:

```powershell
.\.venv\Scripts\python.exe -m tools.spec_diff path\to\webapi.json
```

It lists breaking, changed and added API operations against the spec Portcullis is built on. Exit code 1 means something breaking. Add `--markdown` for a table you can paste into an issue. Updating the pinned spec is covered in [DEVELOPING.md](DEVELOPING.md#when-amp-ships-a-new-build).

## 9. Where things live

| Path | What it is |
|---|---|
| `.env` | Your settings. Git ignores it. |
| `captures\` | Recorder output. Git ignores it. |
| `portcullis\amp\` | The AMP 3 client: login and renewal, API calls, the live event hub. |
| `portcullis\amp\spec\` | The pinned AMP 3 API spec the client is generated from. |
| `portcullis\core\` | The bot's own types, independent of AMP. |
| `portcullis\db\` | The database code and its migrations. Not used by anything yet; the bot will keep its file at `PORTCULLIS_DB` (default `data\portcullis.db`) and save a `.bak` copy before each schema upgrade. |
| `tools\` | The recorder, spec-diff and the client generator. |
| `docs\` | The design ([DESIGN.md](DESIGN.md)), developer notes ([DEVELOPING.md](DEVELOPING.md)) and API notes. |

## 10. Known gaps

- **No Discord bot yet.** `DISCORD_TOKEN` and `PORTCULLIS_OWNER_IDS` are read and checked, but nothing uses them.
- **Untested against a real AMP 3.** Everything has been tested against a fake AMP built from the API docs. Not yet checked against a real panel: whether login and the live hub work as the docs say, which connection mode works, whether joining a shard's events succeeds, what the event payloads really look like, and whether the generated models accept real responses. The recorder exists to answer these questions.
- **Player events depend on the game.** Join and chat are verified for Unturned only (AMP's own `UserJoined` event, plus a chat pattern on the console); leave has not been captured yet. Arma Reforger gives no player names or chat through AMP. Other games need a capture with a player in game before their chat can be relayed.
- **One AMP preview build.** Portcullis is built against AMP 3.0.0.6, which expires on 7 Dec 2026. A newer build may change the API; run spec-diff on it first.
- **Licence.** The pinned spec and generated client contain CubeCoders' text. That's fine while the repo is private. See [DEVELOPING.md](DEVELOPING.md#licence-note) before it goes public.

## 11. Troubleshooting

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
| PowerShell: "running scripts is disabled" | Only happens if you use `Activate.ps1`. Use `.\.venv\Scripts\python.exe` as shown, or see step 3. |
| `py` not found | Python isn't installed, or wasn't added to PATH. Reinstall it with "Add python.exe to PATH" ticked. |
