# Portcullis: plan and design

Status: **draft**, written before the AMP 3 API is available.
Everything that depends on the API is marked as an assumption (A-n) or an open question (Q-n) and must be checked against the real API browser once the beta is running.

Contents: 1 Goal · 2 Lessons from V2/V3 · 3 What we know about AMP 3 · 4 Supported games · 5 Command design · 6 Architecture · 7 Accessibility · 8 Delivery plan · 9 Assumptions · 10 Open questions · 11 Decisions · 12 Risks · 13 Plan check against the real API · Appendix A: V3 command map · Appendix B: full candidate command list

---

## 1. Goal

A Discord bot that lets a community run and watch its CubeCoders AMP 3 game servers from Discord, the same job Gatekeeper V2 (k8thekat/GatekeeperV2) and Gatekeeper V3 (MinVT-dev/GatekeeperV3-Beta) do for AMP 2.

"Same area as V2/V3" is taken to mean:

- same product: server control, console/chat/event relay to Discord channels, whitelist requests, banners, permissions, audit, schedules, alerts, polls, player tracking, account linking;
- same stack: Python 3.11+, discord.py 2.x, SQLite, a `bot_perms.json` style permission file, Docker plus "run as an AMP instance";
- same habits from V3 4.8.0: credentials only from env or `.env`, secrets redacted in logs, console input validated and rate limited, confirmation on destructive actions, `pytest` + `pip-audit` in CI.

Two things change on purpose:

- **The inside.** AMP 3's API is a different shape (section 3) and V3's AMP layer cannot be reused (section 2).
- **The command surface.** V3 has 117 slash commands. This edition targets 23 (section 5).

## 2. What we learned from V2 and V3

V3 is V2 with a large reliability and security pass on top. Both share one structure:

| Piece | V2/V3 file | Keep / change |
|---|---|---|
| AMP client | `AMP.py` (`AMPInstance`, ~1,060 lines) | **Replace.** See below. |
| Instance discovery | `AMP_Handler.py` | Replace with a shard registry. |
| Console polling | `AMP_Console.py` (thread per instance, `Core/GetUpdates` every second) | Replace with whatever AMP 3 offers for streaming or webhooks (Q-4). |
| Per-game behaviour | `modules/<Game>/amp_*.py` + `cog_*.py`, subclassing `AMPInstance` | Mostly replace with data (section 4). Code only where data can't express it. |
| Discord features | `cogs/*.py` (19 cogs, 117 commands) | Port feature by feature into a much smaller command set (section 5). |
| Storage | `DB.py` + `DB_Update.py` (SQLite, hand-rolled version upgrades) | Keep SQLite, use numbered migrations. |
| Permissions | `bot_perms.json` nodes like `server.console.interact`, `-node` denies | Keep the file format so V3 users can copy theirs. |
| Event bus, rate limiter, validation | `utils_events.py`, `utils_ratelimit.py`, `utils_validation.py` | Keep, already tested. |

Why `AMP.py` cannot be carried over:

1. **It is RPC over POST.** Every call is `POST /API/<Module>/<Method>` with a JSON body. AMP 3 is REST (`GET /api/shards/{shardId}/schedules`, `PUT`, `DELETE`, ...).
2. **It does too many jobs in one class.** HTTP transport, login, AMP role setup, DB access (`_setDBattr`), console threads and game behaviour all live on `AMPInstance`, and API response fields are copied onto the object with `setattr`. Nothing can be tested without a live AMP and a database (V3's CI only byte-compiles the cogs for this reason).
3. **It is synchronous.** `requests` plus `time.sleep` inside a discord.py bot. V3 4.8.0 spent a lot of effort moving blocking calls to worker threads; a fresh core should be async from the start (`aiohttp` is already a dependency).
4. **It guesses response shapes.** `CallAPI` has branches for `result` being an int, a bool, a dict, or a `Title: Unauthorized Access` body. An OpenAPI spec lets us type every response instead.

So the plan is: **new core, ported features.** Not a line-by-line port, and not a from-scratch rewrite of the features either.

## 3. What we know about AMP 3 so far

From Mike's messages and the API browser screenshot (2026-10-07):

- The beta will include an API browser. It is **Scalar over an OpenAPI document**, served by AMP itself (screenshot: `localhost:7278/scalar/v1`).
- Paths are REST under `/api/`, e.g. `GET /api/shards/{shardId}/schedules/catalog`. `shardId` is a UUID.
- Sidebar sections: initial setup (internet reachability probe, create first admin, **passkey** sign-in begin/finish), System & Health, My Account, Users & Access, Audit Log, **Shards**, Files & Backups, **Scheduling**, **Spires & Datastores**, Networking, and a "Generate MCP" link.
- **Observed on Min's beta VM (2026-11-07), not documentation.** The machine listens on four AMP-related ports: `webapi` on **HTTP 8443**, a second `webapi` on **2222** (not HTTP, as far as the network view can tell), `nats-server` on **4222**, and `sshd` on 22. Min also reports that `/api` does not exist on the beta. Two things in this contradict the picture above: the Scalar address in the screenshot used port 7278, not 8443, and a NATS server plus a non-HTTP API port point to a message-based transport next to, or instead of, REST (the panel pages in the next bullet make that more likely). Do not design around either reading until the beta says which is true (Q-13, Q-14).
- **More from the beta panel's system pages (2026-11-07), also observations.** The panel reports AMP **v3.0.0** on the Preview release channel, running on VMware, with a **Message Bus** card: the panel shows "NATS Connection: Connected", a `nats-server` **v2.15.2** that appears to be started and monitored by AMP itself, round-trip latency of about 0.2 ms, and **JetStream** enabled with **16 streams and 35 consumers** (8.6 MB of file storage). A small number of JetStream API errors is shown against a large total. The container runtime is **Podman 5.4.2**, reported as usable. So NATS is a visible, first-class part of AMP 3 and not a side detail, and JetStream suggests durable, replayable messages. Whether any of it is open to outside clients is still unknown (Q-14).
- **Steam Web API key (observed).** AMP 3 has an optional Steam Web API key under Settings, Analytics: with a key, lookups are quicker and ban counts are exact. That suggests AMP itself looks up Steam players and ban counts. If that data is exposed through the API, Portcullis's player lookup and ban-check features may be able to use it instead of its own Steam key; if not, Portcullis keeps its own. Not confirmed (Q-6).
- Scheduling is a full API: catalog of triggers and tasks (with emitted signals), list/create/get/replace/delete schedules, enable/disable, trigger an immediate run, run history, preview next fire times.
- Access is **capability based per shard**: "Requires the shard's `ScheduleRead` capability", and `403` means the caller lacks that capability on the shard. `404` means no such shard.

From CubeCoders' public posts ("Announcing - AMP 3", 2026-08-29, and "Licencing changes in AMP 3", 2026-09-21, both on discourse.cubecoders.com):

- **AMP 3 uses all existing AMP 2 templates**, so the game list carries over (section 4).
- AMP 3 can read in existing AMP 2 instance data. **Users and roles are not imported**, because they "work differently in AMP 3". The Portcullis bot account therefore has to be set up again on AMP 3.
- The old controller/target hierarchy is gone, and the panel no longer needs filesystem access to instances. This matches "Spires" in the API.
- **Webhooks become available on all licence tiers.** If they cover server events, they are a candidate for console/player events instead of polling (Q-4).
- OIDC single sign-on moves to all tiers. A bot still needs a non-interactive login (Q-1).
- AMP 2 development continues next to AMP 3. Beta access is invitation-only.

Re-scan on 2026-10-24 (public forum, CubeCoders GitHub organisation, `cubecoders.com/AMP3`):

- **Beta timing.** A forum post from 2026-10-19, "AMP 3 Release Premiere Countdown!", says a premiere video will show AMP 3 and that the beta keys "will also going out" at the same time, with the premiere explaining how the beta works. Min reports the beta is announced for 7 Nov around 21:00 (Min's timezone). The post's title says "October 6th, 20:00 UTC", which is before the post date, so the title looks like a typo. If Min is on Central European time, 21:00 on 7 Nov is 20:00 UTC, which would match the time in the title. That match is an inference, not something CubeCoders states. The countdown page itself (`cubecoders.com/AMP3`) renders with JavaScript and could not be read.
- **No public API documentation yet.** No forum post, GitHub issue or CubeCoders repository mentions the AMP 3 API, API keys, OpenAPI or webhook details. The CubeCoders GitHub organisation has no AMP 3 or SDK repository. Everything in the list above about the API still comes from the screenshot.
- **No new games.** The `AMPTemplates` commits since 2026-10-05 are fixes and updates (a DayZ DLC, new console regexes after a game update, OpenStarbound download fix, Python 3.15). No game was added, so the 244-template figure in section 4 still stands (recounted on 2026-10-24).
- **"AMP 3 AI usage disclosure" (2026-08-31).** Says AMP 3's accessibility attributes were first drafted with AI and then tested by hand, and that AMP 2's Generic module code was ported to the AMP 3 platform. This supports the claim that templates carry over, and says nothing about the API.
- **A Python template exists.** `AMPTemplates` has a "Python App Runner" template that downloads an app from a git repo, GitHub release or PyPI, installs `requirements.txt`, and runs a script or module, with environment variables read from a `.env` file. That is a ready-made way to run Portcullis inside AMP (Q-9), assuming the template works unchanged in AMP 3.

That is enough to fix the architecture; it is not enough to write the client.

## 4. Supported games

**Short answer: there is no separate AMP 3 games list yet, but CubeCoders has said AMP 3 uses all AMP 2 templates, so the AMP 2 list is the AMP 3 list for now.**

The sources:

- The canonical list is the forum post "Supported Applications Compatibility" (discourse.cubecoders.com/t/supported-applications-compatibility/1828). It has two dedicated modules (**Minecraft Java Edition** and **Rust**); everything else runs on AMP's **Generic** module through templates.
- The templates themselves live in the public repo `CubeCoders/AMPTemplates`. On 2026-10-08 it held **244 templates** (last commit 2026-10-05).

What the templates already tell AMP, counted from that repo:

| Template field | Templates that set it | What it means for Portcullis |
|---|---|---|
| `Console.UserJoinRegex` / `UserLeaveRegex` | 162 of 244 | AMP itself can see joins and leaves for these games. |
| `Console.UserChatRegex` | 59 of 244 | AMP can pick out in-game chat for these games. |
| `Console.UserActions` (kick/ban commands) | 1 of 244 | Almost no template describes moderation commands. |
| `App.AdminMethod` | 156 STDIO, 48 Source RCON, the rest Quake/Telnet/GoldSrc/BattlEye RCON or log tailing | How AMP talks to the server. Not Portcullis's problem. |

### What this means for the design

V2/V3 hard-code nine game modules (Generic, Minecraft, Valheim, Terraria, Factorio, 7 Days to Die, Project Zomboid, Starbound, CS:GO). Each one re-implements chat parsing and whitelist commands. With 244 templates that does not scale, and most of it duplicates what the templates already describe.

So Portcullis gets three levels of support instead of a game list:

| Level | Games | What works |
|---|---|---|
| **Every game** | All 244 templates plus Minecraft and Rust | Status, start/stop/restart/kill, console relay and console commands, backups, schedules, alerts, banners. Nothing game-specific. |
| **Player and chat aware** | Templates with join/leave regexes (162) and chat regexes (59) | Player join/leave events and the player tracker. Chat relay where a chat regex exists. This depends on AMP 3 exposing these as API events (Q-6). If it doesn't, Portcullis reads the same regexes from the template. |
| **Game profile** | Games with a profile file in `games/profiles/*.toml` | Whitelist add/remove, sending chat from Discord into the game, kick/ban, avatar lookup. |

A game profile is data, not code:

```toml
# games/profiles/valheim.toml
match = ["valheim"]                 # template / module ids this applies to
whitelist_add = "addpermitted {id}"
whitelist_remove = "removepermitted {id}"
say = "say {name}: {message}"
id_kind = "steamid"                 # what /link and /whitelist ask the user for
```

Only Minecraft Java needs real code: `tellraw` JSON for chat (with V3's `json.dumps` fix), and the Mojang UUID/name lookup. Profiles for the eight games V3 already supports come first. After that, anyone can add a game with a pull request that contains no Python.

## 5. Command design

### The problem

V3 has **117 slash commands** (not counting the cog template) under 23 top-level names; the full list is in Appendix A. **51 of them each set one value** (`/server settings host`, `/banner_settings timezone`, ...), and **14 are developer tools** (`/bot utils roleid`, `/bot cog reload`, ...). The result is a long command picker that is hard to scan, and admins have to know a command exists before they can find a setting.

### Target

**23 slash commands under 13 top-level names, plus one right-click user menu.** Members see six commands at most.

### Principles

1. **Settings are panels, not commands.** One `/setup` command opens a panel (select menus, buttons, modals) for bot-wide settings, and `/setup server:<name>` opens the same panel for one server. This replaces the 51 setter commands. A panel also shows the current values, which a setter command never does.
2. **Actions on a thing are buttons on that thing.** `/server status` shows Start/Stop/Restart/Kill/Backup buttons. `/schedule list`, `/alert list`, `/poll list` and `/backups` show Run/Pause/Delete-type buttons next to each item. That removes the separate `delete`, `pause`, `resume`, `close`, `results` and `mute` commands.
3. **Choices become options, not separate commands.** `/server power server:<name> action:<start|stop|restart|kill>` replaces four commands. `/server send ... as:<console|chat|broadcast>` replaces `msg` and `broadcast`.
4. **Developer tools are not slash commands.** Sync, cog reload, restart and ping become owner-only text commands (`$gk sync`) that never show in the picker. Discord's developer mode already gives role, channel and user IDs.
5. **Permissions follow actions, not commands.** `bot_perms.json` nodes stay fine-grained (`server.power.kill`, `setup.console.channel`), and each button and panel field checks its own node. There are fewer commands, but control stays as fine-grained as in V3. Default member permissions hide `/setup`, `/admin` and the moderator commands from ordinary members.
6. **Discord's limits stay out of reach.** Discord allows 25 subcommands per group and two levels of nesting. Every group here stays under five.

### Proposed set

| Command | Who | Replaces in V3 |
|---|---|---|
| `/server status [server]` | everyone | `server status`, `server users`, `player online`. Without a server it lists all of them. Power and backup buttons appear for members allowed to use them. |
| `/server power <server> <action>` | moderators | `server start/stop/restart/kill` |
| `/server send <server> <text> [as]` | moderators | `server msg`, `server broadcast` |
| `/backups <server>` | moderators | `backup list/create/delete`, `server backup` (create and delete are buttons) |
| `/whitelist request <server> [name]` | everyone | `whitelist_request` |
| `/whitelist add` · `/whitelist remove` | moderators | `whitelist add/remove` |
| `/link` | everyone | `link start/status/clear` (one panel showing link state, with buttons) |
| `/player lookup <name>` · `/player top [server]` | everyone | `player lookup/top` |
| `/poll create` · `/poll list` | moderators | `poll create/close/results/list/delete` |
| `/schedule create` · `/schedule list` | admins | `schedule create/list/delete/pause/resume/run`. These front AMP 3's own scheduler (section 6). |
| `/alert create` · `/alert list` | admins | `alert create/list/delete/mute`. `alert channel` moves into `/setup`. |
| `/audit [filters]` | admins | `audit log/search/stats/export`. `audit channel` moves into `/setup`; export is a button. |
| `/user <member>` + right-click **Apps → Portcullis user** | moderators | `user info/add/update`, `role` |
| `/setup [server]` | admins | the 51 setters: `bot donator/moderator/permissions/settings/message_timeout`, `server settings`, `server console/chat/event`, `server regex`, `regex_pattern`, `banner`, `banner_settings`, `bannergroup`, the `whitelist` settings and `whitelist_reply` |
| `/admin diagnostics` · `/admin export` · `/admin import <file>` | admins | `diagnostics`, `config export/import`, `dbserver cleanup`. Import stays a slash command because panels can't take a file upload. |
| `/help` | everyone | `help`, `about` |

`dbserver change_instance_id` goes away, because shards have stable UUIDs.

### What we give up, and how we handle it

- **Panels are harder to script and to document.** A setter command can be copied from a guide; a panel can't. `/admin export` and `import` cover bulk changes, and the docs show panel screenshots.
- **Panels expire.** Discord interactions stop working after 15 minutes. Panels say so, and re-running the command opens a fresh one.
- **Panels need more tests.** Each panel field is a small handler with its own permission check. The fake AMP from Phase 0 makes these testable without Discord or AMP.
- **Muscle memory.** V3 admins have to relearn where settings live. A table in the docs maps each old command to its panel field (Appendix A is the draft).

## 6. Architecture

```
portcullis/
  amp/            # The ONLY code that knows AMP URLs, auth, or wire formats
    transport.py  #   aiohttp session, auth, retries, timeouts, rate limit, redacted logging
    api/          #   one module per API tag: shards, scheduling, files, users, system, ...
    models.py     #   typed request/response models generated from the OpenAPI document
    errors.py     #   AmpError, NotFound, MissingCapability(cap, shard), AuthExpired, Unreachable
    streams.py    #   console/event streaming or webhook receiver (shape depends on Q-4)
  core/           # Domain logic, AMP-agnostic types, fully unit-testable
    servers.py    #   server registry (shard id <-> friendly name <-> DB row), status cache
    relay.py      #   console/chat/event fan-out to the event bus
    whitelist.py, linking.py, schedules.py, alerts.py, players.py, audit.py
    permissions.py#   bot_perms.json evaluation (ported from V3, already tested)
  games/
    profile.py    #   loads and validates profiles/*.toml
    profiles/     #   valheim.toml, terraria.toml, factorio.toml, ...
    minecraft.py  #   the one game that needs code (tellraw, Mojang lookups)
  discord/        # discord.py commands, panels (views), embeds, autocomplete. No AMP calls.
    commands/     #   one file per top-level command from section 5
    panels/       #   /setup sections, /link, list views with item buttons
  db/             # aiosqlite, numbered migrations, repositories
  config.py       # env / .env loading, validation at startup
  main.py
tests/
  fakes/amp.py    # in-memory fake of the AMP port, used by core and command tests
```

Rules that keep the AMP 3 unknowns contained:

1. **Discord code never imports `portcullis.amp`.** It calls `core` services. When the API changes during the beta, only `amp/` changes.
2. **`core` depends on a small interface (`AmpPort`), not on the HTTP client.** Something like:
   ```python
   class AmpPort(Protocol):
       async def list_servers(self) -> list[ServerInfo]: ...
       async def get_status(self, server: ServerId) -> ServerStatus: ...
       async def power(self, server: ServerId, action: PowerAction) -> None: ...
       async def send_console(self, server: ServerId, line: str) -> None: ...
       def events(self, server: ServerId) -> AsyncIterator[ServerEvent]: ...
       async def list_backups(self, server: ServerId) -> list[Backup]: ...
       # schedules, players, files ... added as features are ported
   ```
   The real implementation lives in `amp/`, the fake in `tests/fakes/`. This is what lets us build and test most of the bot **before** the API docs arrive.
3. **Generated models, hand-written facade.** Generate types from AMP's OpenAPI JSON (e.g. `datamodel-code-generator` to pydantic v2), pinned to a spec snapshot checked into `amp/spec/`. Generated classes never leave `amp/`; they are mapped to `core` types. Re-generating after a beta update then shows up as a reviewable diff, and a CI job can fail when the live spec drifts from the snapshot.
4. **Capabilities become first-class errors.** A `403` turns into `MissingCapability("ScheduleRead", shard)`, which Discord shows as "Portcullis's AMP account is missing ScheduleRead on Survival", not a stack trace.

### Two permission layers

| Layer | Who it protects | Mechanism |
|---|---|---|
| Discord side | Which Discord members may use which command, button or panel field | `bot_perms.json` nodes, same format as V3, nodes named after actions (section 5, principle 5). |
| AMP side | What the bot's AMP account may do on each shard | AMP 3 capabilities on the bot's account. |

V2/V3 asked for a **Super Admin** AMP account and then built a "Gatekeeper" role (`amp_permissions.py`). AMP 3 doesn't import roles anyway, so this is the moment to stop doing that. The bot gets an account with only the capabilities its enabled features need, and `/admin diagnostics` lists the missing ones per shard. Whether the bot can still set up its own role depends on Q-2 and Q-3.

### Scheduling: use AMP's, don't duplicate it

V3 runs its own scheduler (`schedule_cog.py`, a 30-second loop over a `Schedules` table). AMP 3 has a complete scheduling API with triggers, tasks, run history and next-fire preview. This edition **fronts AMP's scheduler** and drops its own, except for Discord-only actions AMP cannot do, like posting a message in a channel. If AMP 3's task catalog has a webhook task, even those could live in AMP (Q-7).

### Console, chat and events

V3 polls `Core/GetUpdates` once per second per instance, in a thread each. For AMP 3, in order of preference:

1. a push stream (WebSocket / SSE) from the API;
2. AMP 3 webhooks into a small HTTP listener in Portcullis. This is simple when Portcullis runs next to AMP, but needs an open port otherwise;
3. one async poller per online shard with backoff.

Whichever it is sits behind `streams.py`. `core/relay.py` publishes `ConsoleLine` / `ChatMessage` / `PlayerJoined` / `PlayerLeft` on the event bus, and the Discord side subscribes. Regex filters, the console denylist, rate limiting and `@everyone` stripping from V3 4.8.0 carry over unchanged.

### Storage

SQLite via `aiosqlite`, numbered SQL migrations (`db/migrations/0001_init.sql`, ...), a `schema_version` row. Server rows are keyed by **shard UUID** instead of the AMP 2 instance id. An import mode in `/admin import` reads a V3 `discordBot.db` and copies users, links, regex patterns, banner settings, whitelist replies and config, asking the admin to match each old instance to a shard.

## 7. Accessibility

Discord does most of the work, but the bot still chooses colours, labels and images.

- **Status is never colour-only.** Embed colour can show online/offline, but the text always says "Online" or "Offline" too. The same goes for alert severity and whitelist approved/denied.
- **Buttons have text labels.** Icons are fine next to a label, but never instead of one. Destructive buttons (Kill, Delete) use Discord's danger style *and* a confirmation step, as in V3.
- **Banner images get a text equivalent.** A generated banner is a picture of text, which screen readers can't read. Each banner message also carries the server name, status and player count as plain text, and the image gets alt text (the attachment `description`).
- **Custom banner colours are contrast-checked.** V3 lets admins pick any text colour over any background. The banner editor checks each text colour against the background under that text with the WCAG ratio (4.5:1 for normal text, 3:1 for large text) and warns when it fails. On photo backgrounds it checks the worst spot under the text, not the average, and suggests the blur or a darkening overlay.
- **Nothing essential lives only in an image or a tooltip.** Discord's buttons and select menus are keyboard-reachable already; Portcullis must not undo that by hiding information where a keyboard or screen reader can't get to it.

## 8. Delivery plan

The beta date is open-ended (CubeCoders announces no dates, and access is by invitation), so phases are gated on the API, not on dates. Phase 0 is sized so that two people can make real progress before the API arrives.

### Phase 0: before the API (now)

| # | Task | Done when |
|---|---|---|
| 0.1 | Repo scaffold: `pyproject.toml` (ruff, pyright, pytest config from V3), `requirements*.txt`, CI (tests, `pip-audit`, the `tokens.py` check from V3), Dockerfile | CI is green on an empty package |
| 0.2 | `config.py` (env / `.env`, validated at start-up) and logging with redaction | The bot refuses to start with a clear message when a variable is missing |
| 0.3 | `db/` with migrations and the first schema (servers keyed by UUID, users, links, settings, audit) | A migration test creates and upgrades a database |
| 0.4 | `AmpPort` interface, `core` types, and the in-memory fake | The fake can list servers, change power state and emit console/player events |
| 0.5 | Port permissions, event bus, rate limiter and validation from V3, with their tests | V3's four test files pass, with only import changes |
| 0.6 | Discord shell: start-up, owner text commands (`sync`, `reload`), `/help`, `/admin diagnostics` | The bot runs against the fake in a test guild |
| 0.7 | `/server status`, `/server power`, `/server send` and console relay against the fake | A moderator can start a fake server from Discord and see its console lines |
| 0.8 | `/setup` panel framework (sections, fields, a permission per field) with the console/chat/event channel fields | Channels set through the panel are saved and used by the relay |
| 0.9 | Game profile loader, the eight V3 game profiles, and the Minecraft plugin | Profile tests cover the whitelist and say commands for each game |

0.1 to 0.3 come first. After that the work splits into two tracks that can run side by side: core and commands (0.4 to 0.7), and panels and games (0.8 to 0.9).

### Phase 1: API spike (first days with the beta)

The beta is announced for 7 Nov around 21:00 (Min's timezone), with keys going out at the premiere. The first session with a running AMP 3 should go through this list in order, because each step unblocks the next:

1. Create the bot account the way AMP 3 allows and note how it authenticates (Q-1). If it only supports passkeys, ask Mike straight away.
2. Save the OpenAPI JSON and the Scalar page's endpoint list into `amp/spec/` (A-3).
3. Read the capability list and see which ones the bot account needs (Q-2, Q-3).
4. Find out how console output, chat and player events reach a client (Q-4, Q-6).
5. Check whether the Python App Runner template works in AMP 3 (Q-9).
6. Fill in the open questions in section 10 and the "confirm against the API" column in Appendix B.

Then:

- Export the OpenAPI JSON into `amp/spec/`, generate models, answer the open questions below and update this doc.
- Prove the four risky paths end to end against a real AMP 3: authenticate as a bot, list shards, start/stop one, receive its console output.
- Implement the real `AmpPort` for exactly what Phase 0 built against the fake, and run the same tests against both.

### Phase 2: MVP

- Everything from Phase 0, now on real AMP 3: status, power, send, console/chat/event relay, `/setup` channels, permissions, audit log, capability diagnostics.
- `/backups`, `/whitelist` and `/link`.
- Release to a few test communities.

### Phase 3: rest of the V3 features

- Banners and banner groups, player tracker, alerts, polls, schedules on AMP's scheduler, regex filters, presence, `/admin export|import`.
- More game profiles, contributed or requested.

### Phase 4: release

- V3 database import, docs (README, command reference with the old-to-new command map, permissions guide), an AMP 3 template for running Portcullis as a shard if AMP 3 supports that (Q-9), tagged release.

## 9. Assumptions (to confirm against the API)

- **A-1** A "shard" is the AMP 3 equivalent of an AMP 2 instance: one game server.
- **A-2** "Spires" are hosts (AMP 2's targets) and "Datastores" are storage locations. Portcullis only needs to read them.
- **A-3** *(Broke, see section 13.)* The OpenAPI JSON is reachable next to the Scalar page (ASP.NET's default would be `/openapi/v1.json`). *Unconfirmed: Min reports no `/api` page on the beta and the beta listens on 8443, not 7278 (Q-13).*
- **A-4** Everything Portcullis needs per server lives under `/api/shards/{shardId}/...`, including power actions, console and players. *Unconfirmed: a NATS server on the beta VM may carry console and events instead (Q-14).*
- **A-5** JSON bodies, UUID ids, standard HTTP status codes (`401`, `403` with the capability name, `404`), as in the screenshot.
- **A-6** One AMP 3 panel per Portcullis install, same as V3. Multi-panel support is not planned.
- **A-7** A shard exposes which template or module it runs, so Portcullis can pick a game profile.

## 10. Open questions

These are what the API page needs to answer. Most are for the docs; a few (marked) are for Mike. What to capture from the beta, in priority order, is in `docs/BETA_CHECKLIST.md`.

- **Q-1 Bot authentication.** The sign-in shown uses passkeys. How does a headless bot authenticate: API keys / personal access tokens, a service account, OAuth/OIDC client credentials, or username + password + TOTP like AMP 2? How long do tokens last and how are they refreshed? On the beta, a search for "api" in Settings finds only the optional Steam Web API key, so there is no obvious API-key setting there (observed 2026-11-07). *(Ask Mike if the docs don't say.)*
- **Q-2 Capabilities.** The full list of capabilities. Are they granted per shard, per role, or both? Can an account with the right capability grant capabilities to a role?
- **Q-3 Least privilege.** Can the bot account get only what it needs on chosen shards, and can the bot read its own effective capabilities (V3 used `Core/CurrentSessionHasPermission`)?
- **Q-4 Console and events.** Is there a WebSocket/SSE stream for console output, chat, player join/leave and state changes? What do AMP 3 webhooks cover, and how are they signed? Or is it polling again? On the beta VM a `nats-server` is running (Q-14).
- **Q-5 Power and status.** Endpoints for start/stop/restart/kill, live state, metrics (CPU, RAM, player count, TPS) and the online player list.
- **Q-6 Game identity and player events.** How does a shard say which template it runs? Does AMP 3 turn the template's join/leave/chat regexes into API events or a player list, or does Portcullis have to apply them to the console itself? Are any game actions (whitelist, kick, ban) API endpoints?
- **Q-7 Scheduling catalog.** Which triggers and tasks exist, and is there a webhook task? Does "emits signals" mean there is an event feed?
- **Q-8 Files & Backups.** Backup create/list/delete/restore endpoints and their capabilities.
- **Q-9 Running inside AMP.** Can Portcullis still be deployed as an AMP 3 shard (V2/V3 shipped an AMP template)? A "Python App Runner" template already exists in `AMPTemplates` (git repo, `requirements.txt`, script, `.env`); does it work unchanged in AMP 3, and does the bot then need its own template at all? The beta reports **Podman 5.4.2** as its container runtime, so the app would likely run in a Podman container: does the Python App Runner template work there (image, rootless Podman, network access to the panel or the message bus, persistent `.env` and data)? *(Ask Mike.)*
- **Q-10 Rate limits and stability.** Are there documented rate limits? Will the API be versioned or change freely during the beta?
- **Q-11 Audit log.** Can the bot write entries to AMP's audit log, so actions taken from Discord show up in AMP with the Discord user named?
- **Q-12 "Generate MCP".** What does it produce? Not needed for Portcullis, but it may be a quick way to explore the API.
- **Q-13 Where is the HTTP API?** The beta VM serves `webapi` over HTTP on 8443, and Min says `/api` does not exist. Is the Scalar/OpenAPI browser (seen on 7278 in the October screenshot) still there, on which port and path, and is the REST shape in section 3 still right? *(Read-only: open the beta's web address in a browser and note what loads.)*
- **Q-14 What do the other ports serve?** `webapi` on 2222 (not HTTP) and `nats-server` on 4222. Is NATS an internal bus between the panel and its game hosts, or something a client such as Portcullis is meant to use? If it is for clients: what subjects exist for console, chat and player events, how does a client authenticate, and is it documented? If it is internal, it may only be reachable from the machine itself and we should not build on it. JetStream is on with 16 streams and 35 consumers: are the streams something a client can read (for example console, chat, player and state events with replay), and what are the stream and subject names, retention and access rules? What is 2222 (a gRPC or custom RPC port)? *(Ask Mike.)*

## 11. Decisions we need from the team

- **D-1 AMP 3 only, or AMP 2 and 3 in one bot?** Recommendation: **AMP 3 only.** V3 stays the AMP 2 bot. CubeCoders keeps developing AMP 2 alongside AMP 3, so V3 users are not stranded. The `AmpPort` design leaves the door open if this changes.
- **D-2 Name and versioning.** Decided (Min, 2026-11-07): the AMP 3 edition is called **Portcullis**. The repo is `MinVT-dev/Portcullis`; the Python package and Docker image use `portcullis`. Brand assets are in `docs/branding/`.
- **D-3 Licence.** This repo is **AGPL-3.0**; V2/V3 files carry **GPL-3.0-or-later** headers (copyright Katelynn Cadwallader). Porting that code into an AGPL project is allowed (GPLv3 section 13), but the original copyright notices must stay on ported files, and k8t should agree the new edition is AGPL. If AGPL wasn't a deliberate choice, now is the cheapest time to switch to GPL-3.0-or-later to match.
- **D-4 Python floor.** V3 targets 3.11 and 3.12. Keep 3.11 as the floor unless the AMP 3 container image dictates otherwise.
- **D-5 Command set.** Agree on section 5 before Phase 0 task 0.7. Commands are the hardest thing to change after release.
- **D-6 Who owns which phase.** Phase 0 splits into two tracks after 0.3; nothing here assumes who takes which.

## 12. Risks

- **The API changes during the beta.** Mitigated by the `amp/` boundary, spec snapshot and drift check; expect to regenerate models several times.
- **No headless auth at beta start** (passkeys only). Would block everything past Phase 0. Worth asking Mike early (Q-1).
- **AMP 3 doesn't expose player/chat events** (Q-6). Portcullis would then have to apply the templates' regexes itself, which needs access to template data it may not get through the API.
- **Panels turn out clumsy for some settings.** If a setting is used often enough to want a command, it can get one later. Adding a command after release is easy; removing one isn't.
- **Game profiles drift from the templates.** If a template changes a whitelist command, the profile breaks without anyone noticing. A weekly CI job can compare profiles with `CubeCoders/AMPTemplates`.

---

## 13. Plan check against the real API (2026-11-07)

Checked against `docs/amp3-api/` (the AMP 3.0.0 preview build `3.0.0.6`, probed on a test VM). Markers below follow that folder: verified on the VM, stated in the shipped spec, or inferred. Sections 3, 9 and 10 above still show the older assumptions; this section is the current view and wins where they differ.

**Verdict.** Portcullis can be built as planned. The shape holds: a typed REST client generated from the spec, a small command set, game profiles as data, AMP's own scheduler and backups. Four things change and two assumptions broke. Nothing found so far blocks the project.

### Works as planned

- **A generated, typed client.** The OpenAPI 3.1.1 spec exists (219 paths, 263 operations, 389 types). It is not served over HTTP, so we ship the file and generate from it (A-3 changes, below).
- **Server control.** Start, stop, restart, sleep, wake, suspend and update are routes; state and metrics are readable. I did not see a separate kill route, although a `power.kill` permission exists, so check how kill is done before promising a Kill command.
- **Players.** `GET /api/Shards/{id}/users` returns the connected-user roster, and AMP applies the template's join and leave patterns itself and emits `UserJoined` and `UserLeft` events. Portcullis no longer needs to parse joins and leaves.
- **Console input.** `POST /api/Shards/{id}/console` sends a command (needs `console.send`, returns 202). This is the only way to send chat, whitelist or kick, as the plan already assumed for whitelist commands.
- **Backups and schedules.** Snapshots (create, list, delete, restore) and schedules (create, list, run, history) are real routes. The schedule catalog has `player.joined`, `player.left`, `backup.create`, `app.consoleSend` and a `flow.webhook` task, so "use AMP's scheduler, don't duplicate it" holds.
- **Permissions.** `GET /api/me/permissions` returns the account's grants, so the bot can check what it may do (Q-3). There are 71 permission nodes, and a small role covers what Portcullis needs: `view`, `power.start|stop|restart`, `console.read`, `console.send`, `analytics.read|users`, `schedule.read`.
- **Game profiles as data.** The template's console spec is readable through `GET /api/Templates/{slug}`, including the join, leave and chat patterns, so game profiles can still be data files that add only what AMP does not give.

### What changes

1. **Sign-in.** There is no API key and no service account. The bot logs in as an ordinary user with a limited role (`POST /api/Auth/login`), gets a token valid for about 8 hours, and must renew it (`/api/Auth/renew`) or log in again. The bot's account should not need 2FA or a passkey, which is an assumption to check. Credentials live in the bot's environment file and are never logged.
2. **Realtime is SignalR, not a raw stream and not NATS.** Three hubs (`/hubs/shard`, `/hubs/spire`, `/hubs/tasks`). Each connection needs a fresh single-use hub token (valid about 90 seconds, accepted only as a query parameter, and the main token is refused). Every reconnect needs a new one. The reconnect loop is the fiddliest part of the client and needs its own tests.
3. **Chat is not an event.** The shard hub has `ConsoleLine` but no chat event. Portcullis applies the template's chat pattern to console lines. The patterns come from AMP, so this is the plan's intended approach, but the parsing stays on our side.
4. **Whitelist, kick, ban and say are console commands.** AMP 3's template console spec has no list of user actions, and no route exists for them. The per-game profile must hold the command text for each, as planned in section 4, and the 146 supported games still each need that data tested on a real server.

### Assumptions that broke or resolved

| Item | Result |
|---|---|
| A-3 spec reachable next to Scalar | **Broke.** No Scalar and no served spec. The spec is a file on disk in the AMP install, and a copy is in `docs/amp3-api/`. |
| A-4 everything under `/api/shards/{id}` | **Mostly holds.** Not chat, and not whitelist, kick or ban. |
| Q-1 bot authentication | **Resolved:** a normal user, username and password, 8 hour token. No API key. |
| Q-3 least privilege | **Resolved:** 71 permission nodes, readable at `/api/me/permissions`. |
| Q-4 events | **Resolved in part:** SignalR hubs with `UserJoined`, `UserLeft`, `UserRoster`, `ConsoleLine`, `ShardStateChanged`. Payload shapes are not yet captured from a live connection. |
| Q-5 power and status | **Resolved,** except how kill works. |
| Q-6 game identity and player events | **Resolved in part:** player events come from AMP; whitelist, kick and ban do not exist as routes. |
| Q-7 scheduling catalog | **Resolved:** catalog, including an outbound `flow.webhook` task. |
| Q-8 backups | **Resolved:** snapshot routes. |
| Q-9 running inside AMP | **Open.** Shards run in rootless Podman containers. The Python App Runner is untested. |
| Q-10 rate limits and stability | **Open.** None documented; the preview build will change. |
| Q-11 audit log | **Open.** Console input is audited by AMP (secrets redacted); whether the bot can add its own entries is unknown. |
| Q-13 where is the HTTP API | **Resolved:** the panel and REST API are on 8443, with SignalR under `/hubs`. |
| Q-14 NATS and 2222 | **Resolved:** 2222 is the built-in SFTP; NATS is AMP's own bus and needs AMP's credentials. Portcullis should not use it. |

### Top risks

1. **Preview expiry and API churn.** The build expires on 7 December 2026 (31 days from the check) and the API may change between previews. Mitigation: pin the spec in the repo, regenerate the client and diff the spec on every new build, and keep contract tests that fail when a route or field we use changes. Keep Gatekeeper V3 running on AMP 2 meanwhile.
2. **The bot needs a user account, token renewal and one-use hub tokens.** An expired token or a failed reconnect silently stops events. Mitigation: a connection manager that renews early, fetches a fresh hub token for each connect, and exposes its health in `/status`.
3. **Unverified event payloads.** Event names come from the panel's JavaScript, not from live traffic or the spec. Mitigation: record real payloads first (see below) and build the typed models from those.
4. **Console commands are game-specific and unverified.** Whitelist, kick and chat-in depend on each game's own commands and on AMP's template. Mitigation: ship profiles per batch and mark a feature unavailable for a game until it passes a real-server test.
5. **Spec licence.** The spec and its quoted descriptions are CubeCoders' material, not AGPL. The repo is private, so this is fine now. Remove the files or ask permission before making the repo public.

### Recommended first coding task

A **realtime recorder spike**, before any Discord code. A small script that logs in with a dedicated bot user, fetches a hub token, connects to `/hubs/shard`, joins a shard, and writes every event it receives to a file, with secrets removed. It reconnects with a fresh hub token each time. Run it on the test VM while a player joins, leaves and chats in one game. This de-risks risks 2 and 3 at once, gives real fixtures for the typed models and tests, and shows whether a Python SignalR client library is enough or we need a thin client of our own. In parallel, generate the REST client from the spec into `amp/`. Both are small and neither depends on Discord.

## Appendix A: V3 command map

Every V3 slash command and where it goes. Counted from `GatekeeperV3-Beta` at commit `760b1e7` (4.8.0): 117 commands, not counting the example in `resources/templates/cog_template.py`.

| V3 command(s) | Count | New home |
|---|---|---|
| `/bot donator`, `moderator`, `permissions`, `settings` | 4 | `/setup` → Bot |
| `/bot utils message_timeout` | 1 | `/setup` → Bot |
| `/bot utils clear, roleid, channelid, userid, steamid, uuid, ping, disconnect, restart, status, sync` | 11 | Owner text commands. ID lookups dropped (Discord developer mode); Steam ID and Minecraft UUID lookups happen inside `/link`. |
| `/bot cog load, unload, reload` | 3 | Owner text command `$gk reload` |
| `/server start, stop, restart, kill` | 4 | `/server power` and buttons on `/server status` |
| `/server msg, broadcast` | 2 | `/server send` |
| `/server status, users` | 2 | `/server status` |
| `/server update` | 1 | Automatic (the registry refreshes from AMP), plus a button in `/admin diagnostics` |
| `/server backup` | 1 | `/backups` |
| `/server settings info, avatar, displayname, host, donator, role, prefix, hidden` | 8 | `/setup server` → General |
| `/server console channel, denylist, rate_limit, filter` | 4 | `/setup server` → Console |
| `/server chat channel`, `/server event channel` | 2 | `/setup server` → Channels |
| `/server regex add, delete, list` | 3 | `/setup server` → Filters |
| `/regex_pattern add, delete, update, list` | 4 | `/setup` → Filters (patterns edited in a modal) |
| `/dbserver cleanup` | 1 | `/admin diagnostics` button |
| `/dbserver change_instance_id` | 1 | Dropped (shard UUIDs are stable) |
| `/user info, add, update`, `/role` | 4 | `/user` and the right-click user menu |
| `/alert create, list` | 2 | `/alert create`, `/alert list` |
| `/alert delete, mute` | 2 | Buttons on `/alert list` |
| `/alert channel` | 1 | `/setup` → Channels |
| `/audit log, search, stats` | 3 | `/audit` with filter options |
| `/audit export` | 1 | Button on `/audit` |
| `/audit channel` | 1 | `/setup` → Channels |
| `/backup list, create, delete` | 3 | `/backups` with buttons |
| `/bannergroup create_group, info, rename, add, remove, delete_group` | 6 | `/setup` → Banners |
| `/banner background, settings` | 2 | `/setup server` → Banner |
| `/banner_settings auto_update, type, auto_remove, timeformat, timezone` | 5 | `/setup` → Banners |
| `/config export, import` | 2 | `/admin export`, `/admin import` |
| `/diagnostics` | 1 | `/admin diagnostics` |
| `/help`, `/about` | 2 | `/help` |
| `/link start, status, clear` | 3 | `/link` |
| `/player channel` | 1 | `/setup` → Channels |
| `/player online` | 1 | `/server status` |
| `/player lookup, top` | 2 | `/player lookup`, `/player top` |
| `/poll create, list` | 2 | `/poll create`, `/poll list` |
| `/poll close, results, delete` | 3 | Buttons on the poll and on `/poll list` |
| `/schedule create, list` | 2 | `/schedule create`, `/schedule list` (on AMP's scheduler) |
| `/schedule delete, pause, resume, run` | 4 | Buttons on `/schedule list` |
| `/whitelist` (server whitelist on/off) | 1 | `/setup server` → Whitelist |
| `/whitelist add, remove` | 2 | `/whitelist add`, `/whitelist remove` |
| `/whitelist request_channel, wait_time, auto, donator_bypass, require_link` | 5 | `/setup` → Whitelist |
| `/whitelist_reply add, remove, list` | 3 | `/setup` → Whitelist |
| `/whitelist_request` | 1 | `/whitelist request` |
| **Total** | **117** | **23 slash commands + 1 user menu** |

---

## Appendix B: full candidate command list

Everything we might want, and what we probably don't need. It covers every V3 command (Appendix A shows where each one lands) plus new ideas that came out of AMP 3's API sections and the template data. **None of this is final until it has been checked against the real API.** The last column says what to look for.

- **Keep**: part of the 23-command set in section 5.
- **Optional**: not in the 23. Add only if members ask for it or the API makes it cheap.
- **Drop**: not carried over, or an idea we recommend skipping.
- **Budget**: stay at 25 slash commands or fewer. A new slash command has to replace or fold into an existing one, or become a button or panel field instead.

Count: 23 slash commands and one user menu to keep, 18 optional ideas (only some of them slash commands), 13 drops, and 4 owner-only text commands.

### B.1 Keep: the core set

| Command | Surface | Status | Why | Confirm against the API |
|---|---|---|---|---|
| `/server status [server]` | slash | Keep | The command members use most. Lists all servers, or one with its players and metrics. | Q-5 live state, metrics, player list |
| `/server power <server> <action>` | slash | Keep | Start, stop, restart, kill in one command. | Q-5 endpoints and capability names |
| `/server send <server> <text> [as]` | slash | Keep | Console command, in-game chat or broadcast. | Q-4 console input, Q-6 chat input |
| `/backups <server>` | slash | Keep | List, create and delete backups as buttons. | Q-8 backup endpoints |
| `/whitelist request` | slash | Keep | Members ask for access. | Q-6 whitelist through a game profile or the API |
| `/whitelist add` | slash | Keep | Moderators add a name. | Q-6 |
| `/whitelist remove` | slash | Keep | Moderators remove a name. | Q-6 |
| `/link` | slash | Keep | Proves a member owns an in-game name, so whitelist requests can't claim someone else's. | Q-4 and Q-6 chat or event feed |
| `/player lookup <name>` | slash | Keep | Who a player is, when last seen. | Q-6 join/leave events |
| `/player top [server]` | slash | Keep, cut first | Playtime ranking. Nice to have, not essential. | Q-6 |
| `/poll create` | slash | Keep, cut first | Community polls. Not AMP-specific, so it is the easiest to drop if the set must shrink. | none |
| `/poll list` | slash | Keep, cut first | Same as above; results and close are buttons. | none |
| `/schedule create` | slash | Keep | Creates a schedule on AMP's own scheduler. | Q-7 triggers and tasks catalog |
| `/schedule list` | slash | Keep | Run, enable/disable and delete are buttons. | Q-7 |
| `/alert create` | slash | Keep | Metric thresholds that post to a channel. Might merge into `/schedule` if AMP 3 triggers can do thresholds. | Q-7 |
| `/alert list` | slash | Keep | Mute and delete are buttons. | Q-7 |
| `/audit [filters]` | slash | Keep | Who did what from Discord. Export is a button. | Q-11 can the bot write to AMP's audit log |
| `/user <member>` | slash | Keep | Info and role for a member. | none |
| Apps → Portcullis user | user menu | Keep | Same as `/user`, from a right-click. | none |
| `/setup [server]` | slash | Keep | One panel for every setting. | none |
| `/admin diagnostics` | slash | Keep | Missing capabilities per shard, connection state, clean-up button. | Q-2, Q-3 capabilities, Q-10 rate limits |
| `/admin export` | slash | Keep | Config backup. | none |
| `/admin import` | slash | Keep | Config restore and V3 database import. | none |
| `/help` | slash | Keep | Lists only what the caller can run. Replaces `/about`. | none |

### B.2 Optional: possible additions

| Idea | Surface | Status | Why or why not | Confirm against the API |
|---|---|---|---|---|
| `/server console [lines]` | slash or button | Optional | Shows recent console lines without needing a relay channel. Could be a button on status instead. | Q-4 can history be read |
| CPU, RAM and TPS on `/server status` | status field | Optional | Useful, costs no command. | Q-5 |
| "Update game" button on status | button | Optional | Runs the game's update. | Q-5 is there an action or task |
| "Restore" button on `/backups` | button | Optional | Destructive, so it needs a confirmation step and its own permission node. | Q-8 restore endpoint |
| `/whitelist list <server>` | slash | Optional | Shows who is whitelisted. Only works for games with a profile. | Q-6 |
| `/kick`, `/ban`, `/unban` | one slash with an action option | Optional | Only 1 of 244 templates defines kick or ban actions, so each game would need a profile entry. | Q-6 |
| `/notify <server>` | slash | Optional | A member asks to be pinged when a server comes online or restarts. Stored in Portcullis's database only. | Q-4 state-change events |
| `/vote restart` | slash | Optional, not recommended | A poll that restarts a server if it passes. Fun, but overlaps `/poll` and `/server power`. | none |
| `/access` | slash | Optional | Read-only view of what the bot's AMP account can do per shard. Writing AMP roles from Discord is riskier and should wait. | Q-2, Q-3 |
| AMP audit entries inside `/audit` | filter | Optional | Shows AMP's own log next to Portcullis's. | Q-11 |
| Spires and datastores in `/admin diagnostics` | diagnostics field | Optional | Read-only host and storage status. | A-2 |
| Schedule run history and next-fire preview | buttons on `/schedule list` | Optional | The API already has both. | Q-7 |
| Quick console presets | buttons | Optional | Admin-defined one-click commands such as "save world", set in `/setup`. | Q-4 |
| Permission editor in `/setup` | panel section | Optional | Edit `bot_perms.json` nodes in Discord. The file stays the source of truth. | none |
| Banner groups | panel section | Optional | V3 needed 6 commands for them. One banner per channel may be enough. | none |
| Custom banner background upload | panel field | Optional | Start with presets and colours. | none |
| Custom whitelist reply messages | panel field | Optional | Low value. | none |
| "Download latest log" button | button | Optional | A safer slice of the file manager. | Q-8 |

### B.3 Drop: not carried over, or not recommended

| Command or idea | Status | Why |
|---|---|---|
| `/bot utils roleid`, `channelid`, `userid` | Drop | Discord's developer mode already gives these. |
| `/bot utils steamid`, `uuid` | Drop | The lookup happens inside `/link` and `/whitelist`. |
| `/bot utils clear` | Drop | Discord's own moderation tools do this, and a purge command is a risk. |
| `/dbserver change_instance_id` | Drop | Shard UUIDs are stable. |
| `/server update` (V3's refresh) | Drop | The registry refreshes automatically. |
| `/about` | Drop | Folded into `/help`. |
| Separate `start`, `stop`, `restart`, `kill`, `msg`, `broadcast` | Drop | Folded into `/server power` and `/server send`. |
| Bot's own scheduler | Drop | AMP 3 has one (section 6). |
| Manage AMP users, roles and passwords from Discord | Drop | AMP's panel does it, sign-in is passkey based, and a Discord bug here would be a security problem. |
| File manager commands (browse, edit, upload) | Drop | Security and size limits, and AMP's panel does it better. |
| Create or delete shards from Discord | Drop | Destructive and capability-heavy. Revisit only if members ask. |
| Networking and tunnel management | Drop for now | We don't know yet what AMP 3's Networking section does; no open question covers it yet, so look at it on day one. |
| AI agent or "ask AMP" commands (AMP's "Generate MCP") | Drop | Not Portcullis's job. |

### B.4 Owner-only text commands (not in the picker)

| Command | Status | Why |
|---|---|---|
| `$gk sync` | Keep | Pushes the slash command list to Discord. |
| `$gk ping`, `$gk status` | Keep | Quick health check. |
| `$gk restart`, `$gk stop` | Keep | Owner can restart or stop the bot. |
| `$gk reload` | Optional | Only if the new design supports hot-reloading; probably not needed. |
