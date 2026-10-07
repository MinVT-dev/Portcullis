# Gatekeeper for AMP 3: plan and design

Status: **draft**, written before the AMP 3 API is available.
Everything that depends on the API is marked as an assumption (A-n) or an open question (Q-n) and must be checked against the real API browser once the beta is running.

---

## 1. Goal

A Discord bot that lets a community run and watch its CubeCoders AMP 3 game servers from Discord, the same job Gatekeeper V2 (k8thekat/GatekeeperV2) and Gatekeeper V3 (MinVT-dev/GatekeeperV3-Beta) do for AMP 2.

"Same area as V2/V3" is taken to mean:

- same product: slash commands for server control, console/chat/event relay to Discord channels, whitelist requests, banners, permissions, audit, schedules, alerts, polls, player tracking, account linking;
- same stack: Python 3.11+, discord.py 2.x, SQLite, a `bot_perms.json` style permission file, Docker plus "run as an AMP instance";
- same habits from V3 4.8.0: credentials only from env or `.env`, secrets redacted in logs, console input validated and rate limited, confirmation on destructive actions, `pytest` + `pip-audit` in CI.

What changes is the inside, because AMP 3's API is a different shape (section 3) and because V3's AMP layer cannot be reused as is (section 2).

## 2. What we learned from V2 and V3

V3 is V2 with a large reliability and security pass on top. Both share one structure:

| Piece | V2/V3 file | Keep / change |
|---|---|---|
| AMP client | `AMP.py` (`AMPInstance`, ~1,060 lines) | **Replace.** See below. |
| Instance discovery | `AMP_Handler.py` | Replace with a shard registry. |
| Console polling | `AMP_Console.py` (thread per instance, `Core/GetUpdates` every second) | Replace with whatever AMP 3 offers for streaming (Q-4). |
| Per-game behaviour | `modules/<Game>/amp_*.py` + `cog_*.py`, subclassing `AMPInstance` | Keep the idea, change the shape (game plugins no longer subclass the API client). |
| Discord features | `cogs/*.py` (~25 cogs, ~210 commands) | Port feature by feature, mostly keeping command names. |
| Storage | `DB.py` + `DB_Update.py` (SQLite, hand-rolled version upgrades) | Keep SQLite, use numbered migrations. |
| Permissions | `bot_perms.json` nodes like `server.console.interact`, `-node` denies | Keep the file format so V3 users can copy theirs. |
| Event bus, rate limiter, validation | `utils_events.py`, `utils_ratelimit.py`, `utils_validation.py` | Keep, already tested. |

Why `AMP.py` cannot be carried over:

1. **It is RPC over POST.** Every call is `POST /API/<Module>/<Method>` with a JSON body. AMP 3 is REST (`GET /api/shards/{shardId}/schedules`, `PUT`, `DELETE`, ...).
2. **It mixes four jobs in one class.** HTTP transport, login, AMP role/permission setup, DB access (`_setDBattr`), console threads and game behaviour all live on `AMPInstance`, and API response fields are copied onto the object with `setattr`. Nothing can be tested without a live AMP and a database (V3's CI only byte-compiles the cogs for this reason).
3. **It is synchronous.** `requests` plus `time.sleep` inside a discord.py bot. V3 4.8.0 spent a lot of effort moving blocking calls to worker threads; a fresh core should be async from the start (`aiohttp` is already a dependency).
4. **It guesses response shapes.** `CallAPI` has branches for `result` being an int, a bool, a dict, or a `Title: Unauthorized Access` body. An OpenAPI spec lets us type every response instead.

So the plan is: **new core, ported features.** Not a line-by-line port, and not a from-scratch rewrite of the features either.

## 3. What we know about AMP 3 so far

From Mike's messages and the API browser screenshot (shared 2026-10-07):

- The beta will include an API browser. It is **Scalar over an OpenAPI document**, served by AMP itself (screenshot: `localhost:7278/scalar/v1`).
- Paths are REST under `/api/`, e.g. `GET /api/shards/{shardId}/schedules/catalog`. `shardId` is a UUID.
- Sidebar sections: initial setup (internet reachability probe, create first admin, **passkey** sign-in begin/finish), System & Health, My Account, Users & Access, Audit Log, **Shards**, Files & Backups, **Scheduling**, **Spires & Datastores**, Networking, and a "Generate MCP" link.
- Scheduling is a full API: catalog of triggers and tasks (with emitted signals), list/create/get/replace/delete schedules, enable/disable, trigger an immediate run, run history, preview next fire times.
- Access is **capability based per shard**: "Requires the shard's `ScheduleRead` capability", and `403` means the caller lacks that capability on the shard. `404` means no such shard.

That is enough to fix the architecture; it is not enough to write the client.

## 4. Architecture

```
gatekeeper/
  amp/            # The ONLY code that knows AMP URLs, auth, or wire formats
    transport.py  #   aiohttp session, auth, retries, timeouts, rate limit, redacted logging
    api/          #   one module per API tag: shards, scheduling, files, users, system, ...
    models.py     #   typed request/response models generated from the OpenAPI document
    errors.py     #   AmpError, NotFound, MissingCapability(cap, shard), AuthExpired, Unreachable
    streams.py    #   console/event streaming (shape depends on Q-4)
  core/           # Domain logic, AMP-agnostic types, fully unit-testable
    servers.py    #   server registry (shard id <-> friendly name <-> DB row), status cache
    relay.py      #   console/chat/event fan-out to the event bus
    whitelist.py, linking.py, schedules.py, alerts.py, players.py, audit.py
    permissions.py#   bot_perms.json evaluation (ported from V3, already tested)
  games/          # Per-game plugins: chat parsing, whitelist commands, player list, avatar
    base.py, minecraft.py, valheim.py, ...
  discord/        # discord.py cogs, views, embeds, autocomplete. No AMP calls directly.
    cogs/...
  db/             # aiosqlite, numbered migrations, repositories
  config.py       # env / .env loading, validation at startup
  main.py
tests/
  fakes/amp.py    # in-memory fake of the AMP port, used by core and cog tests
```

Rules that keep the AMP 3 unknowns contained:

1. **Cogs never import `gatekeeper.amp`.** They call `core` services. When the API changes during the beta, only `amp/` changes.
2. **`core` depends on a small interface (`AmpPort`), not on the HTTP client.** Something like:
   ```python
   class AmpPort(Protocol):
       async def list_servers(self) -> list[ServerInfo]: ...
       async def get_status(self, server: ServerId) -> ServerStatus: ...
       async def power(self, server: ServerId, action: PowerAction) -> None: ...
       async def send_console(self, server: ServerId, line: str) -> None: ...
       def console_stream(self, server: ServerId) -> AsyncIterator[ConsoleLine]: ...
       async def list_backups(self, server: ServerId) -> list[Backup]: ...
       # schedules, users/players, files ... added as features are ported
   ```
   The real implementation lives in `amp/`, the fake in `tests/fakes/`. This is what lets us build and test most of the bot **before** the API docs arrive.
3. **Generated models, hand-written facade.** Generate types from AMP's OpenAPI JSON (e.g. `datamodel-code-generator` to pydantic v2), pinned to a spec snapshot checked into `amp/spec/`. Generated classes never leave `amp/`; they are mapped to `core` types. Re-generating after a beta update then shows up as a reviewable diff, and a CI job can fail when the live spec drifts from the snapshot.
4. **Capabilities become first-class errors.** A `403` turns into `MissingCapability("ScheduleRead", shard)`, which the Discord layer renders as "Gatekeeper's AMP account is missing ScheduleRead on Survival", not a stack trace.

### Two permission layers

| Layer | Who it protects | Mechanism |
|---|---|---|
| Discord side | Which Discord members may run which bot command | `bot_perms.json` nodes, same format as V3 (`server.start`, `-server.kill`, wildcards). |
| AMP side | What the bot's AMP account may do on each shard | AMP 3 capabilities on the bot's account. |

V2/V3 asked for a **Super Admin** AMP account and then built a "Gatekeeper" role with permission nodes (`amp_permissions.py`). For AMP 3 the default should be the opposite: the bot gets an account with only the capabilities its enabled features need, and `/bot diagnostics` lists which capabilities are missing per shard. Whether the bot can still set up its own role depends on Q-2 and Q-3.

### Scheduling: use AMP's, don't duplicate it

V3 runs its own scheduler (`schedule_cog.py`, a 30 s loop over a `Schedules` table). AMP 3 has a complete scheduling API with triggers, tasks, run history and next-fire preview. The AMP 3 edition should **front AMP's scheduler** (`/schedule create|list|run|enable|disable|history`) and drop its own, except for Discord-only actions AMP cannot do (e.g. "post a message in a channel"). If AMP 3 has a webhook or HTTP task in the catalog, even those could live in AMP (Q-7).

### Console, chat and events

V3 polls `Core/GetUpdates` once per second per instance, in a thread each. For AMP 3:

- prefer a push stream (WebSocket / SSE) if the API has one (Q-4);
- otherwise one async poller per online shard with backoff, behind `streams.py`;
- either way, `core/relay.py` publishes `ConsoleLine` / `ChatMessage` / `PlayerJoined` / `PlayerLeft` on the event bus, and cogs subscribe. Regex filters, the console denylist, rate limiting and `@everyone` stripping from V3 4.8.0 carry over unchanged.

### Games

V2/V3 pick a module from the instance's module name and subclass the client (`AMPMinecraft(AMPInstance)`). In the new layout a game plugin is a plain class that receives the `AmpPort` and implements optional hooks: `parse_chat`, `whitelist_add/remove`, `list_players`, `avatar_url`, `format_outgoing_chat` (keeping V3's `json.dumps` tellraw fix for Minecraft). Generic is the fallback. How the plugin is picked depends on what the shard object exposes (Q-6).

### Storage

SQLite via `aiosqlite`, numbered SQL migrations (`db/migrations/0001_init.sql`, ...), a `schema_version` row. Server rows are keyed by **shard UUID** instead of the AMP 2 instance id. A one-off `import-v3` command reads a V3 `discordBot.db` and copies users, links, regex patterns, banner settings, whitelist replies and config, asking the admin to map each old instance to a shard.

## 5. Delivery plan

The beta date is "within about 10 weeks" and not fixed, so phases are gated on the API, not on dates.

**Phase 0: before the API (now)**
- Repo scaffold: `pyproject.toml` (ruff, pyright, pytest config from V3), CI (tests, `pip-audit`, credential check from V3), Dockerfile.
- `config.py`, logging with redaction, `db/` with migrations and the first schema.
- `AmpPort` interface plus the in-memory fake.
- Port the AMP-independent pieces from V3 with their tests: permissions, event bus, rate limiter, validation, embeds, pagination.
- Discord shell: bot start-up, command sync, `/help`, `/about`, `/bot diagnostics`, all running against the fake.

**Phase 1: API spike (first days with the beta)**
- Export the OpenAPI JSON into `amp/spec/`, generate models, answer the open questions below and update this doc.
- Prove the four risky paths end to end against a real AMP 3: authenticate as a bot, list shards, start/stop one, read its console.

**Phase 2: MVP**
- `/server list|status|start|stop|restart|kill|backup`, console/chat/event channel relay, Discord permissions, audit log, capability diagnostics.
- Generic game plugin and Minecraft.

**Phase 3: feature parity with V3**
- Whitelist + `/link`, banners and banner groups, player tracker, alerts, polls, schedules on AMP's scheduler, backups, regex filters, presence, config export/import.
- Remaining game plugins (Valheim, Terraria, Factorio, 7 Days to Die, Project Zomboid, Starbound, CS).

**Phase 4: release**
- `import-v3` migration, docs (README, COMMANDS, PERMISSIONS adapted from V3), AMP 3 template for running Gatekeeper as an AMP instance if AMP 3 still supports that (Q-9), tagged beta release.

## 6. Assumptions (to confirm against the API)

- **A-1** A "shard" is the AMP 3 equivalent of an AMP 2 instance: one game server.
- **A-2** "Spires" are hosts/controllers (AMP 2's ADS targets) and "Datastores" are storage locations. Gatekeeper only needs to read them.
- **A-3** The OpenAPI JSON is reachable next to the Scalar page (ASP.NET's default would be `/openapi/v1.json`).
- **A-4** Everything Gatekeeper needs per server lives under `/api/shards/{shardId}/...`, including power actions, console and players.
- **A-5** JSON bodies, UUID ids, standard HTTP status codes (`401`, `403` with the capability name, `404`), as in the screenshot.
- **A-6** One AMP 3 panel per Gatekeeper install, same as V3. Multi-panel support is not planned.

## 7. Open questions

These are what the API page needs to answer. Most are for the docs; a few (marked) are for Mike.

- **Q-1 Bot authentication.** Sign-in shown is passkey based. How does a headless bot authenticate: API keys / personal access tokens, a service-account type, OAuth client credentials, or username + password + TOTP like AMP 2? How long do tokens last and how are they refreshed? *(Ask Mike if the docs don't say.)*
- **Q-2 Capabilities.** Full list of capabilities; are they granted per shard, per role, or both; can an account with the right capability grant capabilities to a role (V3 configured its own AMP role)?
- **Q-3 Least privilege.** Can the bot account get only what it needs on chosen shards, and can the bot read its own effective capabilities (V3 used `Core/CurrentSessionHasPermission`)?
- **Q-4 Console and events.** Is there a WebSocket/SSE stream for console output, chat, player join/leave and state changes, or is it polling again? What are the message shapes, and is chat separated from console?
- **Q-5 Power and status.** Endpoints for start/stop/restart/kill, live state, metrics (CPU, RAM, player count, TPS) and the online player list.
- **Q-6 Game identity.** How does a shard say which game/module it runs? Are game-specific actions (whitelist, kick, ban, in-game chat) API endpoints, or console commands only?
- **Q-7 Scheduling catalog.** Which triggers and tasks exist, and is there an HTTP/webhook task Gatekeeper could receive? Does "emits signals" mean there is an event feed we can subscribe to?
- **Q-8 Files & Backups.** Backup create/list/delete/restore endpoints and their capabilities.
- **Q-9 Running inside AMP.** Can Gatekeeper still be deployed as an AMP 3 shard (V2/V3 shipped an AMP template), and is there a generic Python/app module for that? *(Ask Mike.)*
- **Q-10 Rate limits and stability.** Are there documented rate limits? Will the API be versioned or change freely during the beta?
- **Q-11 Audit log.** Can the bot write entries to AMP's audit log, so actions taken from Discord show up in AMP with the Discord user named?
- **Q-12 "Generate MCP".** What does it produce? Not needed for Gatekeeper, but it may be a quick way to explore the API.

## 8. Decisions we need from the team

- **D-1 AMP 3 only, or AMP 2 and 3 in one bot?** Recommendation: **AMP 3 only.** V3 stays the AMP 2 bot. Supporting both doubles the client layer and its testing for a version that is being phased out. The `AmpPort` design leaves the door open if this changes.
- **D-2 Name and versioning.** "Gatekeeper V4"? "Gatekeeper AMP 3"? Affects package name, Docker image and docs.
- **D-3 Licence.** This repo is **AGPL-3.0**; V2/V3 files carry **GPL-3.0-or-later** headers (copyright Katelynn Cadwallader). Porting that code into an AGPL project is allowed (GPLv3 section 13), but the original copyright notices must stay on ported files, and k8t should agree the new edition is AGPL. If AGPL wasn't a deliberate choice, now is the cheapest time to switch to GPL-3.0-or-later to match.
- **D-4 Python floor.** V3 targets 3.11 and 3.12. Keep 3.11 as the floor unless the AMP 3 container image dictates otherwise.
- **D-5 Who owns which phase.** Min and k8t split; nothing in this doc assumes a split.

## 9. Risks

- **The API changes during the beta.** Mitigated by the `amp/` boundary, spec snapshot and drift check; expect to regenerate models several times.
- **No headless auth at beta start** (passkeys only). Would block everything past Phase 0. Worth asking Mike early (Q-1).
- **Feature parity is large.** V3 is ~14k lines and ~210 commands. Phase 2 deliberately ships a small MVP first, and not every V3 command needs to come back (e.g. the bot's own scheduler).
- **Game-specific behaviour may move.** If AMP 3 drops module-specific endpoints, whitelist and chat fall back to console commands per game, which is more fragile.
