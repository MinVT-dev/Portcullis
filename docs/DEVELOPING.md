# Developing Portcullis

The first code is the AMP 3 client layer (`portcullis/amp/`) and three tools. There is no Discord code yet.

## Layout

| Path | What it is |
|---|---|
| `portcullis/amp/spec/` | The pinned AMP OpenAPI spec (`webapi-3.0.0.6.json`) and `PIN.json` with its sha256 and build. |
| `portcullis/amp/models.py` | **Generated.** Pydantic v2 models for all 389 schemas. |
| `portcullis/amp/api/` | **Generated** (except `_base.py`). One class per spec tag, one async method per operation (263). |
| `portcullis/amp/auth.py` | Login, renewal 30 minutes before expiry, fallback to a fresh login, single-use hub tickets. |
| `portcullis/amp/transport.py` | Bearer header, one retry after a 401, retries for GETs, a concurrency cap, typed parsing. |
| `portcullis/amp/signalr.py` | A small SignalR (JSON protocol) client that fetches a new hub ticket for every connect and reconnect. |
| `portcullis/amp/redact.py` | Removes tokens, tickets and passwords from logs and captures. |
| `portcullis/amp/client.py` | `AmpClient`, which ties the above together. |
| `tools/generate_client.py` | Regenerates `models.py` and `api/` from the pinned spec. `--check` fails when they are stale. |
| `tools/spec_diff.py` | Compares two specs and lists breaking changes. Exits 1 if there are any. |
| `tools/record_events.py` | Records live hub events from your own AMP 3 to `captures/`. |
| `tests/fakes/amp_server.py` | A fake AMP (login, renew, hub tickets, a SignalR hub) built from the API docs, used by the tests. |

Generated code is committed, so a regenerate after a new AMP build shows up as a reviewable diff. Nothing outside `portcullis/amp/` should import `models` or `api`; the rest of the bot will use its own types (DESIGN.md section 6).

Method names come from the HTTP method and path, because the spec has no operation ids: `GET /api/Shards/{id}/console/history` is `amp.shards.get_shards_console_history(id, limit=50)`.

## Setup

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

## When AMP ships a new build

1. Copy `data/versions/<version>/webapi/webapi.json` off the AMP host.
2. `python -m tools.spec_diff path/to/webapi.json` lists what changed against the pinned spec. Exit code 1 means something breaking.
3. Save it as `portcullis/amp/spec/webapi-<build>.json`, update `PIN.json` (file, build, sha256), delete the old file, and run `python -m tools.generate_client`.
4. Run the tests and review the diff in `models.py` and `api/`.

## Licence note

The spec in `portcullis/amp/spec/` and the generated `models.py` and `api/` contain CubeCoders' descriptions. They are not AGPL code of ours. The repo is private, so this is fine for now. Before the repo goes public, either get CubeCoders' permission or stop committing the spec and generated files and generate them at install time instead.
