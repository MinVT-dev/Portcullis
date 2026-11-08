# portcullis-api

An async, typed Python client for the **CubeCoders AMP 3** web API. It is the AMP layer of the [Portcullis](../../README.md) Discord bot, kept in its own folder so you can build your own bot, dashboard or script on it without any Discord code.

- **Every REST operation** in AMP 3.0.0.6's OpenAPI spec (263 of them) as an `async` method with typed arguments, grouped like the spec (`amp.shards`, `amp.scheduling`, `amp.files_backups`, ...).
- **Pydantic v2 models** for all 390 schemas, generated from a pinned copy of the spec.
- **Login that looks after itself.** It renews the session 30 minutes before the roughly 8-hour expiry and logs in again after a 401.
- **Live events over SignalR**, with a fresh single-use hub ticket on every connect and an automatic re-join after a reconnect.
- **Secrets stay out of logs.** Passwords, tokens and hub tickets are redacted.
- **A fake panel for your tests** (`portcullis_api.testing`), so you can test without a real AMP.

Not affiliated with or endorsed by CubeCoders. "AMP" is their product name.

## Install

From a checkout of this repo:

```bash
pip install ./packages/portcullis-api
```

It needs Python 3.11 or newer, `aiohttp` and `pydantic`.

## Quick start

```python
import asyncio
from portcullis_api import AmpClient, Credentials

async def main() -> None:
    async with AmpClient("https://panel.example:8443", Credentials("bot", "password")) as amp:
        for shard in await amp.shards.get_shards():
            state = await amp.shards.get_shards_state(shard.id)
            print(shard.display_name, "running" if state.is_running else "stopped")

asyncio.run(main())
```

Give your program its own AMP account with only the permissions it needs. Read the password from an environment variable or a secrets store, never from source code.

### Start, stop and console

```python
from portcullis_api import models

await amp.shards.post_shards_start(shard_id)
await amp.shards.post_shards_console(shard_id, models.ConsoleInputBody(command="say Hello"))
history = await amp.shards.get_shards_console_history(shard_id, limit=50)
```

### Live events

```python
from portcullis_api import watch_shard

async for event in watch_shard(amp, shard_id):
    print(event.target, event.arguments)
```

`watch_shard` opens one hub connection for one shard, joins it with `JoinShard(tenantId, shardId)` and leaves when you stop iterating. Run one per shard: AMP's `ConsoleLine` carries no shard id, so a connection that joined several shards can't tell their console lines apart.

For more control, use `amp.hub("shard", on_connected=...)` and `HubConnection.run(handler)` directly.

More in [`examples/`](examples/): list servers, watch a console, send a command.

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

**Not seen working yet:** `UserLeft`, the scheduler's step and trigger formats, making a backup, the update action, analytics and webhooks. The methods exist because the spec has them, but their payloads may not match what a real panel does. AMP also runs without SignalR's detailed errors, so every failed hub call returns the same generic message.

## Errors

Every error is an `AmpError`. The useful subclasses are `AuthFailed` (wrong login), `MissingCapability` (403: the account lacks a permission, and the message names it), `NotFound`, `BadRequest` (with AMP's validation errors), `Conflict`, `RateLimited` and `Unreachable`. Hub errors are `HubError`; `HubGaveUp` means the same join failed several times in a row, so reconnecting won't fix it.

## Testing your own code

```python
from aiohttp.test_utils import TestServer
from portcullis_api import AmpClient, Credentials
from portcullis_api.testing import FakeAmp, USERNAME, PASSWORD, SHARD_ID

fake = FakeAmp()
server = TestServer(fake.app())
await server.start_server()
async with AmpClient(str(server.make_url("")), Credentials(USERNAME, PASSWORD)) as amp:
    await amp.shards.post_shards_start(SHARD_ID)
    await fake.broadcast("Metrics", SHARD_ID, {"cpuUsagePercent": 5})  # push a hub event
```

The fake follows the documented and observed behaviour above. It is a model, not a recording.

## A new AMP build

The client is generated from the spec in `src/portcullis_api/spec/`; `PIN.json` holds its name and sha256. To move to a new build:

1. Download `webapi.json` from the new panel and compare: `python -m codegen.spec_diff path/to/webapi.json`. It lists removed and changed operations, which would break callers.
2. Save it as `src/portcullis_api/spec/webapi-<build>.json`, update `PIN.json`, and delete the old file.
3. Regenerate: `python -m codegen.generate_client`. CI runs it with `--check` and fails if the committed code is out of date.

Run these from this folder after `pip install -e ".[dev]"`.

## Development

```bash
cd packages/portcullis-api
pip install -e ".[dev]"
pytest -q
ruff check .
```

## Licence

MIT, so you can use it in your own projects, closed or open. The Portcullis bot itself is AGPL-3.0-or-later. The pinned spec and the generated `models.py` and `api/` contain CubeCoders' API descriptions, which aren't ours to license; see the main repo's `docs/DEVELOPING.md`.
