# portcullis-api

An async, typed Python client for the **CubeCoders AMP 3** web API.

It is the AMP layer of the [Portcullis](../../README.md) Discord bot, kept in its own package so you can build your own bot, dashboard or script on it without any Discord code. It is **MIT licensed**.

```python
from portcullis_api import AmpClient, Credentials

async with AmpClient("https://panel.example:8443", Credentials("bot", "password")) as amp:
    for shard in await amp.shards.get_shards():
        state = await amp.shards.get_shards_state(shard.id)
        print(shard.display_name, "running" if state.is_running else "stopped")
```

Not affiliated with or endorsed by CubeCoders. "AMP" is their product name.

## Contents

- [Features](#features)
- [Install](#install)
- [Quick start](#quick-start)
- [How AMP 3 is organised](#how-amp-3-is-organised)
- [The API groups](#the-api-groups)
- [Login and sessions](#login-and-sessions)
- [Live events](#live-events)
- [Errors and retries](#errors-and-retries)
- [Keeping secrets out of logs](#keeping-secrets-out-of-logs)
- [Testing your own code](#testing-your-own-code)
- [What has been checked on a real panel](#what-has-been-checked-on-a-real-panel)
- [A new AMP build](#a-new-amp-build)
- [Development](#development)
- [Licence](#licence)

## Features

- **The whole API.** Every REST operation in the OpenAPI spec of AMP 3.0.0.6 (263 of them) is an `async` method with typed arguments, grouped the way the spec groups them (`amp.shards`, `amp.scheduling`, `amp.files_backups`, ...).
- **Typed models.** Pydantic v2 models cover all 390 schemas, generated from a pinned copy of the spec. Responses are parsed into them, so your editor can autocomplete fields. The package ships `py.typed`.
- **Login that looks after itself.** It logs in on start, renews the session 30 minutes before the roughly 8-hour expiry, and logs in again after a 401.
- **Live events over SignalR**, through a small built-in client that fetches a fresh single-use hub ticket on every connect, rejoins after a reconnect and backs off when the panel is down.
- **`watch_shard()`.** One `async for` loop gives you one server's console, players, metrics and state changes.
- **Safe defaults:**
  - at most 8 calls run at once;
  - read-only calls are retried when the panel doesn't answer;
  - writes are never retried on their own;
  - every call has a timeout.
- **Secrets stay out of logs.** Passwords, tokens and hub tickets are redacted wherever they would appear.
- **A fake panel for your tests** (`portcullis_api.testing`). It follows the documented and observed behaviour, so you can test without a real AMP.

## Install

It needs **Python 3.11 or newer**. Its only dependencies are `aiohttp` and `pydantic`.

From a checkout of the Portcullis repo:

```bash
pip install ./packages/portcullis-api
```

Or straight from GitHub (this needs read access to the repository):

```bash
pip install "portcullis-api @ git+https://github.com/MinVT-dev/Portcullis.git#subdirectory=packages/portcullis-api"
```

The import name is `portcullis_api`. It isn't on PyPI yet, because the name `portcullis` is taken there.

## Quick start

### Credentials

Give your program **its own AMP user** with only the permissions it needs, and without two-factor login or a forced password change, because a program can't complete either. Read the password from an environment variable or a secrets store, never from source code:

```python
import os
from portcullis_api import AmpClient, Credentials

creds = Credentials(os.environ["AMP_USERNAME"], os.environ["AMP_PASSWORD"])
amp = AmpClient(os.environ["AMP_URL"], creds)  # e.g. http://192.168.1.10:8443, no /api path
```

Use it as `async with AmpClient(...) as amp:`, or call `await amp.start()` and `await amp.close()` yourself. `start()` logs in straight away, so wrong credentials fail fast with `AuthFailed`.

### Start, stop and console

```python
from portcullis_api import models

await amp.shards.post_shards_start(shard_id)
await amp.shards.post_shards_console(shard_id, models.ConsoleInputBody(command="say Hello"))
history = await amp.shards.get_shards_console_history(shard_id, limit=50)
for line in history.lines:
    print(line.text)
```

### Players online

```python
users = await amp.shards.get_shards_users(shard_id)  # needs the analytics.users permission
```

### More examples

The [`examples/`](examples/) folder has scripts you can run as they are. Each reads `AMP_URL`, `AMP_USERNAME` and `AMP_PASSWORD` from the environment.

| Script | What it does |
|---|---|
| [`list_servers.py`](examples/list_servers.py) | Lists every server with its state. |
| [`watch_console.py`](examples/watch_console.py) | Prints one server's console and player joins as they happen. |
| [`send_command.py`](examples/send_command.py) | Sends a console command, then prints the last console lines. |

## How AMP 3 is organised

- **Shards** are what AMP 2 called instances: one game server each. Their ids are UUIDs.
- **Tenants** group shards. A normal single-install panel has one tenant, `_root` (`portcullis_api.DEFAULT_TENANT`).
- **Spires** are the machines that run shards.
- **Permissions** are nodes such as `view`, `console.read` and `analytics.users`, granted through roles per shard. A call the account may not make fails with `MissingCapability`, and the message names the node.
- **Live data** comes over SignalR hubs (`/hubs/shard`), not by polling.

The full reference, with live results from a real panel, is in [`docs/amp3-api/`](../../docs/amp3-api/README.md).

## The API groups

Each group is an attribute of `AmpClient`. Method names follow the HTTP method and the path, for example `GET /api/Shards/{id}/state` becomes `amp.shards.get_shards_state(id)`.

| Attribute | Operations | Covers |
|---|---|---|
| `shards` | 51 | Servers: list, state, start, stop, restart, sleep, wake, update, console, players, settings |
| `files_backups` | 30 | Files and backups (snapshots) |
| `authentication_setup` | 23 | Login methods, two-factor login, passkeys |
| `networking` | 22 | HTTPS, gateways, published ports and the firewall |
| `spires_datastores` | 19 | Spires and their datastores |
| `users_access` | 18 | Users, roles and permissions |
| `my_account` | 14 | The signed-in user's own account |
| `analytics` | 11 | Player sessions, addresses and profiles |
| `scheduling` | 10 | Schedules: list, create, edit, run, turn on or off, history, delete |
| `system_health` | 10 | Health, status and version |
| `profile` | 7 | Passkeys and recovery codes |
| `licensing` | 6 | The panel licence and its terms |
| `platform_settings` | 6 | Panel-wide settings |
| `spires` | 6 | Enrolling new machines (spires): requests, approve, deny |
| `templates` | 6 | Game templates |
| `audit_log` | 5 | The panel's own audit log |
| `credentials` | 5 | Stored credentials and secrets |
| `tasks` | 5 | Running tasks and cancelling them |
| `import_` | 4 | Importing existing servers into a spire |
| `monitoring` | 3 | Platform warnings and thresholds |
| `branding_appearance` | 1 | Panel branding |
| `security` | 1 | Content-security-policy reports |

Not every operation has been run on a real panel; see [what has been checked](#what-has-been-checked-on-a-real-panel).

## Login and sessions

- `POST /api/Auth/login` returns a token that lasts about 8 hours.
- AMP 3 has no API keys or service accounts, so your program is an ordinary user.

`AmpClient` handles the session for you:

| Option | Default | What it does |
|---|---|---|
| `refresh_margin` | 30 minutes | Renew the session this long before it expires. |
| `keep_session_renewed` | `True` | Run a background task that renews on time, even when no calls are made. |
| `timeout` | 30 s | The time limit for each request. |
| `session` | a new one | Pass your own `aiohttp.ClientSession` to share connections. The client won't close a session it didn't create. |
| `redactor` | a new one | A shared `Redactor`, if you log secrets through other code too. |

After a 401, the client renews the session or logs in again, then retries the call once. If that fails too, you get `AuthExpired`.

## Live events

```python
from portcullis_api import watch_shard

async for event in watch_shard(amp, shard_id):
    print(event.target, event.arguments)
```

`watch_shard` does four things:
- opens one hub connection for one shard;
- joins the shard with `JoinShard(tenantId, shardId)`;
- yields every event for that shard;
- leaves with `LeaveShard` when you stop iterating.

**Run one `watch_shard` per shard.** AMP's `ConsoleLine` carries no shard id, so a connection that joined several shards can't tell their console lines apart.

What a real panel sends:

| Event | Arguments | How often |
|---|---|---|
| `ConsoleLine` | `[line]` with `text`, `seq` and `streamId` (`main`, `agent` or `system`) | as lines are printed |
| `Metrics` | `[shardId, sample]` | about every 3 s |
| `UserRoster` | `[shardId, users]` | about every 10 s |
| `ShardStateChanged` | `[tenantId, shardId, state]` | about every 9 s, for every shard of the tenant (`watch_shard` filters out other shards) |
| `UserJoined` | `[shardId, {name, id, joinedAt, idKind}]` | when a player joins (seen on Unturned) |

**For more control,** use `amp.hub("shard", on_connected=...)` and `HubConnection.run(handler)` directly:
- `on_connected` runs after every connect and reconnect, which is where you join groups;
- `invoke()` calls a hub method and waits for its result;
- reconnects back off through 0, 2, 5, 10, 30 and 60 seconds;
- `HubGaveUp` is raised when the same join fails `join_attempts` times in a row, because reconnecting won't fix it.

AMP's hub tickets are single-use and last 60 to 90 seconds, so the client fetches a new one for every connect. The `mode` option chooses how a ticket is presented:

| Mode | How it connects | Status |
|---|---|---|
| `direct` | Opens the socket with `?access_token=<ticket>`. | Default. Works on a real panel. |
| `negotiate` | Negotiates first, then opens the socket with a second ticket. | Kept for future builds. |
| `negotiate-reuse` | Negotiates first, then reuses the same ticket, as browsers do. | Kept for future builds. |

## Errors and retries

Every error is an `AmpError`. Its `status`, `code`, `method`, `path` and `body` tell you what failed:

| Error | When |
|---|---|
| `AuthFailed` | Wrong username or password. |
| `LoginNotSupported` | The account needs two-factor login, a passkey or a password change, which a program can't do. |
| `AuthExpired` | The session ended, and logging in again didn't help. |
| `MissingCapability` | 403: the account lacks a permission. The message names it. |
| `NotFound` | 404: no such shard, schedule, backup and so on. |
| `BadRequest` | 400, with AMP's validation errors in the message. |
| `Conflict` | 409: for example, starting a server that is already starting. |
| `RateLimited` | 429. `retry_after` holds the seconds from AMP's `Retry-After` header, or `None` if it sent none. |
| `Unreachable` | The panel didn't answer in time, or refused the connection. |
| `HubError`, `HubGaveUp` | Hub problems. `HubGaveUp` means reconnecting won't help. |

```python
from portcullis_api import MissingCapability, NotFound

try:
    await amp.shards.post_shards_restart(shard_id)
except MissingCapability as exc:
    print("The bot's AMP role needs:", exc)
except NotFound:
    print("No such server")
```

**Retries:**
- Read-only calls (`GET`, `HEAD`, `OPTIONS`) are retried twice when the panel doesn't answer, or when it answers with 502, 503 or 504.
- A 429 on a read-only call is retried after the wait AMP names in `Retry-After`, if that wait is 10 seconds or less (`max_rate_limit_wait` on `AmpTransport`). A longer wait, or a 429 without the header, raises `RateLimited`.
- A 429 on login holds every caller off for the wait AMP names (at least `login_cooldown`, at most an hour) instead of asking again, because repeated login attempts extend the limit.
- Writes are never retried on their own, so a start or a console command can't run twice.
- At most 8 requests are in flight at once.

**Hub errors:** AMP runs SignalR without its detailed errors, so every failed hub call returns the same generic message.

## Keeping secrets out of logs

`Redactor` replaces secrets with `<redacted>` in everything the client logs. It works two ways:
- **Known values:** the password, the session token and every hub ticket are registered the moment the client sees them, and masked wherever they appear.
- **Patterns:** anything shaped like a JWT, a `Bearer` header, an `access_token=` value, or a JSON key whose name says it is a secret is masked too.

You can use it in your own logging:

```python
amp.redactor.text("Authorization: Bearer eyJ...")  # -> "Authorization: Bearer <redacted>"
```

## Testing your own code

`portcullis_api.testing.FakeAmp` is an in-process AMP 3 panel built with aiohttp. It covers:
- login, renew and logout;
- hub tickets;
- listing shards, state, players, console and console history;
- power actions;
- the backup list;
- the shard hub, with `JoinShard` and `LeaveShard`.

```python
from aiohttp.test_utils import TestServer
from portcullis_api import AmpClient, Credentials
from portcullis_api.testing import FakeAmp, PASSWORD, SHARD_ID, USERNAME

fake = FakeAmp()
server = TestServer(fake.app())
await server.start_server()
async with AmpClient(str(server.make_url("")), Credentials(USERNAME, PASSWORD)) as amp:
    await amp.shards.post_shards_start(SHARD_ID)
    await fake.broadcast("Metrics", SHARD_ID, {"cpuUsagePercent": 5})  # push a hub event
```

Switches on `FakeAmp` let you test failure paths:

| Switch | What it does |
|---|---|
| `fake.clock.advance(hours=8)` | Moves time on, to test session expiry. |
| `fake.expire_all()`, `fake.revoke_all()` | End every session. |
| `fake.roster_forbidden = True` | The player list returns 403. |
| `fake.join_error = "..."` | `JoinShard` fails with this error. |
| `fake.login_response = {...}` | Simulates, for example, a forced password change. |
| `fake.preview = {"expiresAt": "2026-12-07T00:00:00Z", "daysRemaining": None}` | The preview-build block on `GET /api/Health/status`. |
| `fake.rate_limits = ["2", None]` | The next login or shard-list calls answer 429, with that `Retry-After` (`None` = no header). |

`fake.calls`, `fake.joined` and `fake.left` record what your code did.

**The fake is a model, not a recording.** It follows the documented and observed behaviour described below.

## What has been checked on a real panel

Tested against AMP 3.0.0.6 (a preview build) on 7 November 2026:

| Works | Shape seen |
|---|---|
| Login, renew, `GET /api/Shards` | as documented |
| Hub connect with `?access_token=<ticket>` (direct mode) | one ticket per connect |
| `JoinShard(tenantId, shardId)`, `LeaveShard(tenantId, shardId)` | tenant `_root`; join returns `{feeds: [...]}` |
| `Metrics` | `[shardId, sample]` about every 3 s |
| `UserRoster` | `[shardId, users]` about every 10 s |
| `ShardStateChanged` | `[tenantId, shardId, state]` about every 9 s, for every shard of the tenant |
| `ConsoleLine` | `[line]`, no shard id, `seq` shared by all shards, `streamId` main, agent or system |
| `UserJoined` (Unturned) | `[shardId, {name, id, joinedAt, idKind}]` |
| Start, stop, restart, console input and history, backup list | as documented |

**Not seen working yet:**
- `UserLeft`;
- the scheduler's step and trigger formats, schedule run history and deleting schedules;
- making, restoring and deleting backups;
- the update action;
- analytics and webhooks.

The methods exist because the spec has them, but their payloads may not match what a real panel does. If you try one, please report what you see.

## A new AMP build

The client is generated from the spec in `src/portcullis_api/spec/`. `PIN.json` holds its name and sha256, and `portcullis_api.SPEC_BUILD` names the build (`3.0.0.6`).

To move to a new build:

1. **Download and compare.** Download `webapi.json` from the new panel (it ships on disk at `data/versions/<version>/webapi/webapi.json`), then run `python -m codegen.spec_diff path/to/webapi.json`. It lists removed and changed operations, which would break callers.
2. **Save it.** Save the file as `src/portcullis_api/spec/webapi-<build>.json`, update `PIN.json`, and delete the old file.
3. **Regenerate.** Run `python -m codegen.generate_client`. CI runs it with `--check` and fails if the committed code is out of date.

Run these from this folder after `pip install -e ".[dev]"`.

## Development

```bash
cd packages/portcullis-api
pip install -e ".[dev]"
pytest -q
ruff check .
python -m codegen.generate_client --check
```

| Path | What it is |
|---|---|
| `src/portcullis_api/client.py` | `AmpClient`: wires the session, the transport and the API groups together |
| `src/portcullis_api/auth.py` | Login, renewal, logout, hub tickets |
| `src/portcullis_api/transport.py` | HTTP calls: auth header, retries, concurrency cap, parsing |
| `src/portcullis_api/signalr.py` | The SignalR client |
| `src/portcullis_api/live.py` | `watch_shard` and the shard join and leave helpers |
| `src/portcullis_api/errors.py` | The error classes |
| `src/portcullis_api/redact.py` | `Redactor` |
| `src/portcullis_api/testing.py` | `FakeAmp` |
| `src/portcullis_api/models.py`, `api/` | Generated from the spec; don't edit by hand |
| `codegen/` | The generator and `spec_diff` |

CI builds the wheel, installs it on its own with no Portcullis code around it, and runs these tests on Python 3.11 and 3.13.

## Licence

**MIT**; see [LICENSE](LICENSE). The Portcullis bot that uses this package is AGPL-3.0-or-later. The two licences are compatible: an AGPL program can use an MIT library.

**The pinned spec and the generated `models.py` and `api/` are not covered by MIT.** They contain CubeCoders' API descriptions, which aren't ours to license. Read the main repo's [licence note](../../docs/DEVELOPING.md#licence-note) before you redistribute them.
