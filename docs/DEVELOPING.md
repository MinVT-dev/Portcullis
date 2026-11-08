# Developing Portcullis

The code has three parts:

- **`packages/portcullis-api/`**: the AMP 3 client on its own (import name `portcullis_api`), with its own README, tests and `pyproject.toml`, so others can build on it without the bot. Installing Portcullis installs it from the same folder.
- **`portcullis/`**: the bot. `amp/` adapts the client to the bot's own types, `core/` holds AMP-agnostic logic, `db/` the database and `bot/` the Discord commands.
- **`tools/`**: the event recorder and the game-art builders.

## Layout

| Path | What it is |
|---|---|
| `packages/portcullis-api/src/portcullis_api/spec/` | The pinned AMP OpenAPI spec (`webapi-3.0.0.6.json`) and `PIN.json` with its sha256 and build. |
| `.../portcullis_api/models.py` | **Generated.** Pydantic v2 models for all 390 schemas. |
| `.../portcullis_api/api/` | **Generated** (except `_base.py`). One class per spec tag, one async method per operation (263). |
| `.../portcullis_api/auth.py` | Login, renewal 30 minutes before expiry, fallback to a fresh login, single-use hub tickets. |
| `.../portcullis_api/transport.py` | Bearer header, one retry after a 401, retries for GETs, a concurrency cap, typed parsing. |
| `.../portcullis_api/signalr.py` | A small SignalR (JSON protocol) client that fetches a new hub ticket for every connect and reconnect. |
| `.../portcullis_api/live.py` | `watch_shard` and the verified `JoinShard(tenantId, shardId)` join. |
| `.../portcullis_api/redact.py` | Removes tokens, tickets and passwords from logs and captures. |
| `.../portcullis_api/client.py` | `AmpClient`, which ties the above together. |
| `.../portcullis_api/testing.py` | A fake AMP (login, renew, hub tickets, a SignalR hub) built from the API docs and live captures, used by the tests. |
| `packages/portcullis-api/codegen/` | `generate_client.py` regenerates `models.py` and `api/` (`--check` fails when stale); `spec_diff.py` lists breaking changes between two specs. |
| `portcullis/amp/port.py` | `AmpServerPort`: the real `AmpPort`. Maps AMP models to core types; one shard-hub connection per followed server. |
| `portcullis/amp/events.py` | Turns hub events into core events. |
| `portcullis/core/types.py`, `port.py` | AMP-agnostic types (`ServerInfo`, `ServerStatus`, `PowerAction`, events) and the `AmpPort` interface the rest of the bot uses. |
| `portcullis/db/` | SQLite through aiosqlite. Numbered migrations in `db/migrations/` (each one atomic, with a `.bak` copy saved before an upgrade) and repositories. |
| `portcullis/config.py` | Reads `.env` and the environment, reports every problem at once without echoing secrets, and sets up redacted logging. |
| `tests/fakes/amp.py` | `FakeAmpPort`: an in-memory `AmpPort` for testing core and command code without AMP. |
| `tools/record_events.py` | Records live hub events from your own AMP 3 to `captures/`. |

Generated code is committed, so a regenerate after a new AMP build shows up as a reviewable diff. Only `portcullis/amp/` imports `portcullis_api`; the rest of the bot uses its own types (DESIGN.md section 6), and a test enforces it. The client package never imports the bot (also tested).

Run the client's own checks from its folder: `cd packages/portcullis-api && pytest -q && python -m codegen.generate_client --check`.

Method names come from the HTTP method and path, because the spec has no operation ids: `GET /api/Shards/{id}/console/history` is `amp.shards.get_shards_console_history(id, limit=50)`.

## Setup

For a step-by-step guide, including the AMP bot user and troubleshooting, see [SETUP.md](SETUP.md).

Python 3.11 or newer.

Windows (PowerShell), from the repo folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -q
```

Linux or macOS:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

## Recording live events

The SignalR event payloads (`UserJoined`, `UserLeft`, `ConsoleLine` and the rest) have never been captured from a real server. The recorder captures them so the typed event models and test fixtures can be built from real data.

1. In the AMP 3 panel, make a user for the bot, for example `portcullis`, with a role that has `view` and `console.read` on the shard you'll test (`analytics.users` too, if you want the roster). Don't turn on 2FA or a passkey for it.
2. Copy `.env.example` to `.env` and fill in `AMP_URL` and `AMP_USERNAME`. You can leave `AMP_PASSWORD` empty and type it when asked. `.env` is in `.gitignore`. Don't paste the password into chat.
3. Run:

   ```powershell
   python -m tools.record_events
   ```

   Use `--shard <id>` to record one shard only, and `--duration 900` to stop after 15 minutes. Otherwise stop with Ctrl+C.
4. While it runs, join the game, say something in chat, run a console command from the panel, and leave. Restarting the server, or briefly disconnecting the PC's network, also tests the reconnect.
5. The capture lands in `captures/<time>/`, which git ignores. Read `summary.txt` first.

What the recorder removes: the session token, every hub ticket, the password, SignalR connection ids, and anything shaped like a JWT. By default it also replaces the panel address and every IPv4 address, because join events may carry player IPs. Player names are kept. Look through `events.jsonl` before sharing it, and never commit a capture: we'll turn the parts we need into small hand-checked fixtures.

**Connection mode.** It isn't known yet whether AMP counts the SignalR negotiate request and the WebSocket upgrade as one use of a ticket or two. With `--mode auto` (the default), the recorder tries three ways in turn (`direct`, `negotiate`, `negotiate-reuse`) and keeps the first that connects. `lifecycle.jsonl` and `summary.txt` record which one worked; the bot will use that one.

## Database changes

Add a new file `portcullis/db/migrations/NNNN_what_it_does.sql` with the next number, and never edit a migration that has already merged. Each file runs in one transaction together with its `schema_version` row. If it fails, nothing is applied. Before upgrading an existing database, Portcullis saves `<db>.v<old version>.bak` next to it. A database from a newer Portcullis is refused rather than guessed at.

## When AMP ships a new build

1. Copy `data/versions/<version>/webapi/webapi.json` off the AMP host.
2. From `packages/portcullis-api/`: `python -m codegen.spec_diff path/to/webapi.json` lists what changed against the pinned spec. Exit code 1 means something breaking.
3. Save it as `src/portcullis_api/spec/webapi-<build>.json`, update `PIN.json` (file, build, sha256), delete the old file, and run `python -m codegen.generate_client`.
4. Run the tests and review the diff in `models.py` and `api/`.

## Licence note

The spec in `packages/portcullis-api/src/portcullis_api/spec/` and the generated `models.py` and `api/` contain CubeCoders' descriptions. They are not our code, under either licence. The bot is AGPL-3.0-or-later; the `portcullis-api` package (everything under `packages/portcullis-api/` except those files) is MIT, so others can build closed or open tools on it. The repo is private, so this is fine for now. Before the repo goes public, either get CubeCoders' permission or stop committing the spec and generated files and generate them at install time instead.
