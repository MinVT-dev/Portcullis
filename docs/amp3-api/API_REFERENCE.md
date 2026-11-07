# AMP 3 API reference

**Build covered:** AMP 3.0.0 preview, `amp3-host 3.0.0.6+f3d1a77cea`, on Linux. **Checked:** 7 Nov 2026, on a throwaway test VM. **Scope:** every API AMP 3 exposes:

- REST: 219 paths and 263 operations
- SignalR: 3 hubs
- The internal NATS message bus
- Schedule webhooks
- Public endpoints, and paths that look like endpoints but aren't

This file is generated from CubeCoders' own OpenAPI spec, which ships on disk with the panel. It is merged with live results from calling **all 263 operations** on the VM. Where the spec and reality differ, both are shown.

**Markers:** **[V]** verified live on the VM · **[S]** stated in the shipped OpenAPI spec · **[B]** seen only in the panel's JavaScript · **[I]** inferred.

**Contents**
1. Quick start
2. Base URL and transport
3. Request headers
4. Response headers
5. Authentication
6. Errors and status codes
7. Permissions (capability nodes)
8. Realtime: SignalR hubs
9. Schedules, triggers and webhooks
10. Message bus, public endpoints, things that are not APIs, and how this was checked
11. Endpoint index
12. Endpoint reference (all 263 operations)
13. Data types (all 389 schemas)

---

## 1. Quick start

```http
POST /api/Auth/login
Content-Type: application/json

{"username":"portcullis","password":"…","rememberMe":false}
```
→ `200 {"token":"<JWT>","expiration":"<UTC time about 8 h from now>"}` **[V]**

```http
GET /api/Shards
Authorization: Bearer <JWT>
```
→ `200 [ …shards… ]` **[V]**

```http
POST /api/Shards/{id}/console
Authorization: Bearer <JWT>
Content-Type: application/json

{"command":"say hello"}
```
→ `202`. The command is published to the shard over the message bus **[S]**. A `{}` body gets 400 `Command` required **[V]**.

Live events: `POST /api/auth/hub-token` returns a single-use ticket. Then connect SignalR to `/hubs/shard?access_token=<ticket>` and call `JoinShard(shardId)`. See section 8.

## 2. Base URL and transport

- **[V]** One HTTP listener on port **8443** (ASP.NET Kestrel) serves the panel UI, REST under `/api/` and SignalR under `/hubs/`. It is plain HTTP by default. AMP can set up HTTPS itself with Caddy and Let's Encrypt (`/api/https/*`), or sit behind your own proxy (`/api/https/own-proxy`).
- **[V]** Route matching ignores case. The spec mixes `/api/Shards/...` and `/api/shards/...`, and both work.
- **[V]** There is no API version in the URL. The spec calls itself `webapi | v1`.
- **[V]** IDs are GUIDs (shards, spires, users, roles, schedules, snapshots, datastores). A few are strings: template `slug`, `portRef`, settings `key`, and `tenantId` (which is `_root` on a single install).
- **[V]** Unknown paths outside `/api` return the panel's HTML page with 200, not 404 (see section 10.3).
- **[S]** Responses are JSON (`application/json; charset=utf-8`). Some 64-bit integers are allowed to arrive as strings (`"type": ["integer","string"]`). Parse them leniently.

## 3. Request headers

| Header | When | Notes |
|---|---|---|
| `Authorization: Bearer <JWT>` | Every non-public `/api` call | **[V]** Missing or invalid → `401`, empty body, `WWW-Authenticate: Bearer`. |
| `Content-Type: application/json` | Every call with a body | **[S]** 82 operations take JSON. The spec also accepts `text/json` and `application/*+json`. One file-upload operation takes form data. |
| `Accept: application/json` | Optional | **[V]** Not needed. JSON is the default. |
| `Origin` | Browsers only | **[V]** `OPTIONS` preflight on `/api/Shards` returns 404. There is no CORS, so a browser on another origin can't call the API. A server-side bot is unaffected. |

No API-key header, tenant header or version header exists **[S][V]**.

## 4. Response headers

Seen on `/api` responses **[V]**:

| Header | Value |
|---|---|
| `Server` | `Kestrel` |
| `Content-Type` | `application/json; charset=utf-8` (an empty 401 has none) |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Content-Security-Policy-Report-Only` | `default-src 'self'; … connect-src 'self' ws://<panel-host>:8443 wss://<panel-host>:8443 https://metrics.c7rs.com; … report-uri /api/csp-report` |
| `WWW-Authenticate` | `Bearer` (on 401 only) |
| `Transfer-Encoding` | `chunked` (large JSON) |

There are **no rate-limit headers** (`X-RateLimit-*`, `Retry-After`) on normal calls **[V]**. The spec mentions rate limiting only for `POST /api/csp-report` **[S]**. Portcullis should still throttle itself.

## 5. Authentication

### 5.1 Login, renew and logout
- **[V]** `POST /api/Auth/login` takes `{username, password, rememberMe?}` and returns `{token, expiration}`.
  - The token is an **HS256 JWT**. It lasts about **8 hours** with `rememberMe:false` ("the working day, renews while the app is open" **[S]**).
  - With `rememberMe:true` it is "a long-lived session that survives closing the browser" **[S]**.
  - Claims: `nameidentifier`, `name`, `emailaddress`, `jti`, `sid` (session id), `exp`, `iss: amp-auth` **[V]**.
- **[V]** `POST /api/Auth/renew` (Bearer, no body) returns a new `{token, expiration}`. A 401 means you must log in again.
- **[S]** `POST /api/Auth/logout` ends the session. `GET /api/Profile/sessions` lists sessions. `DELETE /api/Profile/sessions/{id}` and `DELETE /api/Profile/sessions/others` revoke them.
- **[S]** `POST /api/Auth/reauth` confirms the password before sensitive actions.

### 5.2 Second factor, passkeys and expired passwords
- **[S]** If login needs 2FA, the response tells you so. Finish with `POST /api/Auth/login/second-factor` (`Code` required **[V]**). Recovery codes come from `/api/profile/recovery-codes`.
- **[V]** Passkeys (`/api/auth/passkey/begin|complete|abandon|reauth`, `/api/profile/passkeys/*`) need HTTPS. On plain HTTP, `begin` returns `409 passkeys.origin.notConfigured`. `GET /api/auth/capabilities` (public) reports the passkey status.
- **[S]** `POST /api/Auth/change-expired-password` is for accounts whose password has expired. `GET /api/Auth/password-policy` (public) returns the rules.

### 5.3 Hub tickets (SignalR)
- **[V]** `POST /api/auth/hub-token` (Bearer) returns `{token, expiresAt}`. The ticket lasts about **90 s** and is **accepted once** **[S][V]**. A second negotiate with the same ticket gets 401, on any hub.
- **[V]** Pass it **only** as `?access_token=` on `/hubs/{shard|spire|tasks}/negotiate` and the connect URL. As an `Authorization` header it gets 401. The main JWT gets 401 on hubs.
- Get a new ticket for every connect and every reconnect.

### 5.4 What doesn't exist
- **[S][V]** There are no inbound API keys, personal access tokens, OAuth clients or service accounts. "ApiKey" only appears as a kind of stored *outbound* credential (`/api/credentials`).
- A bot logs in as an ordinary user. Make it a dedicated user with a role that holds only the nodes it needs (section 7).
- **[V]** There is no OIDC. `/.well-known/openid-configuration` is the HTML fallback.

## 6. Errors and status codes

Three error body shapes occur **[V]**:

1. **Validation (RFC 9110 ProblemDetails):**
   ```json
   {"type":"https://tools.ietf.org/html/rfc9110#section-15.5.1","title":"One or more validation errors occurred.","status":400,"errors":{"Command":["The Command field is required."]}}
   ```
2. **AMP `ApiError`:** `{"code":"spire.error.unreachable","params":null,"message":"No spire host answered. The target spire may be offline."}`. `code` is a stable, dotted, machine-readable key. `message` is English fallback text. `params` fills placeholders.
3. **Analytics:** `{"error":"analytics.error.badAddress"}`.

| Status | Meaning in AMP 3 | Seen |
|---|---|---|
| 200 / 201 / 202 / 204 | OK / created / queued (console, power) / nothing to report | **[V]** |
| 400 | Validation failed, or a bad query value | **[V]** |
| 401 | No token, bad token, or expired token (empty body) | **[V]** |
| 403 | Signed in but missing the capability node | **[S]** (admin was used, so not seen) |
| 404 | Unknown ID (ProblemDetails "Not Found" or an `ApiError`) | **[V]** |
| 409 | Conflict: e.g. `passkeys.origin.notConfigured`, `shards.error.steamLoginRequired` | **[V]** |
| 503 | Setup routes after setup is done (`auth.setup.code.unavailable`). Spire-wide actions aimed at an unknown spire (`spire.error.unreachable`) | **[V]** |

**Watch out [V]:**
- `DELETE /api/Spires/{id}/datastores/{datastoreId}` returned **200 for IDs that don't exist**.
- `PUT /api/Spires/{id}/usage/interval` returned 200 for an unknown spire.

So a 200 does not prove that the object existed. Check that it exists first.

## 7. Permissions (capability nodes)

- **[V]** `GET /api/me/permissions` returns `{grants, catalog}`. The admin's grant is `">"`, which means everything.
- Nodes are dotted, NATS-style strings with three scopes:
  - **Shard:** granted per shard.
  - **Spire:** granted per host.
  - **Platform:** granted install-wide.
- `pathScopable` nodes can be limited to file paths. `dangerous` nodes are flagged in the UI. `byNameOnly` nodes are never covered by a wildcard and must be granted by name.
- Roles are managed with `/api/roles` and assigned to users with `/api/users/{id}`.

All 71 nodes **[V]**:

| Node | Scope | Dangerous | Path-scopable | By name only |
|---|---|---|---|---|
| `view` | Shard |  |  |  |
| `power.start` | Shard |  |  |  |
| `power.stop` | Shard |  |  |  |
| `power.restart` | Shard |  |  |  |
| `power.kill` | Shard | yes |  |  |
| `update.run` | Shard |  |  |  |
| `update.abort` | Shard |  |  |  |
| `delete` | Shard | yes |  |  |
| `clone` | Shard |  |  |  |
| `move` | Shard |  |  |  |
| `suspend` | Shard | yes |  |  |
| `suspend.bypass` | Shard | yes |  | yes |
| `console.read` | Shard |  |  |  |
| `console.send` | Shard |  |  |  |
| `settings.read` | Shard |  |  |  |
| `settings.write` | Shard |  |  |  |
| `infra.read` | Shard |  |  |  |
| `infra.write` | Shard |  |  |  |
| `infra.limits` | Shard | yes |  |  |
| `ports.read` | Shard |  |  |  |
| `ports.write` | Shard |  |  |  |
| `files.read` | Shard |  | yes |  |
| `files.write` | Shard |  | yes |  |
| `files.delete` | Shard | yes | yes |  |
| `files.bypassrestrictions` | Shard | yes |  |  |
| `snapshots.read` | Shard |  |  |  |
| `snapshots.create` | Shard |  |  |  |
| `snapshots.restore` | Shard |  |  |  |
| `snapshots.delete` | Shard | yes |  |  |
| `schedule.read` | Shard |  |  |  |
| `schedule.write` | Shard |  |  |  |
| `analytics.read` | Shard |  |  |  |
| `analytics.users` | Shard |  |  |  |
| `analytics.addresses` | Shard |  |  |  |
| `view` | Spire |  |  |  |
| `shards.create` | Spire |  |  |  |
| `settings.read` | Spire |  |  |  |
| `settings.write` | Spire |  |  |  |
| `startup.read` | Spire |  |  |  |
| `startup.write` | Spire |  |  |  |
| `datastores.read` | Spire |  |  |  |
| `datastores.write` | Spire |  |  |  |
| `discover` | Spire | yes |  |  |
| `delete` | Spire | yes |  |  |
| `spire.create` | Platform |  |  |  |
| `audit.read` | Platform |  |  |  |
| `platform.settings.read` | Platform |  |  |  |
| `platform.settings.write` | Platform |  |  |  |
| `templates.manage` | Platform |  |  |  |
| `platform.debug` | Platform |  |  |  |
| `platform.support` | Platform |  |  |  |
| `platform.update.check` | Platform |  |  |  |
| `platform.update.apply` | Platform | yes |  |  |
| `users.read` | Platform |  |  |  |
| `users.create` | Platform |  |  |  |
| `users.edit` | Platform |  |  |  |
| `users.suspend` | Platform | yes |  |  |
| `users.delete` | Platform | yes |  |  |
| `users.resetpassword` | Platform | yes |  |  |
| `users.invite` | Platform |  |  |  |
| `roles.read` | Platform |  |  |  |
| `roles.create` | Platform |  |  |  |
| `roles.edit` | Platform |  |  |  |
| `roles.delete` | Platform | yes |  |  |
| `roles.assign` | Platform |  |  |  |
| `network.read` | Platform |  |  |  |
| `network.manage` | Platform | yes |  |  |
| `licence.read` | Platform |  |  |  |
| `licence.manage` | Platform |  |  |  |
| `webhooks.internal` | Platform | yes |  |  |
| `credentials.manage` | Platform | yes |  |  |

**Suggested Portcullis role [I]:**
- Shard: `view`, `power.start`, `power.stop`, `power.restart`, `console.read`, `console.send`, `analytics.read`, `analytics.users`, `schedule.read`.
- Add `schedule.write` only if the bot sets up its own webhooks (section 9).
- Avoid every node marked dangerous.

## 8. Realtime: SignalR hubs

| Hub | Negotiate | Client → server **[B]** | Server → client events **[B]** |
|---|---|---|---|
| `/hubs/shard` | **[V]** 200 with a fresh ticket. Transports: WebSockets, ServerSentEvents, LongPolling | `JoinShard(shardId)`, `LeaveShard(shardId)` | `ConsoleLine`, `Metrics`, `UserJoined`, `UserLeft`, `UserRoster`, `ShardStateChanged`, `Lifecycle`, `ScheduleRun`, `Snapshot`, `Migration` |
| `/hubs/spire` | **[V]** 200 with a fresh ticket | `JoinSpire`, `LeaveSpire`, `JoinNetwork`, `LeaveNetwork` | `SpirePresence`, `SpireState`, `NetworkStatus` |
| `/hubs/tasks` | **[V]** 200 with a fresh ticket | none | `TaskUpsert` |

- Event payloads were **not** captured live. The REST twins show the shapes:
  - `ConsoleLine` ≈ [ConsoleLineDto](#schema-consolelinedto): `{streamId, text, timestamp, seq, kind, state, code, params, level, origin}`. `seq` is a per-shard sequence that merges history with the live feed.
  - `Metrics` ≈ [ShardMetricsSample](#schema-shardmetricssample).
  - `ShardStateChanged` ≈ [ShardState](#schema-shardstate).
- **REST fallbacks [V]:**
  - `GET /api/Shards/{id}/console/history?limit=N`
  - `GET /api/Shards/{id}/users` (current roster)
  - `GET /api/Shards/{id}/state`
  - `GET /api/Shards/{id}/metrics`
- **Shard status [V]:** a number ([ApplicationState](#schema-applicationstate), "stopped through configuring, running, sleeping and failure"). The running Veloren shard read **20**, and later **25**. The spec doesn't list the values. Map them by watching transitions.

## 9. Schedules, triggers and webhooks

- **[V]** `GET /api/shards/{shardId}/schedules/catalog` lists everything a schedule can react to and do. Schedules are created with `POST /api/shards/{shardId}/schedules` ([ScheduleBody](#schema-schedulebody)). A schedule needs a trigger: cron, interval or event.
- **Webhooks in AMP 3 are a schedule task (`flow.webhook`), not a subscription API.** For example: trigger `player.joined` → task `flow.webhook {url, body}`, with template variables like `{{User}}`. This is how Portcullis can get push events without holding a SignalR connection **[I]**.

**Event triggers [V]:**

| Trigger | Category | Emits |
|---|---|---|
| `player.joined` | players | `User`, `UserID` |
| `player.left` | players | `User`, `UserID` |
| `app.stateChanged` | application | `NewState` |
| `app.updateComplete` | application | `Success`, `Version` |
| `app.error` | application | `Message` |

**Tasks [V]:**

| Task | Category | Async | On failure | Parameters |
|---|---|---|---|---|
| `power.start` | power | yes | Abort | none |
| `power.stop` | power | yes | Abort | none |
| `power.restart` | power | yes | Abort | none |
| `power.kill` | power |  | Abort | none |
| `power.update` | power | yes | Abort | none |
| `app.consoleSend` | app |  | Continue | `command` (template, required) |
| `backup.create` | backups | yes | Abort | `name` (template) |
| `flow.wait` | flow | yes | Continue | `seconds` (number, required) |
| `flow.webhook` | flow |  | Continue | `url` (template, required), `body` (template) |

**Template variables every trigger provides [V]:** `Time`, `TriggerId`, `TriggerName`, `ShardName`, `State`, `UserCount`, `MaxUsers`, `CPUUsage`, `RAMUsage`. Cron and interval triggers also exist ([TriggerCronTrigger](#schema-triggercrontrigger), [TriggerIntervalTrigger](#schema-triggerintervaltrigger)). Steps can branch with if, switch and stop ([Step](#schema-step)).

## 10. Message bus, public endpoints, non-APIs, and method

### 10.1 NATS message bus (internal) [V]
- `nats-server` 2.15.2 listens on **4222**. It uses TLS with AMP's own CA ("AMP Bus CA") and operator JWT auth, with JetStream on and a 1 MB max payload. There is no HTTP monitoring port.
- AMP uses it between the panel, the hosts and the shard agents. Portcullis **shouldn't** connect: it would need AMP's own bus credentials, and REST plus SignalR cover everything.
- Streams: `console` (`amp.tn.*.sr.*.ev.con.main|update|agent`, `…ev.life`, 14 days), `jobs` (`amp.tn.*.job.>`), `audit` (1 year), `analytics` (2 years), `analytics-rollup`, `analytics-addresses` (90 days), `analytics-flows` (6 hours), `log`, and KV buckets `KV_amp`, `KV_amp_geo`, `KV_identity`, `KV_amp_sr_<shardId>`. There are 12 streams and 27 consumers once one shard exists.
- **[S]** Other subjects: `amp.tn.{tenant}.sr.{shardId}.ev.con.input` (console input), `…ev.metrics`, `amp.tn.{t}.sched.{id}[.status]`, `amp.tn.{t}.usr.{user}…` and `amp.tn.{t}.sp.{spire}…`. `tn` = tenant, `sr` = shard, `sp` = spire.
- Bus stats for admins: `GET /api/Support/message-bus-stats`. Join info for adding hosts: `GET /api/System/bus`.

### 10.2 Public endpoints (no token) [V]
| Endpoint | Returns |
|---|---|
| `GET /api/Health/status` | `{phase:"ready", message, preview:{expiresAt, daysRemaining, reason, update}, messageParams, plainText}`. `phase` is the liveness signal. `preview.expiresAt` is when this preview build stops working (7 Dec 2026 on this VM). `plainText:true` = served over HTTP **[I]** |
| `GET /api/branding` | Product name, logo and theme |
| `GET /api/Auth/password-policy` | Password rules |
| `GET /api/auth/capabilities` | `{passkeysAvailable, passkeyStatus, passkeyMessageKey, rpId, expectedOrigin, passkeyPolicy}` |
| `POST /api/Auth/login`, `/login/second-factor`, `/change-expired-password` | Sign-in flow |
| `POST /api/auth/passkey/begin`, `/complete`, `/abandon` | Passkey sign-in |
| `POST /api/invites/preview`, `/api/invites/accept` | Invite flow |
| `POST /api/csp-report` | Browser CSP reports (204) |
| `/api/Auth/setup/*` | First-run wizard. Returns 503 `auth.setup.code.unavailable` once setup is finished |

Every other `/api` operation returned **401 without a token**. That is 240 of the 257 calls made without a token.

### 10.3 Things that look like APIs but aren't [V]
- **The OpenAPI document isn't served.** `/openapi/v1.json`, `/openapi/v1.yaml` and `/swagger/v1/swagger.json` give 401, then 404 with a token. `/swagger`, `/scalar`, `/scalar/v1` and `/api/dev/options` give 404. The spec only exists on disk: `/opt/cubecoders/amp3/data/versions/3.0.0/webapi/webapi.json`. The spec mentions a "Developer" tag that is compiled out of release builds **[S]**.
- `/health`, `/healthz`, `/metrics` and `/.well-known/openid-configuration` return **200 HTML**: the panel's catch-all page. They are not Prometheus, health or OIDC endpoints.
- **There is no AMP 2 API.** `/API/Core/GetStatus` and `/API/ADSModule/GetInstances` return 404. AMP 2 clients (GatekeeperV2's included) won't work.
- **Other ports:**
  - **2222** is SFTP into shard files, with the same user accounts. Connection details come from `GET /api/Shards/{id}/sftp`.
  - **4222** is NATS (internal).
  - Game ports (e.g. 14004-14005 for Veloren) belong to the game itself.
- **Outbound calls AMP makes:**
  - `POST https://metrics.c7rs.com/api/event` from the panel on every page view (analytics, can't be turned off in the preview).
  - `https://cdn-downloads.c7rs.com/AMP3/manifest.json(.p7s)` for update checks.
  - `cdn-repo.c7rs.com` git repos for templates.

### 10.4 How this was checked
- **Spec:** every operation, parameter, body and response below comes from the shipped `webapi.json` (OpenAPI 3.1.1). It is the authoritative contract for this build.
- **No-token pass:** 257 of the 263 operations were called without a token. Made-up IDs were used, plus `{}` for bodies. The 6 POSTs to `/api/Auth/setup/*` were left out, because they only work before setup.
- **Token pass:** 206 calls with an admin token.
  - **GET (110 of 111):** called with the real shard, spire, user and datastore IDs. Only `GET /api/Support/diagnostics/bundle` was skipped, because it builds a support archive.
  - **Write calls (96):** 93 aimed at a **made-up ID**, so they could only fail with 400, 404 or 503, or no-op. The other 3 were harmless calls with no ID: `Auth/renew`, `auth/hub-token` and `csp-report`. Afterwards the real datastore, the spire settings and the shard were re-read and found unchanged.
  - **Not called with a token (56 write calls with no ID):** these include login, create shard, create user, apply update, HTTPS setup, licence activate, logout others and regenerate recovery codes, because calling them would change the install. Their behaviour comes from the spec. They are marked "not called" in the reference.
- The made-up ID is `11111111-2222-4333-8444-555555555555`. No passwords, tokens or keys appear in this document.

### 10.5 Gotchas
- **The preview expires:** `Health/status.preview.expiresAt`. Have the bot warn as the date gets close.
- **"API uptime" is wrong in the preview.** The System page showed 0m and diagnostics reported `uptimeSeconds:114` while the process had been up about 21 minutes with no restarts.
- **Steam prompt bug:** the panel's deploy dialog asks for a Steam login on non-Steam templates. `POST /api/Shards` doesn't.
- **Plain HTTP by default:** logins travel in clear text and passkeys are unavailable. Put AMP behind HTTPS before pointing a bot at it over the internet.
- **Slow call:** `GET /api/Templates` took about 17 s on the first call (catalogue load). Give it a long timeout.


## 11. Endpoint index

Every operation, grouped as the spec groups them. Click through for full details.

- **[Authentication & Setup](#12-authentication--setup)** (23)
- **[System & Health](#12-system--health)** (10)
- **[My Account](#12-my-account)** (14)
- **[Profile](#12-profile)** (7)
- **[Users & Access](#12-users--access)** (18)
- **[Credentials](#12-credentials)** (5)
- **[Audit Log](#12-audit-log)** (5)
- **[Shards](#12-shards)** (51)
- **[Files & Backups](#12-files--backups)** (30)
- **[Scheduling](#12-scheduling)** (10)
- **[Analytics](#12-analytics)** (11)
- **[Spires & Datastores](#12-spires--datastores)** (19)
- **[Spires](#12-spires)** (6)
- **[Import](#12-import)** (4)
- **[Networking](#12-networking)** (22)
- **[Templates](#12-templates)** (6)
- **[Tasks](#12-tasks)** (5)
- **[Monitoring](#12-monitoring)** (3)
- **[Platform Settings](#12-platform-settings)** (6)
- **[Licensing](#12-licensing)** (6)
- **[Branding & Appearance](#12-branding--appearance)** (1)
- **[Security](#12-security)** (1)

## 12. Endpoint reference

Each entry gives: what it does, the permission the spec says it needs, path and query parameters, the request body, the documented responses, and **Live**: what the VM actually returned on 7 Nov 2026. "No token" is a call without `Authorization`. "Token" is an admin call. GETs used the real shard and spire IDs. Write calls used a made-up ID so they could not change anything (see section 10).

### 12-Authentication & Setup

_Signing in, the first-run setup wizard, and the database and message-bus provisioning steps._

#### `GET /api/auth/capabilities`

**Get the sign-in capabilities for this installation and origin**

Evaluated against the caller's own origin, so the same installation can answer differently
to a browser on its public hostname and one on its LAN address. Not cached for that reason.

**Responses:**

- `200` The current capabilities → [AuthCapabilitiesDto](#schema-authcapabilitiesdto) (`text/plain`)

**Live:** no token → `200` · token → `200`

---

#### `POST /api/Auth/change-expired-password`

**Change an expired password and sign in**

The self-service path for a user held out of login by a required password change: they
prove identity with their CURRENT password and set a new one, then continue exactly as
Login would have - straight in, or on to the second factor the account owes. Every
other session the account had is revoked, as any password change does. Anonymous
because a held user has no session to authenticate the change with.

**Request body** (required, `application/json`): [ChangeExpiredPasswordRequest](#schema-changeexpiredpasswordrequest) — The username, current password, and desired new password

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | yes | The account's username |
| `currentPassword` | string | yes | The current password, used to prove identity since the user has no session |
| `newPassword` | string | yes | The replacement password to set |

**Responses:**

- `200` The password was changed: either a session token, or the same `PasskeyRequired` marker Login returns
- `400` The account does not require a password change, or the new password failed policy → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `401` Invalid credentials, or the account is suspended → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `429` This address has had too many failed sign-in attempts → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `400` validation: `Username` required · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/auth/hub-token`

**Issue a hub ticket**

Bound to the caller's current session: the hub connection it opens is re-validated against
that session for as long as it lives, and ends when the session does. Single use and valid
for sixty seconds, so the client fetches a fresh one for every connect and reconnect.
Not audited: it is a mechanical derivation of an existing sign-in, which is already recorded.

**Responses:**

- `200` A ticket → [HubTokenResponse](#schema-hubtokenresponse) (`text/plain`)
- `401` Not authenticated, or the token presented carries no session → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Auth/login`

**Log in and issue a session token**

Anonymous. Unknown-user and wrong-password cases are deliberately indistinguishable (same 401,
same message, matched hash-verification timing); the distinction lives only in the audit log.
If the account is valid but held pending a password change (admin-forced or policy-expired),
no token is issued: the 200 body carries `PasswordChangeRequired` and a `Reason`, and
the client should run the forced-change flow.

**Request body** (required, `application/json`): [LoginRequest](#schema-loginrequest) — The login credentials

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | yes | The account's username |
| `password` | string | yes | The account's password |
| `rememberMe` | boolean |  | The user ticked "stay signed in": issue a long-lived session that survives closing the browser. Left false, the session lasts the working day and renews while the app is open. Default `false`. |

**Responses:**

- `200` Either an issued session token, a `PasswordChangeRequired` marker directing the client to the forced-change flow, or a `PasskeyRequired` marker when the account must sign in with its passkey
- `401` Invalid credentials, or the account is suspended → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `429` This address has had too many failed sign-in attempts → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `400` validation: `Password` required · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/Auth/login/second-factor`

**Finish a sign-in with a recovery code in place of the passkey it owes**

A recovery code is the way back in when passkeys are REQUIRED and the passkey is gone.
It is single-use, and spending one is audited at Security severity because it is either
a bad day or an intrusion, and both want looking at.

**Request body** (required, `application/json`): [SecondFactorRequest](#schema-secondfactorrequest) — The original credentials plus the recovery code

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | yes | The account's username |
| `password` | string | yes | The same password from the first step, re-presented so this call stands on its own |
| `code` | string | yes | One recovery code |
| `rememberMe` | boolean |  | Carried through from the first step Default `false`. |

**Responses:**

- `200` An issued session token, or a `PasswordChangeRequired` marker when the account is held pending a password change
- `401` The password or the code was wrong, or the account is suspended → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `429` This address has had too many failed sign-in attempts → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `400` validation: `Code` required · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/Auth/logout`

**Sign out of the current session**

Revokes the caller's OWN session server-side, so the token they were holding stops
working the moment they sign out rather than lingering until it expires. Idempotent: a
session that is already gone still answers 204, because the caller's intent is met
either way. Open to an enrolment-only session, which must have a way to give up.

**Responses:**

- `204` Signed out
- `401` Not authenticated → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/auth/passkey/abandon`

**Abandon a passkey sign-in**

Releases a ceremony the browser aborted, such as the inline autofill prompt a password
sign-in dismisses, so it stops counting against its address before it would expire. Answers
204 whether or not the flow existed, so it says nothing about which ceremonies are live.
Not audited: it only discards a pending challenge, and an anonymous endpoint that audits
would let anyone write to the audit log at will.

**Request body** (required, `application/json`): [AbandonRequest](#schema-abandonrequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `flowId` | string (uuid) | yes | The handle returned by the begin call |

**Responses:**

- `204` The ceremony is gone, or never existed

**Live:** no token → `204` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/auth/passkey/begin`

**Begin signing in with a passkey**

Takes no username. The returned options carry an empty allow-list, so the browser offers
whatever discoverable credential it holds for this installation. Refused with 429 for an
address the sign-in throttle has blocked, on the same terms as password login, and for an
address that already has as many ceremonies in progress as one address may hold. Reaching
that cap is not a failed sign-in: every login page view begins a ceremony, so counting it
would let ordinary browsing behind one address lock that address out of password sign-in too.

**Responses:**

- `200` Assertion options and the flow handle
- `409` Passkeys are unavailable here; the body carries the reason → [ApiError](#schema-apierror) (`text/plain`)
- `429` This address has had too many failed sign-in attempts, or has too many ceremonies in progress → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `409` passkeys.origin.notConfigured · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/auth/passkey/complete`

**Finish signing in with a passkey**

**Request body** (required, `application/json`): [CompleteRequest](#schema-completerequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `flowId` | string (uuid) | yes | The handle returned by the begin call |
| `response` | [AuthenticatorAssertionRawResponse](#schema-authenticatorassertionrawresponse) | yes | The raw WebAuthn assertion response |
| `rememberMe` | boolean |  | Issue a long-lived session that survives closing the browser Default `false`. |

**Responses:**

- `200` An issued session token
- `401` The assertion was rejected, or the account may not sign in; the body carries the reason → [ApiError](#schema-apierror) (`text/plain`)
- `429` This address has had too many failed sign-in attempts → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `400` validation: `Response` required · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/auth/passkey/reauth`

**Confirm the caller's identity with a passkey from a live session**

The passkey counterpart of `POST /api/auth/reauth`: begins with the same anonymous
begin call, and the assertion must belong to the signed-in account. Records a credential
proof on the caller's session, which the sensitive self-service actions require to be recent.

**Request body** (required, `application/json`): [ReauthRequest](#schema-reauthrequest) — The assertion coming back from the browser

| Field | Type | Required | Description |
|---|---|---|---|
| `password` | string | yes | The account's current password |

**Responses:**

- `204` The proof was recorded on the session
- `401` The assertion was rejected, belongs to another account, or the session has ended → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Auth/password-policy`

**Get the password policy**

Anonymous so the sign-up and setup screens can render a live requirements panel that always
matches what the server will accept.

**Responses:**

- `200` The effective password policy → [PasswordPolicy](#schema-passwordpolicy) (`text/plain`)

**Live:** no token → `200` · token → `200`

---

#### `POST /api/Auth/reauth`

**Confirm the caller's password from a live session**

The sensitive self-service actions (new recovery codes, a passkey added or removed)
require a credential proved within the last few minutes, so a bearer token that was
lifted cannot be turned into a lasting foothold. This records a password proof on the
caller's session; a passkey assertion does the same through the passkey endpoints.
Throttled like a sign-in, because it verifies a password like one.

**Request body** (required, `application/json`): [ReauthRequest](#schema-reauthrequest) — The caller's current password

| Field | Type | Required | Description |
|---|---|---|---|
| `password` | string | yes | The account's current password |

**Responses:**

- `204` The proof was recorded on the session
- `401` The password was wrong, or the session has ended → [ApiError](#schema-apierror) (`text/plain`)
- `429` This address has had too many failed sign-in attempts → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/Auth/renew`

**Extend the caller's session**

Reissues a token for the SAME session with a fresh expiry. The client calls this on a
timer while the app is open, so an active user is never dumped back to the login screen
mid-task. Not audited: it is a mechanical refresh of an existing sign-in, not a new one,
and on a timer it would bury the log. The sign-in itself is already recorded, and
revoking the session still ends it - a renewal cannot resurrect a killed session.
Open to an enrolment-only session, which renews with the enrolment scope it was
created with: renewing extends a session, it never widens one.

**Responses:**

- `200` A replacement token and its new expiry
- `401` Not authenticated, or the session has been revoked or expired → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Auth/setup/admin`

**Create the initial administrator account**

Anonymous, but only meaningful once the database connection has been persisted and is reachable.
Atomically claims the one-time setup-complete marker before creating the user, so a second
request (or a retried duplicate) cannot create a stray account. The administrator role and
superuser grant are baked onto the user as it is created, and the caller is signed straight in.
If account creation fails the marker is rolled back so the operator can retry.
Success retires the setup code: this is the last step it gates, and the steps after it are
gated by the session token this call returns.

**Request body** (required, `application/json`): [SetupAdminRequest](#schema-setupadminrequest) — The new administrator's username, email, and password

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | yes | Username for the new administrator |
| `email` | string | yes | Email address for the new administrator |
| `password` | string | yes | Password for the new administrator; must satisfy the Identity password policy |

**Responses:**

- `200` The administrator was created and a session token issued
- `400` The password did not satisfy the policy (setup marker is rolled back so it can be retried) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` Initial setup has already been completed → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The database connection has not been configured yet, or is not reachable

**Live:** no token → not called (setup-phase only) · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Auth/setup/code`

**Checks the setup code**

Anonymous, gated by the setup code like every setup action: the wizard calls this when a code
has been typed in, so a wrong code is caught before the first real step rather than by it.

**Responses:**

- `204` The code was accepted, or none is required
- `403` No code was presented, or it did not match → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `503` auth.setup.code.unavailable · token → `503` (auth.setup.code.unavailable)

---

#### `POST /api/Auth/setup/database`

**Save the database connection**

Anonymous setup step, but one-way: once `bootstrap.local.json` is written the connection is
sealed (re-running would turn a network outage into an admin-account-creation vector), so this
refuses when already configured. Tests the connection before writing, persists any CA cert,
reloads configuration, and returns the freshly computed setup phase so the boot gate advances.

**Request body** (required, `application/json`): [DatabaseSetupRequest](#schema-databasesetuprequest) — The connection details to persist

| Field | Type | Required | Description |
|---|---|---|---|
| `servers` | string[] | yes | One or more server URLs (an "advanced" manual field) |
| `prefix` | string? | yes | Subject/bucket prefix that scopes this install (an "advanced" manual field) |
| `credentials` | string? | yes | Optional NATS credentials (an "advanced" manual field) |
| `caCert` | string? |  | Optional CA certificate (PEM) for a self-signed `tls://` server (an "advanced" manual field) |
| `blob` | string? |  | A single `.ampbus` descriptor. When present it is decoded server-side and supersedes the manual fields, so there is one apply path and the manual fields are just the already-decoded case. |

**Responses:**

- `200` The connection was saved; the body carries the fresh setup status
- `400` The request could not be resolved (malformed blob), or no server URL was supplied → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The database connection is already configured and cannot be changed here,     or a join file is still waiting to be admitted (`auth.setup.database.joinPending`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The connection could not be established

**Live:** no token → not called (setup-phase only) · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/Auth/setup/database/provision`

**Start managed NATS provisioning**

Kicks off managed-NATS provisioning in the background (download is slow) and returns
immediately. The wizard polls Task<IActionResult> AuthController.ProvisionStatus(CancellationToken ct) for Downloading, Configuring,
Testing, done, then reads the join info + fresh phase from the final status.
Anonymous setup step. The same one-way seal as Task<IActionResult> AuthController.SaveDatabase(DatabaseSetupRequest request, CancellationToken ct) refuses once the
connection is configured, with one exception: a provisioning that failed after writing
the connection may be run again, since the wizard's retry is the only way out of it. The
bus starts single-machine; the network step expands it when other machines will join.

**Responses:**

- `202` Provisioning started; poll the provision status endpoint for progress
- `409` The database connection is already configured by a provisioning that did not fail, so it cannot be re-provisioned → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → not called (setup-phase only) · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Auth/setup/database/provision/status`

**Poll live provisioning progress**

Live provisioning progress for the wizard to poll. Anonymous (pre-auth setup), and readable
throughout provisioning and the immediate post-provision poll so the client can collect the
final join info. Sealed once setup completes: the "done" status carries the managed bus join
info (including its token), so it must not stay anonymously readable on a live install.

**Responses:**

- `200` The current provisioning status snapshot
- `409` Setup has already been completed, so provisioning status is no longer available → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `503` auth.setup.code.unavailable · token → `503` (auth.setup.code.unavailable)

---

#### `POST /api/Auth/setup/database/test`

**Test a database connection**

Anonymous setup step, sealed once this install's database connection is configured: after that
there is nothing left to test, and it must not remain an anonymous "connect to any host" probe
on a live install. Resolves the connection from the request (decoding the `.ampbus` blob
when present), then attempts a live connection. A supplied CA cert is written to a throwaway file
for the duration of the test only.

**Request body** (required, `application/json`): [DatabaseSetupRequest](#schema-databasesetuprequest) — The connection details to test

| Field | Type | Required | Description |
|---|---|---|---|
| `servers` | string[] | yes | One or more server URLs (an "advanced" manual field) |
| `prefix` | string? | yes | Subject/bucket prefix that scopes this install (an "advanced" manual field) |
| `credentials` | string? | yes | Optional NATS credentials (an "advanced" manual field) |
| `caCert` | string? |  | Optional CA certificate (PEM) for a self-signed `tls://` server (an "advanced" manual field) |
| `blob` | string? |  | A single `.ampbus` descriptor. When present it is decoded server-side and supersedes the manual fields, so there is one apply path and the manual fields are just the already-decoded case. |

**Responses:**

- `200` The connection succeeded
- `400` The request could not be resolved (malformed blob), or no server URL was supplied → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The database connection is already configured, so testing is no longer available → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The connection could not be established

**Live:** no token → not called (setup-phase only) · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Auth/setup/entrynode`

**How far a public entry node's bring-up has got**

Anonymous, and polled by the wizard while it waits. A machine being set up as an entry node
     has no bus and therefore no user database to authenticate against - reaching the bus is the
     thing it is waiting for - so this reports from local observation alone.

     Returns 204 on any machine that is not an entry node, which is how the wizard knows to show
its ordinary waiting spinner rather than a ladder.

**Responses:**

- `200` The ladder, in order → [EntryNodeSetupProgress](#schema-entrynodesetupprogress) (`text/plain`)
- `204` Not a public entry node

**Live:** no token → `503` auth.setup.code.unavailable · token → `503` (auth.setup.code.unavailable)

---

#### `POST /api/Auth/setup/entrynode/reset`

**Abandon a public entry node's setup and start over**

Clears this machine's connection details and the tunnel it was building, returning the
     wizard to its first step. Anonymous, like every other setup step, and refused once the
     machine has actually joined - at that point it is a member of an installation and this is
     no longer a setup question.

     It exists because the address in a join file is typed by hand at the other end, and a
mistyped one leaves this machine waiting for a caller that is knocking somewhere else
entirely, with nothing here able to fix it.

**Responses:**

- `200` Reset; the wizard can start again
- `409` Not part-way through entry-node setup, or already joined → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → not called (setup-phase only) · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Auth/setup/network`

**Probe this machine's internet reachability**

Probes how this machine reaches the internet (Direct / NAT / CGNAT) so the setup wizard can
tell the user whether their servers will be reachable as-is, need a port-forward, or require a
tunnel, and says whether the bus can be opened to other machines from the same step. Takes a
moment (traceroute + a public-IP lookup); the result is cached briefly.
The wizard reaches this page only after creating the administrator account, which retires
the setup code and signs the administrator in, so the bearer gates it and the setup code
is not asked for. Requires the platform spire-create capability, the same one the step's
expand and the add a machine flow's address probe demand, which the account the wizard
just created holds; there is no anonymous surface for the outbound traceroute and HTTP the
probe performs.

**Responses:**

- `200` The network reachability probe result (Direct / NAT / CGNAT plus details) and the bus state → [SetupNetwork](#schema-setupnetwork) (`text/plain`)
- `401` The caller is not authenticated → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the spire-create capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Auth/setup/network/expand`

**Let other machines join this installation**

The setup wizard's network step, answered yes: expands the managed bus exactly as the add a
machine flow does, so the firewall sweep runs first and the answer carries the bus port's
outcome. An outcome other than Open is information for the wizard to show, never a failed
setup. Requires the platform spire-create capability, which the account the wizard just
created holds. Like the probe, it follows the account, so the administrator's bearer gates
it and the setup code, retired when the account was created, is not asked for.

**Responses:**

- `200` The bus is multi-machine; the response carries the join info and the bus port's firewall outcome → [BusExpansion](#schema-busexpansion) (`text/plain`)
- `401` The caller is not authenticated → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the spire-create capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The bus is not managed by AMP, so it cannot be reconfigured here → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → not called (setup-phase only) · token → not called (state-changing call with no id to aim at a non-existent object)

---

### 12-System & Health

_Anonymous health probes and machine-level diagnostics for this install._

#### `GET /api/Health/status`

**Report the app's lifecycle phase**

The single source of truth polled by the client boot gate.
Auth-independent and anonymous. The reported phase is one of: `setup-database` or
`setup-admin` (the first-run wizard still needs to run), `waiting` (a bus is
configured but currently unreachable), `starting` (settling after boot), or
`ready` (fully operational).

A preview build adds two more, and keeps answering here when it is refusing to answer
anywhere else - this is how the client learns why: `preview-licence` (no valid licence,
so an administrator has to activate one) and `preview-blocked` (expired, running on an
untrustworthy clock, or offline). Such a build also carries a `preview` block on every
status, giving its expiry date, the days left and what it knows about a newer build (whether
one is available, and once expired whether it is installing it, could not, or found none);
a release build carries none.

Every status also says whether THIS request travelled in clear text from a public address
(`plainText`): plain HTTP, and a peer that is neither this machine nor a private
network. The login screen warns on it, as AMP 2 did; a connection from localhost or a LAN
address never sees the warning, and one that arrived over TLS, directly or through a
trusted proxy, does not either.

**Responses:**

- `200` The current lifecycle status → [AppStatus](#schema-appstatus) (`text/plain`)

**Live:** no token → `200` · token → `200`

---

#### `GET /api/Support/diagnostics`

**Get a diagnostics snapshot**

Runtime diagnostics snapshot: AMP version, OS, CPU, RAM, NATS status, uptime. It is the
system information a support ticket carries. Requires the support capability or the
platform settings read capability.

**Responses:**

- `200` The diagnostics snapshot → [DiagnosticsDto](#schema-diagnosticsdto) (`text/plain`)
- `403` The caller holds neither the support capability nor the platform settings read capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Support/diagnostics/bundle`

**Download the diagnostics bundle**

A zip of what a support request needs: `diagnostics.json` (the diagnostics snapshot) and
`bus-stats.json` (the message bus statistics taken now: monitoring counters, this host's
connection, the account's JetStream use and limits, the account's revocation count and the
bus certificates' expiry). The bus statistics are bounded to 5 seconds; a bus that does not
answer leaves an error field in `bus-stats.json` and the bundle is still produced.
`blacklist.json` lists the reason record of every blacklisted version under the versions
directory (the version, whether its marker is present, and the record, or why it could not be
read); with no record it is an empty list. No credential, key or token is included. Requires
the support capability or the platform settings read capability.

**Responses:**

- `200` The bundle (`application/zip`)
- `403` The caller holds neither the support capability nor the platform settings read capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (generates a support bundle)

---

#### `GET /api/Support/message-bus-stats`

**Get message bus health metrics**

Live NATS/JetStream health and performance metrics: RTT, server identity,
JetStream account storage stats. Performs an active ping so latency is real.

**Responses:**

- `200` The bus stats. When no client is connected, a Disconnected shell is returned rather than an error → [MessageBusStatsDto](#schema-messagebusstatsdto) (`text/plain`)
- `403` The caller lacks the platform settings read capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/System/bus`

**Get the message bus state**

Requires the platform spire-create capability.

**Responses:**

- `200` The bus topology and, when public, shareable join info → [BusInfo](#schema-businfo) (`text/plain`)
- `403` The caller lacks the spire-create capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/System/bus/expand`

**Expand the managed bus to other machines**

Makes an AMP-managed single-machine bus reachable by other machines: the bus port's firewall
rule goes from host-only to open, and nothing restarts. The firewall sweep runs before the
answer, which carries the join info and what became of the bus port's rule. Expanding a bus
that is already public changes nothing and reports the rule as it stands.

**Responses:**

- `200` The bus is multi-machine; the response carries the join info to share and the bus port's firewall outcome → [BusExpansion](#schema-busexpansion) (`text/plain`)
- `403` The caller lacks the spire-create capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The bus is not managed by AMP, so it cannot be reconfigured here → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/Updates/apply`

**Apply the latest available AMP update**

Stages the newest version on the installation's channel, tells every other spire to move to the
same version, and on success restarts this AMP onto it (shard agents are unaffected). Returns a
running-task id to follow progress; spires that could not follow are named in that task. Requires
the `platform.update.apply` capability and that this install runs under amp3-host. Writes an
audit event.

**Permission:** `platform.update.apply`

**Responses:**

- `200` Staging has begun; the body carries the running-task id
- `400` No update is available, the install is unsupervised, or staging pre-flight failed (coded error) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the update-apply capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Updates/check`

**Check for an available AMP update**

Fetches and verifies the signed release manifest and reports whether a newer version is available
on the installation's channel. Requires the `platform.update.check` capability.

**Permission:** `platform.update.check`

**Responses:**

- `200` The update posture for this install → [UpdateStatusDto](#schema-updatestatusdto) (`text/plain`)
- `400` The manifest could not be fetched, verified or parsed (coded error) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the update-check capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Updates/spires/{spireId}`

**Update one spire that has fallen behind**

Tells a single lagging spire to move to the installation's version, from the manifest source this
spire has just fetched and verified as the check does. Only AMP on that spire restarts; its game servers keep running, and their agents adopt the new version the next time
each is restarted. Requires the `platform.update.apply` capability. Writes an audit event.

**Permission:** `platform.update.apply`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `spireId` | path | string (uuid) | yes | The spire to update |

**Responses:**

- `200` The spire accepted; the body is the refreshed update posture → [UpdateStatusDto](#schema-updatestatusdto) (`text/plain`)
- `400` The spire is unknown, offline or already current, this spire's manifest could not be fetched, verified or parsed, or the spire refused (coded error with its reason) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the update-apply capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (updates.error.spireUnknown)

---

#### `PUT /api/Updates/stream`

**Switch the release channel the installation follows**

Puts every spire on the named channel from now on and re-checks against it. Nothing is installed:
only a version newer than the running one is ever offered, so a switch cannot downgrade. Someone on
a preview build returns to retail by switching here and applying the next retail release that is
newer than their preview. Requires the `platform.update.apply` capability. Writes an audit
event.

**Permission:** `platform.update.apply`

**Request body** (required, `application/json`): [SetStreamRequest](#schema-setstreamrequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `stream` | string | yes | Mainline (retail), Preview, or Development where the installation is already on it |

**Responses:**

- `200` The update posture on the new channel → [UpdateStatusDto](#schema-updatestatusdto) (`text/plain`)
- `400` The channel is not one this installation may follow (coded error) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the update-apply capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

### 12-My Account

_The signed-in user's own profile, active sessions, and preferences._

#### `GET /api/me/permissions`

**Get the caller's permissions**

Grants combine the caller's self-role, named roles, and the superuser bridge for a
bootstrap admin. Intended for UI gating, never as a denial, using the same matcher
the backend enforces with.

**Responses:**

- `200` The caller's grants and the capability catalogue → [MyPermissions](#schema-mypermissions) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Profile`

**Get the current user's profile**

**Responses:**

- `200` The caller's profile → [ProfileDto](#schema-profiledto) (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PUT /api/Profile`

**Update the current user's profile**

An email already in use by another account, or one the identity store rejects,
fails validation. Changes are written to the audit log.

**Request body** (required, `application/json`): [UpdateProfileRequest](#schema-updateprofilerequest) — The display name and email to apply

| Field | Type | Required | Description |
|---|---|---|---|
| `displayName` | string? | yes | New display name; blank clears it |
| `email` | string? | yes | New email address; ignored if unchanged |

**Responses:**

- `200` The updated profile → [ProfileDto](#schema-profiledto) (`text/plain`)
- `400` The email is already in use or otherwise invalid → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/Profile/password`

**Change the current user's password**

The current password is verified first. On success the password-age clock resets,
any forced-change hold clears, and every other session is revoked as
credential-rotation hygiene, the caller's live SFTP connections on this spire
included. The change is written to the audit log.

**Request body** (required, `application/json`): [ChangePasswordRequest](#schema-changepasswordrequest) — The current and new passwords

| Field | Type | Required | Description |
|---|---|---|---|
| `currentPassword` | string | yes | The caller's existing password, verified before the change |
| `newPassword` | string | yes | The replacement password, subject to the configured strength policy |

**Responses:**

- `204` The password was changed
- `400` The current password was wrong or the new one failed the strength policy → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Profile/sessions`

**List the current user's sessions**

The session making the request is flagged with `IsCurrent`.

**Responses:**

- `200` The caller's active sessions → [SessionDto](#schema-sessiondto)[] (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `DELETE /api/Profile/sessions/others`

**Revoke all other sessions**

Returns the number of sessions revoked; the action is audited.

**Responses:**

- `200` The count of revoked sessions
- `401` The caller's identity or current session could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `DELETE /api/Profile/sessions/{id}`

**Revoke a session**

Only the caller's own sessions can be revoked; the revocation is audited.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The id of the session to revoke |

**Responses:**

- `204` The session was revoked
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such session belongs to the caller → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Profile/sshkeys`

**List the caller's SFTP public keys**

Only public halves are stored, so nothing here is a secret.

**Responses:**

- `200` The caller's registered keys, newest first → [SshKeyDto](#schema-sshkeydto)[] (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Profile/sshkeys`

**Register an SFTP public key on the caller's account**

The key must be a single `authorized_keys` line. It is validated structurally,
not just decoded: the algorithm named inside the key blob must match the one named
in the text, ECDSA curve names must agree, and RSA keys below the minimum size are
refused.

A key already registered anywhere in the tenant is refused. This is NOT needed to
resolve who is connecting - the username does that, and the connect path never searches
keys globally. It is what keeps one key meaning one person, which is what the audit log
relies on: two accounts sharing a key would let whoever holds it choose which of them
to be, with only the chosen username recorded. It also keeps revocation complete, since
removing a key that lives in two places only half-removes it.

The refusal reads the same whether the key is on this account or another. See the
call site for why telling them apart leaked more than it helped.

**Request body** (required, `application/json`): [AddSshKeyRequest](#schema-addsshkeyrequest) — The key, its label, and an optional expiry

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | yes | The key in one-line `authorized_keys` form: `type base64 [comment]`. This is the contents of a `.pub` file, never a private key. |
| `label` | string? | yes | Friendly name for the key. Blank falls back to the line's trailing comment, and then to the key type, so a key is never listed with no name at all |
| `expiresAt` | string? (date-time) | yes | Optional expiry; omit for a key that does not expire |

**Responses:**

- `200` The registered key → [SshKeyDto](#schema-sshkeydto) (`text/plain`)
- `400` The key could not be parsed, is an unsupported type, or is too small → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The key is already registered, to this account or another → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `DELETE /api/Profile/sshkeys/{id}`

**Remove one of the caller's SFTP public keys**

Only the caller's own keys can be removed. Removing a key ends any SFTP session it
authenticated, so the client is disconnected rather than left running on a credential
that no longer exists.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string | yes | The id of the key to remove |

**Responses:**

- `204` The key was removed
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such key belongs to the caller → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/users/me/preferences`

**Get the caller's preferences**

A user who has never saved preferences gets the system defaults.

**Responses:**

- `200` The caller's preferences, or defaults for a new user → [UserPreferences](#schema-userpreferences) (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PATCH /api/users/me/preferences`

**Update the caller's preferences**

Only fields present (non-null) in the request body are changed; omitted fields
retain their current values.

**Request body** (required, `application/json`): [PatchPreferencesRequest](#schema-patchpreferencesrequest) — The fields to change; null fields are left unchanged

| Field | Type | Required | Description |
|---|---|---|---|
| `theme` | string? |  |  |
| `locale` | string? |  |  |
| `favouriteAppSlugs` | string[]? |  |  |
| `pinnedShardIds` | string[]? |  |  |
| `notifyOnStateChange` | boolean? |  |  |
| `notifyOnUpdateAvailable` | boolean? |  |  |
| `compactShardHeader` | boolean? |  |  |
| `hideUnroutedShards` | boolean? |  |  |
| `hidePrivatePorts` | boolean? |  |  |
| `fileDetailsWidth` | integer (int32) |  |  |
| `ampExperience` | null or [UserExperienceLevel](#schema-userexperiencelevel) |  |  |
| `showTutorials` | boolean? |  |  |
| `tutorialPromptDismissed` | boolean? |  |  |
| `tutorialsCompleted` | string[]? |  |  |
| `accessibilityHintsEnabled` | boolean? |  |  |
| `onboardingCompleted` | boolean? |  |  |
| `reducedMotion` | boolean? |  |  |
| `analyticsEnabled` | boolean? |  |  |
| `streamerMode` | boolean? |  |  |
| `timeFormat` | null or [TimeFormat](#schema-timeformat) |  |  |

**Responses:**

- `200` The full preferences after the merge → [UserPreferences](#schema-userpreferences) (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/users/me/preferences/favourites/{slug}`

**Add a favourite template**

Newly favourited slugs are inserted at the front of the list; adding an existing favourite is a no-op.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | string | yes | The template slug to favourite |

**Responses:**

- `200` The full preferences after the change → [UserPreferences](#schema-userpreferences) (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `DELETE /api/users/me/preferences/favourites/{slug}`

**Remove a favourite template**

Removing a slug that is not favourited is a no-op.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | string | yes | The template slug to unfavourite |

**Responses:**

- `200` The full preferences after the change → [UserPreferences](#schema-userpreferences) (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

### 12-Profile

#### `GET /api/profile/passkeys`

**List the caller's registered passkeys**

**Responses:**

- `200` The caller's passkeys, newest first → [PasskeySummary](#schema-passkeysummary)[] (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/profile/passkeys/register/begin`

**Begin registering a new passkey**

Returns the creation options to hand to `navigator.credentials.create` along with a
flow handle to echo back. Fails with a translated reason when this installation's public
address cannot carry passkeys at all.

**Responses:**

- `200` Creation options and the flow handle
- `403` The session has not proved a credential recently; the body carries the reason → [ApiError](#schema-apierror) (`text/plain`)
- `409` Passkeys are unavailable here; the body carries the reason → [ApiError](#schema-apierror) (`text/plain`)
- `429` This address already has too many passkey ceremonies in progress → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/profile/passkeys/register/complete`

**Finish registering a new passkey**

From an enrolment-only session this also issues the full session the caller was waiting
for: the passkey they just added is exactly what the enrolment session existed to get,
so it is retired and replaced in the same call.

**Request body** (required, `application/json`): [RegisterCompleteRequest](#schema-registercompleterequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `flowId` | string (uuid) | yes | The handle returned by the begin call |
| `response` | [AuthenticatorAttestationRawResponse](#schema-authenticatorattestationrawresponse) | yes | The raw WebAuthn attestation response |
| `name` | string |  | The label to give the credential Default `""`. |

**Responses:**

- `200` The registered passkey, and a fresh session when the caller was enrolment-only → [RegisteredPasskeyDto](#schema-registeredpasskeydto) (`text/plain`)
- `400` The attestation was rejected; the body carries the reason → [ApiError](#schema-apierror) (`text/plain`)
- `403` The session has not proved a credential recently; the body carries the reason → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `PATCH /api/profile/passkeys/{id}`

**Rename one of the caller's passkeys**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes |  |

**Request body** (required, `application/json`): [RenameRequest](#schema-renamerequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | The label. Trimmed, and capped at 64 characters |

**Responses:**

- `204` Renamed
- `404` No such passkey on this account → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Name` required)

---

#### `DELETE /api/profile/passkeys/{id}`

**Revoke one of the caller's passkeys**

Refused when it would remove the caller's LAST way in: an account with no password whose
only credential is this passkey, or any account's only passkey while the installation
requires one. Same shape as the self-revocation rule: a hard 409 with a reason, never a
confirmation prompt, because the answer is no rather than "are you sure".

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes |  |

**Responses:**

- `204` Revoked
- `403` The session has not proved a credential recently; the body carries the reason → [ApiError](#schema-apierror) (`text/plain`)
- `404` No such passkey on this account → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` This is the caller's only remaining way to sign in → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/profile/recovery-codes`

**Get the caller's recovery-code status**

**Responses:**

- `200` The current status → [RecoveryStatusDto](#schema-recoverystatusdto) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/profile/recovery-codes`

**Generate a fresh set of recovery codes**

Replaces any existing set - the old codes stop working immediately. The plaintext is
returned exactly once, here, and is unrecoverable afterwards because only hashes are kept.
Requires a credential proved within the last few minutes: regenerating kills the set the
owner wrote down, which is exactly what a hijacked session would want to do.

**Responses:**

- `200` The new codes, for one-time display → [RecoveryCodesDto](#schema-recoverycodesdto) (`text/plain`)
- `403` The session has not proved a credential recently; the body carries the reason → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

### 12-Users & Access

_User accounts, roles, node-based permissions, and one-time onboarding invites._

#### `GET /api/invites`

**List all invites**

Requires the users-invite capability.

**Responses:**

- `200` The invites, each as an InviteDto → [InviteDto](#schema-invitedto)[] (`text/plain`)
- `403` The caller lacks the users-invite capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/invites`

**Create a one-time invite**

Requires the users-invite and roles-assign capabilities, and at least one role: an account with
no role can do nothing, and nothing would tell the invitee why. Every role must be within the
caller's own authority, checked here because redemption is anonymous. Only a hash of
the code is stored, so the returned string CreatedInvite.Token is the only chance to capture
it; the caller composes the link. At most one live invite exists per username; re-arm or revoke
an existing one instead of stacking.

**Request body** (required, `application/json`): [CreateInviteBody](#schema-createinvitebody) — The invite's target username, roles, and lifetime

| Field | Type | Required | Description |
|---|---|---|---|
| `userName` | string | yes | The username the invite will create. Required and must not already exist |
| `displayName` | string? | yes | Optional friendly name to suggest to the invitee |
| `roleIds` | string[]? | yes | Roles to assign the new account. Invalid or self-role ids are dropped, and at least one must remain |
| `expiresInHours` | integer (int32) | yes | Optional lifetime in hours. Clamped between 1 and 720 (30 days); defaults to 72 |

**Responses:**

- `201` The created invite plus its one-time code as a CreatedInvite → [CreatedInvite](#schema-createdinvite) (`text/plain`)
- `400` The username is missing, or no valid role is given → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the users-invite or roles-assign capability, or a role carries permissions the caller does not hold → [ApiError](#schema-apierror) (`text/plain`)
- `409` A user with that username already exists, or a live invite for it already exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/invites/accept`

**Redeem an invite**

Anonymous: no authentication required. The invitee sets their own email and password while the
username and roles stay fixed by the inviting admin, much like first-run admin setup. The roles
are measured here against what the inviting admin is configured to hold (see
InviteRoleCap): an invite can sit for weeks, and a role the admin could no
longer assign is withheld, audited, and named in the response. On success the account is
created, its hidden self-role is set up, the invite is marked accepted, any onboarding-derived
preferences are seeded best-effort, and auth tokens are returned. The invite is claimed with
a create-only write before the account is created, so two accepts of one link create one
account; the username index is create-only as well, so a name taken since the invite was
issued is refused rather than overwritten.

**Request body** (required, `application/json`): [AcceptInviteBody](#schema-acceptinvitebody) — The token plus the invitee's self-chosen email, password, and optional onboarding fields

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | The one-time secret code from the invite link |
| `email` | string | yes | The invitee's own email. Marked confirmed on the created account |
| `password` | string | yes | The invitee's own password. Must satisfy the configured password policy |
| `displayName` | string? |  | Optional friendly name chosen in the wizard; falls back to the admin's suggestion |
| `locale` | string? |  | Optional preferred locale; seeded into the account's preferences |

**Responses:**

- `200` The account was created and signed in; auth tokens and any withheld roles are returned as an AcceptedInvite → [AcceptedInvite](#schema-acceptedinvite) (`text/plain`)
- `400` The email or password failed validation → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `410` The token is invalid or expired, or the username was already taken (link already redeemed) → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `400` validation: `Email` required · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/invites/preview`

**Preview an invite**

Anonymous: no authentication required, so the invite landing page can render before sign-in.
Returns only the safe subset (username and suggested display name). A POST with the code in
the body, never a query string: the code is the whole secret of the link, and query strings
are kept by every access log on the way.

**Request body** (required, `application/json`): [InvitePreviewBody](#schema-invitepreviewbody) — The one-time secret code from the invite link

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | The one-time secret code from the invite link |

**Responses:**

- `200` The preview as an InvitePreview → [InvitePreview](#schema-invitepreview) (`text/plain`)
- `404` The token is invalid or the invite has expired → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `400` validation: `Token` required · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `DELETE /api/invites/{id}`

**Revoke an invite**

Requires the users-invite capability, and the same ceiling as re-arming: roles-assign for the
invite's roles, every role within the caller's own authority, and the caller its creator or
outranking them. Any outstanding link for the invite stops working.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The invite's unique identifier |

**Responses:**

- `204` The invite was revoked
- `403` The caller lacks the users-invite capability, lacks roles-assign for the invite's roles, a role carries permissions the caller does not hold, or the invite's creator outranks the caller → [ApiError](#schema-apierror) (`text/plain`)
- `404` No invite with that id exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/invites/{id}/rearm`

**Re-arm an invite**

Requires the users-invite capability, plus roles-assign when the invite carries roles, with
every role within the caller's own authority, and the caller must be the invite's creator or
outrank them: the same ceiling as creating it. Issues a new one-time code (invalidating the
old link), resets the lifetime, and makes the caller the invite's creator, so the roles it
redeems with are capped by the caller's authority. An already-accepted invite cannot be re-armed,
and neither can one none of whose roles exist any more, since it would create an account that
can do nothing.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The invite's unique identifier |

**Request body** (optional, `application/json`): null or [ReArmBody](#schema-rearmbody) — Optional new lifetime. May be omitted to use the default

**Responses:**

- `200` The re-armed invite plus its new one-time code as a CreatedInvite → [CreatedInvite](#schema-createdinvite) (`text/plain`)
- `400` None of the invite's roles exist any more → [ApiError](#schema-apierror) (`text/plain`)
- `403` The caller lacks the users-invite capability, lacks roles-assign for the invite's roles, a role carries permissions the caller does not hold, or the invite's creator outranks the caller → [ApiError](#schema-apierror) (`text/plain`)
- `404` No invite with that id exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The invite has already been accepted → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/roles`

**List all named roles**

Requires the roles-read capability. Hidden per-user self-roles are excluded. Ordered by name, case-insensitively.

**Responses:**

- `200` The roles, each as a RoleDto → [RoleDto](#schema-roledto)[] (`text/plain`)
- `403` The caller lacks the roles-read capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/roles`

**Create a role**

Requires the roles-create capability. The delegation rule is enforced: you may only grant
permission nodes you yourself hold, a deny needs the same authority as the allow it carves
out of, and granting a wildcard requires holding a matching wildcard or superuser grant
rather than a single concrete leaf.

**Request body** (required, `application/json`): [CreateRoleBody](#schema-createrolebody) — The new role's name, description, and grants

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Display name. Required and unique |
| `description` | string? | yes | Optional human description |
| `permissions` | string[]? | yes | Optional permission-node grants. You may only grant permissions you yourself hold |

**Responses:**

- `201` The created role as a RoleDto → [RoleDto](#schema-roledto) (`text/plain`)
- `400` The name is missing → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the roles-create capability, or the grant set includes permissions the caller does not hold → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/roles/{id}`

**Get a single role by id**

Requires the roles-read capability. Hidden per-user self-roles are treated as not found.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The role's unique identifier |

**Responses:**

- `200` The requested role as a RoleDto → [RoleDto](#schema-roledto) (`text/plain`)
- `403` The caller lacks the roles-read capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such role exists, or it is a hidden self-role → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `PUT /api/roles/{id}`

**Update a role**

Requires the roles-edit capability. Hidden per-user self-roles cannot be edited here. The
delegation rule applies to every grant the change touches: adding or removing a grant, allow
or deny, requires that you hold it yourself. A malformed grant already stored may be removed
by anyone with the capability.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The role's unique identifier |

**Request body** (required, `application/json`): [UpdateRoleBody](#schema-updaterolebody) — The fields to change. Null fields are left unchanged

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string? | yes | New display name, or null to leave unchanged |
| `description` | string? | yes | New description, or null to leave unchanged |
| `permissions` | string[]? | yes | Replacement grant set, or null to leave unchanged. Every grant added or removed is subject to the delegation rule |

**Responses:**

- `200` The updated role as a RoleDto → [RoleDto](#schema-roledto) (`text/plain`)
- `400` The role store rejected the change → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the roles-edit capability, or a changed grant is a permission the caller does not hold → [ApiError](#schema-apierror) (`text/plain`)
- `404` No such role exists, or it is a hidden self-role → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The change would take away the caller's own permissions → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `DELETE /api/roles/{id}`

**Delete a role**

Requires the roles-delete capability, and the delegation rule applies to everything the role
carries: deleting it takes those grants off every member, allow and deny alike, so you must
hold them yourself. A malformed grant the role carries does not count. Hidden per-user
self-roles cannot be deleted here. Members keep a dangling role id, which effective-grant
resolution simply skips, so no fan-out write is needed.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The role's unique identifier |

**Responses:**

- `204` The role was deleted
- `403` The caller lacks the roles-delete capability, or the role carries permissions the caller does not hold → [ApiError](#schema-apierror) (`text/plain`)
- `404` No such role exists, or it is a hidden self-role → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` Deleting the role would take away the caller's own permissions → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/users`

**List all user accounts**

Requires the users-read capability. Results are ordered by username, case-insensitively.

**Responses:**

- `200` The users, each as a UserDto → [UserDto](#schema-userdto)[] (`text/plain`)
- `403` The caller lacks the users-read capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/users`

**Create a user**

Requires the users-create capability, plus roles-assign when roles are given; every role must be
within the caller's own authority. The account is given an empty hidden self-role ready for
per-user grants. Requested role ids that don't exist or are hidden self-roles are silently dropped.

**Request body** (required, `application/json`): [CreateUserBody](#schema-createuserbody) — The new user's details

| Field | Type | Required | Description |
|---|---|---|---|
| `userName` | string | yes | Login name. Required and unique |
| `password` | string | yes | Initial password. Must satisfy the configured password policy |
| `displayName` | string? | yes | Optional friendly name |
| `email` | string? | yes | Optional contact email |
| `roleIds` | string[]? | yes | Optional named roles to assign. Ids that don't exist or are hidden self-roles are dropped |
| `passwordExpires` | boolean? | yes | Optional. Whether the password is subject to age-based expiry. Defaults to false |
| `requirePasswordChange` | boolean? | yes | Optional. Whether the user must change their password on first sign-in. Defaults to false |

**Responses:**

- `201` The created user as a UserDto → [UserDto](#schema-userdto) (`text/plain`)
- `400` The username is missing, or the password or other fields fail validation → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the users-create capability, lacks roles-assign while giving roles, or a role carries permissions the caller does not hold → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/users/{id}`

**Get a single user by id**

Requires the users-read capability.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The user's unique identifier |

**Responses:**

- `200` The requested user as a UserDto → [UserDto](#schema-userdto) (`text/plain`)
- `403` The caller lacks the users-read capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No user with that id exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PUT /api/users/{id}`

**Update a user**

Requires the users-edit capability, and the target must not hold more authority than the caller.
Changing role membership additionally requires roles-assign, and every role added or removed must
be within the caller's own authority. Changing the suspended state additionally requires the
users-suspend capability. Suspending the user, or newly forcing a password change, revokes all
of that user's live sessions, SFTP included, so the change takes effect immediately. Turning on
password expiry for a password already past the maximum age ends their SFTP connections too.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The user's unique identifier |

**Request body** (required, `application/json`): [UpdateUserBody](#schema-updateuserbody) — The fields to change. Null fields are left unchanged

| Field | Type | Required | Description |
|---|---|---|---|
| `displayName` | string? | yes | New friendly name, or null to leave unchanged |
| `email` | string? | yes | New contact email, or null to leave unchanged |
| `roleIds` | string[]? | yes | Replacement role set, or null to leave unchanged. Invalid or self-role ids are dropped |
| `suspended` | boolean? | yes | New suspended state, or null to leave unchanged. Changing this requires the suspend capability |
| `passwordExpires` | boolean? | yes | New password-expiry flag, or null to leave unchanged |
| `requirePasswordChange` | boolean? | yes | New forced-change flag, or null to leave unchanged. Newly forcing a change revokes live sessions |

**Responses:**

- `200` The updated user as a UserDto → [UserDto](#schema-userdto) (`text/plain`)
- `400` One or more fields failed validation → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks a required capability, the target outranks the caller, or a changed role carries permissions the caller does not hold → [ApiError](#schema-apierror) (`text/plain`)
- `404` No user with that id exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The change would take away the caller's own permissions → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `DELETE /api/users/{id}`

**Delete a user account**

Requires the users-delete capability, and the target must not hold more authority than the caller.
First deletes the user's passkeys, preferences and every schedule that runs as them, then the
account and its hidden self-role, and revokes their live sessions. A part of that cleanup that
fails is reported as a warning and does not stop the deletion. You cannot delete your own account.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The user's unique identifier |

**Responses:**

- `200` The user was deleted, with any cleanup warnings → [UserDeletedDto](#schema-userdeleteddto) (`text/plain`)
- `400` The id refers to the caller's own account → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the users-delete capability, or the target outranks the caller → [ApiError](#schema-apierror) (`text/plain`)
- `404` No user with that id exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `500` The account store refused the deletion; the cleanup had already run → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/users/{id}/logout`

**End all of a user's sessions**

Ends all of a user's live sessions (account-recovery / "kick everywhere"). The user keeps
their password and can sign back in immediately unless separately suspended or held.
Requires the users-edit capability, and the target must not hold more authority than the caller.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The user's unique identifier |

**Responses:**

- `204` All of the user's sessions were revoked
- `403` The caller lacks the users-edit capability, or the target outranks the caller → [ApiError](#schema-apierror) (`text/plain`)
- `404` No user with that id exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/users/{id}/reset-password`

**Reset a user's password**

Requires the users-reset-password capability, and the target must not hold more authority than
the caller. Sets the given password and revokes all of the user's live sessions, SFTP included,
so they must sign in again. A forced-password-change hold is deliberately left intact, so an
admin can reset to a temporary password that the user must then replace.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The user's unique identifier |

**Request body** (required, `application/json`): [ResetPasswordBody](#schema-resetpasswordbody) — The new password

| Field | Type | Required | Description |
|---|---|---|---|
| `newPassword` | string | yes | The replacement password. Must satisfy the configured password policy |

**Responses:**

- `204` The password was reset and the user's sessions were revoked
- `400` The new password failed validation → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the users-reset-password capability, or the target outranks the caller → [ApiError](#schema-apierror) (`text/plain`)
- `404` No user with that id exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `NewPassword` required)

---

### 12-Credentials

#### `GET /api/credentials`

**List the credentials the caller may see**

The caller's own credentials with their usernames, every Public credential by title and kind, and for a holder of
`credentials.manage` every credential (still without the usernames of others).

**Responses:**

- `200` The credentials, newest first → [SharedCredentialView](#schema-sharedcredentialview)[] (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/credentials`

**Keep a new credential**

Any signed-in user may keep a credential of their own; it is Private unless the body says Public. A title,
username or secret the kind cannot take is refused as `credentials.error.invalid` and the attempt is
audited at Security. Audited as `SharedCredentialCreated`.

**Request body** (required, `application/json`): [CreateCredentialBody](#schema-createcredentialbody) — The credential to keep

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | [SharedCredentialKind](#schema-sharedcredentialkind) | yes | What it signs in to; SteamCmd today |
| `title` | string | yes | What everybody who may use it sees; blank takes the kind's default, such as "SteamCMD login" |
| `username` | string | yes | The account's username for a kind that has one (SteamCmd, Smtp); shown to the creator alone afterwards. Empty for a kind whose secret stands alone |
| `secret` | string | yes | The password, webhook URL or token; sealed at rest and never returned |
| `visibility` | [CredentialVisibility](#schema-credentialvisibility) |  | Private (the creator alone, the default) or Public (everybody in the tenant may use it) |

**Responses:**

- `201` The credential as its creator sees it → [SharedCredentialView](#schema-sharedcredentialview) (`text/plain`)
- `400` The title, username or secret is not one the kind can take → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `DELETE /api/credentials/{id}`

**Delete a credential**

Its creator's or a `credentials.manage` holder's act. Every shard that used it points at nothing afterwards
and resolves a credential afresh on its next update. Audited as `SharedCredentialDeleted`.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The credential's id |

**Responses:**

- `204` The credential is gone
- `403` The caller neither created the credential nor manages credentials → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such credential → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `PUT /api/credentials/{id}/secret`

**Replace a credential's secret**

Its creator's or a `credentials.manage` holder's act. Audited as `SharedCredentialReplaced`.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The credential's id |

**Request body** (required, `application/json`): [ReplaceSecretBody](#schema-replacesecretbody) — The new secret

| Field | Type | Required | Description |
|---|---|---|---|
| `secret` | string | yes | The password, webhook URL or token; sealed at rest and never returned |

**Responses:**

- `204` The secret was replaced
- `400` The secret is not one the kind can take → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller neither created the credential nor manages credentials → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such credential → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Secret` required)

---

#### `PUT /api/credentials/{id}/visibility`

**Make a credential Public or Private**

Its creator's or a `credentials.manage` holder's act. Audited as `SharedCredentialVisibilityChanged`.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The credential's id |

**Request body** (required, `application/json`): [SetVisibilityBody](#schema-setvisibilitybody) — Who may use it

| Field | Type | Required | Description |
|---|---|---|---|
| `visibility` | [CredentialVisibility](#schema-credentialvisibility) | yes | Private or Public |

**Responses:**

- `204` The visibility is as asked
- `403` The caller neither created the credential nor manages credentials → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such credential → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

### 12-Audit Log

_Querying the durable record of every state-changing action._

#### `GET /api/Audit`

**Query the audit log**

General audit query: every filter is optional and they combine.
Newest-first unless sort is "asc"; paginated.
Requires the `platform.audit.read` capability. Results are always
restricted to the authenticated tenant; the tenant is taken from the
request context and cannot be widened by the caller.

**Permission:** `platform.audit.read`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `from` | query | string (date-time) |  | Lower time bound (inclusive), if set |
| `to` | query | string (date-time) |  | Upper time bound (inclusive), if set |
| `userId` | query | string |  | Restrict to events raised by this user, or inside a flow they started |
| `action` | query | string |  | Restrict to a single action name (e.g. `ShardSettingChanged`) |
| `targetType` | query | string |  | Restrict to a target type (e.g. `Setting`, `Shard`) |
| `targetId` | query | string |  | Restrict to a single target instance |
| `correlationId` | query | string (uuid) |  | Return the full chain of events sharing this correlation id |
| `search` | query | string |  | Free-text match across the indexed event fields |
| `origin` | query | [AuditOrigin](#schema-auditorigin) |  | Restrict to events of one origin: `User`, `UserFlow`, `System`, `Scheduled` or `Impersonation` |
| `minSeverity` | query | [AuditSeverity](#schema-auditseverity) |  | Restrict to events of this severity or louder: `Warning` or `Security` |
| `page` | query | integer (int32) |  | 1-based page number |
| `pageSize` | query | integer (int32) |  | Page size |
| `sort` | query | string |  | "asc" for oldest-first; anything else (default) is newest-first |

**Responses:**

- `200` A page of matching audit events plus paging metadata → [AuditQueryResult](#schema-auditqueryresult) (`text/plain`)
- `403` The caller lacks the audit-read capability → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Audit/facets`

**Get audit filter facets**

Requires the `platform.audit.read` capability. Scoped to the authenticated tenant.

**Permission:** `platform.audit.read`

**Responses:**

- `200` The distinct facet values available for filtering → [AuditFacets](#schema-auditfacets) (`text/plain`)
- `403` The caller lacks the audit-read capability → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Audit/last/{action}/{targetType}/{targetId}`

**Get the last matching audit event**

Requires the `platform.audit.read` capability. Scoped to the authenticated tenant.

**Permission:** `platform.audit.read`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `action` | path | string | yes | The action name to match (e.g. `ShardSettingChanged`) |
| `targetType` | path | string | yes | The target type to match (e.g. `Setting`) |
| `targetId` | path | string | yes | The target instance to match |

**Responses:**

- `200` The most recent audit event matching the action/target
- `403` The caller lacks the audit-read capability → [ApiError](#schema-apierror) (`text/plain`)
- `404` No audit event matches the given action and target → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `404` · token → `404`

---

#### `GET /api/Audit/roots`

**List the audit flows by their root**

One row per flow: the request a user made, the tick the system ran or the schedule that
fired, with how many events came of it and when the last one was. Every filter applies to
the root event, except minSeverity, which lists a flow when any event
inside it reached that severity. Newest root first unless sort is "asc";
paginated.
Requires the `platform.audit.read` capability. Scoped to the authenticated tenant.

**Permission:** `platform.audit.read`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `from` | query | string (date-time) |  | Lower bound on the root's time (inclusive), if set |
| `to` | query | string (date-time) |  | Upper bound on the root's time (inclusive), if set |
| `userId` | query | string |  | Restrict to flows started by this user |
| `action` | query | string |  | Restrict to flows whose root carries this action |
| `targetType` | query | string |  | Restrict to flows whose root acts on this target type |
| `targetId` | query | string |  | Restrict to flows whose root acts on this target |
| `origin` | query | [AuditOrigin](#schema-auditorigin) |  | Restrict to flows of this origin: `User`, `System`, `Scheduled` or `Impersonation` (a root is never `UserFlow`) |
| `search` | query | string |  | Free-text match across the root's indexed fields |
| `minSeverity` | query | [AuditSeverity](#schema-auditseverity) |  | Restrict to flows in which some event reached this severity or louder: `Warning` or `Security` |
| `page` | query | integer (int32) |  | 1-based page number |
| `pageSize` | query | integer (int32) |  | Page size |
| `sort` | query | string |  | "asc" for oldest-first; anything else (default) is newest-first |

**Responses:**

- `200` A page of flows plus paging metadata → [AuditRootsResult](#schema-auditrootsresult) (`text/plain`)
- `403` The caller lacks the audit-read capability → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Audit/tree/{correlationId}`

**Get one audit flow as a tree**

The root event, then everything that came from it, nested by cause and in time order
within each level: the task a click started under the click, the task's stages under its
start, a spire's events under the hub's command. Requires the `platform.audit.read`
capability. Scoped to the authenticated tenant.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `correlationId` | path | string (uuid) | yes | The flow's correlation id, as the roots listing or any event gives it |

**Responses:**

- `200` The flow as a tree → [AuditTree](#schema-audittree) (`text/plain`)
- `403` The caller lacks the audit-read capability → [ApiError](#schema-apierror) (`text/plain`)
- `404` No audit event carries that correlation → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `404` (audit.error.flowNotFound)

---

### 12-Shards

_Deploying, controlling, configuring, cloning, and migrating game and application servers._

#### `GET /api/Shards`

**List the tenant's shards**

The result is filtered to shards on which the caller holds the `view` capability, so denied shards are silently omitted rather than refused.

**Permission:** `view`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `includeDeleted` | query | boolean |  | When true, soft-deleted shards are included in the listing |

**Responses:**

- `200` The viewable shards for this tenant → [DeployedShard](#schema-deployedshard)[] (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Shards`

**Create a shard from a template**

Requires the `shards.create` capability on the target spire (the default local spire when none is specified).
With `startNow`, the new shard's first update is run for the caller as `POST /api/shards/{id}/update` would run
it, and the shard is started once that update has finished successfully, through the same checks as
`POST /api/shards/{id}/start`. Both are recorded under this request in the activity log. A shared credential
named here is pointed at before that first update, which signs in with it, and must be a SteamCMD one the caller may
use, else the request is refused before anything is created and audited at Security; a shard whose game needs an owning
Steam account and points at nothing fails its update task at once with that reason and nothing starts.

**Permission:** `shards.create`

**Request body** (required, `application/json`): [CreateShardBody](#schema-createshardbody) — The template, naming, placement, start options and shared credential for the new shard

| Field | Type | Required | Description |
|---|---|---|---|
| `templateSlug` | string | yes | Slug of the app template to instantiate. Required |
| `displayName` | string | yes | Human-readable name for the new shard |
| `description` | string | yes | Free-text description for the new shard |
| `spireId` | string? (uuid) | yes | Target spire to create the shard on. When null, the tenant's default local spire is used |
| `startNow` | boolean | yes | When true, the shard's first update runs straight away and the shard starts once that update has finished successfully; a failed or cancelled update starts nothing |
| `startOnBoot` | boolean | yes | When true, the shard is configured to start automatically when its spire boots |
| `credentialId` | string? (uuid) |  | The shared credential the new shard's updates sign in with, for a game that can only be downloaded by an account that owns it: one the caller may use (their own or a Public one), pointed at before the first update runs |

**Responses:**

- `201` The shard was created; the response carries the created shard and its provisioning result → [CreateShardResult](#schema-createshardresult) (`text/plain`)
- `400` The template slug is missing, the named credential is not one the caller may use     (`shards.error.credentialUnknown`), or the shard could not be created (for example an invalid template) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not create shards on the target spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Shards/attention`

**List shards awaiting user action**

Lets the UI rehydrate its global toasts after a page refresh. The live shard state feed reports only the transition into awaiting the user; this endpoint returns the durable equivalent, filtered to shards the caller may view.

**Responses:**

- `200` The attention entries visible to the caller → [ShardAttention](#schema-shardattention)[] (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Shards/endpoints`

**Where to connect to every server the caller can see**

The list view's counterpart to the per-server endpoint: one call rather than one per row. Same
     resolution (see `GetEndpoints`), keyed by server id, and filtered to servers the caller holds
     `view` on - one you cannot see is simply absent rather than refused.

     The routing records (publications, gateway bindings) and each machine's reported addresses are read
ONCE and shared across every server resolved, so the cost is one sweep however many there are.

**Responses:**

- `200` Resolved endpoints keyed by server id; servers with no addresses are omitted → map<string, [ShardEndpoint](#schema-shardendpoint)[]> (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Shards/{id}`

**Get a shard's definition**

Requires the `view` capability.

**Permission:** `view`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The requested shard → [DeployedShard](#schema-deployedshard) (`text/plain`)
- `403` The caller may not view this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PATCH /api/Shards/{id}`

**Update a shard's own record**

The third settings surface, alongside `/settings` (template-defined) and `/infra` (AMP-owned
    infrastructure): the shard record itself. Omitted fields are left unchanged.
    Only user-facing labels are mutable here. The shard's machine name (Name) is immutable - it backs
log paths, subject tokens and the working directory - so a rename moves nothing on disk and does not
require the shard to be stopped.
    Requires the `infra.write` capability, the same as the rest of the AMP-owned settings surface.

**Permission:** `infra.write`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Request body** (required, `application/json`): [PatchShardBody](#schema-patchshardbody) — The fields to change

| Field | Type | Required | Description |
|---|---|---|---|
| `displayName` | string? | yes | The friendly name shown throughout the UI |
| `description` | string? | yes | Free-text note about what this shard is for |
| `tags` | string[]? | yes | Free-form labels for grouping and searching |

**Responses:**

- `200` The updated shard → [DeployedShard](#schema-deployedshard) (`text/plain`)
- `400` The display name is empty or too long → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not write this shard's settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `DELETE /api/Shards/{id}`

**Soft-delete the shard**

Requires the `delete` capability. Reversible via `restore`; the on-disk working directory is only removed by `purge`.

**Permission:** `delete`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The updated, now soft-deleted shard → [DeployedShard](#schema-deployedshard) (`text/plain`)
- `403` The caller may not delete this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Shards/{id}/agent`

**Get the shard agent's version status**

Answers "would restarting this shard pick up a newer agent?". Reported only for a RUNNING
shard - a stopped one takes the newest agent the moment it next starts, so there would be
nothing to act on.

The comparison is agent-to-agent, against the newest agent the OWNING spire would launch: a
shard restarts into its own spire's versions directory, never this machine's. It deliberately
does NOT compare against the webapi's own version: only the agent project carries Nerdbank
versioning, so that comparison would mark every agent permanently out of date. When the owning
spire is itself behind the installation, `spireBehind` says so, because then a restart
cannot bring the agent up to date and the spire is what needs updating.

The restart endpoint takes its route from this same verdict: a restart of an out-of-date agent
goes to the owning spire as a stop and relaunch whenever that spire is online, so the restart
this verdict asks for adopts the newer agent. With the spire offline the restart stays
in-process through the agent, on the same binary, and the verdict stays out of date.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The agent version status → [AgentStatusDto](#schema-agentstatusdto) (`text/plain`)
- `403` The caller may not view this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/shards/{id}/clone`

**Clone a shard**

Configuration is duplicated in KV; on-disk data is copied by the destination spire (locally or
pulled from the source). A data clone requires the source stopped so its files are at rest, and
the new shard's ports are reassigned so it can never collide with the source. Gated on the source
shard's clone capability and on `shards.create` on the spire the clone is placed on (the
source's own spire for an in-place clone). A half-created clone is removed if the destination
spire refuses the copy.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | Id of the source shard to clone |

**Request body** (required, `application/json`): [CloneShardBody](#schema-cloneshardbody) — The clone request

| Field | Type | Required | Description |
|---|---|---|---|
| `newName` | string | yes | Display name for the new shard |
| `content` | [CloneContent](#schema-clonecontent) | yes | What to duplicate: configuration, on-disk data, or both. `None` is rejected |
| `targetSpireId` | string? (uuid) | yes | Spire to place the clone on; null clones in place on the source's spire |

**Responses:**

- `200` Clone started (or, for config-only, completed). The body carries the new `shardId`
- `400` The request asked to clone nothing (`Content` was `None`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the clone capability on the source shard, or may not create shards on the destination spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` A data clone was requested while the source shard is running or busy → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `NewName` required)

---

#### `POST /api/Shards/{id}/config/import`

**Read an AMP-managed config file back into the shard's settings**

Only files AMP merges into (`AutoMap`) and that the template marks importable can be
read back: a generated file has no addressing to read through, because AMP produced every
byte of it from the settings.

The file is parsed BEFORE anything is written, so a file that does not parse leaves both the
settings and the file itself untouched, and the 400 names the line and column that broke.
Only values that actually differ are applied, and each one clears the same gates as the
settings page (per-setting write authorization, the enum safety gate, host caps, per-key
audit) - the file editor is not a way around a permission the settings page enforces.
Settings the template marks `ExcludeFromImport` are reported but never read back.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Request body** (required, `application/json`): [ConfigImportRequest](#schema-configimportrequest) — The file to import, and optionally the edited content to save with it

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | The config file, relative to the shard's file-manager root |
| `content` | string? |  | The edited file to import and then save. Omit it to import the file as it already stands on disk, which is how a change made outside AMP (over SFTP, or by the app itself) is picked up |

**Responses:**

- `200` The import succeeded; the response names what changed → [ConfigImportResponse](#schema-configimportresponse) (`text/plain`)
- `400` The file is not importable, it did not parse, or a value in it is not one its setting allows; a refused import says under `fileState` whether the file was left `unchanged` or is `written` but not read back → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not read/write the file, or may not write one of the settings it changes → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Path` required)

---

#### `POST /api/Shards/{id}/console`

**Send a console command to the shard**

Requires the `console.send` capability. Published over NATS to the subject the shard agent subscribes to
(`amp.tn.{tenant}.sr.{shardId}.ev.con.input`) and audited. A command sent as a prompt reply, while the shard
is blocked on an interactive prompt, or within a few seconds of one closing, may be a secret (Steam password or
Guard code) and is redacted from the audit entry; so is everything after a command word that takes a credential.

**Permission:** `console.send`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Request body** (required, `application/json`): [ConsoleInputBody](#schema-consoleinputbody) — The command to send

| Field | Type | Required | Description |
|---|---|---|---|
| `command` | string | yes | The command string to write to the shard's stdin |
| `promptReply` | boolean |  | True when the command answers an interactive prompt (a Steam password or Guard code), so it is never audited in the clear Default `false`. |

**Responses:**

- `202` The command was published to the shard
- `403` The caller may not send console input to this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Command` required)

---

#### `GET /api/Shards/{id}/console/history`

**Get the shard's console history**

Requires the `console.read` capability. Paged backwards: omit the cursor for the newest page, then pass the first
line's Seq as beforeSeq to fetch the previous page, repeating while `hasMore` is set. The client
appends live hub entries on top, deduping by `Seq` (one sequence space covers both paths).

**Permission:** `console.read`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |
| `beforeSeq` | query | integer (int64) |  | Fetch entries older than this sequence number; omit for the newest page |
| `limit` | query | integer (int32) |  | Maximum number of entries to return in the page |

**Responses:**

- `200` A page of console history → [ConsoleHistoryPage](#schema-consolehistorypage) (`text/plain`)
- `403` The caller may not read this shard's console → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Shards/{id}/credential`

**Get the shared credential the shard's updates sign in with**

Requires the `settings.read` capability. No password is ever returned, and a username only for a credential
the caller created. The response says whether the game needs an owning account, which credential the shard
points at, and which credentials the caller could point it at instead.

**Permission:** `settings.read`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The shard's credential state → [ShardCredentialDto](#schema-shardcredentialdto) (`text/plain`)
- `403` The caller may not read this shard's settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PUT /api/Shards/{id}/credential`

**Point the shard at a shared credential for its updates**

Requires the `settings.write` capability. The credential must be a SteamCMD one the caller may use: one they
created, or a Public one. Audited as `ShardCredentialSet`; an unknown id, a credential of another kind and a
Private credential of somebody else are all refused as unknown and the attempt audited at Security.

**Permission:** `settings.write`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Request body** (required, `application/json`): [SetShardCredentialBody](#schema-setshardcredentialbody) — The credential to point at

| Field | Type | Required | Description |
|---|---|---|---|
| `credentialId` | string (uuid) | yes | A credential the caller may use: their own or a Public one |

**Responses:**

- `200` The shard's credential state as it now stands → [ShardCredentialDto](#schema-shardcredentialdto) (`text/plain`)
- `400` No such credential the caller may use (`shards.error.credentialUnknown`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not write this shard's settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `DELETE /api/Shards/{id}/credential`

**Take the shard off its shared credential**

Requires the `settings.write` capability. The shard points at nothing afterwards; its next update resolves
a credential afresh. Audited as `ShardCredentialCleared`. A shard pointing at nothing answers 204 too.

**Permission:** `settings.write`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `204` The shard points at no credential
- `403` The caller may not write this shard's settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Shards/{id}/disk`

**Get the shard's disk usage against its budget**

The shard's entry from its spire's last usage scan, carrying the measured size, the budget,
     and the spire's verdict. Cached, not measured on demand: the walk that produces it takes
     seconds and belongs to the machine holding the files.

     A 204 means there is nothing to report - no budget is set, the spire has never been scanned,
or the scan did not find this shard's directory. Read it as unknown, never as "under budget".

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The shard's usage entry from the last scan → [ShardDiskUsage](#schema-sharddiskusage) (`text/plain`)
- `204` No budget set, or no scan has covered this shard yet
- `403` The caller may not view this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `204`

---

#### `POST /api/Shards/{id}/disk-warning/ack`

**Acknowledge the shard's disk-usage warning**

Hides the dismissible "approaching its disk budget" notice for this shard. Requires only the
     `view` capability: acknowledging a warning changes nothing about the server, and anyone
     who can see the notice should be able to put it away.

     It only ever silences the first, advisory level. The critical notice and the stop that
follows it are not dismissible, and the next usage scan to find this shard back under the
warning threshold clears the acknowledgement, so a later approach to the budget is announced
again rather than staying hidden behind an old dismissal.

**Permission:** `view`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The updated shard → [DeployedShard](#schema-deployedshard) (`text/plain`)
- `403` The caller may not view this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Shards/{id}/endpoints`

**Where to connect to this shard**

Resolves the address a user should actually type in, which is not simply the shard's bind address
     and port. A published port is served by a DIFFERENT machine on a DIFFERENT number (the front spire
     relays it); a machine behind NAT has one address that works from outside and another that works
     from inside, and a router without hairpin NAT will refuse to loop the outside one back; and the
     operator may have declared an advertised address that outranks everything probed.

     So both are returned per port, each carrying where it came from and what is known to stand in the
way (a port forward still needed, carrier-grade NAT, a route still coming up). Where the template
declares an EndpointURIFormat, each address also carries the client URI it renders to.
One entry per port worth connecting to: the application's primary port, plus anything published.
     Requires the `view` capability - anyone who can see the server can see how to reach it.

**Permission:** `view`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The resolved endpoints, primary port first → [ShardEndpoint](#schema-shardendpoint)[] (`text/plain`)
- `403` The caller may not view this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Shards/{id}/eula/accept`

**Accept the application's end-user licence for this shard**

Requires the `settings.write` capability. Records acceptance in the setting the template
marked `Special=EULA`, which for most apps is a real line in the application's own config
file (Minecraft's `eula.txt`). Audited: accepting a licence is an act taken on the tenant's
behalf, and who did it and when is exactly what gets asked about afterwards.

There is deliberately no matching "decline": declining is simply not accepting, and
nothing needs recording for it.

An application that declares no licence succeeds without doing anything, so a caller
accepting licences across many shards does not have to know which templates have one.

**Permission:** `settings.write`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `204` Acceptance recorded, or there was no licence to accept
- `400` The template marks a path or host-sourced setting as its licence acceptance → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not change this shard's settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Shards/{id}/infra`

**Get the shard's infra settings**

Requires the `infra.read` capability. Loads the spire definition, spire state, and the shard's own spec in parallel to populate the context fields (spire name, OS, container policy, logical cores, template OS).

**Permission:** `infra.read`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The infra settings and their rendering context → [ShardInfraContext](#schema-shardinfracontext) (`text/plain`)
- `403` The caller may not read this shard's infra settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or it has no application definition (`shards.error.noSpec`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PATCH /api/Shards/{id}/infra`

**Update the shard's infra settings**

Updates container mode, resource limits, boot behaviour, user-slot cap, and disk budget.
    Requires the `infra.write` capability. Memory values are validated for Docker/Podman consistency (reservation must
    not exceed the limit; swap must not be below the limit). Changed fields are diffed and audited. If a new `MaxUsersCap`
    is below the stored `$MaxUsers`, the config value is clamped immediately so the next start enforces the cap;
    when the template does not allow that write, the cap is still saved and `clampRefused` carries the reason.
    A new `DiskLimitMb` takes effect at the owning spire's next usage scan, not on save.
    The isolation and resource fields (ContainerMode, ContainerImageOverride, the memory and CPU
limits, MaxUsersCap and DiskLimitMb) may be sent back unchanged with infra.write alone;
changing any of them also requires the infra.limits capability on the shard. Setting
ContainerImageOverride to an image outside AMP's own image family also requires the
Platform.TemplatesManage permission, because that image decides what code the container runs.
    Moving the shard to Native is refused when its template must run in a container on the owning
spire, and also when the template or the spire record cannot be read, since the policy is then unknown.

**Permission:** `infra.write`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Request body** (required, `application/json`): [ShardInfraSettings](#schema-shardinfrasettings) — The new infra settings to apply

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `startOnBoot` | boolean |  | Start this shard automatically when the host Spire starts. |
| `autoRestartOnCrash` | boolean |  | If the application reaches the Ready state and then exits unexpectedly (no stop was requested) after running for at least 5 minutes, the agent will automatically restart it instead of stopping. Disable to opt out of this behaviour for shards that intentionally exit after finishing a task. |
| `startupOrder` | integer (int32) |  | Determines the order in which shards start on boot. Lower numbers start first. Shards with the same order start in parallel. Default 100; use lower values for dependencies. |
| `sleepMode` | boolean |  | Let this shard sleep while nobody is connected: AMP stops it, holds its ports so it still              appears online, and starts it again the moment someone tries to connect.               The operator's ONLY say in sleep mode, and the only part of it that belongs to them. Every timing - how long empty before sleeping, how long to doze, how slow a start disqualifies it - is a property of how the application's clients behave, so it is set by the template maintainer on ApplicationLifecycle. This switch does nothing at all unless that template declares SupportsUniversalSleep, and the UI hides it entirely when it does not. |
| `advertisedAddress` | string |  | The address to give people for this shard, overriding everything AMP works out for itself.              A hostname or IP ("play.example.com"), or one with a port pinned ("play.example.com:25565").              Empty = AMP resolves it (see `ShardEndpointResolver`).               It exists because the operator routinely knows something AMP cannot observe: a domain pointed at the machine, a proxy or SRV record in front of it, a load balancer. Declaring one IS them saying they have arranged for it to work, so it is reported as given rather than second-guessed. |
| `advertisedAddressIncludesPort` | boolean |  | Whether a bare string ShardInfraSettings.AdvertisedAddress still carries the shard's port. False when something in front routes it - an SRV record or a reverse proxy - and players connect to the host alone; appending a port then would send them somewhere nothing is listening. Ignored when the advertised value already pins a port of its own. |
| `containerMode` | [ShardContainerMode](#schema-shardcontainermode) |  | Per-shard container mode - always explicitly set |
| `containerImageOverride` | string? |  | Override the container image specified by the template. Null = use the template default. |
| `memoryLimitMb` | integer (int32) |  | Memory limit in MiB. 0 = unlimited (host default) |
| `memoryReservationMb` | integer (int32) |  | Memory reservation (soft limit) in MiB. Must be <= MemoryLimitMb when both non-zero. 0 = no reservation. |
| `memorySwapMb` | integer (int32) |  | Total memory + swap in MiB. Docker/Podman semantics: -1 = unlimited swap; 0 = runtime default (typically 2x MemoryLimitMb); >0 must be >= MemoryLimitMb. Not meaningful when MemoryLimitMb = 0. |
| `cpuLimit` | number/string (double) |  | CPU limit as a fractional number of cores. 0.0 = unlimited |
| `maxUsersCap` | integer (int32) |  | Hard ceiling on concurrent players, enforced by AMP regardless of the application's own `$MaxUsers` setting. 0 = no infra-level cap (the app setting or template default governs). Intended for slot-based hosting where the operator sells a fixed number of player slots and must prevent the tenant from raising the in-game limit above what was purchased. |
| `enableAnalytics` | boolean |  | "Enable Analytics": collect data about users who join the server (where available).               AMP 2's wording, and AMP 2's meaning: with this off the agent records nothing at all, and the shard contributes no sessions to analytics. |
| `userSpecificData` | boolean |  | "Store user-specific data": if enabled, you will be able to see which users spend the most              time on the server and other data related to specific users. However this requires that you              disclose the use of analytics to all users who join the server.               This is the personal-data switch, so in AMP 3 it also governs addresses: with it off the agent does not look a user's IP up at all, and every session is recorded with an unknown country. Names and raw ids are likewise left out. Turning it on does not reach back, so sessions already recorded stay as they were. |
| `minimumSessionTimeForRetention` | integer (int32) |  | "Minimum Session Time for Retention": the minimum session time in seconds for a session to be retained in the analytics data. This can be used to filter out very short sessions that may not be meaningful for analysis. |
| `enableGeoFiltering` | boolean |  | "Enable Geo-Filtering": block connections from certain countries.               Works the way AMP 2's did: when a user connects from a country this shard does not accept, the agent sends string ShardInfraSettings.GeoFilterKickCommand to the application's console. Cruder than a firewall, and better for it: it needs no privilege, works the same on every platform and inside a container, and the game itself tells the user why. It does need the application to log connections with an address and to accept a kick command. |
| `blockedCountries` | string[] |  | "Blocked Countries": countries to block connections from, as ISO 3166-1 alpha-2 codes. |
| `allowedCountries` | string[] |  | "Allowed Countries": countries to allow connections from, as ISO 3166-1 alpha-2 codes. An empty list implies that all countries are allowed except those in the block list. |
| `blockUnknownCountries` | boolean |  | Also block users whose country cannot be worked out. That includes everybody who arrives through a published port without gateway mode, because the address the game sees is the tunnel's. |
| `geoFilterKickMessage` | string |  | "Filtering Message": the message to display to users who are disconnected by geo filtering. {CountryCode} and {CountryName} are replaced with the user's country. |
| `geoFilterKickCommand` | string |  | "Kick Command": the console command that disconnects a filtered user. {UserName}, {UserID}, {Reason}, {CountryCode} and {CountryName} are replaced; {Reason} is the filtering message. |
| `diskLimitMb` | integer (int64) |  | Disk budget for this shard's working directory in MB. 0 = no limit.               Deliberately NOT in the resource-limits block above: those are container knobs handed to Docker/Podman and mean nothing to a native shard, whereas this is measured and enforced by AMP itself from the spire's periodic usage scan, so it works the same either way.Soft by design. AMP warns as the shard approaches the budget and stops it once it is meaningfully over (see DiskLimitPolicy); it never gives the application a full filesystem, because a failed write mid-save is how game servers lose worlds. The trade is that a shard can exceed this between scans - the budget is what AMP acts on, not a wall the application can feel. |
| `importConfigFilesFromSftp` | boolean |  | When a managed, importable configuration file is written over SFTP, read the change back              into this shard's settings instead of leaving it to be overwritten on the next start.               On is the useful default: bulk-editing config is most of what SFTP is for, and without this a write appears to succeed and is then silently lost the next time AMP regenerates the file. Off suits a shard whose files are being moved around wholesale - restoring a backup, syncing a config tree in from elsewhere - where parsing every arriving file back into settings is not what was meant.Per SHARD rather than per spire because it follows the application, not the machine: whether a given server's config should be read back depends on its template and how its owner works with it, and one spire hosts servers that differ. |
| `extraEnvironment` | map<string, string> |  | Additional environment variables injected into the process or container. Merged with any variables defined by the template, with these taking precedence on collision. |
| `updatedAt` | string (date-time) |  |  |
| `updatedByUserId` | string |  |  |

**Responses:**

- `200` The updated infra settings, with `clampRefused` set when the stored `$MaxUsers`     could not be lowered to the new cap (`shards.error.maxUsersClampRefused`) → [ShardInfraSaveResult](#schema-shardinfrasaveresult) (`text/plain`)
- `400` The memory limit, reservation, and swap values are inconsistent; the advertised address     or a country code is invalid; the shard is being moved to `Native` while its template requires a     container on this spire (`shards.error.containerRequired`); or it is being moved to `Native` while     its template or spire cannot be read (`shards.error.containerPolicyUnknown`); or it is being set to a     container mode on a Windows spire, where this release runs no containers (`shards.error.containerModeWithheld`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not write this shard's infra settings; changed an isolation or resource     field without `infra.limits` (`shards.error.infraLimitsDenied`); or chose an image outside AMP's     own family without `Platform.TemplatesManage` (`shards.error.imageOverrideNotAllowed`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Shards/{id}/ip-options`

**Classified bind-address choices for a shard (its spire's IP pool or reported IPs)**

Powers the shard's IP-binding picker: each entry carries internal/external, DHCP/static,
primary and tunnel facts so the UI can tag it. Already filtered for the app (IPv6 omitted
when unsupported, 0.0.0.0 omitted when the template forces an explicit bind).

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The classified bind-address options for the shard → [IpBindingOption](#schema-ipbindingoption)[] (`text/plain`)
- `403` The caller may not read this shard's settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or it has no application definition (`shards.error.noSpec`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Shards/{id}/metrics`

**Get the shard's metrics history**

Requires the `view` capability. Held in memory (roughly 2 hours at the agent's 5-second cadence); live samples stream via the ShardHub "Metrics" event.

**Permission:** `view`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |
| `minutes` | query | integer (int32) |  | When greater than zero, trims the returned history to this many minutes; otherwise the full retained window is returned |

**Responses:**

- `200` The metrics samples for the requested window → [ShardMetricsSample](#schema-shardmetricssample)[] (`text/plain`)
- `403` The caller may not view this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` Not Found → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/shards/{id}/migration`

**Get the shard's migration record**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard to query |

**Responses:**

- `200` The shard's current migration record
- `204` No migration record exists for this shard
- `403` The caller lacks the settings-read capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `204`

---

#### `POST /api/shards/{id}/migration`

**Begin a copy-and-validate migration**

Migrates the shard to the target datastore. The owning spire host validates synchronously
(shard stopped, target exists, not already migrating) and answers accept/reject; on accept it has
written the migration record and started the copy, whose progress the UI then polls.
The spire must be reachable and accept the command. A no-start guard means the migration
is refused unless the shard is stopped, and a shard already migrating is refused as a conflict.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard to migrate |

**Request body** (required, `application/json`): [StartMigrationRequest](#schema-startmigrationrequest) — The target datastore to copy into

| Field | Type | Required | Description |
|---|---|---|---|
| `targetDatastoreId` | string (uuid) | yes | The datastore to copy the shard's working directory into |

**Responses:**

- `202` The spire accepted the migration and started the copy
- `403` The caller lacks the Move capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The spire refused the migration (e.g. shard running, target missing, or already migrating) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The owning spire was unreachable or did not answer in time

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `DELETE /api/shards/{id}/migration`

**Clear a finished migration record**

Dismissing removes the record so the shard's migration UI returns to a clean state; it does not undo a completed migration.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard whose migration record to clear |

**Responses:**

- `204` The migration record was cleared
- `403` The caller lacks the Move capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/shards/{id}/migration/cancel`

**Cancel a running migration**

Cancellation is audited. If no migration is currently running for the shard the request is a conflict.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard whose running migration to cancel |

**Responses:**

- `202` Cancellation was requested
- `403` The caller lacks the Move capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` No migration is currently running for this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/shards/{id}/migration/spire`

**Move a shard to another spire**

Begins a CROSS-SPIRE move: the destination spire pulls the shard's files from the spire it
currently lives on, validates them, then takes ownership. The shard must be stopped: a running
shard is refused unless the request says to stop it, in which case the stop is sent to the
source spire, audited as `ShardStopRequested` with the move as its reason, and waited for
(the shard's stop grace plus a margin) before the move is sent; a stop that does not complete
in time leaves the shard where it is. The Move capability is anchored to the shard's current
(source) spire, and the caller must also hold `shards.create` on the destination spire,
which receives the command and does the placement work. Moving to the spire the shard already
lives on is rejected as a bad request, as is a destination that could not have been deployed
to in the first place - a move is a placement and answers to the same rule.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard to move |

**Request body** (required, `application/json`): [MoveToSpireRequest](#schema-movetospirerequest) — Ends the wait for the stop when the request is abandoned

| Field | Type | Required | Description |
|---|---|---|---|
| `targetSpireId` | string (uuid) | yes | The spire that should take ownership of the shard. Must differ from the shard's current spire |
| `targetDatastoreId` | string (uuid) | yes | The datastore on the destination spire to place the shard's files into |
| `bringBackups` | boolean |  | Copy the shard's snapshot archives to the destination too. Defaults to false: backups are often larger than the shard itself. Snapshots left behind stay on the source spire, and the timeline marks them as unrestorable from here. Default `false`. |
| `stopIfRunning` | boolean |  | Stop the server first when it is running, and wait for it to stop before the move is sent. Without it a running server is refused with `shards.error.moveNeedsStop`. Default `false`. |
| `startAfterMove` | boolean? |  | Start the server on the destination spire once the move has completed. Defaults to the value of StopIfRunning, so a server the move stopped comes back where it now lives. |

**Responses:**

- `202` The destination spire accepted the move and started pulling the files
- `400` The target spire is the shard's current spire, or cannot accept the shard (a public entry node, closed to deployments, at its shard limit, or a Windows spire for an app that needs a Linux container: `shards.error.needsLinuxSpire`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the Move capability on this shard's source spire, or may not create shards on the destination spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The move was refused: the shard is running and the request did not say to stop it (`shards.error.moveNeedsStop`), it did not stop in time (`shards.error.moveStopTimedOut`), or the destination refused it → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The destination spire was unreachable or did not answer in time

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/shards/{id}/migration/targets`

**List datastores this shard can move to**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard whose candidate target datastores to list |

**Responses:**

- `200` The enabled datastores on the shard's spire, excluding its current one
- `403` The caller lacks the settings-read capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Shards/{id}/ports`

**List the shard's ports**

Lists the shard's declared and custom ports with their assigned numbers and public-access flags.
Requires the `ports.read` capability. Joins template metadata (name, description, protocol) to the allocator's
assignments, then adds user-registered custom ports and any assignments with no surviving template metadata. Live
listening state is not included here; it rides the metrics samples (`ListeningPorts`) and the client matches by
port number.

**Permission:** `ports.read`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The shard's ports → [ShardPortInfo](#schema-shardportinfo)[] (`text/plain`)
- `403` The caller may not read this shard's ports → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, and no port assignments exist for it → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PUT /api/Shards/{id}/ports`

**Reassign the shard's ports**

Requires the `ports.write` capability. Guarded: ports must sit inside the spire's pool and must not collide with
another shard on the same spire whose IP binding overlaps (0.0.0.0 overlaps everything). Violations surface as
translated `ApiError` responses. Changes apply at the next start.

**Permission:** `ports.write`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Request body** (required, `application/json`): map<string, integer (int32)> — Map of port ref to new port number

**Responses:**

- `204` The ports were reassigned
- `400` A requested port is out of the spire's pool or collides with another shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not write this shard's ports → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/Shards/{id}/ports/custom`

**Add a custom port**

Requires the `ports.write` capability. A custom port covers something the template does not declare, for example a plugin web interface like Dynmap. Returns the shard's full port list, as `GetPorts` would.

**Permission:** `ports.write`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Request body** (required, `application/json`): [AddCustomPortBody](#schema-addcustomportbody) — The custom port's name, protocol, and optional preferred number

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Display name for the custom port |
| `protocol` | string | yes | Transport protocol; parsed case-insensitively, defaulting to TCP when unrecognised |
| `port` | integer (int32) | yes | Preferred port number, or null to let the allocator assign one |

**Responses:**

- `200` The shard's port list including the new custom port → [ShardPortInfo](#schema-shardportinfo)[] (`text/plain`)
- `403` The caller may not write this shard's ports → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Name` required)

---

#### `DELETE /api/Shards/{id}/ports/custom/{portRef}`

**Remove a custom port**

Requires the `ports.write` capability. Refused with 409 while a port publication still routes to the ref, so the
user must unpublish first and the removal stays a deliberate two-step. Returns the shard's remaining port list on success.

**Permission:** `ports.write`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |
| `portRef` | path | string | yes | The custom port's reference key to remove |

**Responses:**

- `200` The shard's port list after removal → [ShardPortInfo](#schema-shardportinfo)[] (`text/plain`)
- `403` The caller may not write this shard's ports → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or no custom port with that ref → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` A port publication still routes to this ref; unpublish it first → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `PUT /api/Shards/{id}/ports/{portRef}/access`

**Set a port's public access**

Requires the `ports.write` capability. The flag gates publishing and the system firewall. The template's primary application port is always public and cannot be marked private. Returns the shard's full port list on success.

**Permission:** `ports.write`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |
| `portRef` | path | string | yes | The port's reference key |

**Request body** (required, `application/json`): [SetPortAccessBody](#schema-setportaccessbody) — The new public-access flag

| Field | Type | Required | Description |
|---|---|---|---|
| `public` | boolean | yes | True to mark the port publicly accessible, false to make it private |

**Responses:**

- `200` The shard's port list with the updated access flag → [ShardPortInfo](#schema-shardportinfo)[] (`text/plain`)
- `400` The port is the template's primary application port, which stays public → [ApiError](#schema-apierror) (`text/plain`)
- `403` The caller may not write this shard's ports → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or no port with that ref → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/Shards/{id}/purge`

**Purge a soft-deleted shard**

Requires the `delete` capability. Irreversible. The shard must already be soft-deleted (the UI's two-step safety);
a shard that is not yet deleted is refused with 400. Every bus credential the shard was launched with is revoked
first, here, since only this host can re-sign the bus account; the spire host then kills any lingering agent,
removes all KV records, and deletes the working directory, resolving the spire-local path before the KV purge.

**Permission:** `delete`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `202` The purge command was accepted for dispatch to the spire
- `400` The shard has not been soft-deleted first → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not purge this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The shard's bus credentials could not be revoked, or the spire refused the command → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The owning spire is unreachable or did not respond in time

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/Shards/{id}/restart`

**Restart the shard's app**

Requires the `power.restart` capability. A suspended shard is rejected. The admin-password policy and effective
config are rebuilt centrally before the restart is sent; the outcome arrives asynchronously over the lifecycle event
stream. A running agent restarts the application in its container, unless the gateway route the run was launched
with differs from the shard's binding now: a route engages only at launch, so that restart, like one with no agent
answering, goes to the spire, which stops the shard and launches it again. A running agent older than the newest
its spire would launch (the verdict `GET api/shards/{id}/agent` reports) also restarts through the spire
whenever the spire is online, so the restart adopts the newer agent; with the spire offline it restarts in its
container on the same agent, and the agent stays out of date. The ShardRestartRequested audit event names the
route taken.

**Permission:** `power.restart`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `202` The restart command was accepted for dispatch to the spire
- `403` The caller may not restart this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The spire refused the command → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The owning spire is unreachable or did not respond in time

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/Shards/{id}/restore`

**Restore a soft-deleted shard**

Requires the `delete` capability, which it shares with soft-delete.

**Permission:** `delete`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The restored shard → [DeployedShard](#schema-deployedshard) (`text/plain`)
- `403` The caller may not restore this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Shards/{id}/settings`

**Get the shard's app settings**

Returns each template setting spec merged with its current effective value.
Requires the `settings.read` capability. Hidden settings are omitted. The IP-binding field is rewritten into a
select sourced from the spire's IP pool (or reported addresses), `$MaxUsers` is clamped to any host-set cap, and
stored password values are replaced with a random-length asterisk mask so neither the value nor its length leaks.

**Permission:** `settings.read`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The effective settings list for the shard → [EffectiveSetting](#schema-effectivesetting)[] (`text/plain`)
- `403` The caller may not read this shard's settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or it has no application definition (`shards.error.noSpec`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PATCH /api/Shards/{id}/settings`

**Update the shard's app settings**

Write is enforced per setting (a role may hold write on one category but not the whole shard), so the whole patch is
rejected with 403 if any changed key is denied. Password fields left at their asterisk mask are treated as unchanged,
hidden fields are stripped, and submitted select/checkbox values are validated against the template's allowed set.
Boundary violations (`ForceIPBinding` set to 0.0.0.0, or `$MaxUsers` above a host cap) are rejected with 400
and audited. Each real change writes a per-setting audit event, and for apps that support it, non-secret changes are
pushed live to the running app.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Request body** (required, `application/json`): map<string, string> — Map of setting field name to new value; omitted fields are left unchanged

**Responses:**

- `204` The settings were applied
- `400` A submitted value violated a template constraint or host cap → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not write one or more of the submitted settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or it has no application definition (`shards.error.noSpec`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Shards/{id}/settings/locale`

**The template's own translations for this shard's settings**

Requires the `settings.read` capability. Separate from the settings payload because it is
     per-template rather than per-setting, and because most templates have none - fetching it
     alongside every settings load would send an empty object on nearly every request.

     Shipped to the browser rather than resolved here, so switching language re-renders without
refetching, and so a key with no entry falls back the same way in both places.

**Permission:** `settings.read`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard whose template's translations are wanted |

**Responses:**

- `200` The locale table, keyed by language; empty when the template has none → object (`text/plain`)
- `403` The caller cannot read this shard's settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or it has no application definition (`shards.error.noSpec`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Shards/{id}/settings/{field}/candidates`

**The versions a manifest-driven setting can be set to**

Requires the `settings.read` capability. Fetched on demand rather than shipped with the
     settings payload: a page with several version pickers would otherwise fan out that many
     upstream requests before the user has opened any of them.

     A list that could not be refreshed is still returned when a cached copy exists, with
failureReason and its age, because a possibly-stale list the user can pick from beats an
empty box. A dependent list whose parent setting has no value yet comes back empty with the
reason naming the parent.

**Permission:** `settings.read`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |
| `field` | path | string | yes | Field name of the setting whose options are wanted |
| `refresh` | query | boolean |  | Bypass the cache and re-read the upstream manifest now |

**Responses:**

- `200` The candidate list, possibly empty with a reason → [VersionCandidateList](#schema-versioncandidatelist) (`text/plain`)
- `403` The caller may not read this shard's settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard or setting, the shard has no application definition (`shards.error.noSpec`), or the setting has no version source → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (shards.error.noValueSource)

---

#### `GET /api/Shards/{id}/sftp`

**How to connect to this shard over SFTP**

Gives the address of the shard's OWNING spire. Any spire's SFTP endpoint can reach any shard
the user may access, but the owner's is the path with no bus hop, so it is the one offered.

Requires `files.read` somewhere in the shard: someone with no file access has nothing to
connect for.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard to connect to |

**Responses:**

- `200` The connection details → [SftpConnectDto](#schema-sftpconnectdto) (`text/plain`)
- `403` The caller may not read this shard's files → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Shards/{id}/sleep`

**Put the shard to sleep now**

Requires the `power.stop` capability - sleeping stops the application, and can do nothing
     a stop cannot. The shard's agent keeps running and holds the shard's ports, answering server
     queries so it still appears online, and starts the application again the moment someone tries
     to connect (or when `/wake` is called).

     Only meaningful for a template that declares SupportsUniversalSleep. Sleep can also be
unavailable for the current run - most often because the server took longer to start than the
template's threshold, so waking it would leave users waiting past the point their game gives
up. Both are rejected here with the reason, and the same reason is on the shard's state
(Sleep) so the UI can explain it beside the setting.

**Permission:** `power.stop`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `202` The sleep command was accepted and sent to the agent
- `403` The caller may not stop this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` This application does not support sleep, or sleep is not available for this run → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` No agent is responding for this shard

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/Shards/{id}/start`

**Start the shard's app**

Requires the `power.start` capability. A suspended shard is rejected. If the shard already appears to be
actively running, the request is refused with 409. Otherwise the admin-password policy and effective config are
rebuilt centrally (a remote spire lacks the template catalogue) and a Start command is published to the spire;
the actual start outcome arrives asynchronously over the lifecycle event stream.

**Permission:** `power.start`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `202` The start command was accepted for dispatch to the spire
- `403` The caller may not start this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The shard is already running, or the spire refused the command → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The owning spire is unreachable or did not respond in time

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Shards/{id}/state`

**Get the shard's runtime state**

Requires the `view` capability. When no state has been reported yet, an empty default ShardState is returned rather than a 404.

**Permission:** `view`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The shard's current state, or an empty default when none has been reported → [ShardState](#schema-shardstate) (`text/plain`)
- `403` The caller may not view this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` Not Found → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Shards/{id}/stop`

**Stop the shard's app**

Requires the `power.stop` capability. Publishes a Stop command to the spire; the stop outcome arrives asynchronously over the lifecycle event stream.

**Permission:** `power.stop`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `202` The stop command was accepted for dispatch to the spire
- `403` The caller may not stop this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The spire refused the command → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The owning spire is unreachable or did not respond in time

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/Shards/{id}/suspend`

**Suspend the shard**

Places an administrative hold: the shard refuses start, restart, wake and an in-place update
    (unless the caller holds `suspend.bypass`), scheduled starts and restarts refuse outright,
    and so does the owning spire for a caller without the right. A running shard is stopped, under
    the `suspend` capability; a stop the spire does not accept is reported on the shard.
    Requires the suspend capability - deliberately NOT infra.write, since a shard's own
operator typically holds that and the point of a hold is that its subject cannot lift it.

**Permission:** `suspend`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The updated shard → [DeployedShard](#schema-deployedshard) (`text/plain`)
- `403` The caller may not suspend this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/Shards/{id}/unsuspend`

**Lift the shard's suspension**

Clears the administrative hold. The shard is not started - that stays a separate, deliberate act.
Requires the `suspend` capability.

**Permission:** `suspend`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The updated shard → [DeployedShard](#schema-deployedshard) (`text/plain`)
- `403` The caller may not suspend this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/Shards/{id}/update`

**Run the shard's app update**

Requires the `update.run` capability. Re-resolves the spec from the catalogue so template changes reach existing
shards, creates a top-level Update task whose sub-tasks are the manifest's update stages, then publishes the Update
command; the agent's lifecycle events advance the task as stages run. A game that requires an owning Steam account
signs in with the shared credential the shard points at (`PUT /api/shards/{id}/credential`), or with the
caller's own newest SteamCMD credential, else the tenant's newest Public one, which the shard is then pointed at;
with none, the update is refused with 409 `shards.error.steamLoginRequired` before any command is sent, so
the UI can ask for a credential.
An update of a suspended shard, in place or cold, is rejected with `shards.error.suspended` unless the caller
holds `suspend.bypass` by name, as a start is.

**Permission:** `update.run`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `202` The update was accepted; the response carries the created task id
- `403` The caller may not update this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` An update is already running on the shard, the game needs an owning Steam account and the shard has none, or the spire refused the command → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The owning spire is unreachable or did not respond in time

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Shards/{id}/users`

**Get the shard's connected users**

Requires the `view` capability. Populated only for templates that define a user-join regex; returns an empty array when the shard is stopped or its template does not track users.

**Permission:** `view`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `200` The connected-users snapshot (possibly empty) → [ConnectedUser](#schema-connecteduser)[] (`text/plain`)
- `403` The caller may not view this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` Not Found → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Shards/{id}/wake`

**Wake a sleeping shard now**

Requires the `power.start` capability - waking starts the application. A suspended shard
is rejected. Not normally needed: a sleeping shard wakes itself when a user tries to connect.
This is for an operator who wants it up before anyone arrives.

**Permission:** `power.start`

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The shard's unique identifier |

**Responses:**

- `202` The wake command was accepted and sent to the agent
- `403` The caller may not start this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The shard is not asleep → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` No agent is responding for this shard

**Live:** no token → `401` · token → `404` (Not Found)

---

### 12-Files & Backups

_Browsing shard files and taking, restoring, and downloading snapshots._

#### `GET /api/shards/{shardId}/files`

**List a directory's entries**

Requires `files.read` on the directory. The returned list omits any entry the caller
cannot read (a denied subdirectory simply does not appear) and the AMP-managed `.amp/`
bookkeeping directory. Path is relative to the shard working directory; an empty path lists the root.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose files are being browsed |
| `path` | query | string |  | Directory to list, relative to the shard working directory. Empty lists the root |

**Responses:**

- `200` The readable entries in the directory → [FileEntry](#schema-fileentry)[] (`text/plain`)
- `403` The caller lacks read access to the directory → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `DELETE /api/shards/{shardId}/files`

**Delete a file or directory**

Requires `files.delete` on the path. Directories are removed with everything in them,
except a folder another disk is mounted at: nothing inside a mount is deleted, the folders
above it stay, and the request is refused with `shards.error.deleteStoppedAtMount`
naming the mounts once the rest has gone, or with `shards.error.deleteIsMount` and
nothing removed when the path is itself a mount, either recorded as a
`ShardFileDeleteStoppedAtMount` audit event. A delete that completes records a `ShardFileDeleted` audit event. Path is
relative to the shard working directory.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose entry is being deleted |
| `path` | query | string |  | Path to delete, relative to the shard working directory |

**Responses:**

- `204` The entry was deleted
- `403` The caller lacks delete access to the path → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `path` required)

---

#### `GET /api/shards/{shardId}/files/acl`

**Read the access rules for a path**

Requires `files.read` on the path (or traversal toward a grant beneath it) plus the
roles-read capability. Each principal reports all three verbs with the effective outcome,
whether the deciding rule names this exact path, and the path it came from - so the UI can
distinguish an inherited allow from one set here without the caller resolving anything.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose file is being described |
| `path` | query | string |  | Path to describe, relative to the shard working directory |

**Responses:**

- `200` The principals with access to this path → [FileAclDto](#schema-fileacldto) (`text/plain`)
- `403` The caller may not read this path, or lacks the roles-read capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PUT /api/shards/{shardId}/files/acl`

**Change the access rules for a path**

Requires `files.read` on the path plus the roles-edit capability, and obeys the same
delegation rule as the role editor: every rule the change touches, added or removed, allow
or deny, must name access you hold yourself. A refused change is audited.

A change that would take away the caller's OWN read of this path is refused outright with
409 - there is no override. Removing someone's access is a job for whoever manages their
roles, so the person losing sight of a folder is never also the only one who could restore
it. Each altered rule is audited individually.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose file is being changed |
| `path` | query | string |  | Path to change, relative to the shard working directory |

**Request body** (required, `application/json`): [FileAclUpdateBody](#schema-fileaclupdatebody) — The changes, and whether a self-lockout has been accepted

| Field | Type | Required | Description |
|---|---|---|---|
| `changes` | [AclChange](#schema-aclchange)[] | yes | Each sets one principal's one verb to allow, deny, or inherit |

**Responses:**

- `200` The rules were applied; the updated principal list is returned → [FileAclDto](#schema-fileacldto) (`text/plain`)
- `400` A change touches access the caller does not hold → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not read this path, or lacks the roles-edit capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The change would take away the caller's own access, which nobody may do to themselves → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Changes` required)

---

#### `POST /api/shards/{shardId}/files/archive`

**Create a zip archive in the working directory**

Zips the given files/folders into a NEW archive written into the working directory
(in-place "Create Archive"), as opposed to Task<IActionResult> FileManagerController.Zip(Guid shardId, PathsBody body, CancellationToken ct = default(CancellationToken)) which streams a download.
This is an internal copy - bytes never leave the server - so it follows the same model
as Task<IActionResult> FileManagerController.Copy(Guid shardId, MoveBody body, CancellationToken ct = default(CancellationToken)): per-path READ authz on each source, WRITE authz on the
destination, and no file-type (download) policy. The .zip extension is owned by the
caller; we just write wherever Destination points.
Requires `files.write` on the destination and `files.read` on each source; every source
root and recursed descendant is read-checked so a denied subtree is never folded in. No download
file-type policy applies because nothing leaves the server. Records a `ShardArchiveCreated`
audit event.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose files are being archived |

**Request body** (required, `application/json`): [ArchiveBody](#schema-archivebody) — The source paths and the destination archive path

| Field | Type | Required | Description |
|---|---|---|---|
| `paths` | string[] | yes | Source paths to include, relative to the shard working directory. Folders are included recursively |
| `destination` | string | yes | Destination path for the new zip, relative to the shard working directory. The caller owns the .zip extension |

**Responses:**

- `204` The archive was written to the destination
- `400` No paths were supplied, or the destination is empty → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks write access to the destination → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Paths` required)

---

#### `GET /api/shards/{shardId}/files/archive-info`

**Summarise an archive without unpacking it**

Requires `files.read` on the archive. Reads only the archive's index, which lives at the
end of the file, so this costs a seek rather than a full read even for a multi-gigabyte
archive on a remote spire. Intended for the confirmation shown before an extract.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard holding the archive |
| `path` | query | string |  | The archive, relative to the shard working directory |

**Responses:**

- `200` The archive's file count and total extracted size → [ArchiveInfoDto](#schema-archiveinfodto) (`text/plain`)
- `400` The path does not name a browsable archive → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks read access to the archive → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `path` required)

---

#### `GET /api/shards/{shardId}/files/content`

**Read a file's text content**

Requires `files.read` on the path. Intended for text files (the file editor); the content
is returned as UTF-8 text. Path is relative to the shard working directory.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose file is being read |
| `path` | query | string |  | Path to read, relative to the shard working directory |

**Responses:**

- `200` The file's text content → [ContentBody](#schema-contentbody) (`text/plain`)
- `403` The caller lacks read access to the path → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `path` required)

---

#### `PUT /api/shards/{shardId}/files/content`

**Overwrite a file's text content**

Requires `files.write` on the path. Writes the body text as UTF-8, creating the file if it does
not exist; a null `Content` is treated as empty. Unless the caller holds
`files.bypassrestrictions`, the tenant upload file-type policy judges the path's name and the
text's leading bytes exactly as it judges an upload, and a refusal answers 403 with the upload's
code and writes nothing. Records a `ShardFileEdited` audit event.
Path is relative to the shard working directory.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose file is being written |
| `path` | query | string |  | Path to write, relative to the shard working directory |

**Request body** (required, `application/json`): [ContentBody](#schema-contentbody) — The new text content

| Field | Type | Required | Description |
|---|---|---|---|
| `content` | string | yes | The full UTF-8 text of the file. On write, a null value is treated as empty |

**Responses:**

- `204` The file was written
- `403` The caller lacks write access to the path, or the file-type policy refused the file → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `path` required)

---

#### `POST /api/shards/{shardId}/files/copy`

**Copy a file or directory**

A copy reads the source and writes the destination, so it requires `files.read` on the source
and `files.write` on the destination; the destination check returns 403 on its own if denied.
Unless the caller holds `files.bypassrestrictions`, a file's destination name is judged by the
upload file-type policy's extension list exactly as a move's is: a refusal answers 403 with
`files.error.uploadExtensionBlocked`, copies nothing and records a `ShardFileTypeRefused`
audit event. Records a `ShardFileCopied` audit event. Paths are relative to the shard
working directory.

When the source is inside an archive this is a copy OUT of it - the same operation
`POST /extract` performs, scoped to one file or folder - so it answers 200 with the
per-entry breakdown instead of 204, and the bytes are copied by whichever machine holds the
disk, under the whole upload policy per entry as an extract is. Copying INTO an archive is
refused: nothing inside one can be changed in place.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose entry is being copied |

**Request body** (required, `application/json`): [MoveBody](#schema-movebody) — The source (`From`) and destination (`To`) paths

| Field | Type | Required | Description |
|---|---|---|---|
| `from` | string | yes | Source path, relative to the shard working directory |
| `to` | string | yes | Destination path, relative to the shard working directory |

**Responses:**

- `200` The source was inside an archive: what was copied out, and what was skipped → [ExtractResultDto](#schema-extractresultdto) (`text/plain`)
- `204` The entry was copied
- `403` The caller lacks read access to the source or write access to the destination, or the file-type policy refused the destination name → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `To` required)

---

#### `GET /api/shards/{shardId}/files/download`

**Download a single file**

Requires `files.read` on the path. Unless the caller holds `files.bypassrestrictions`,
the tenant download file-type policy sniffs the file's leading bytes and may block it with 403.
The download name is the last path segment. Path is relative to the shard working directory.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose file is being downloaded |
| `path` | query | string |  | Path to download, relative to the shard working directory |

**Responses:**

- `200` The file stream
- `403` The caller lacks read access, or the file-type policy blocked the download → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `path` required)

---

#### `POST /api/shards/{shardId}/files/extract`

**Extract an archive, or part of one, onto the shard's disk**

Requires `files.read` on the archive and `files.write` on the destination, plus
`files.write` on each individual destination file - a denied destination is skipped
rather than failing the whole extract, so a carve-out cannot be written through by unpacking
an archive over it. Unless the caller holds `files.bypassrestrictions`, the tenant UPLOAD
file-type policy applies per entry (content is sniffed only when the policy is configured to
look at it). Files AMP writes from the server's settings are skipped, since a plain write
there is overwritten on the next start.

The bytes are copied by whichever machine holds the disk, so extracting a large archive on a
remote spire does not move it across the network. Records a `ShardArchiveExtracted` audit
event.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose archive is being extracted |

**Request body** (required, `application/json`): [ExtractBody](#schema-extractbody) — The archive (or part of it), the destination, and the overwrite choice

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | The archive, or a folder/file within it. A path inside an archive extracts just that part |
| `destination` | string? | yes | Target directory. Optional; defaults to the directory holding the archive, i.e. extract in place |
| `overwrite` | boolean | yes | Whether files already at the destination are replaced. When false they are left alone and counted as skipped |

**Responses:**

- `200` What was extracted, and what was skipped and why → [ExtractResultDto](#schema-extractresultdto) (`text/plain`)
- `400` The path does not name a browsable archive → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks read access to the archive or write access to the destination → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Path` required)

---

#### `POST /api/shards/{shardId}/files/folder`

**Create a directory**

Requires `files.write` on the target path. Any missing parent directories are created too.
Records a `ShardFolderCreated` audit event. Path is relative to the shard working directory.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard in which to create the folder |

**Request body** (required, `application/json`): [PathBody](#schema-pathbody) — The folder path to create

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Path relative to the shard working directory (forward-slash separated) |

**Responses:**

- `204` The folder was created
- `403` The caller lacks write access to the path → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Path` required)

---

#### `GET /api/shards/{shardId}/files/ignore`

**Read a directory's snapshot-ignore status**

Reports the ignore status of the entries in a directory: which are AMP base-data (auto, from
`.ampsnapshotignore`) and which the user has excluded (`.usersnapshotignore`).
User exclusion wins, so an entry never appears in both lists.
Requires `files.read` on the directory. Only entries the caller may read are considered, and
AMP-internal entries are skipped. Path is relative to the shard working directory.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose ignore status is being read |
| `path` | query | string |  | Directory to evaluate, relative to the shard working directory. Empty means the root |

**Responses:**

- `200` The AMP base-data and user-excluded entries in the directory → [IgnoreStatusDto](#schema-ignorestatusdto) (`text/plain`)
- `403` The caller lacks read access to the directory → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/shards/{shardId}/files/ignore`

**Add or remove user snapshot exclusions**

Adds or removes the given paths from `.usersnapshotignore` (excluded from ALL
snapshots). Directories are stored with a trailing `/` so the whole subtree is
covered. The file is AMP-managed (regenerated with a fixed header); per-path READ is
required, and writing the file itself is WRITE-authorized.
Requires `files.write` on `.usersnapshotignore` and `files.read` on each toggled
path (a denied path returns 403). AMP-internal paths are skipped. The file is rewritten with its
managed header. Records a `ShardSnapshotIgnoreChanged` audit event.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose exclusion list is being changed |

**Request body** (required, `application/json`): [IgnoreToggleBody](#schema-ignoretogglebody) — The paths to toggle and whether to exclude or include them

| Field | Type | Required | Description |
|---|---|---|---|
| `paths` | string[] | yes | Paths to toggle, relative to the shard working directory |
| `ignored` | boolean | yes | True to exclude the paths from all snapshots, false to include them again |

**Responses:**

- `204` The exclusion list was updated
- `400` No paths were supplied → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks write access to the ignore file, or read access to a toggled path → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Paths` required)

---

#### `GET /api/shards/{shardId}/files/managed`

**List the AMP-managed config files in a directory**

Lets the file manager mark the files AMP writes before the user opens one: a generated file
is served read-only (AMP rewrites it wholesale from the settings on every start), and a
merged one is saved through `POST /api/shards/{id}/config/import` so the user's change
reaches the settings instead of being overwritten.

Requires `files.read` on the directory. Only entries directly in it are returned, and
only ones the caller may read. A shard with no app specification returns an empty list.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose files are being browsed |
| `path` | query | string |  | Directory to report on, relative to the shard working directory |

**Responses:**

- `200` The managed files in the directory → [ManagedFileDto](#schema-managedfiledto)[] (`text/plain`)
- `403` The caller lacks read access to the directory → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/shards/{shardId}/files/move`

**Move or rename a file or directory**

A move mutates both ends, so it requires `files.write` on both the source and the destination;
the destination check returns 403 on its own if denied. Unless the caller holds
`files.bypassrestrictions`, a file's destination name is judged by the upload file-type
policy's extension list, so a blocked type cannot be renamed into place: a refusal answers 403
with `files.error.uploadExtensionBlocked`, moves nothing and records a
`ShardFileTypeRefused` audit event. A folder is not judged by its name. Records a
`ShardFileMoved` audit event. Paths are relative to the shard working directory.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose entry is being moved |

**Request body** (required, `application/json`): [MoveBody](#schema-movebody) — The source and destination paths

| Field | Type | Required | Description |
|---|---|---|---|
| `from` | string | yes | Source path, relative to the shard working directory |
| `to` | string | yes | Destination path, relative to the shard working directory |

**Responses:**

- `204` The entry was moved
- `403` The caller lacks write access to the source or the destination, or the file-type policy refused the destination name → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `To` required)

---

#### `GET /api/shards/{shardId}/files/stat`

**Get a file or directory's metadata**

Requires `files.read` on the path. Returns 404 when the shard is missing or the path does
not resolve to an existing entry. Path is relative to the shard working directory.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose file is being inspected |
| `path` | query | string |  | Path to stat, relative to the shard working directory |

**Responses:**

- `200` Metadata for the entry → [FileEntry](#schema-fileentry) (`text/plain`)
- `403` The caller lacks read access to the path → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist, or the path was not found → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/shards/{shardId}/files/upload`

**Upload files into a directory**

Requires `files.write` on the target directory and on each computed destination; the whole
drop is rejected with 403 if any destination is denied. Unless the caller holds
`files.bypassrestrictions`, the tenant upload file-type policy sniffs each file and may block
the drop with 403 before anything is written. For folder drops, each file's relative path is
supplied in the matching `paths` entry; otherwise the file name is used. Records a
`ShardFileUploaded` audit event. Suited to smaller payloads; for large files use the
chunked upload-start / upload-chunk / upload-commit flow.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard receiving the files |
| `path` | query | string |  | Target directory, relative to the shard working directory. Optional; empty means the root |

**Request body** (required, `application/x-www-form-urlencoded`): object or object — Optional per-file relative destination paths (aligned by index with files), used for folder drops

**Responses:**

- `204` All files were written
- `403` A destination is not writable, or the file-type policy blocked a file → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/shards/{shardId}/files/upload-chunk`

**Append a chunk to an upload**

The chunk is the raw request body (max 16 MB). The session is validated against the shard, tenant, and calling
user, so no per-path check runs here (that happened at upload-start and runs again at upload-commit).
Local path: appends to the temp file and returns cumulative bytes on disk. Bus path (double-buffer):
begins forwarding the previous chunk to the agent over NATS while reading this chunk from the HTTP
body, returning only when both are done so the browser gets accurate backpressure from the agent.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the session belongs to |
| `session` | query | string |  | The upload session ID returned by upload-start |

**Responses:**

- `200` The cumulative number of bytes received so far
- `403` The session belongs to a different shard, tenant, or user → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such upload session (unknown or expired) → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `session` required)

---

#### `POST /api/shards/{shardId}/files/upload-commit`

**Finalize a chunked upload**

Validates the session (shard/tenant/user) and re-checks `files.write` on the destination, then
verifies the total received matches the declared size (400 if not). Unless the caller holds
`files.bypassrestrictions`, the upload file-type policy is applied to the captured head bytes
and may block the file with 403. Local path reads the assembled temp file and writes it; bus path
flushes the last buffered chunk over NATS (earlier chunks were forwarded during upload-chunk), and
every bus-path refusal (lost write access, an AMP-managed destination, the file-type policy) deletes
the partial destination those earlier chunks wrote before answering.
On success records a `ShardFileUploaded` audit event.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the session belongs to |
| `session` | query | string |  | The upload session ID returned by upload-start |

**Responses:**

- `204` The file was written and the session removed
- `400` The uploaded byte count does not match the declared size → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The session belongs to another shard/tenant/user, write access was lost, or the file-type policy blocked the file → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such upload session (unknown or expired) → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `session` required)

---

#### `POST /api/shards/{shardId}/files/upload-start`

**Start or resume a chunked upload**

Returns a session ID and the number of bytes already received so the client can skip ahead on
reconnect. Requires `files.write` on the destination. For bus-path (remote agent) uploads the session
operates in pipeline mode: the destination is preallocated to its final size and each chunk is
forwarded to the agent over NATS as the next arrives from the browser, so the browser experiences
true end-to-end throughput rather than local disk write speed. Unless the caller holds
`files.bypassrestrictions`, a bus-path destination whose name the upload file-type policy
blocks is refused here with the upload's code and audit, before anything is allocated; the
content is judged at commit. The response includes `via`
(`agent` or `direct`) so the client can surface the routing.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard receiving the file |

**Request body** (required, `application/json`): [UploadStartBody](#schema-uploadstartbody) — The destination path and expected total size

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Destination path for the assembled file, relative to the shard working directory |
| `size` | integer (int64) | yes | Expected total file size in bytes, used to preallocate the destination and validate completion |

**Responses:**

- `200` The session ID, bytes already received, and transfer routing
- `403` The caller lacks write access to the destination, or the file-type policy blocked its name → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Path` required)

---

#### `POST /api/shards/{shardId}/files/zip`

**Download a zip of files or folders**

Requires `files.read`; the shard is resolved from and authorized against the first path, and
every requested root plus every recursed descendant is read-checked, so a denied subtree is skipped
rather than exfiltrated. Unless the caller holds `files.bypassrestrictions`, the download
file-type policy is applied per file and blocked files are omitted from the archive. Entry names
are made relative to the parent of the first path. Records a `ShardFilesDownloaded` audit event.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose files are being zipped |

**Request body** (required, `application/json`): [PathsBody](#schema-pathsbody) — The paths to include

| Field | Type | Required | Description |
|---|---|---|---|
| `paths` | string[] | yes | Paths relative to the shard working directory. Folders are included recursively |

**Responses:**

- `200` A `files.zip` archive stream
- `400` No paths were supplied → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks read access to the first path → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard does not exist → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Paths` required)

---

#### `GET /api/shards/{shardId}/snapshots`

**Get the snapshot timeline**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose timeline to return |

**Responses:**

- `200` The shard's snapshot graph (nodes, HEAD, and settings) → [ShardSnapshotGraph](#schema-shardsnapshotgraph) (`text/plain`)
- `403` The caller lacks the snapshots-read capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/shards/{shardId}/snapshots`

**Take a snapshot**

Capture runs in the background: the response returns immediately with the new node while
the spire performs a quiesce-or-stop and writes the snapshot zip. Poll the graph for the
node's status to know when it finishes.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard to snapshot |

**Request body** (required, `application/json`): [TakeBody](#schema-takebody) — What to capture and how to label the snapshot

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | [SnapshotKind](#schema-snapshotkind) | yes | Whether this is a full snapshot or a delta against its parent |
| `content` | [SnapshotContent](#schema-snapshotcontent) | yes | Flags for which parts to capture: game state, base install data, and/or AMP configuration |
| `name` | string? | yes | Optional friendly name for the snapshot node |
| `description` | string? | yes | Optional longer description shown on the timeline |
| `sticky` | boolean | yes | When true the snapshot is pinned and exempt from automatic retention pruning |

**Responses:**

- `202` Capture has been queued; the returned node starts in the Running state → [ShardSnapshot](#schema-shardsnapshot) (`text/plain`)
- `403` The caller lacks the snapshots-create capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/shards/{shardId}/snapshots/settings`

**Get the shard's snapshot settings**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose snapshot settings to return |

**Responses:**

- `200` The shard's snapshot storage and retention settings → [ShardSnapshotSettings](#schema-shardsnapshotsettings) (`text/plain`)
- `403` The caller lacks the snapshots-read capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PUT /api/shards/{shardId}/snapshots/settings`

**Update the shard's snapshot settings**

Snapshot schedule and retention settings ride the snapshots-create capability.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose snapshot settings to update |

**Request body** (required, `application/json`): [ShardSnapshotSettings](#schema-shardsnapshotsettings) — The replacement storage and retention settings

| Field | Type | Required | Description |
|---|---|---|---|
| `maxBackupSizeMb` | integer (int64) |  | Maximum uncompressed size of a single snapshot, MB. 0 = no limit |
| `maxTotalStorageMb` | integer (int64) |  | Maximum aggregate snapshot storage for this shard, MB. 0 = no limit |
| `stopWhenNoQuiesce` | boolean |  | When the app declares no quiescence command, stop the shard for the duration of the snapshot (consistent capture) rather than snapshotting it hot. Default true. |

**Responses:**

- `200` The saved settings → [ShardSnapshotSettings](#schema-shardsnapshotsettings) (`text/plain`)
- `403` The caller lacks the snapshots-create capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `DELETE /api/shards/{shardId}/snapshots/{snapshotId}`

**Delete a snapshot**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the snapshot belongs to |
| `snapshotId` | path | string (uuid) | yes | The snapshot node to delete |

**Responses:**

- `204` The snapshot was deleted
- `403` The caller lacks the snapshots-delete capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/shards/{shardId}/snapshots/{snapshotId}/download`

**Download a snapshot's zip**

The downloaded zip lets a user restore the snapshot by hand.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the snapshot belongs to |
| `snapshotId` | path | string (uuid) | yes | The snapshot node whose zip to download |

**Responses:**

- `200` The snapshot zip stream (`application/zip`)
- `403` The caller lacks the snapshots-read capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The shard, snapshot, or its stored zip could not be found → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `PUT /api/shards/{shardId}/snapshots/{snapshotId}/meta`

**Edit a snapshot's metadata**

Editing snapshot metadata rides the snapshots-create capability, since it is part of managing the timeline.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the snapshot belongs to |
| `snapshotId` | path | string (uuid) | yes | The snapshot node to edit |

**Request body** (required, `application/json`): [MetaBody](#schema-metabody) — The new name, description, and sticky flag

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string? | yes | New friendly name for the snapshot node |
| `description` | string? | yes | New longer description for the snapshot node |
| `sticky` | boolean | yes | When true the snapshot is pinned and exempt from automatic retention pruning |

**Responses:**

- `204` The snapshot metadata was updated
- `403` The caller lacks the snapshots-create capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/shards/{shardId}/snapshots/{snapshotId}/restore`

**Restore a snapshot**

Before rewinding, the service captures an automatic PreRestore snapshot of the current state
so the restore itself is reversible, then moves HEAD onto the target node. Restore runs in the
background; poll the graph for the returned node's status.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard to restore |
| `snapshotId` | path | string (uuid) | yes | The snapshot node to restore the shard to |

**Responses:**

- `202` Restore has been queued; a PreRestore safety snapshot is taken first → [ShardSnapshot](#schema-shardsnapshot) (`text/plain`)
- `403` The caller lacks the snapshots-restore capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

### 12-Scheduling

_Time and event triggered tasks that run against a shard._

#### `GET /api/shards/{shardId}/schedules`

**List every schedule on the shard**

Requires the shard's `ScheduleRead` capability. Results are ordered by creation time. A
webhook step's `url` and `body` are returned only to a caller who also holds
`ScheduleWrite`; anyone else reads `[withheld]` in their place, listed in
`withheld`, since a webhook URL routinely carries a token.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose schedules to list |

**Responses:**

- `200` The shard's schedules → [ScheduleDto](#schema-scheduledto)[] (`text/plain`)
- `403` The caller lacks the `ScheduleRead` capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/shards/{shardId}/schedules`

**Create a schedule on the shard**

Requires the shard's `ScheduleWrite` capability. The schedule records the caller as both
its creator and its `RunAsUserId`, so every firing executes under the caller's live
permissions. Writes a `ScheduleCreated` audit event whose details hold the schedule's name,
trigger and an outline of its steps.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard to attach the schedule to |

**Request body** (required, `application/json`): [ScheduleBody](#schema-schedulebody) — The schedule definition to create

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Display name for the schedule. Required (rejected if blank) |
| `description` | string? | yes | Optional free-text description; trimmed, stored as empty when omitted |
| `trigger` | [Trigger](#schema-trigger) | yes | What fires the schedule: a cron, interval, or event trigger |
| `steps` | [Step](#schema-step)[] | yes | The ordered step tree executed on each firing (tasks, conditions, switches) |
| `timeout` | integer? | yes | Per-run time budget. Clamped to between one minute and two hours; defaults to 30 minutes when omitted |
| `enabled` | boolean | yes | Whether the schedule is armed. Disabled schedules are stored but never fire |

**Responses:**

- `201` The schedule was created; the body is the new schedule → [ScheduleDto](#schema-scheduledto) (`text/plain`)
- `400` The definition is invalid (bad name, trigger, or step tree), or sends `[withheld]` for a webhook parameter, which a new schedule has no stored value for → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the `ScheduleWrite` capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Name` required)

---

#### `GET /api/shards/{shardId}/schedules/catalog`

**Get the schedule editor catalogs**

Requires the shard's `ScheduleRead` capability.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard whose schedules are being edited |

**Responses:**

- `200` The catalog of available triggers, tasks, and common emitted signals → [CatalogDto](#schema-catalogdto) (`text/plain`)
- `403` The caller lacks the `ScheduleRead` capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or it has no application definition (`shards.error.noSpec`) → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/shards/{shardId}/schedules/preview-firings`

**Preview a trigger's next fire times**

Requires the shard's `ScheduleRead` capability. Used by the editor to preview a trigger the
user is composing; returns up to five upcoming fire times.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard being edited (scopes the capability check) |

**Request body** (required, `application/json`): [PreviewBody](#schema-previewbody) — The trigger to project

| Field | Type | Required | Description |
|---|---|---|---|
| `trigger` | [Trigger](#schema-trigger) | yes | The trigger to project fire times for |

**Responses:**

- `200` The projected fire times → string (date-time)[] (`text/plain`)
- `400` The trigger is invalid (bad cron, sub-minute interval, or unknown event type) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the `ScheduleRead` capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Trigger` required)

---

#### `GET /api/shards/{shardId}/schedules/{scheduleId}`

**Get a single schedule**

Requires the shard's `ScheduleRead` capability. A webhook step's `url` and `body`
are withheld from a caller without `ScheduleWrite`, as in the list.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the schedule belongs to |
| `scheduleId` | path | string (uuid) | yes | The schedule to fetch |

**Responses:**

- `200` The requested schedule → [ScheduleDto](#schema-scheduledto) (`text/plain`)
- `403` The caller lacks the `ScheduleRead` capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or the schedule does not belong to it → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `PUT /api/shards/{shardId}/schedules/{scheduleId}`

**Replace a schedule's definition**

Requires the shard's `ScheduleWrite` capability. Re-binds `RunAsUserId` to the caller,
so editing another user's schedule cannot smuggle in actions the editor could not run themselves.
Clears a step refusal recorded on the schedule, so a refusal that persists is shown and audited
again, and, when the schedule is saved turned on, the reason AMP turned it off. A webhook
`url` or `body` sent as `[withheld]` keeps the value stored for the same step.
Writes a `ScheduleUpdated` audit event whose details list what the edit changed: the name,
the trigger, each step added, removed, moved or changed, the enabled state, and who it now runs
as. A save that changed nothing is recorded with an empty list.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the schedule belongs to |
| `scheduleId` | path | string (uuid) | yes | The schedule to update |

**Request body** (required, `application/json`): [ScheduleBody](#schema-schedulebody) — The replacement definition

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Display name for the schedule. Required (rejected if blank) |
| `description` | string? | yes | Optional free-text description; trimmed, stored as empty when omitted |
| `trigger` | [Trigger](#schema-trigger) | yes | What fires the schedule: a cron, interval, or event trigger |
| `steps` | [Step](#schema-step)[] | yes | The ordered step tree executed on each firing (tasks, conditions, switches) |
| `timeout` | integer? | yes | Per-run time budget. Clamped to between one minute and two hours; defaults to 30 minutes when omitted |
| `enabled` | boolean | yes | Whether the schedule is armed. Disabled schedules are stored but never fire |

**Responses:**

- `200` The updated schedule → [ScheduleDto](#schema-scheduledto) (`text/plain`)
- `400` The definition is invalid (bad name, trigger, or step tree), or sends `[withheld]` for a webhook parameter this schedule has no stored value for → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the `ScheduleWrite` capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or the schedule does not belong to it → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Name` required)

---

#### `DELETE /api/shards/{shardId}/schedules/{scheduleId}`

**Delete a schedule**

Requires the shard's `ScheduleWrite` capability. Writes a `ScheduleDeleted` audit event
whose details hold what the schedule was: its name, trigger and an outline of its steps.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the schedule belongs to |
| `scheduleId` | path | string (uuid) | yes | The schedule to delete |

**Responses:**

- `204` The schedule was deleted
- `403` The caller lacks the `ScheduleWrite` capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or the schedule does not belong to it → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/shards/{shardId}/schedules/{scheduleId}/enabled`

**Enable or disable a schedule**

Requires the shard's `ScheduleWrite` capability. Re-binds `RunAsUserId` to the caller:
switching on another user's schedule makes its later firings the caller's own actions, run
under the caller's live permissions. Turning it on clears the reason AMP turned it off. Writes a
`ScheduleEnabled` or `ScheduleDisabled` audit event depending on the new state.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the schedule belongs to |
| `scheduleId` | path | string (uuid) | yes | The schedule to toggle |

**Request body** (required, `application/json`): [EnabledBody](#schema-enabledbody) — The desired enabled state

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | yes | True to arm the schedule, false to disable it |

**Responses:**

- `200` The enabled state was updated
- `403` The caller lacks the `ScheduleWrite` capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or the schedule does not belong to it → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/shards/{shardId}/schedules/{scheduleId}/history`

**Get a schedule's run history**

Requires the shard's `ScheduleRead` capability.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the schedule belongs to |
| `scheduleId` | path | string (uuid) | yes | The schedule whose history to fetch |
| `limit` | query | integer (int32) |  | Maximum number of executions to return. Clamped to between 1 and 200; defaults to 50 |

**Responses:**

- `200` The schedule's execution history → [JobExecution](#schema-jobexecution)[] (`text/plain`)
- `403` The caller lacks the `ScheduleRead` capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or the schedule does not belong to it → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/shards/{shardId}/schedules/{scheduleId}/run`

**Trigger an immediate run of the schedule**

Requires the shard's `ScheduleWrite` capability. Re-binds `RunAsUserId` to the caller
first, so a manual run executes under the caller's live permissions rather than those of the
schedule's last author. Publishes a job execution with a UNIQUE
dedup id so a manual run can never be suppressed by (or suppress) a scheduled firing. Returns
immediately with the execution id; the run itself completes asynchronously. Writes a
`ScheduleRunNow` audit event.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The shard the schedule belongs to |
| `scheduleId` | path | string (uuid) | yes | The schedule to run |

**Responses:**

- `202` The run was queued; the body carries the new execution id
- `403` The caller lacks the `ScheduleWrite` capability on this shard → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such shard, or the schedule does not belong to it → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

### 12-Analytics

#### `GET /api/Analytics/addresses`

**Who connected from an address or a range**

Every address seen in it, on the servers the caller holds `analytics.addresses` on, each
with the users seen there. A range is CIDR ("203.0.113.0/24", "2001:db8::/32") or the leading
parts of an IPv4 address ("203.0.113.", "198.51.*"); IPv6 is only ever matched down to its /64.
Names appear only where the caller may also see them, by the same rule as search.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `q` | query | string |  | The address or range |
| `demo` | query | boolean |  | Debug builds only: answer from the demonstration data, for the developer switch |

**Responses:**

- `200` What it found → [AddressSearch](#schema-addresssearch) (`text/plain`)
- `400` That is not an address or a range → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not see addresses on any server → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (analytics.error.badAddress)

---

#### `GET /api/Analytics/addresses/users`

**Who has connected from an address**

Everyone seen at it, or for IPv6 anywhere in its /64 network, on the servers the caller holds
`analytics.addresses` on. Names appear only where the caller may also see them, by the
same rule as search. The kind says how much a match means: users at a tunnel's own address
have nothing in common but the server, and an ISP's shared (carrier-grade NAT) address is used
by many unrelated customers.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `address` | query | string |  | The address, as text: IPv4 or IPv6, with or without a port |
| `demo` | query | boolean |  | Debug builds only: answer from the demonstration data, for the developer switch |

**Responses:**

- `200` Who used it → [AddressAnswer](#schema-addressanswer) (`text/plain`)
- `400` That is not an address → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller may not see addresses on any server → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (analytics.error.badAddress)

---

#### `GET /api/Analytics/avatars/{hash}`

**A Steam avatar**

The picture Steam files under this hash, which the profiles answer gives. Fetched by the panel
so that nobody viewing analytics has their address handed to Steam.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `hash` | path | string | yes | The avatar hash: forty lower-case hex digits |
| `full` | query | boolean |  | The large picture rather than the medium one |

**Responses:**

- `200` The picture
- `404` Steam has no such picture, or did not answer → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (analytics.error.badAvatar)

---

#### `GET /api/Analytics/connected/{shardId}`

**Somebody connected to a server**

One entry from the server's connected users, by their id as the list gives it, with their Steam
or Minecraft profile when they are known by an id that can be looked up (AMP Professional
Edition and above), and the key to their analytics when the caller holds `analytics.users`
on the server. Requires `view`, the same as the list itself, which already shows their
name, id and address.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes | The server |
| `user` | query | string |  | The user's id, as the server's user list gives it (their name, when the server has no other) |

**Responses:**

- `200` Who they are → [ConnectedProfile](#schema-connectedprofile) (`text/plain`)
- `403` The caller may not view this server → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` There is no such server, or nobody by that id is connected → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `user` required)

---

#### `GET /api/Analytics/profiles`

**Look users up on Steam and with Mojang**

For users known by a Steam64 id, their avatar, profile link, bans and whether the account is
limited; for users known by a Minecraft UUID, their current name and skin. Only for servers
that keep user-specific data. Asked by the panel, never by the browser, and held in memory for
a day. Someone is only looked up when the caller holds `analytics.users` on every server
of theirs the caller can see, the rule that decides whether their name is shown.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `key` | query | string[] |  | The user keys, as the summary and search carry them; up to a hundred |
| `demo` | query | boolean |  | Debug builds only: answer for the demonstration data, for the developer switch |

**Responses:**

- `200` The profiles found → [ProfilesAnswer](#schema-profilesanswer) (`text/plain`)
- `403` The caller may not see user detail on any server → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Analytics/sessions`

**Summarise a period**

The headline figures, the activity graph, the country breakdown and the top users, compared
against the period of the same length before it. Requires `analytics.read` on at least one
server; the answer covers only the servers the caller holds it on.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `from` | query | string (date-time) |  | Start of the period. Defaults to 30 days before to. |
| `to` | query | string (date-time) |  | End of the period. Defaults to now. |
| `shard` | query | string (uuid)[] |  | Restrict to these servers. Omit for every server the caller may read. |
| `country` | query | string |  | Restrict to one country, as a lower-case ISO 3166-1 alpha-2 code |
| `user` | query | string |  | Restrict to one user, by the key the records carry |
| `bounceSeconds` | query | integer (int32) |  | A session shorter than this counts as a bounce |
| `topUsers` | query | integer (int32) |  | How many users the top list holds |
| `demo` | query | boolean |  | Debug builds only: answer from the demonstration data, for the developer switch |

**Responses:**

- `200` The period, summarised → [SessionSummary](#schema-sessionsummary) (`text/plain`)
- `403` The caller may not read analytics on any server → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Analytics/sessions/users/{userKey}`

**What one user did**

Their sessions across every server the caller holds `analytics.users` on. The key is the
one the summary's top-user list carries, not a name: analytics identifies a person by a key
that means nothing outside this installation.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `userKey` | path | string | yes |  |
| `from` | query | string (date-time) |  |  |
| `to` | query | string (date-time) |  |  |
| `shard` | query | string (uuid)[] |  |  |
| `demo` | query | boolean |  |  |

**Responses:**

- `200` The user's sessions and totals → [UserDetail](#schema-userdetail) (`text/plain`)
- `403` The caller may not see user detail on any server → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `DELETE /api/Analytics/sessions/users/{userKey}`

**Forget one user**

Deletes everything analytics holds about one person, on every server: their session records,
the record of when they were first seen, and their entries in the totals, which are rebuilt
without them. The counts the totals keep about everybody else are unaffected.

A person's analytics span every server they used, so forgetting them does too: this needs
`analytics.users` on every server, not only the ones in view. The session records go at
once; the totals are rebuilt in the background, normally within a few minutes.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `userKey` | path | string | yes |  |
| `demo` | query | boolean |  | Debug builds only: go through the whole of forgetting an invented person, for the developer switch, and answer with what it would have removed. Nothing is removed and nothing audited. |

**Responses:**

- `200` What was removed → [Erased](#schema-erased) (`text/plain`)
- `403` The caller may not see user detail on every server → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Analytics/skins/{hash}`

**A Minecraft skin**

The skin Mojang files under this hash, which the profiles answer gives; the page shows its face.
Fetched by the panel so that nobody viewing analytics has their address handed to Mojang.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `hash` | path | string | yes | The texture hash |

**Responses:**

- `200` The skin, a PNG
- `404` Mojang has no such skin, or did not answer → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (analytics.error.badAvatar)

---

#### `GET /api/Analytics/users`

**Find a user**

Searches everyone analytics knows by name, by raw id (a SteamID, for instance) and by the
short code an unnamed user is shown as. A name matches anywhere in it; an id or code matches
from its start. Names and ids are only included, and only searched, for people whose servers
the caller holds `analytics.users` on; anyone else can still be found by their code.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `q` | query | string |  | What to look for; at least two characters |
| `limit` | query | integer (int32) |  | How many matches to return, at most |
| `demo` | query | boolean |  | Debug builds only: search the demonstration data, for the developer switch |

**Responses:**

- `200` The matches, best first → [UserSearch](#schema-usersearch) (`text/plain`)
- `403` The caller may not see user detail on any server → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Analytics/users/{userKey}/addresses`

**Where one user has connected from**

Their addresses on the servers the caller holds `analytics.addresses` on, with how many
other users have connected from each. Only recorded for servers that keep user-specific data,
and kept for the address retention setting (90 days unless changed). An address that says
nothing about who shares it (a tunnel's own, a private or loopback one) is listed but not
matched.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `userKey` | path | string | yes |  |
| `demo` | query | boolean |  | Debug builds only: answer from the demonstration data, for the developer switch |

**Responses:**

- `200` Their addresses → [UserAddressesAnswer](#schema-useraddressesanswer) (`text/plain`)
- `403` The caller may not see addresses on any server → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

### 12-Spires & Datastores

_Host machines in the cluster and the datastores that shards are deployed onto._

#### `GET /api/Spires`

**List visible spires**

The result is filtered per spire against the `spire.<id>.view` node, so an
absent spire means the caller lacks view rights rather than a denial for the whole
call. Each returned definition has its bool SpireDefinition.IsLocal flag
stamped relative to the host answering the request.

**Responses:**

- `200` The visible spires, with per-response local flags stamped → [SpireDefinition](#schema-spiredefinition)[] (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Spires/roster`

**Get the cluster roster**

Every visible spire with its live online state, so the UI need not fan out a state
request per spire. Entries come from the heartbeat roster snapshot and are filtered per
spire against the caller's view rights, so unviewable spires are silently omitted rather
than denied.

**Responses:**

- `200` The roster of visible spires with online state and last-seen times → [SpireRosterItem](#schema-spirerosteritem)[] (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Spires/{id}`

**Get a spire by id**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Responses:**

- `200` The spire definition, with its local flag stamped → [SpireDefinition](#schema-spiredefinition) (`text/plain`)
- `403` The caller lacks view rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No spire with that id exists in the tenant → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `DELETE /api/Spires/{id}`

**Delete a spire**

A spire's shards live on its own machine. A normal delete refuses (409) while the spire
still owns any shard, so those must be migrated or deleted first. Force delete is the
escape hatch for an unrecoverable machine: it soft-deletes the stranded shards alongside
the spire, since their files are already gone. The local spire cannot be deleted; that
guard surfaces as a 400 ApiError.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |
| `force` | query | boolean |  | When true, soft-deletes any shards the spire still owns and deletes it anyway |

**Responses:**

- `204` The spire was deleted
- `400` The spire cannot be deleted (for example, it is the local spire) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks delete rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No spire with that id exists in the tenant → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The spire still owns active shards and force was not set → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Spires/{id}/datastores`

**List the spire's datastores**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Responses:**

- `200` The spire's configured datastores → [SpireDatastore](#schema-spiredatastore)[] (`text/plain`)
- `403` The caller lacks datastore-read rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Spires/{id}/datastores`

**Create or update a datastore**

Name and Path are both required. Path-validation failures surface as a 400
ApiError. Returns the spire's full datastore list after the upsert.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Request body** (required, `application/json`): [SpireDatastore](#schema-spiredatastore) — The datastore to create, or, with its Id set, the existing one to update

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) |  | Stable identifier a deployed shard references to record which datastore it was placed in |
| `name` | string |  | Operator-facing name, e.g. "Fast NVMe", "Bulk HDD" |
| `path` | string |  | Absolute path on the spire host under which shard directories are created |
| `isDefault` | boolean |  | The datastore used when a shard does not specify one |
| `enabled` | boolean |  | When false, the datastore is hidden from new deployments |
| `softLimitMb` | integer (int64) |  | Soft usage limit in MB. Once the scanned usage reaches this, new shards cannot be created in this datastore (existing shards keep running and may still grow - it is a placement guardrail, not a quota). 0 = no limit. |
| `tags` | string[] |  | Operator/auto-applied labels for placement and presentation - e.g. the storage medium ("NVMe", "SSD", "HDD", auto-detected where possible) plus any free-form tags ("scratch", "backups", "fast"). |

**Responses:**

- `200` The spire's datastores after the upsert → [SpireDatastore](#schema-spiredatastore)[] (`text/plain`)
- `400` Name or Path was missing, or the path failed validation → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks datastore-write rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (datastore.error.nameAndPathRequired)

---

#### `DELETE /api/Spires/{id}/datastores/{datastoreId}`

**Remove a datastore**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |
| `datastoreId` | path | string (uuid) | yes | The id of the datastore to remove |

**Responses:**

- `200` The spire's datastores after the removal → [SpireDatastore](#schema-spiredatastore)[] (`text/plain`)
- `403` The caller lacks datastore-write rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Spires/{id}/datastores/{datastoreId}/purge-orphan`

**Purge an orphan directory from a datastore**

Permanently deletes a true-orphan directory in a datastore, one with no matching shard,
active or deleted. Directories that belong to a (possibly soft-deleted) shard are refused
here and must go through the shard restore/purge path so their KV records are handled too.
The name must be a single directory leaf with no path separators or traversal. The
resolved path is checked to stay inside the datastore root, and a directory that
matches a known shard's home on this datastore is refused. A directory left behind by
a completed move (the shard now lives elsewhere) is treated as a stale copy and may be
purged. On failure the underlying OS reason is surfaced. The datastore is rescanned
afterwards, and the fresh usage report is returned.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |
| `datastoreId` | path | string (uuid) | yes | The id of the datastore holding the directory |

**Request body** (required, `application/json`): [PurgeOrphanBody](#schema-purgeorphanbody) — The orphan directory leaf name to delete

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | The plain directory leaf name (no separators or traversal) |

**Responses:**

- `200` The directory was purged; the refreshed usage report is returned
- `400` The name was invalid, resolved outside the datastore, belongs to a known shard, or is reserved by AMP (such as the backups directory) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks datastore-write rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No datastore with that id exists on the spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Name` required)

---

#### `POST /api/Spires/{id}/datastores/{datastoreId}/purge-orphans`

**Purge every orphan directory in a datastore**

Reclaims space by permanently deleting ALL true-orphan directories in one datastore in a
single sweep - the ones with no matching shard, plus stale copies left behind by a completed
move (whose shard now lives on another datastore). A directory that is a known shard's home
on this datastore (active OR soft-deleted) is never touched - those keep their restore/purge
path so their KV records are handled too - and neither is a directory AMP reserves for itself,
such as the one holding every shard's backups. Each delete is path-checked to stay inside the
datastore root; a directory that can't be deleted (locked, permissions) is skipped and the
rest still proceed. The datastore is rescanned afterwards and the fresh usage report returned.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |
| `datastoreId` | path | string (uuid) | yes | The id of the datastore to sweep |

**Responses:**

- `200` The sweep ran; the refreshed usage report is returned (any un-deletable dirs remain in it)
- `403` The caller lacks datastore-write rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No datastore with that id exists on the spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `503` (spire.error.unreachable)

---

#### `GET /api/Spires/{id}/ip-options`

**Classified bind-address options for this spire**

The spire's IReadOnlyList<string> SpireDefinition.IpPool literals if any are declared, otherwise the
host's reported addresses, always plus 0.0.0.0 (all interfaces) and 127.0.0.1 (localhost).
Each carries internal/external, DHCP/static, primary and tunnel facts for tagging in the UI.
Powers the spire's "default IP binding for new shards" picker.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |
| `includeAuto` | query | boolean |  | Include the "first from pool" auto sentinel option |

**Responses:**

- `200` The classified bind-address options → [IpBindingOption](#schema-ipbindingoption)[] (`text/plain`)
- `403` The caller lacks view rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Spires/{id}/ports`

**Get the spire's port map**

Every port a shard on this spire expects to use, who owns it, its IP binding, live
listening state (from the owner's metrics samples), and whether it conflicts with another
shard's port on an overlapping binding. Conflicts are flagged when the same port number
is claimed by different shards on overlapping bindings, where a wildcard binding
(0.0.0.0 or ::) overlaps everything. Listening state is only meaningful while the owning
shard is running.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Responses:**

- `200` The ordered port map for every shard on the spire → [SpirePortEntry](#schema-spireportentry)[] (`text/plain`)
- `403` The caller lacks view rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Spires/{id}/purge-expired-shards`

**Purge shards past their retention window**

Bulk-purges every shard on this spire that has been soft-deleted for longer than the
retention window (30 days), the housekeeping "Purge old" action. Every bus credential those
shards were launched with is revoked first, here, since only this host can re-sign the bus
account; a shard whose credentials cannot be revoked refuses the whole purge and nothing is
removed. The work then runs on the spire that owns the shards, never on the machine that
received the request, and continues there after this returns: each shard is stopped (in case
an agent lingers), has its KV records removed, and its working directory deleted. The outcome,
including any shard that could not be purged and is left for the next run, is written to the
audit log, and the spire's usage report is refreshed. Irreversible.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Responses:**

- `200` How many shards the spire started purging (0 when none were eligible) → [ExpiredPurgeBody](#schema-expiredpurgebody) (`text/plain`)
- `403` The caller lacks datastore-write rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` A shard's bus credentials could not be revoked, so nothing was purged → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The spire was unreachable or did not answer in time

**Live:** no token → `401` · token → `503` (spire.error.unreachable)

---

#### `GET /api/Spires/{id}/startup`

**List shards in startup order**

Returns all shards on this spire, sorted by their startup order, with their start-on-boot
flags. Position in the array equals the startup sequence.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Responses:**

- `200` The spire's shards ordered by startup sequence → [SpireStartupEntry](#schema-spirestartupentry)[] (`text/plain`)
- `403` The caller lacks startup-read rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PUT /api/Spires/{id}/startup`

**Save the spire's shard startup configuration**

Saves the startup configuration for all shards on this spire. Position in the submitted
list determines startup order (index 0 starts first). Audited as a single spire-level
event. Entries whose shard is missing or no longer belongs to this spire are skipped.
Each shard whose boot flag or order actually changed also emits a per-shard audit event
so the change surfaces in that shard's Activity tab.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Request body** (required, `application/json`): [SpireStartupEntryBody](#schema-spirestartupentrybody)[] — The shards in the desired startup order, each with its start-on-boot flag

**Responses:**

- `200` The saved startup entries, ordered by startup sequence → [SpireStartupEntry](#schema-spirestartupentry)[] (`text/plain`)
- `403` The caller lacks startup-write rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No spire with that id exists in the tenant → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `$` required)

---

#### `GET /api/Spires/{id}/state`

**Get a spire's live state**

The bool SpireState.Online flag is taken from the heartbeat roster rather
than the possibly stale flag baked into the last metrics write. If no state has been
recorded yet, an empty state carrying just the spire id is returned.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Responses:**

- `200` The spire state with roster-derived online status → [SpireState](#schema-spirestate) (`text/plain`)
- `403` The caller lacks view rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Spires/{id}/usage`

**Get the latest datastore usage scan**

The most recent cached datastore usage scan (~10-min cadence). A 204 means the spire has
never been scanned, so the UI shows an explicit "not yet scanned" state instead of zeros.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Responses:**

- `200` The most recent cached usage report → [DatastoreUsageReport](#schema-datastoreusagereport) (`text/plain`)
- `204` The spire has never been scanned
- `403` The caller lacks view rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Spires/{id}/usage/interval`

**Get the disk usage scan interval**

Reports the cadence as the spire will actually apply it, clamped to the supported range, so
the UI never shows a stored value the reporter is quietly ignoring.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Responses:**

- `200` The spire's scan cadence in minutes → [UsageScanIntervalBody](#schema-usagescanintervalbody) (`text/plain`)
- `403` The caller lacks view rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `PUT /api/Spires/{id}/usage/interval`

**Set the disk usage scan interval**

A scan walks every file under every datastore, so the right cadence depends on the machine's
disks and how much is on them. It is also the resolution of every per-shard disk budget: a
shard that goes over its budget is noticed, at worst, one interval later. Requires the
datastore-write capability, and takes effect on the spire's next report tick.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Request body** (required, `application/json`): [UsageScanIntervalBody](#schema-usagescanintervalbody) — The requested cadence

| Field | Type | Required | Description |
|---|---|---|---|
| `minutes` | integer (int32) | yes | Scan cadence in minutes. Clamped to the supported range rather than rejected |

**Responses:**

- `200` The spire's config after the change, with the interval as actually applied → [SpireConfig](#schema-spireconfig) (`text/plain`)
- `403` The caller lacks datastore-write rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Spires/{id}/usage/rescan`

**Force a disk usage rescan**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The spire's unique id |

**Responses:**

- `200` The freshly scanned usage report → [DatastoreUsageReport](#schema-datastoreusagereport) (`text/plain`)
- `204` The rescan produced no report (the spire reported nothing to scan)
- `403` The caller lacks view rights on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `503` (spire.error.unreachable)

---

### 12-Spires

#### `GET /api/spires/enrollments`

**List machine enrollments**

Every enrollment, newest first: pending, redeemed, expired and revoked. Requires the platform spire-create capability.

**Responses:**

- `200` The enrollments → [EnrollmentDto](#schema-enrollmentdto)[] (`text/plain`)
- `403` The caller lacks the spire-create capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/spires/enrollments`

**Enroll a machine**

Issues a one-time join file for one machine. The response is the only time the join file exists:
     it carries a throwaway credential scoped to this enrollment alone, which the machine spends for a
     real one bound to a key it generates locally. Requires the platform spire-create capability.

     With entryNode set, the file instead adds a public entry node: a relay and panel front
door that hosts no shards. It carries one bus address - this end's tunnel address, which nothing
can reach until the tunnel it describes is up - plus the tunnel's slot, this end's WireGuard
public key, and the bootstrap secret. It needs the network-manage capability as well, and a
public address to dial. See docs/public-entry-nodes.md.

**Request body** (required, `application/json`): [CreateEnrollmentBody](#schema-createenrollmentbody)

| Field | Type | Required | Description |
|---|---|---|---|
| `label` | string | yes | A name for the machine, shown as the spire's display name once it joins |
| `expiresInHours` | integer (int32) | yes | How long the join file stays usable (1-720, default 72) |
| `hosts` | string[]? |  | The addresses to put in the join file. The joining machine tries each and uses the first it can reach, so listing a LAN address AND a public one covers both cases without the operator having to know which network the machine is on. Empty means "whatever this bus reports", which is the LAN address it is bound to. |
| `entryNode` | boolean |  | True to add the machine as a public entry node instead of an ordinary spire: a relay and panel front door that hosts no shards. Changes what the file contains and how it is reached, so it is chosen here rather than by the machine. See docs/public-entry-nodes.md. Default `false`. |
| `publicHost` | string? |  | Required for an entry node, ignored otherwise: the public address that machine is reachable at. This end has to know it, because this end is the one that dials - so there is no arrangement in which the operator does not supply it. |

**Responses:**

- `201` The enrollment, plus the join file to hand to the machine → [CreatedEnrollment](#schema-createdenrollment) (`text/plain`)
- `400` No label, or this bus can't issue credentials. For an entry node: no public address given, this installation does not run its own bus, no tunnel slots are left, or WireGuard is not installed on this machine. → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the spire-create capability, or network-manage for an entry node → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` Another entry node is being created for this installation right now → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/spires/enrollments/hosts`

**List the addresses a joining machine could reach this bus on**

Candidates for the join file's host list: this machine's own addresses, its declared public host
if it has one, and the address the internet sees it as. That last one is offered even behind NAT
- a port-forward makes it work, and only the operator knows whether they have set one up - so it
arrives unticked and carrying a note rather than being decided for them. The exception is
carrier-grade NAT, where no forward is possible at all; that is said outright.
Requires the platform spire-create capability.

**Responses:**

- `200` The candidate addresses, best-first → [PublicHostOption](#schema-publichostoption)[] (`text/plain`)
- `403` The caller lacks the spire-create capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `DELETE /api/spires/enrollments/{id}`

**Withdraw an enrollment**

Revokes the join file's credential so it can no longer be redeemed, and tombstones the record so
the withdrawal stays on the books. This does NOT evict a machine that already enrolled - that is
its own credential; delete the spire instead. Requires the platform spire-create capability.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes |  |

**Request body** (optional, `application/json`): null or [RevokeBody](#schema-revokebody)

**Responses:**

- `204` Withdrawn
- `403` The caller lacks the spire-create capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such enrollment → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/spires/enrollments/{id}/approve`

**Approve the machine waiting on an enrollment**

Admits the machine that presented this join file. Compare the fingerprint the enrollment shows
with the one printed on the installing machine's console first: they are the same machine only
when the two strings match. The machine collects its credential on its next ask, within
seconds. Requires the platform spire-create capability.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes |  |

**Responses:**

- `200` Approved; the enrollment as it now stands → [EnrollmentDto](#schema-enrollmentdto) (`text/plain`)
- `403` The caller lacks the spire-create capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such enrollment → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` No machine is waiting on this enrollment → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/spires/enrollments/{id}/deny`

**Refuse the machine waiting on an enrollment**

Refuses the machine that presented this join file, which finishes the file: the machine is
told so on its next ask and its throwaway credential is withdrawn once it has been. Issue a
new join file for the machine you meant. Requires the platform spire-create capability.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes |  |

**Responses:**

- `204` Denied
- `403` The caller lacks the spire-create capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No such enrollment → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` No machine is waiting on this enrollment → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

### 12-Import

#### `GET /api/shards/{shardId}/import`

**What an import changed on this shard: ports it had to move, settings it kept authority over,      where the files came from. Null when the shard was not imported.       Kept for the life of the shard rather than shown once. The question it answers, "why is my port different?", gets asked days after the import, by which time a completion summary is long gone. Read from KV, so it needs no spire round trip.**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `shardId` | path | string (uuid) | yes |  |

**Responses:**

- `200` The record, or null if this shard was created normally → [ImportChangeRecord](#schema-importchangerecord) (`text/plain`)

**Live:** no token → `401` · token → `204`

---

#### `POST /api/spires/{spireId}/import`

**Imports a selection of servers: copies each one's files, creates the shard, and applies the      parts that were asked for and are available.       The batch is gated on the spire before the first byte is copied. A bulk import that dies two thirds of the way through leaves somebody working out which servers made it, on a machine they were already nervous about. Items are then imported one at a time and reported individually, so one server that turns out to be running does not cost the rest.**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `spireId` | path | string (uuid) | yes |  |

**Request body** (required, `application/json`): [ImportRequest](#schema-importrequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `items` | [ImportSelection](#schema-importselection)[] | yes | The selection |
| `enterOtherVolumes` | boolean |  | Copy through a volume mounted inside a server's folder rather than stopping at it. A server set up by hand may keep its worlds on a second disk; whether that disk belongs to it is the user's to say. A backup or a move never crosses one. Default `false`. |

**Responses:**

- `200` Per-item results, each with its change record or its reason for failing

**Live:** no token → `401` · token → `400` (validation: `Items` required)

---

#### `POST /api/spires/{spireId}/import/preflight`

**Checks whether a selection can actually be imported: disk space, licence headroom, and whether      each source is readable and at rest.       Called to render the results screen's live totals, and again when the user commits, using the same code both times. A screen that says "this fits" while the request refuses it would be worse than either alone.**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `spireId` | path | string (uuid) | yes |  |

**Request body** (required, `application/json`): [ImportRequest](#schema-importrequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `items` | [ImportSelection](#schema-importselection)[] | yes | The selection |
| `enterOtherVolumes` | boolean |  | Copy through a volume mounted inside a server's folder rather than stopping at it. A server set up by hand may keep its worlds on a second disk; whether that disk belongs to it is the user's to say. A backup or a move never crosses one. Default `false`. |

**Responses:**

- `200` What would import, what would not, and the shortfall if it cannot proceed → [PreflightReport](#schema-preflightreport) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Items` required)

---

#### `POST /api/spires/{spireId}/import/scan`

**Searches the given paths on this spire and reports what it found, what it could not read, and      what an import of each would be able to recover.       Read-only. Nothing is created, copied or changed, which is why this asks nobody to stop anything: the message about shutting servers down belongs at import time, against the specific servers that are actually running.**

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `spireId` | path | string (uuid) | yes | The spire to search. Its own host does the work |

**Request body** (required, `application/json`): [ScanRequest](#schema-scanrequest) — Cancellation

| Field | Type | Required | Description |
|---|---|---|---|
| `roots` | string[] | yes | Absolute paths on the target spire to search beneath. Ignored when Automatic is set |
| `automatic` | boolean |  | Search the roots the spire finds for itself instead. The paths are never sent by the caller, so the tool each result is attributed to is something the spire established rather than something it was told. Default `false`. |

**Responses:**

- `200` What the scan found, including everything it could not do → [DiscoveryResult](#schema-discoveryresult) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `Roots` required)

---

### 12-Networking

_Publishing shard ports across spires over WireGuard reverse tunnels._

#### `GET /api/https`

**What is configured**

**Responses:**

- `200` The current HTTPS state → [HttpsState](#schema-httpsstate) (`text/plain`)
- `403` The caller may not read platform settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/https/check`

**Where a domain name points, before anything is set up**

The first three rungs of the step alone: the name is in the grammar, it resolves, and it
points at this machine's public address. Nothing is configured, the agent is never asked,
and a step already running is left alone; the HTTPS step asks this before it shows the
record to create and polls it while the record is being created. A name outside the
grammar is answered, not refused: the outcome is `invalid` and the name rung carries
the `https.name.*` code.

**Request body** (required, `application/json`): [CheckRequest](#schema-checkrequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `hostName` | string | yes | The domain name to look up |

**Responses:**

- `200` Where the name points and the three rungs → [HttpsNameCheck](#schema-httpsnamecheck) (`text/plain`)
- `403` The caller may not change networking settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/https/own-proxy`

**Trust a reverse proxy of the operator's own**

The escape hatch: forwarded headers from the given address are honoured from now on, and
with `apiOnly` the panel serves the API alone from its next start, leaving the web app
to the proxy.

**Request body** (required, `application/json`): [OwnProxyRequest](#schema-ownproxyrequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string | yes | The proxy's address, or its network in CIDR form |
| `apiOnly` | boolean |  | The proxy serves the web app itself, so the panel serves the API alone from its next start Default `false`. |

**Responses:**

- `200` Recorded; the response carries the new state → [HttpsState](#schema-httpsstate) (`text/plain`)
- `400` The address is not an IP address or a CIDR network → [ApiError](#schema-apierror) (`text/plain`)
- `403` The caller may not change networking settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` AMP's own front is configured, or the configuration could not be written → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `DELETE /api/https/own-proxy`

**Stop trusting the operator's own proxy**

**Responses:**

- `200` Cleared; the response carries the new state → [HttpsState](#schema-httpsstate) (`text/plain`)
- `403` The caller may not change networking settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/https/recheck`

**Look again for the certificate at a configured front**

**Responses:**

- `202` The certificate rung is being checked
- `403` The caller may not change networking settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` Nothing is configured, or a step is running → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/https/remove`

**Take the managed front away**

Root puts Caddy's previous configuration back or stops it, the ports close, and the panel stays reachable on its own port as it always was.

**Responses:**

- `200` The front is gone; the response carries the new state → [HttpsState](#schema-httpsstate) (`text/plain`)
- `403` The caller may not change networking settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` Nothing is configured, a step is running, or the agent could not remove it → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/https/setup`

**Start the HTTPS step for a domain name**

Runs the checks in the background; poll Task<IActionResult> HttpsController.Status() for the ladder. A name that
cannot ever be validated, a headless entry node, a panel kept out of the firewall or a run
already in progress is refused here with a coded reason before anything starts.

**Request body** (required, `application/json`): [SetupRequest](#schema-setuprequest)

| Field | Type | Required | Description |
|---|---|---|---|
| `hostName` | string | yes | The domain name that points at this machine |

**Responses:**

- `202` The step has started
- `400` The name is not one a certificate can be issued for → [ApiError](#schema-apierror) (`text/plain`)
- `403` The caller may not change networking settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The step cannot run on this machine right now; the code says why → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/https/setup/status`

**The ladder of the running or last step**

**Responses:**

- `200` The checks and where they stand → [HttpsSetupProgress](#schema-httpssetupprogress) (`text/plain`)
- `403` The caller may not read platform settings → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/networking/firewall`

**Get the local firewall status**

Reports what the privileged system agent last did on THIS machine, read straight out of the status
    file it writes. `installed` false means no agent has ever run here, in which case AMP
    manages no firewall rules at all and the operator opens their own ports.
    Local-spire only. The status file is a local handover, not a bus record, so a remote spire's
firewall state is not visible here yet.
    Requires the platform NetworkRead capability. A status older than five minutes is treated as stopped.

**Responses:**

- `200` The firewall status; Running is false when no agent has run or its status is stale → [FirewallStatusDto](#schema-firewallstatusdto) (`text/plain`)
- `403` The caller lacks the NetworkRead capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/networking/gateway-eligibility`

**Which servers and public spires can take part in gateway mode**

Requires the platform NetworkRead capability. Gateway mode has two hard requirements that the UI
     cannot work out for itself, and which would otherwise only surface as a rejection AFTER the
     operator picked it: the server must run in a container (the tunnel is brought up inside it), and
     the front must run Linux (it DNATs the arriving traffic, which Windows cannot do for UDP).

     Answered for every server and spire at once rather than per selection, so a picker can show the
ineligible ones disabled - with the reason - instead of hiding them or failing late.

**Responses:**

- `200` The eligible servers and front spires → [GatewayEligibilityDto](#schema-gatewayeligibilitydto) (`text/plain`)
- `403` The caller lacks the NetworkRead capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/networking/gateways`

**List gateway bindings**

Requires the platform NetworkRead capability. Each binding carries its live phase and the address
held for the shard on its tunnel. The in-container half is still being built, so a binding rests at
AwaitingShard until a running shard publishes its ephemeral key.

**Responses:**

- `200` The gateway bindings, ordered by creation time → [GatewayDto](#schema-gatewaydto)[] (`text/plain`)
- `403` The caller lacks the NetworkRead capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/networking/gateways`

**Route a server through a front spire**

Requires the platform NetworkManage capability. The front spire must exist and be publicly routable,
and the server must not already run on that spire. At most one gateway binding per server. Because
the gateway tunnels the whole server, any existing individual port publications for it are deleted.
Writes a ShardGatewayBound audit event.

**Request body** (required, `application/json`): [CreateGatewayBody](#schema-creategatewaybody) — The server to route and the front spire to route it through

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) | yes | Server to route wholesale through the gateway |
| `frontSpireId` | string (uuid) | yes | Publicly routable spire that fronts the server's traffic |

**Responses:**

- `201` The created gateway binding → [GatewayDto](#schema-gatewaydto) (`text/plain`)
- `400` The front spire is missing or not publicly routable, the server does not exist, or the server already runs on that spire → [ApiError](#schema-apierror) (`text/plain`)
- `403` The caller lacks the NetworkManage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The server is already routed through a spire → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/networking/gateways/preflight`

**Check a gateway route for port clashes without creating it**

Gateway ports map one-to-one onto the front's public face, so every public port the server has is
a claim there. This answers what would be in the way, and for each clash a port that is free -
which for a gateway is what the SERVER would have to move to, since its public port cannot be
translated. Creating the route applies the same check, so a clean preflight is not a reservation.

**Request body** (required, `application/json`): [GatewayPreflightBody](#schema-gatewaypreflightbody) — The proposed server and front spire

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) | yes | The server that would be routed |
| `frontSpireId` | string (uuid) | yes | The public spire that would front it |

**Responses:**

- `200` The clashes, if any
- `403` The caller lacks network-manage → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `DELETE /api/networking/gateways/{id}`

**Remove a gateway binding**

Requires the platform NetworkManage capability. Also clears any cached status for the binding. Writes a ShardGatewayUnbound audit event.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | Identifier of the gateway binding to delete |

**Responses:**

- `204` The binding was deleted
- `403` The caller lacks the NetworkManage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No gateway binding with that identifier exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `PUT /api/networking/gateways/{id}/egress`

**Set which private networks a gateway-routed server may reach**

Requires the platform NetworkManage capability. A server routed through a gateway sends ALL of
its traffic through the front, and the front drops anything bound for a private network
(10/8, 172.16/12, 192.168/16, 100.64/10, 169.254/16 and loopback) by default, so the server
cannot reach other machines on the front's LAN, its cloud metadata service or other tunnels.
The front host itself is not covered: a service it serves on its LAN address or on all
interfaces stays reachable unless the operator firewalls it. This list is the exception: each
entry is an IPv4 network in CIDR form, no wider than a /8, this server may reach. At most 32
entries; the whole list replaces the previous one. Writes a ShardGatewayEgressUpdated audit
event, and a Security GatewayEgressDestinationViolation event for an entry wider than a /8.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | Identifier of the gateway binding |

**Request body** (required, `application/json`): [UpdateGatewayEgressBody](#schema-updategatewayegressbody) — The complete allow list

| Field | Type | Required | Description |
|---|---|---|---|
| `egressAllowList` | string[] | yes | IPv4 networks in CIDR form no wider than a /8, at most 32. Empty means the internet only, which is the default |

**Responses:**

- `200` The updated gateway binding → [GatewayDto](#schema-gatewaydto) (`text/plain`)
- `400` An entry is not an IPv4 network in CIDR form or is wider than a /8, or there are more than 32 → [ApiError](#schema-apierror) (`text/plain`)
- `403` The caller lacks the NetworkManage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No gateway binding with that identifier exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (validation: `EgressAllowList` required)

---

#### `GET /api/networking/publications`

**List port publications**

Requires the platform NetworkRead capability. Each publication carries its last reported status where available.

**Responses:**

- `200` The publications, ordered by public port → [PublicationDto](#schema-publicationdto)[] (`text/plain`)
- `403` The caller lacks the NetworkRead capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/networking/publications`

**Publish a shard port**

Requires the platform NetworkManage capability. The port must exist on the server and be marked
publicly accessible on the server's Ports view. The protocol is taken from that port, never from
the request. Only one listener is allowed per (front spire, public port), counting Both as
overlapping TCP and UDP. A server routed through a gateway is tunnelled wholesale, so its
individual ports cannot be published. Writes a PortPublished audit event.

**Request body** (required, `application/json`): [CreatePublicationBody](#schema-createpublicationbody) — The front spire, public port, server, and port ref

| Field | Type | Required | Description |
|---|---|---|---|
| `frontSpireId` | string (uuid) | yes | Publicly routable spire that will listen on the public port |
| `publicPort` | integer (int32) | yes | Public port to bind on the front spire (1-65535) |
| `shardId` | string (uuid) | yes | Server whose port is being published |
| `portRef` | string | yes | Named port on the server to expose |
| `frontPublicHost` | string? |  | Adopted as the front spire's PublicHost when it has none, so publishing to a public spire can establish its dial host inline rather than via a detour through Spire settings. Ignored once that setting exists: it is the operator's, and every tunnel on that spire already dials it. |

**Responses:**

- `201` The created publication → [PublicationDto](#schema-publicationdto) (`text/plain`)
- `400` The port number is out of range, the spire or server does not exist, the offered public host is not a bare host or the spire has none, the named port is missing, or the port is not marked publicly accessible → [ApiError](#schema-apierror) (`text/plain`)
- `403` The caller lacks the NetworkManage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The server is routed through a gateway, or the public port is already published on that spire → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `PUT /api/networking/publications/{id}`

**Update a publication**

Requires the platform NetworkManage capability. Only the supplied fields change. Writes a PortPublicationUpdated audit event.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | Identifier of the publication to update |

**Request body** (required, `application/json`): [UpdatePublicationBody](#schema-updatepublicationbody) — The fields to change; null fields are left as-is

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean? | yes | When set, enables or disables the publication |
| `publicPort` | integer (int32) | yes | When set, rebinds the publication to a new public port (1-65535) |

**Responses:**

- `200` The updated publication → [PublicationDto](#schema-publicationdto) (`text/plain`)
- `400` The supplied public port is out of range → [ApiError](#schema-apierror) (`text/plain`)
- `403` The caller lacks the NetworkManage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No publication with that identifier exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The new public port is already claimed on that spire → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `DELETE /api/networking/publications/{id}`

**Remove a publication**

Requires the platform NetworkManage capability. Also clears any cached status for the publication. Writes a PortUnpublished audit event.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | Identifier of the publication to delete |

**Responses:**

- `204` The publication was deleted
- `403` The caller lacks the NetworkManage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No publication with that identifier exists → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/networking/spires/{id}/port-claims`

**Everything already claiming a port on a public spire's face**

Publications, the one-to-one ports of every gateway-bound server, and the spire's own services
(panel, SFTP, tunnel listen ports, entry-node bootstrap). Lets a port picker show what is taken
and by what, rather than offering a number that will be refused on submit.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | The front spire to describe |

**Responses:**

- `200` Every claim on that spire's public face
- `403` The caller lacks network-manage → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/networking/tunnel-fronts`

**List the spires that could be the public end of a tunnel**

Only spires something outside their network could actually reach: one holding a public address
     of its own, or one whose public host the operator has declared. A machine seen only from behind
     NAT is left out - knowing its external address is evidence it sits behind something, not
     evidence a port-forward exists - as is carrier-grade NAT, which no forward can rescue. An
     operator who HAS forwarded a port declares the host, and the spire appears.

     Each one arrives with its candidate addresses, best-first, so the wizard can offer a choice
without a round-trip per selection. It is all from what each spire reports about ITSELF, which
is what makes it work for a remote spire the hub cannot probe.
     Requires the platform NetworkManage capability.

**Responses:**

- `200` The eligible spires with their candidate hosts. Empty when none qualify. → [TunnelFrontDto](#schema-tunnelfrontdto)[] (`text/plain`)
- `403` The caller lacks the NetworkManage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/networking/tunnels`

**List spire-to-spire tunnels**

Requires the platform NetworkRead capability. Each tunnel includes both ends and, where available, the current link status.

**Responses:**

- `200` The tunnels, ordered by creation time → [TunnelDto](#schema-tunneldto)[] (`text/plain`)
- `403` The caller lacks the NetworkRead capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

### 12-Templates

_The application catalogue that new shards are created from._

#### `GET /api/Templates`

**Get the app catalogue.**

Higher-priority sources win on slug collision. Requires being able to create a
shard on at least one spire. Returns 409 with an ApiError if no source has synced yet.

**Responses:**

- `200` The merged catalogue of app specs → [AppSpec](#schema-appspec)[] (`text/plain`)
- `403` The caller cannot create a shard on any spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` No templates are loaded because no source has synced yet → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `GET /api/Templates/sources`

**List the template sources.**

Source configuration is part of template management, not catalogue browsing,
so this requires the platform templates management capability.

**Responses:**

- `200` The configured template sources → [TemplateSource](#schema-templatesource)[] (`text/plain`)
- `403` The caller lacks the templates management capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/Templates/sources/sync`

**Sync all template sources.**

Each source is cloned on first run; subsequent calls fetch and hard-reset
to the remote branch tip, which handles force-pushes and rebases cleanly.
Requires the platform templates management capability and writes an audit event.

**Responses:**

- `200` Per-source sync results → [SourceSyncResult](#schema-sourcesyncresult)[] (`text/plain`)
- `403` The caller lacks the templates management capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Templates/sources/{sourceId}`

**List a source's templates.**

Requires being able to create a shard on at least one spire.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `sourceId` | path | string | yes | The configured id of the source to load |

**Responses:**

- `200` The app specs from that source → [AppSpec](#schema-appspec)[] (`text/plain`)
- `403` The caller cannot create a shard on any spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No source with that id is configured → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (No source with id 'zz-probe' is configured.)

---

#### `POST /api/Templates/sources/{sourceId}/sync`

**Sync a template source.**

Requires the platform templates management capability and writes an audit event.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `sourceId` | path | string | yes | The configured id of the source to sync |

**Responses:**

- `200` The sync result for the source → [SourceSyncResult](#schema-sourcesyncresult) (`text/plain`)
- `403` The caller lacks the templates management capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No source with that id is configured → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/Templates/{slug}`

**Get a template by slug.**

Requires being able to create a shard on at least one spire.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | string | yes | The template slug to resolve against the merged catalogue |

**Responses:**

- `200` The matching app spec → [AppSpec](#schema-appspec) (`text/plain`)
- `403` The caller cannot create a shard on any spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No template with that slug exists in the catalogue → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

### 12-Tasks

_Long-running background operations and their progress._

#### `GET /api/running-tasks`

**List the caller's running tasks**

Returns only tasks the caller owns, ordered oldest-first by start time. A failed task is
listed until it is dismissed, a later task of the same type on the same target succeeds or
fails, or 30 minutes pass.

**Responses:**

- `200` The caller's current tasks
- `403` The request has no authenticated user identity → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `DELETE /api/running-tasks/{id}`

**Dismiss a finished or stuck task**

Clears the task's record. Finished tasks can always be dismissed; a task still marked as
running can be dismissed once it has stopped reporting for a couple of minutes, which is how
a record left behind by a crashed producer is cleared. Dismissing does not stop any work -
a producer that is still alive writes its task back on the next report.
A task the caller does not own returns 404 to avoid revealing its existence.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | Identifier of the task to dismiss |

**Responses:**

- `204` The task was dismissed
- `404` No such task owned by the caller → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The task is actively reporting and cannot be dismissed yet → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/running-tasks/{id}/cancel`

**Cancel a running task**

A task the caller does not own returns 404 to avoid revealing its existence.
A task that is not cancellable or has already finished returns 409.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `id` | path | string (uuid) | yes | Identifier of the task to cancel |

**Responses:**

- `202` Cancellation was requested
- `404` No such task owned by the caller → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The task is not cancellable or has already finished → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `GET /api/Tasks/{tenantId}/{taskId}`

**Get a task by id.**

A task the caller may not view returns 404 (not 403) so its existence is not revealed.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `tenantId` | path | string | yes | Owning tenant of the task |
| `taskId` | path | string (uuid) | yes | Identifier of the task |

**Responses:**

- `200` The requested task
- `404` No such task, or it is not visible to the caller → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

#### `POST /api/Tasks/{tenantId}/{taskId}/cancel`

**Cancel a running task.**

Cancellation is cooperative: the request is dispatched to the shard the task targets and
the endpoint returns 202 without waiting for the task to stop. A task not visible to the
caller returns 404 to avoid revealing its existence; a task that has already reached a
terminal state, or that targets no shard, returns 409.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `tenantId` | path | string | yes | Owning tenant of the task |
| `taskId` | path | string (uuid) | yes | Identifier of the task to cancel |

**Responses:**

- `202` Cancellation was requested
- `403` The caller may view the task but not abort it → [ApiError](#schema-apierror) (`text/plain`)
- `404` No such task, or it is not visible to the caller → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The task has already completed, or cannot be cancelled → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → `404` (Not Found)

---

### 12-Monitoring

#### `GET /api/monitoring/platform-warnings`

**Get the installation-wide warnings the overview shows**

Each warning is a coded message naming a standing condition of this installation: a message
bus running without credentials, or a bus whose JetStream is not answering for this
installation's account, with the reason when the server's log gives one, or an installed
update that amp3-host could not put in place, with its reason, or an update that failed to
start here or on another spire and was rolled back, with why and when (only the most recent
rollback of each spire stands; earlier ones are in the audit log). They describe how
the installation is configured, which is what the platform-settings write capability is for,
so anyone without it is told nothing: a user who can only view servers has no way to act on
the warning and should not learn which protections the installation runs without. Each
warning carries the identity of its occurrence and whether it is informational: a rollback
is, and the caller sees it until they dismiss it; every other condition needs action and
stands until it is resolved.

**Responses:**

- `200` The current warnings the caller has not dismissed, empty when there are none or the caller may not configure the platform → [PlatformWarning](#schema-platformwarning)[] (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/monitoring/platform-warnings/acknowledge`

**Dismiss an informational platform warning for the caller**

Records, for the signed-in user only, that they have read one occurrence of an informational
condition (an update that was rolled back), so the overview stops showing it to them in any
browser. A later occurrence (another rollback) has a new identity and shows again. A condition
that needs action cannot be dismissed: it stands until it is resolved. A condition that is no
longer standing is not recorded, since there is nothing left to show.

**Request body** (required, `application/json`): [AcknowledgePlatformWarningRequest](#schema-acknowledgeplatformwarningrequest) — The identity of the occurrence, as the listing returned it

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | The string PlatformWarning.Id the listing returned |

**Responses:**

- `200` The caller's warnings after the change → [PlatformWarning](#schema-platformwarning)[] (`text/plain`)
- `400` The warning needs action and cannot be dismissed → [ApiError](#schema-apierror) (`text/plain`)
- `401` The caller's identity could not be resolved → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/monitoring/thresholds`

**Get the resource-usage warning thresholds**

Returns the CPU/RAM/disk warning ("amber") and high ("red") thresholds the overview uses to
flag spires running hot. Unset thresholds come back as their defaults. RAM and disk values are
quantities: a percentage ("80%", meaning usage at/above it) or an absolute amount ("100GB",
meaning free headroom at/below it).

**Responses:**

- `200` The current resource-usage thresholds for the authenticated tenant → [MonitoringSettings](#schema-monitoringsettings) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

### 12-Platform Settings

_Install-wide configuration and the actions that apply it._

#### `POST /api/settings/actions/{actionId}`

**Run a settings inline action**

Runs a settings inline action (smtp.test, templates.sync, …). Gated as a write under its
section, authorised as `platform.settings.write.<actionId>`. Returns the
action's own result detail.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `actionId` | path | string | yes | Identifier of the inline action to run (e.g. `smtp.test`) |

**Responses:**

- `200` The action ran; the body carries its result detail
- `403` The caller lacks write authority for the action's section → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/settings/manifest`

**Get the settings manifest**

Requires the `platform.settings.read` capability. Secret values are never
returned; the manifest only reports whether each secret is currently set.

**Permission:** `platform.settings.read`

**Responses:**

- `200` The full settings manifest for the authenticated tenant → [SettingsManifest](#schema-settingsmanifest) (`text/plain`)
- `403` The caller lacks the settings-read capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `DELETE /api/settings/secrets/{node}`

**Remove a stored secret setting**

Forgets a write-only secret such as `smtp.password`. Needed because an empty value in
a batch of changes means "keep the stored secret". Authorised as
`platform.settings.write.<node>` and audited. Removing a secret that is not set
changes nothing.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `node` | path | string | yes | The secret node to clear (e.g. `smtp.password`) |

**Responses:**

- `204` No value is stored for the secret
- `400` The node is unknown or is not a secret → [ApiError](#schema-apierror) (`text/plain`)
- `403` The caller lacks write authority on the node → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/settings/values`

**Apply a batch of platform setting changes**

Applies a batch of node changes ({"smtp.host": "…"}). Each key is authorised individually
as `platform.settings.write.<key>`, so a role can be limited to specific
sections/settings, while `platform.settings.write.>` covers everything. Audited
per node. A change to the security policy ends the live SFTP connections on this spire of
every account it puts on hold. Turning panel exposure off while AMP's HTTPS front is
configured removes the front as its Remove action does; when that cannot be done the
change still stands and the answer carries a notice saying ports 80 and 443 stay open.

**Request body** (required, `application/json`): map<string, string> — Cancels the request

**Responses:**

- `200` All changes were applied, with notices about something that followed from them and could not be done → [SettingsSaveResult](#schema-settingssaveresult) (`text/plain`)
- `204` All changes were applied
- `400` A value cannot be taken: an unknown node, an unparsable value, a node this build holds at a fixed     value (`settings.error.locked`), or a shared credential the caller may not choose     (`settings.error.credentialNotUsable`, audited at Security); no change is applied → [ApiError](#schema-apierror) (`text/plain`)
- `403` The caller lacks write authority on at least one of the supplied keys; no change is applied → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` A change names a node this machine cannot take a value for (the panel exposure setting on a public entry node); no change is applied → [ApiError](#schema-apierror) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/settings/{scopeType}/{scopeId}/manifest`

**Get a scoped settings manifest**

Spire scopes require the spire's own settings-read cap; other scopes fall back to
platform settings-read. An unrecognised scopeType is rejected.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `scopeType` | path | string | yes | Settings scope kind (e.g. `spire`) |
| `scopeId` | path | string | yes | Identifier of the scoped entity |

**Responses:**

- `200` The settings manifest for the scoped entity → [SettingsManifest](#schema-settingsmanifest) (`text/plain`)
- `400` The requested scope type is not recognised → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks read authority for this scope → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `400` (settings.error.unknownScope)

---

#### `POST /api/settings/{scopeType}/{scopeId}/values`

**Apply a batch of scoped setting changes**

Applies a batch of node changes to an entity-scoped surface, authorising each key
individually (spire settings: `spire.<id>.settings.write.<key>`) so a
role can be limited to specific settings. Audited per node.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `scopeType` | path | string | yes | Settings scope kind (e.g. `spire`) |
| `scopeId` | path | string | yes | Identifier of the scoped entity |

**Request body** (required, `application/json`): map<string, string> — Map of setting node key to new string value. Every key is authorised individually

**Responses:**

- `204` All changes were applied
- `400` The requested scope type is not recognised → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks write authority on at least one of the supplied keys; no change is applied → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

### 12-Licensing

_Activated licences, the effective feature set, and licence activation. Keys are write-only and never stored._

#### `GET /api/licence`

**The installation's licensing status**

Requires the licence-read capability. Rescans the activation files so external changes show up.

**Responses:**

- `200` Licences and shard usage as a LicenceStatusDto → [LicenceStatusDto](#schema-licencestatusdto) (`text/plain`)
- `403` The caller lacks the licence-read capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `401` · token → `200`

---

#### `POST /api/licence`

**Activate a licence key**

Requires the licence-manage capability. On success the licensing client writes a
machine-bound activation file and the key itself is discarded - it is never stored
and never appears in the audit trail. Activating an edition that's already active
refreshes its activation rather than adding a duplicate.

A key whose preview said TermsRequired must come with the holder's acceptance of the current
terms. The acceptance is recorded with the licensing service first, vouched for by the
activation's credentials, and the licence counts only once the service answers with the new
terms version. An agreement hash the service no longer serves is refused as
termsUnknownAgreement, a wrong version as termsWrongVersion: fetch the agreement again and ask again.

**Request body** (required, `application/json`): [LicenceActivateBody](#schema-licenceactivatebody) — The key to activate, and the terms acceptance when the key needs one

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | yes | The licence key exactly as issued. AMP keys are GUIDs |
| `terms` | null or [TermsAcceptanceBody](#schema-termsacceptancebody) | yes |  |

**Responses:**

- `201` The activated licence as a LicenceDto → [LicenceDto](#schema-licencedto) (`text/plain`)
- `400` The key is empty or malformed, the key needs a terms acceptance that was not given (termsRequired), the acceptance named the wrong version (termsWrongVersion) or an agreement the service does not serve (termsUnknownAgreement), the migration does not apply (termsNotApplicable), the activation could not vouch for the key (termsNotAuthentic), or the service refused it (termsRefused) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the licence-manage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The licensing server has no record of the key → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `502` The licensing server could not be reached, or could not confirm the terms - try again later

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `POST /api/licence/query`

**Preview a licence key**

Requires the licence-manage capability. Asks the licensing server what the key is
without activating it, so the user can confirm before committing an activation slot.
The key is not stored.

**Request body** (required, `application/json`): [LicenceKeyBody](#schema-licencekeybody) — The key to look up

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | yes | The licence key exactly as issued. AMP keys are GUIDs |

**Responses:**

- `200` What the key would grant as a LicencePreviewDto → [LicencePreviewDto](#schema-licencepreviewdto) (`text/plain`)
- `400` The key is empty → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the licence-manage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The licensing server has no record of the key, or its edition isn't usable with AMP3 → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `502` The licensing service could not confirm where the key stands against the licence terms

**Live:** no token → `401` · token → not called (state-changing call with no id to aim at a non-existent object)

---

#### `GET /api/licence/terms/agreement`

**The licence migration agreement**

Requires the licence-manage capability. The text of the AMP 2 to AMP 3 Licence Migration as the
licensing service serves it, in the language asked for or English when it has no translation
(then `fallbackFrom` names the language asked for). Its hash is what an acceptance quotes.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `version` | query | integer (int32) |  | The agreement version, from a key's preview or a licence's requiredTermsVersion |
| `lang` | query | string |  | The panel's language |

**Responses:**

- `200` The agreement as a LicenceAgreementDto → [LicenceAgreementDto](#schema-licenceagreementdto) (`text/plain`)
- `403` The caller lacks the licence-manage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` The service has no agreement of that version → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `502` The licensing service did not answer verifiably

**Live:** no token → `401` · token → `404` (licence.error.termsUnknownVersion)

---

#### `DELETE /api/licence/{spireId}/{gradeId}`

**Remove a licence**

Requires the licence-manage capability. Deletes the edition's activation file on the spire
holding it - this one, or another spire asked to do it, since a file bound to a machine can
only be deleted there. Any features only that edition granted stop being licensed immediately.
The key itself is unaffected and can be activated again later.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `spireId` | path | string (uuid) | yes | The spire holding the licence |
| `gradeId` | path | string (uuid) | yes | The grade identifier of the licence to remove |

**Responses:**

- `204` The licence was removed
- `403` The caller lacks the licence-manage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No licence with that grade is activated on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The spire holding it refused → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `503` The spire holding it is unreachable or did not respond in time

**Live:** no token → `401` · token → `503` (spire.error.unreachable)

---

#### `POST /api/licence/{spireId}/{gradeId}/terms`

**Accept the current licence terms for an activated licence**

Requires the licence-manage capability. For a licence that stands TermsRequired: records the
holder's acceptance of the AMP 2 to AMP 3 Licence Migration with the licensing service, vouched
for by the activation held on the spire named (this one, or another spire asked to do it, since
only the spire holding the file can read its credentials), and reports usage straight after so
the licence counts again. Accepting terms already held is a success.

The body carries the key the licence was activated with: the activation file does not hold it,
AMP keeps no key, and the licensing service verifies it against the activation. It is used for
this one call and never stored, logged or audited.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `spireId` | path | string (uuid) | yes | The spire holding the licence |
| `gradeId` | path | string (uuid) | yes | The grade identifier of the licence |

**Request body** (required, `application/json`): [LicenceTermsAcceptBody](#schema-licencetermsacceptbody) — The key, and the acceptance as the panel collected it

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | yes | The licence key the licence was activated with. AMP keeps no key, so it comes with every     acceptance; the licensing service verifies it against the activation |
| `termsVersion` | integer (int32) | yes | The version accepted; the required version the agreement was fetched for |
| `agreementHash` | string | yes | The hash of the agreement text shown. A hash that is no longer current is refused |
| `attestation` | string | yes | The attestation exactly as it was affirmed |
| `locale` | string | yes | The panel's language at the time |
| `clickedAt` | string (date-time) | yes | When the accepter committed |

**Responses:**

- `204` The acceptance was recorded
- `400` The key is empty or malformed (malformedKey), the acceptance is incomplete, named the wrong version (termsWrongVersion) or an agreement the service does not serve (termsUnknownAgreement), the migration does not apply (termsNotApplicable), the activation could not vouch for the key (termsNotAuthentic), or the service refused it (termsRefused) → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `403` The caller lacks the licence-manage capability → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `404` No licence with that grade is activated on this spire → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `409` The spire holding it refused → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `502` The licensing service did not answer verifiably
- `503` The spire holding it is unreachable or did not respond in time

**Live:** no token → `401` · token → `400` (validation: `Key` required)

---

### 12-Branding & Appearance

_The operator's public-facing product name, logo, and theme._

#### `GET /api/branding`

**Get the operator's branding**

Anonymous and cacheable for 30 seconds; contains only presentation fields that are public by definition.

**Responses:**

- `200` The current branding → [BrandingDto](#schema-brandingdto) (`text/plain`)

**Live:** no token → `200` · token → `200`

---

### 12-Security

#### `POST /api/csp-report`

**Report a Content-Security-Policy violation**

Accepts the body a browser sends for a `report-uri` directive (`application/csp-report`)
or the Reporting API (`application/reports+json`) and logs it. At most 8 KiB, and
rate limited per client address.

**Responses:**

- `204` The report was logged
- `413` The report is larger than 8 KiB → [ProblemDetails](#schema-problemdetails) (`text/plain`)
- `429` Too many reports from this address; try again later → [ProblemDetails](#schema-problemdetails) (`text/plain`)

**Live:** no token → `204` · token → `204`

---

## 13. Data types (schemas)

All 389 schemas in the spec. A `?` after a type means it can be null. Integer fields written as "integer" may also arrive as numeric strings: the spec allows both for 64-bit values.

<a id="schema-aarch64support"></a>
#### Aarch64Support

Whether and how the application runs on aarch64 (64-bit ARM) hosts

Type: integer

<a id="schema-abandonrequest"></a>
#### AbandonRequest

A ceremony the browser gave up on

| Field | Type | Required | Description |
|---|---|---|---|
| `flowId` | string (uuid) | yes | The handle returned by the begin call |

<a id="schema-acceptedinvite"></a>
#### AcceptedInvite

The redeemed account's sign-in, plus any invited roles it did not receive

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | The session token, as a normal sign-in returns it |
| `expiration` | string (date-time) | yes | When the token expires |
| `droppedRoles` | string[] | yes | Names of invited roles withheld because the inviting admin no longer holds everything they carry |

<a id="schema-acceptinvitebody"></a>
#### AcceptInviteBody

Fields the invitee submits to redeem an invite. Username and roles are fixed by the admin

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | The one-time secret code from the invite link |
| `email` | string | yes | The invitee's own email. Marked confirmed on the created account |
| `password` | string | yes | The invitee's own password. Must satisfy the configured password policy |
| `displayName` | string? |  | Optional friendly name chosen in the wizard; falls back to the admin's suggestion |
| `locale` | string? |  | Optional preferred locale; seeded into the account's preferences |

<a id="schema-acknowledgeplatformwarningrequest"></a>
#### AcknowledgePlatformWarningRequest

A request to dismiss one occurrence of an informational platform warning

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | The string PlatformWarning.Id the listing returned |

<a id="schema-aclchange"></a>
#### AclChange

One requested change: set a principal's verb to allow, deny, or inherit

| Field | Type | Required | Description |
|---|---|---|---|
| `principalId` | string | yes | Role id, or user id for an individual |
| `verb` | string | yes | read / write / delete |
| `state` | string | yes | `allow` writes a grant at this path, `deny` writes a carve-out, and `inherit` removes this path's own rule so whatever an ancestor says applies again |

<a id="schema-aclprincipal"></a>
#### AclPrincipal

A role, or an individual person via their self-role, with their access to one path

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Role id, or user id (a self-role's id IS the user id) |
| `name` | string | yes | Display name |
| `isUser` | boolean | yes | True when this is a person rather than a named role |
| `verbs` | [AclVerb](#schema-aclverb)[] | yes | One entry per verb, always all three, in read/write/delete order |

<a id="schema-aclverb"></a>
#### AclVerb

What a principal may do with one verb on one path, and where that came from

| Field | Type | Required | Description |
|---|---|---|---|
| `verb` | string | yes | read / write / delete |
| `state` | string | yes | The effective outcome: `allow`, `deny`, or `none` |
| `explicit` | boolean | yes | True when a rule names THIS path exactly, rather than being inherited |
| `source` | string? | yes | The path whose rule decided this, as the user writes it (empty = the shard root). Null when nothing matched, i.e. the state is a plain "no access". |
| `inheritedState` | string | yes | What this verb would fall back to if the rule on THIS path were removed - the answer the folders above give on their own. Lets the editor show where "clear this rule" is returning TO while the change is still unsaved, instead of an empty box that reads as "no access" |
| `inheritedSource` | string? | yes | The path deciding string AclVerb.InheritedState, on the same terms as string? AclVerb.Source |

<a id="schema-actionfacet"></a>
#### ActionFacet

One action value and how many events used it

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | The audit action identifier |
| `count` | integer (int32) | yes | Number of events with this action |

<a id="schema-actionid"></a>
#### ActionId

| Field | Type | Required | Description |
|---|---|---|---|
| `value` | string (uuid) |  |  |

<a id="schema-activityrule"></a>
#### ActivityRule

| Field | Type | Required | Description |
|---|---|---|---|
| `visits` | integer (int32) |  |  |
| `days` | integer (int32) |  |  |

<a id="schema-addcustomportbody"></a>
#### AddCustomPortBody

Request body for registering a user-defined custom port

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Display name for the custom port |
| `protocol` | string | yes | Transport protocol; parsed case-insensitively, defaulting to TCP when unrecognised |
| `port` | integer (int32) | yes | Preferred port number, or null to let the allocator assign one |

<a id="schema-addressanswer"></a>
#### AddressAnswer

The users seen at one address

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string | yes |  |
| `network` | string | yes | What was matched: the address, or its IPv6 /64 network |
| `kind` | [AddressKind](#schema-addresskind) | yes |  |
| `users` | [AddressUser](#schema-addressuser)[] | yes | Most recently seen first; names only where the caller may see them |
| `serverInfo` | map<string, [AnalyticsServer](#schema-analyticsserver)> | yes |  |
| `isDemo` | boolean |  |  Default `false`. |

<a id="schema-addressgroup"></a>
#### AddressGroup

One address in a search, and who used it

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string | yes |  |
| `network` | string | yes |  |
| `kind` | [AddressKind](#schema-addresskind) | yes |  |
| `recovered` | boolean | yes | Every session here was followed back through AMP's relays to it |
| `users` | [AddressUser](#schema-addressuser)[] | yes |  |

<a id="schema-addresskind"></a>
#### AddressKind

What an address says about who is behind it, which decides whether two users seen at it are likely to be one household or merely share infrastructure.

Type: object enum: `Public`, `Tunnel`, `Shared`, `Private`, `Loopback`

<a id="schema-addresssearch"></a>
#### AddressSearch

What an address search found: the range asked about, and each address in it with its users

| Field | Type | Required | Description |
|---|---|---|---|
| `range` | string | yes | The range as it was understood: one address, or a network |
| `kind` | [AddressKind](#schema-addresskind) | yes | What the range's own address says about who is behind it |
| `addresses` | [AddressGroup](#schema-addressgroup)[] | yes | Each address seen in it, most recently used first, with who used it |
| `users` | integer (int32) | yes | How many different users across all of them |
| `serverInfo` | map<string, [AnalyticsServer](#schema-analyticsserver)> | yes |  |
| `isDemo` | boolean |  |  Default `false`. |

<a id="schema-addresssummary"></a>
#### AddressSummary

An address or range typed into the search, as the search shows it

| Field | Type | Required | Description |
|---|---|---|---|
| `range` | string | yes |  |
| `kind` | [AddressKind](#schema-addresskind) | yes |  |
| `addresses` | integer (int32) | yes |  |
| `users` | integer (int32) | yes |  |

<a id="schema-addressuse"></a>
#### AddressUse

One address a user has connected from, over the sessions the reader can see

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string | yes | The most recent address seen; for IPv6, one of the network's |
| `network` | string | yes | What matching is done on: the address itself for IPv4, the /64 network for IPv6 |
| `token` | string | yes | The same, as a bus token, for asking who else used it |
| `kind` | [AddressKind](#schema-addresskind) | yes |  |
| `country` | string | yes |  |
| `firstSeen` | string (date-time) | yes |  |
| `lastSeen` | string (date-time) | yes |  |
| `sessions` | integer (int32) | yes |  |
| `shards` | string (uuid)[] | yes |  |
| `otherUsers` | integer (int32) |  | How many other users the reader can see have connected from it; null where matching means nothing |
| `recovered` | boolean |  | The server saw a relay's address; this one was found by following the connection back through it Default `false`. |

<a id="schema-addressuser"></a>
#### AddressUser

One user seen at an address

| Field | Type | Required | Description |
|---|---|---|---|
| `userKey` | string | yes |  |
| `displayName` | string? | yes |  |
| `firstSeen` | string (date-time) | yes |  |
| `lastSeen` | string (date-time) | yes |  |
| `sessions` | integer (int32) | yes |  |
| `shards` | string (uuid)[] | yes |  |

<a id="schema-addsshkeyrequest"></a>
#### AddSshKeyRequest

A public key to register against the caller's account

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | yes | The key in one-line `authorized_keys` form: `type base64 [comment]`. This is the contents of a `.pub` file, never a private key. |
| `label` | string? | yes | Friendly name for the key. Blank falls back to the line's trailing comment, and then to the key type, so a key is never listed with no name at all |
| `expiresAt` | string? (date-time) | yes | Optional expiry; omit for a key that does not expire |

<a id="schema-adminmethods"></a>
#### AdminMethods

The channel AMP uses to send administrative commands to, and read output from, the application

Type: integer

<a id="schema-admissiondto"></a>
#### AdmissionDto

A machine's request to be admitted on a join file, as the person deciding sees it

| Field | Type | Required | Description |
|---|---|---|---|
| `fingerprint` | string | yes | The fingerprint of the key the machine proved; the installing machine printed the same string |
| `machineName` | string | yes | The machine's own name, as it reported it |
| `sourceAddress` | string? | yes | Where the request came from as the bus server saw it; null when the bus could not be asked |
| `requestedAt` | string (date-time) | yes | When the machine first asked |
| `lastSeenAt` | string (date-time) | yes | When it last asked whether it had been approved |
| `expiresAt` | string (date-time) | yes | When an unanswered request lapses |
| `approvedAt` | string? (date-time) | yes | When a person approved it, if one has |
| `approvedByName` | string? | yes | Who approved it |
| `deniedAt` | string? (date-time) | yes | When a person refused it, if one has |
| `deniedByName` | string? | yes | Who refused it |
| `suspect` | boolean | yes | True when another machine presented the same join file after this request was made |
| `replayCount` | integer (int32) | yes | How many times another machine did |
| `replayFingerprint` | string? | yes | The fingerprint the most recent other machine presented |

<a id="schema-agentstatusdto"></a>
#### AgentStatusDto

Whether this shard's running agent predates the one the spire would launch now

| Field | Type | Required | Description |
|---|---|---|---|
| `runningVersion` | string? | yes | The agent version this shard is running right now, or null when nothing is running. Deliberately NOT the last-known version of a stopped shard: `ShardState.AgentVersion` persists after a stop, and reporting it here would name a version nothing is executing. |
| `availableVersion` | string? | yes | The agent version a restart would pick up, or null if it cannot be determined |
| `outOfDate` | boolean | yes | True only when the shard is RUNNING, both versions are known, and the running one is older. Unknown on either side is reported as false: "cannot confirm" must never present as "out of date". |
| `spireId` | string (uuid) | yes | The spire this shard runs on, whose newest agent is the one a restart picks |
| `spireName` | string | yes | That spire's display name |
| `spireVersion` | string? | yes | The AMP version that spire reports, or null if unknown |
| `installationVersion` | string? | yes | The version the installation is on (the highest any spire reports) |
| `spireBehind` | boolean | yes | True when the owning spire runs an older AMP than the installation. Its agents can never be newer than it, so a restart cannot help: the spire has to be updated first. |

<a id="schema-amplistenerkind"></a>
#### AmpListenerKind

Which of AMP's own listeners holds a port

Type: object enum: `Panel`, `Bus`, `Sftp`, `EntryNodeBootstrap`, `WireGuardTunnel`, `TunnelProbe`

<a id="schema-analyticsserver"></a>
#### AnalyticsServer

What the page needs to show a server: its name, the game it runs, and the artwork to draw it with. Supplied by the caller, which knows the shards; a summary only knows their ids.

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes |  |
| `game` | string | yes | The template slug, so servers of one game group together |
| `gameName` | string | yes | The template's display name |
| `image` | string? | yes | The template's image source, as a spec writes it (steam:, url: or internal:) |

<a id="schema-apierror"></a>
#### ApiError

Machine-readable error payload returned by the API for user-facing failures. The frontend translates string ApiError.Code (with IReadOnlyDictionary<string, string>? ApiError.Params) via its i18n catalog; string ApiError.Message is an English developer/fallback string used when the client has no translation for the code.

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | yes |  |
| `params` | map<string, string> | yes |  |
| `message` | string | yes |  |

<a id="schema-applicationlifecycle"></a>
#### ApplicationLifecycle

Defines how an application launches, is detected as ready, and shuts down: executable paths, command-line arguments, ready/exit behaviour, and quiescence.

| Field | Type | Required | Description |
|---|---|---|---|
| `rootDir` | string |  | Root directory the application is installed into, relative to the shard directory |
| `baseDirectory` | string |  | Base directory that config-file paths are resolved against |
| `executableWin` | string |  | Executable to launch on Windows |
| `executableLinux` | string |  | Executable to launch on Linux |
| `executableContainer` | string |  | Executable to launch when the shard runs in a container AMP manages; empty means string ApplicationLifecycle.ExecutableLinux. Besides a path it may be `java:<major>`, which the agent resolves where it runs to the newest installed JDK of that major, so a template chooses a Java by version from whatever the image carries rather than by a host path the container does not have. |
| `workingDir` | string |  | Working directory to run the executable from; empty means the base directory |
| `compatibility` | [CompatibilityModes](#schema-compatibilitymodes) |  | Console-buffering workarounds the app needs (App.Compatibility): apps that detect a pipe and switch to block-buffered output get a pseudo-terminal instead (ConPTY on Windows, socat pty on Linux). |
| `linuxCommandLineArgs` | string |  | Extra command-line arguments applied only on Linux |
| `windowsCommandLineArgs` | string |  | Extra command-line arguments applied only on Windows |
| `commandLineArgs` | string |  | Base command-line arguments applied on all platforms |
| `commandLineParameterFormat` | string |  | Format string for each generated setting argument: {0}=key, {1}=value |
| `commandLineParameterDelimiter` | string |  | Delimiter placed between generated command-line parameters |
| `rapidStartup` | boolean |  | Skip the usual start-up delays and bring the app up as fast as possible |
| `applicationReadyMode` | [ReadyIndicator](#schema-readyindicator) |  | How AMP determines the app has finished starting |
| `applicationReadyTimeoutSeconds` | integer (int32) |  | Seconds before ReadyIndicator.Timeout mode declares the app ready (also the cap for other modes if you want a fallback) |
| `monitorChildProcessName` | string |  | A regex over a process's command line naming the real server when the executable AMP launches is only a launcher for it (Proton's script, a mod loader). Its template tokens are expanded with their values regex-escaped. Empty means the launched process is the server. When set, metrics, liveness, the exit code and the stop path follow the matching process with the most resident memory among the shard's own processes. |
| `monitorDirectChildOnly` | boolean |  | Only a descendant of the launched process may be the monitored server |
| `supportsUniversalSleep` | boolean |  | This application can be put to sleep: stopped while empty, with AMP holding its ports and              starting it again when someone tries to connect (AMP 2's App.SupportsUniversalSleep).               A capability claim about the app's clients: that they touch the declared port before they need a reply, and that they wait long enough for the real server to come up. False leaves sleep hidden entirely rather than offered and broken. |
| `wakeupMode` | [SleepWakeupModes](#schema-sleepwakeupmodes) |  | Which triggers may wake this shard from sleep |
| `sleepOnStart` | boolean |  | Put the shard straight to sleep once it has started and proved it can |
| `sleepDelayMinutes` | integer (int32) |  | How many minutes the shard must be empty before it sleeps |
| `sleepDozeDelayMinutes` | integer (int32) |  | How many minutes to wait before sleeping again when the shard was woken but nobody actually joined. Shorter than int ApplicationLifecycle.SleepDelayMinutes on purpose: a wake that came to nothing (a server browser refreshing, a port scan) should not hold the shard up for the full delay. |
| `sleepStartThresholdSeconds` | integer (int32) |  | The longest a cold start may take, in seconds, for sleep to be offered at all.               Waking is only invisible if the server accepts connections before the client that woke it gives up, and most give up around thirty seconds. So AMP times every start, and a shard that took longer than this is not offered sleep for that run - the alternative is a shard that sleeps and then cannot be reached, which is worse than one that never slept. The maintainer raises it only for titles whose clients are known to wait longer. |
| `steamUpdateAnonymousLogin` | [SteamAnonymousLoginMode](#schema-steamanonymousloginmode) |  | Whether SteamCMD update stages can log in anonymously |
| `exitMethod` | [ExitMethod](#schema-exitmethod) |  | How to shut the app down (used on all platforms unless overridden by ExitMethod ApplicationLifecycle.ExitMethodWindows) |
| `exitMethodWindows` | [ExitMethod](#schema-exitmethod) |  | Windows-specific shutdown method; ExitMethod.None means use ExitMethod ApplicationLifecycle.ExitMethod |
| `startupAuthenticationRequired` | boolean |  | The app requires an authentication step during startup before it can run |
| `exitTimeout` | integer (int32) |  | Seconds to wait for a graceful exit before force-killing the process |
| `exitString` | string |  | Command string sent to the console for ExitMethod.String shutdown |
| `exitFile` | string |  | File written for ExitMethod.WriteToFile shutdown |
| `quiescenceCommand` | string |  | Command that tells the app to flush state and pause (for consistent snapshots/backups). Several may be given, separated by `;` - Minecraft needs "save-off;save-all flush", since disabling writes and forcing the flush are two separate commands. |
| `dequiescenceCommand` | string |  | Command that resumes the app after a quiescence; `;`-separated as above |
| `quiesceAckRegex` | string |  | Console line proving the flush finished - "Saved the game" and the like. |
| `quiesceSettleDelayMilliseconds` | integer (int32) |  | After issuing string ApplicationLifecycle.QuiescenceCommand, how long to wait before touching files. |

<a id="schema-applicationmanagement"></a>
#### ApplicationManagement

How AMP administers a running application: console/RCON access, admin credentials, IP binding behaviour, and declared user capacity.

| Field | Type | Required | Description |
|---|---|---|---|
| `hasWriteableConsole` | boolean |  | The app accepts commands typed into its console |
| `hasReadableConsole` | boolean |  | The app produces console output AMP can read |
| `udpLogger` | boolean |  | The app emits its log over UDP rather than to the console |
| `supportsLiveSettingsChanges` | boolean |  | The app can apply setting changes at runtime without a restart |
| `liveSettingChangeCommandFormat` | string |  | Console command format used to apply a live setting change: {0}=key, {1}=value |
| `adminMethod` | [AdminMethods](#schema-adminmethods) |  | The channel used to send admin commands to the app |
| `remoteAdminPassword` | string |  | Template default for the remote-admin (RCON/telnet) password, surfaced as the `$RemoteAdminPassword` shared variable. Usually blank in the template; the per-shard value is generated at creation and stored in the shard config under the same key. |
| `useRandomAdminPassword` | boolean |  | When true, AMP owns the admin password: it is randomly generated and the user has no say (the safe default for most templates). When false, the user supplies and manages it. See bool ApplicationManagement.PersistRandomPassword. |
| `persistRandomPassword` | boolean |  | Only meaningful with bool ApplicationManagement.UseRandomAdminPassword. When true, the generated password is kept across starts; when false (default), a fresh one is generated on every start. |
| `rconConnectDelaySeconds` | integer (int32) |  | Seconds to wait after the app starts before attempting the first RCON connection |
| `rconHeartbeatMinutes` | number/string (double) |  | Interval in minutes between RCON heartbeat commands; 0 disables the heartbeat |
| `rconHeartbeatCommand` | string |  | Command sent to keep the RCON connection alive on each heartbeat |
| `ignoreSTDOUTAfterRCON` | boolean |  | For games whose STDOUT becomes noise/duplicate once the remote console (RCON) is live: stop forwarding the process's STDOUT to the console once RCON connects. |
| `rconSelectIPMethod` | [RCONIPSelectMethod](#schema-rconipselectmethod) |  | Which local address AMP connects to for RCON |
| `telnetLoginFormat` | string |  | Format for the telnet login/authentication line: {0}=password |
| `telnetNewLineType` | [NewLineType](#schema-newlinetype) |  | Line-ending sent on the telnet admin channel |
| `tailLogFilePath` | string |  | Path to the log file tailed when AdminMethods.TailLogFile is in use |
| `applicationIPBinding` | string |  | Template default for the bind address; per-shard override via the $ApplicationIPBinding setting |
| `forceIPBinding` | boolean |  | The app MUST be given an explicit bind address (never run unbound) |
| `supportsIPv6` | boolean |  | The app can bind to an IPv6 address. When false (the default, AMP 2 parity: e.g. TF2/srcds), an IPv6 bind is rejected at start and IPv6 addresses are not offered as binding options. |
| `useSteamQueryForStatus` | boolean |  | Track connected users by polling the app's Steam query port rather than by reading the console (AMP 2's App.UseSteamQueryForStatus). For titles that report nothing usable on STDOUT, this is the only way AMP can tell whether anyone is playing - and therefore the only way sleep mode can know the server is empty. |
| `maxUsers` | integer (int32) |  | Template-declared maximum concurrent users/players; surfaced as the `$MaxUsers` shared variable. 0 = unlimited (the default). Per-shard overrides are stored in the shard config under the same key. |
| `environmentVariables` | map<string, string> |  | Environment variables set on the app process, on top of the inherited environment.              Values carry the usual `{{$FullBaseDir}}`-style template tokens, and may reference              the INHERITED value of a variable as `%NAME%` - which is what makes an append like              `LD_LIBRARY_PATH` possible rather than a destructive replace:               ```"LD_LIBRARY_PATH": "{{$FullBaseDir}}linux64:{{$FullRootDir}}linux64:%LD_LIBRARY_PATH%"```Not optional dressing: on Linux this is how a title finds its bundled shared objects and how loader shims are installed (LD_PRELOAD, BepInEx/doorstop). A title whose template declares these will fail to start, or start crippled, without them. |

<a id="schema-applicationstate"></a>
#### ApplicationState

The lifecycle state of a shard, from stopped through configuring, running, sleeping, and failure

Type: integer

<a id="schema-appspec"></a>
#### AppSpec

The complete definition of a manageable application (a game server or app), parsed from a template's .kvp file. Everything AMP needs to install, configure, launch, monitor, and update a shard of this application.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) |  | Stable unique identifier for this template definition |
| `schemaVersion` | integer (int32) |  | Version of the AppSpec schema this document conforms to, for forward migration |
| `templateSlug` | string |  | The template slug as it appears in the repository (e.g. "ark-se", "minecraft"). Derived from the .kvp filename. Stable across updates. |
| `lifecycle` | [ApplicationLifecycle](#schema-applicationlifecycle) |  | How the application launches, becomes ready, and exits |
| `management` | [ApplicationManagement](#schema-applicationmanagement) |  | How AMP administers the running application (console, RCON, IP binding, capacity) |
| `metadata` | [SpecMetadata](#schema-specmetadata) |  | Descriptive and platform metadata (display name, OS/arch support, container policy, release state) |
| `console` | [ConsoleSpec](#schema-consolespec) |  | How AMP reads and interprets the application's console output |
| `portReferences` | [PortReferences](#schema-portreferences) |  | Named port references used to resolve which port serves each role (admin, primary, sleep query) |
| `updateStages` | [UpdateStage](#schema-updatestage)[] |  | Ordered stages that install and update the application (SteamCMD, downloads, extraction, etc.) |
| `preStartStages` | [UpdateStage](#schema-updatestage)[] |  | Stages run on EVERY start, after PreStart and before the app launches (App.PreStartStages - same shape as update stages, e.g. Factorio creating its world save). Condition gating typically makes them one-time in practice. |
| `portRequirements` | [PortRequirement](#schema-portrequirement)[] |  | Network ports the application needs, with their roles, protocols, and ranges |
| `versionSources` | [VersionSource](#schema-versionsource)[] |  | Upstream manifests this app's installable versions are read from, so version settings can be real dropdowns rather than a free-text box. Empty for the majority of templates, whose download URL is fixed or resolved by an update stage. |
| `settings` | [SettingSpec](#schema-settingspec)[] |  | The configurable settings exposed for this application (command-line args, config-file values, presentation) |
| `configFileMappings` | [ConfigFileMapping](#schema-configfilemapping)[] |  | How settings are read from and written back to the application's config files |
| `events` | [EventSpec](#schema-eventspec)[] |  | Events this application reports on its console, declared as regexes whose named capture groups become the event's parameters. They appear in the schedule editor's trigger picker beside AMP's own events and behave identically once fired. |
| `backupExclusions` | string[] |  | Files this application produces that are worthless in a backup - logs, crash dumps,              caches. AMP 2 carried these in a `.backupExclude` file shipped inside the template's              data; declaring them here instead keeps the whole application definition in one document.               Used once, to seed a new shard's exclusion list (.amp/.usersnapshotignore) - after that the list is the user's, and the file manager's snapshot toggle edits it. Paths are relative to the app's base directory and use the same grammar as that file: logs/ for a folder and its subtree, *.log for a single-segment glob. |
| `locale` | object |  | The template's own translations, keyed by language, in Transloco's own shape. Any user-facing string elsewhere in the spec may be a key into this table. |
| `containerRuntimeAvailable` | boolean |  | Whether AMP has a container image it can run this app in. Not authored: the API sets it when a spec is served, from the live ContainerImageCatalog for whatever image this template names, and it is false only when that image is one of AMP's own that the image publisher has not built. A spec read from disk or the bus carries the default, true.  Carried on the spec so the catalogue can grey the app out using the SAME table the launcher resolves against, rather than the browser keeping its own copy of which images exist. It is spire-independent on purpose - whether a missing image actually BLOCKS creation also depends on whether the app would be containerised on the chosen spire, which is the caller's half of the question (see ContainerModePolicy.CanCreate). |

<a id="schema-appstatus"></a>
#### AppStatus

One status, the single source of truth for "what state is the app in?"

| Field | Type | Required | Description |
|---|---|---|---|
| `phase` | string | yes | One of the AppPhase values |
| `message` | string? |  | A coded reason for the phase (a locale key), when there is one to give |
| `preview` | null or [PreviewStatus](#schema-previewstatus) |  |  |
| `messageParams` | map<string, string> |  | Values the Message key interpolates |
| `plainText` | boolean |  | The request that fetched this status came over plain HTTP from a public address, so the sign-in details about to be typed would travel in clear text. Decided per request by the status endpoint (PlainTextAccess); the login screen shows its warning on it. Never set on a status a setup action returns. Default `false`. |

<a id="schema-architecture"></a>
#### Architecture

Type: integer

<a id="schema-archivebody"></a>
#### ArchiveBody

Sources plus a destination for an in-place archive

| Field | Type | Required | Description |
|---|---|---|---|
| `paths` | string[] | yes | Source paths to include, relative to the shard working directory. Folders are included recursively |
| `destination` | string | yes | Destination path for the new zip, relative to the shard working directory. The caller owns the .zip extension |

<a id="schema-archiveinfodto"></a>
#### ArchiveInfoDto

What an archive holds, so the user can be told before unpacking it

| Field | Type | Required | Description |
|---|---|---|---|
| `entries` | integer (int32) | yes | Number of files in the archive (folders are implied by their contents, not counted) |
| `totalSize` | integer (int64) | yes | Total size on disk after extraction, in bytes |

<a id="schema-assertionresponse"></a>
#### AssertionResponse

| Field | Type | Required | Description |
|---|---|---|---|
| `authenticatorData` | object (byte) | yes |  |
| `signature` | object (byte) | yes |  |
| `clientDataJSON` | object (byte) | yes |  |
| `userHandle` | object (byte) |  |  |

<a id="schema-attestationresponse"></a>
#### AttestationResponse

| Field | Type | Required | Description |
|---|---|---|---|
| `attestationObject` | object (byte) | yes |  |
| `clientDataJSON` | object (byte) | yes |  |
| `transports` | [AuthenticatorTransport](#schema-authenticatortransport)[]? | yes |  |

<a id="schema-auditevent"></a>
#### AuditEvent

An immutable record of a significant action that occurred in the system. Appended to a JetStream stream - never mutated, never deleted in normal operation.  Subject pattern: `amp.tn.{tenant}.audit.{auditId}` Stream filter: `amp.tn.*.audit.>`

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) |  | Unique identifier for this audit entry, used as the subject suffix on the stream |
| `tenantId` | string |  | Tenant the event belongs to, scoping it to the tenant's audit stream |
| `timestamp` | string (date-time) |  | When the action occurred, in UTC |
| `severity` | [AuditSeverity](#schema-auditseverity) |  | How significant this event is for alerting purposes |
| `principal` | [TaskPrincipal](#schema-taskprincipal) |  | Who caused this event |
| `action` | string |  | Categorised action identifier, e.g. "ApplicationStarted", "ServerUpdated", "UserLoggedIn", "TaskCancelled", "ScheduleCreated". Should be PascalCase and stable across versions - used for filtering/querying. |
| `targetType` | string |  | Entity type the action was performed on, e.g. "Instance", "User" |
| `targetId` | string |  | ID of the entity the action was performed on |
| `taskId` | string? (uuid) |  | If this event is linked to a ManagedTask, its ID is recorded here so the audit log can link out to the task queue entry. |
| `correlationId` | null or [CorrelationId](#schema-correlationid) |  |  |
| `actionId` | null or [ActionId](#schema-actionid) |  |  |
| `causationActionId` | null or [ActionId](#schema-actionid) |  |  |
| `origin` | [AuditOrigin](#schema-auditorigin) |  | How this event came to be (see AuditOrigin): directly in a request the user made, inside the flow such a request started, on the system's own initiative, from a schedule, or under impersonation. Independent of TaskPrincipal AuditEvent.Principal, which says who. An event written before this existed reads as AuditOrigin.System. |
| `rootPrincipal` | null or [TaskPrincipal](#schema-taskprincipal) |  |  |
| `details` | string? |  | Optional free-form JSON payload for additional context, e.g. `{"PreviousVersion":"1.0","NewVersion":"1.1"}`. Not queried directly - present for human/diagnostics inspection. |
| `originIp` | string? |  | Remote IP the triggering request came from, when the event originated from an HTTP request. Filled automatically by `AuditWriter` from the ambient HttpContext; null for system/background-originated events. |
| `originSpireId` | string? (uuid) |  | The spire whose process raised this event - i.e. which AMP node was serving the request.              Filled automatically by `AuditWriter`, so every event carries it without call sites              having to remember.               A multi-node install writes into ONE shared audit stream, so without this an entry says what happened and to whom but not where, and "which of my front ends is that scanner actually hitting" has no answer. Null only for events written before this existed. |
| `originNode` | string? |  | The machine name of the node that raised the event. Redundant against Guid? AuditEvent.OriginSpireId and kept anyway: the id is the durable handle, but it needs a lookup to mean anything, and an audit entry read during an incident should be legible on its own - including for a spire that has since been removed. |

<a id="schema-auditfacets"></a>
#### AuditFacets

Distinct-value counts used to populate the audit log's filter dropdowns

| Field | Type | Required | Description |
|---|---|---|---|
| `actions` | [ActionFacet](#schema-actionfacet)[] | yes | Each action seen, with how many events carry it |
| `users` | [UserFacet](#schema-userfacet)[] | yes | Each acting user seen, with how many events they produced |
| `targetTypes` | string[] | yes | The distinct target types present in the window |
| `origins` | [OriginFacet](#schema-originfacet)[] | yes | Each origin seen, with how many events carry it |

<a id="schema-auditorigin"></a>
#### AuditOrigin

How an audit event came to be, as distinct from who it names: PrincipalKind TaskPrincipal.Kind says WHO acted, this says HOW the action arose. The seam it draws is between "a user did this, in the request they made" and "the system did this, inside the flow that request started": the same System principal (an updater, a launcher, a reconciler) carries a different origin depending on whether a user's click is behind it. Numbered explicitly because the value is stored on the audit stream; an event written without one reads as AuditOrigin.System.

Type: integer

<a id="schema-auditqueryresult"></a>
#### AuditQueryResult

A page of audit events plus the paging totals the UI needs to render controls

| Field | Type | Required | Description |
|---|---|---|---|
| `total` | integer (int32) | yes | Total number of events matching the query across all pages |
| `page` | integer (int32) | yes | 1-based index of the page returned |
| `pageSize` | integer (int32) | yes | Maximum number of events per page |
| `caughtUp` | boolean | yes | True when the in-memory index has finished replaying the stream, so the result is complete rather than partial |
| `items` | [AuditEvent](#schema-auditevent)[] | yes | The audit events on this page |

<a id="schema-auditrootitem"></a>
#### AuditRootItem

One flow as the roots listing shows it: its root event and the span of what followed

| Field | Type | Required | Description |
|---|---|---|---|
| `correlationId` | string (uuid) | yes | The flow's thread, which `GET api/audit/tree/{correlationId}` expands |
| `root` | [AuditEvent](#schema-auditevent) | yes | The root event: the user's request, the system's tick or the schedule's firing, or the flow's first event when the request or tick wrote none of its own. When RootMissing, the earliest event the index holds instead |
| `eventCount` | integer (int32) | yes | Every event of the flow, the root included |
| `startedAt` | string (date-time) | yes | The earliest event's time |
| `endedAt` | string (date-time) | yes | The latest event's time |
| `maxSeverity` | [AuditSeverity](#schema-auditseverity) | yes | The loudest severity any event of the flow carries |
| `rootMissing` | boolean | yes | True when the index dropped events of the flow from its window and none of those it still holds was written by the request or tick itself: the flow is listed on its earliest held event, which may not be its first and is not passed off as the root |

<a id="schema-auditrootsresult"></a>
#### AuditRootsResult

A page of flows, newest root first unless asked otherwise

| Field | Type | Required | Description |
|---|---|---|---|
| `total` | integer (int32) | yes | Flows matching across all pages |
| `page` | integer (int32) | yes | 1-based index of the page returned |
| `pageSize` | integer (int32) | yes | Maximum number of flows per page |
| `caughtUp` | boolean | yes | True when the index has finished replaying the stream |
| `items` | [AuditRootItem](#schema-auditrootitem)[] | yes | The flows on this page |

<a id="schema-auditseverity"></a>
#### AuditSeverity

How loud this audit event should be treated by alerting/dashboards

Type: integer

<a id="schema-audittree"></a>
#### AuditTree

One flow as a tree: the root, then everything that came from it, nested by cause

| Field | Type | Required | Description |
|---|---|---|---|
| `correlationId` | string (uuid) | yes | The flow's thread |
| `rootMissing` | boolean | yes | True when the root event may have been dropped from the index window and the earliest held event stands at the top instead |
| `eventCount` | integer (int32) | yes | Every event of the flow |
| `startedAt` | string (date-time) | yes | The earliest event's time |
| `endedAt` | string (date-time) | yes | The latest event's time |
| `root` | [AuditTreeNode](#schema-audittreenode) | yes | The top of the tree |

<a id="schema-audittreenode"></a>
#### AuditTreeNode

One event in a flow's tree, with the events it caused beneath it in time order

| Field | Type | Required | Description |
|---|---|---|---|
| `event` | [AuditEvent](#schema-auditevent) | yes | The event |
| `children` | [AuditTreeNode](#schema-audittreenode)[] | yes | The events hanging under it, earliest first |

<a id="schema-authcapabilitiesdto"></a>
#### AuthCapabilitiesDto

What this installation can offer at sign-in, from the caller's current origin

| Field | Type | Required | Description |
|---|---|---|---|
| `passkeysAvailable` | boolean | yes | Whether a passkey ceremony started from this origin would work |
| `passkeyStatus` | string | yes | The verdict name, e.g. "IpAddressHost" - see PasskeyOriginStatus |
| `passkeyMessageKey` | string | yes | Transloco key explaining the verdict to the user |
| `rpId` | string? | yes | The relying-party id passkeys are bound to, when one could be derived |
| `expectedOrigin` | string? | yes | The origin passkeys are bound to, when one could be derived |
| `passkeyPolicy` | string | yes | How hard this installation pushes passkeys - "Optional", "Encouraged" or "Required". Not sensitive: the sign-in screen reveals it by behaviour anyway, and the enrolment step needs it before anyone has a token in order to know whether declining is even offered. |

<a id="schema-authenticationextensionsclientoutputs"></a>
#### AuthenticationExtensionsClientOutputs

| Field | Type | Required | Description |
|---|---|---|---|
| `example.extension.bool` | boolean? |  |  |
| `appid` | boolean |  |  |
| `exts` | string[]? |  |  |
| `uvm` | integer (uint64)[][]? |  |  |
| `credProps` | null or [CredentialPropertiesOutput](#schema-credentialpropertiesoutput) |  |  |
| `prf` | null or [AuthenticationExtensionsPRFOutputs](#schema-authenticationextensionsprfoutputs) |  |  |
| `largeBlob` | null or [AuthenticationExtensionsLargeBlobOutputs](#schema-authenticationextensionslargebloboutputs) |  |  |
| `credProtect` | null or [CredentialProtectionPolicy](#schema-credentialprotectionpolicy) |  |  |

<a id="schema-authenticationextensionslargebloboutputs"></a>
#### AuthenticationExtensionsLargeBlobOutputs

| Field | Type | Required | Description |
|---|---|---|---|
| `supported` | boolean |  |  |
| `blob` | object (byte) |  |  |
| `written` | boolean |  |  |

<a id="schema-authenticationextensionsprfoutputs"></a>
#### AuthenticationExtensionsPRFOutputs

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean |  |  |
| `results` | [AuthenticationExtensionsPRFValues](#schema-authenticationextensionsprfvalues) |  |  |

<a id="schema-authenticationextensionsprfvalues"></a>
#### AuthenticationExtensionsPRFValues

| Field | Type | Required | Description |
|---|---|---|---|
| `first` | object (byte) | yes |  |
| `second` | object (byte) |  |  |

<a id="schema-authenticatorassertionrawresponse"></a>
#### AuthenticatorAssertionRawResponse

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string? | yes |  |
| `rawId` | object (byte) | yes |  |
| `response` | [AssertionResponse](#schema-assertionresponse) |  |  |
| `type` | null or [PublicKeyCredentialType](#schema-publickeycredentialtype) | yes |  |
| `extensions` | [AuthenticationExtensionsClientOutputs](#schema-authenticationextensionsclientoutputs) |  |  |
| `clientExtensionResults` | [AuthenticationExtensionsClientOutputs](#schema-authenticationextensionsclientoutputs) | yes |  |

<a id="schema-authenticatorattestationrawresponse"></a>
#### AuthenticatorAttestationRawResponse

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string? | yes |  |
| `rawId` | object (byte) | yes |  |
| `type` | null or [PublicKeyCredentialType](#schema-publickeycredentialtype) | yes |  |
| `response` | [AttestationResponse](#schema-attestationresponse) | yes |  |
| `extensions` | [AuthenticationExtensionsClientOutputs](#schema-authenticationextensionsclientoutputs) |  |  |
| `clientExtensionResults` | [AuthenticationExtensionsClientOutputs](#schema-authenticationextensionsclientoutputs) | yes |  |

<a id="schema-authenticatortransport"></a>
#### AuthenticatorTransport

Type: object enum: `usb`, `nfc`, `ble`, `smart-card`, `hybrid`, `internal`

<a id="schema-authresponse"></a>
#### AuthResponse

The issued auth token and when it expires, returned to a successful sign-in

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | Signed JWT bearer token to send on subsequent requests |
| `expiration` | string (date-time) | yes | UTC instant at which the token stops being valid |

<a id="schema-brandingdto"></a>
#### BrandingDto

The operator's public-facing branding for the pre-auth screens

| Field | Type | Required | Description |
|---|---|---|---|
| `productName` | string | yes | Product name to display. Blank = the client falls back to "AMP" |
| `loginWelcomeText` | string | yes | Free-text welcome shown on the login screen |
| `logoUrl` | string | yes | Logo URL. Blank = the client falls back to its bundled emblem |
| `defaultTheme` | string | yes | Frontend theme id (e.g. "high-contrast") |

<a id="schema-busconnectioninfo"></a>
#### BusConnectionInfo

Everything another machine needs to join this install's message bus

| Field | Type | Required | Description |
|---|---|---|---|
| `servers` | string[] | yes | Bus endpoint URLs the joining machine should connect to |
| `prefix` | string | yes | Subject/namespace prefix scoping this install on the bus |
| `hostCandidates` | string[] | yes | Reachable host addresses to try, best-first |
| `caCert` | string |  | PEM CA certificate for a TLS bus; empty when TLS is not used Default `""`. |
| `joinBlob` | string |  | A single encoded `.ampbus` descriptor bundling all of the above for one-paste joining Default `""`. |

<a id="schema-busexpansion"></a>
#### BusExpansion

The answer to an expand: the join information to share, and what became of the bus port in the firewall

| Field | Type | Required | Description |
|---|---|---|---|
| `joinInfo` | [BusConnectionInfo](#schema-busconnectioninfo) | yes | Connection details other machines use to join |
| `firewall` | [BusPortFirewall](#schema-busportfirewall) | yes | What the firewall sweep the expand ran did with the bus port's rule |

<a id="schema-businfo"></a>
#### BusInfo

State of this install's message bus, for the "add a machine" flow:     * Managed=false: an external bus - AMP can't reconfigure it; Servers are             the configured endpoints. * Managed=true, IsPublic=false: AMP-managed single-machine - can be expanded. * Managed=true, IsPublic=true: already reachable; JoinInfo is ready to share.

| Field | Type | Required | Description |
|---|---|---|---|
| `managed` | boolean | yes | True when AMP itself provisions and supervises the bus (versus an external one) |
| `isPublic` | boolean | yes | True when the bus is already reachable by other machines |
| `joinInfo` | null or [BusConnectionInfo](#schema-busconnectioninfo) | yes |  |
| `servers` | string[] | yes | Configured endpoint URLs, populated for an external (unmanaged) bus |

<a id="schema-busportfirewall"></a>
#### BusPortFirewall

The bus port's firewall rule as the expand left it

| Field | Type | Required | Description |
|---|---|---|---|
| `outcome` | [BusPortFirewallOutcome](#schema-busportfirewalloutcome) | yes | Whether the rule now opens the port, and if not, why not |
| `port` | integer (int32) | yes | The bus port |
| `generation` | integer? (int64) | yes | The published state generation that carries the open rule; null unless the outcome is Open |
| `agentRunning` | boolean | yes | Whether the system agent, which applies the published state, was seen running. When it is not, nothing opens the port and it has to be opened by hand |

<a id="schema-busportfirewalloutcome"></a>
#### BusPortFirewallOutcome

Type: object enum: `Open`, `NotManaged`, `Pending`, `WriteFailed`

<a id="schema-busservicecondition"></a>
#### BusServiceCondition

What the panel can tell about the bus service from the record it leaves

Type: object enum: `Absent`, `Stale`, `Stopped`, `Idle`, `Blocked`, `Running`, `Backoff`, `CrashLoop`

<a id="schema-busservicestatus"></a>
#### BusServiceStatus

The bus service as the panel sees it, for the wizard, the Support page and the diagnostics bundle

| Field | Type | Required | Description |
|---|---|---|---|
| `condition` | [BusServiceCondition](#schema-busservicecondition) | yes | What the record says, or that there is none |
| `pid` | integer (int32) | yes | nats-server's process id while it runs |
| `startedAtUtc` | string? (date-time) | yes | When the running nats-server was launched |
| `binaryVersion` | string? | yes | The release the running or last server was launched from |
| `configHash` | string? | yes | SHA-256 of the config the running or last server was launched from |
| `configCurrent` | boolean | yes | Whether that hash matches the config file as it is now; false while a restart the panel asked for is pending |
| `fileStoreBytes` | integer? (int64) | yes | The file-store cap the service measured at the last launch |
| `lastExitReason` | string? | yes | Why the last server is gone, or what the service waits for |
| `updatedAtUtc` | string? (date-time) | yes | When the record was written |
| `serviceVersion` | string? | yes | The amp3-host build that wrote it |
| `serviceUp` | boolean |  | A service that is up, whatever it is doing: it acts on a config the panel writes |

<a id="schema-candidatesize"></a>
#### CandidateSize

How big a candidate is, and whether we managed to see all of it

| Field | Type | Required | Description |
|---|---|---|---|
| `bytes` | integer (int64) | yes | Total size of the files measured |
| `files` | integer (int32) | yes | How many files were counted |
| `truncated` | boolean | yes | A cap stopped the measurement, so long CandidateSize.Bytes is a floor rather than a total |
| `unreadableDirectories` | string[] | yes | Directories that could not be read, so their contents are not counted |
| `links` | integer (int32) |  | Symbolic links found and not followed. An import recreates the ones pointing inside the server and leaves the rest behind, so a non-zero count is worth telling the user before they import. Zero when the privileged agent did the measuring, because it does not count them. Default `0`. |
| `isUnderstated` | boolean |  | Nothing measured here can be trusted as a total |

<a id="schema-candidatesource"></a>
#### CandidateSource

Where a candidate was found, which becomes the source badge on its row. Recorded at scan time rather than inferred later, because the same directory can be reachable several ways and the honest answer is "this is how we got here".

Type: integer

<a id="schema-capabilitydto"></a>
#### CapabilityDto

One capability from the permission catalogue

| Field | Type | Required | Description |
|---|---|---|---|
| `node` | string | yes | The capability's permission node string |
| `scope` | string | yes | The scope root the node applies at |
| `dangerous` | boolean | yes | Whether the capability is flagged as dangerous |
| `pathScopable` | boolean | yes | Whether the capability can be narrowed to a path |
| `byNameOnly` | boolean | yes | Whether the capability is held only through a grant that names it: the superuser grant and every wildcard over it leave it out, so the role editor shows it as not held under them |

<a id="schema-catalogdto"></a>
#### CatalogDto

The trigger and task catalogs plus common emitted signals that drive the schedule editor

| Field | Type | Required | Description |
|---|---|---|---|
| `triggers` | [TriggerDescriptorDto](#schema-triggerdescriptordto)[] | yes | Available trigger descriptors |
| `tasks` | [TaskDescriptorDto](#schema-taskdescriptordto)[] | yes | Available task descriptors |
| `commonEmits` | [EmittedValueDto](#schema-emittedvaluedto)[] | yes | Signal names commonly emitted by triggers, offered for condition editing |

<a id="schema-changeexpiredpasswordrequest"></a>
#### ChangeExpiredPasswordRequest

Credentials for the forced/expired password-change flow

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | yes | The account's username |
| `currentPassword` | string | yes | The current password, used to prove identity since the user has no session |
| `newPassword` | string | yes | The replacement password to set |

<a id="schema-changepasswordrequest"></a>
#### ChangePasswordRequest

Request to change the caller's own password

| Field | Type | Required | Description |
|---|---|---|---|
| `currentPassword` | string | yes | The caller's existing password, verified before the change |
| `newPassword` | string | yes | The replacement password, subject to the configured strength policy |

<a id="schema-checkrequest"></a>
#### CheckRequest

| Field | Type | Required | Description |
|---|---|---|---|
| `hostName` | string | yes | The domain name to look up |

<a id="schema-clonecontent"></a>
#### CloneContent

What a shard clone copies. CloneContent.Configuration is the shard's SETTINGS - the config              values, container mode, and start-on-boot held in KV; CloneContent.Data is the on-disk              working directory (installed game files, world/save data). They compose, so CloneContent.Both              is a full duplicate.               Config-only clones need no downtime (pure KV). Any clone that includes CloneContent.Data requires the SOURCE shard to be stopped, so its files are at rest while they are copied.

Type: integer

<a id="schema-cloneshardbody"></a>
#### CloneShardBody

Request to clone a shard

| Field | Type | Required | Description |
|---|---|---|---|
| `newName` | string | yes | Display name for the new shard |
| `content` | [CloneContent](#schema-clonecontent) | yes | What to duplicate: configuration, on-disk data, or both. `None` is rejected |
| `targetSpireId` | string? (uuid) | yes | Spire to place the clone on; null clones in place on the source's spire |

<a id="schema-compatibilitymodes"></a>
#### CompatibilityModes

Console-buffering and platform quirks an application needs worked around (combinable flags)

Type: integer

<a id="schema-completerequest"></a>
#### CompleteRequest

The assertion coming back from the browser

| Field | Type | Required | Description |
|---|---|---|---|
| `flowId` | string (uuid) | yes | The handle returned by the begin call |
| `response` | [AuthenticatorAssertionRawResponse](#schema-authenticatorassertionrawresponse) | yes | The raw WebAuthn assertion response |
| `rememberMe` | boolean |  | Issue a long-lived session that survives closing the browser Default `false`. |

<a id="schema-condition"></a>
#### Condition

One condition row: a {@Var} template on the left, an operator, and a comparand template on the right (unused for IsEmpty/IsNotEmpty). String operators compare case-insensitively; numeric operators parse both sides invariant-culture and yield an explicit condition error (journaled, treated as non-match) when an operand isn't numeric.

| Field | Type | Required | Description |
|---|---|---|---|
| `value` | string |  |  |
| `operator` | [ConditionOperator](#schema-conditionoperator) |  |  |
| `comparand` | string |  |  |

<a id="schema-conditionmatch"></a>
#### ConditionMatch

Type: object enum: `All`, `Any`

<a id="schema-conditionoperator"></a>
#### ConditionOperator

Type: object enum: `Equals`, `NotEquals`, `Contains`, `NotContains`, `StartsWith`, `EndsWith`, `MatchesRegex`, `GreaterThan`, `GreaterOrEqual`, `LessThan`, `LessOrEqual`, `IsEmpty`, `IsNotEmpty`

<a id="schema-configfilemapping"></a>
#### ConfigFileMapping

Parsed from {name}metaconfig.json - describes how to read/write each config file that backs the settings manifest entries. Mirrors AMP 2's `MetaConfigFile`.

| Field | Type | Required | Description |
|---|---|---|---|
| `configFile` | string |  | Relative path within the app's base directory |
| `configFileTemplate` | string |  | Optional template file copied/merged when generating string ConfigFileMapping.ConfigFile |
| `configFormat` | string |  | Format string used when writing a value: {0}=key, {1}=value |
| `configFormatRegex` | string |  | Regex used when reading - named groups 'key' and 'value' |
| `configHeaderFormat` | string |  | Format for a section heading line: {0}=heading |
| `configHeaderRegex` | string |  | Regex used when reading a section heading line - named group 'header' |
| `configType` | string |  | "auto" (by extension), "ini", "kvp", "json", "jsonkvp", "xml", "template" |
| `configEncoding` | string |  | Text encoding: "utf8"/"utf8-nobom" (default), "utf16-le-nobom", or a named encoding |
| `autoMap` | boolean |  | When true, AMP merges values into an existing/template file in place (import-style) |
| `importable` | boolean |  | Whether existing values in this file may be imported back into settings |
| `subsections` | [MetaConfigSubsection](#schema-metaconfigsubsection)[] |  | The sections written when generating the file from scratch |

<a id="schema-configimportrequest"></a>
#### ConfigImportRequest

A request to read an AMP-managed config file back into the shard's settings

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | The config file, relative to the shard's file-manager root |
| `content` | string? |  | The edited file to import and then save. Omit it to import the file as it already stands on disk, which is how a change made outside AMP (over SFTP, or by the app itself) is picked up |

<a id="schema-configimportresponse"></a>
#### ConfigImportResponse

What an import changed. Only names are returned, never values - the file can hold secrets

| Field | Type | Required | Description |
|---|---|---|---|
| `applied` | string[] | yes | Settings whose value the file changed |
| `excluded` | string[] | yes | Settings present in the file that AMP holds authority over and did not read back (ports and IP bindings, chiefly). AMP writes its own value over these again on the next start |
| `missing` | string[] | yes | Settings AMP manages whose key is no longer in the file. AMP only rewrites keys that are already there, so these have stopped reaching the app |

<a id="schema-connectedprofile"></a>
#### ConnectedProfile

Somebody connected to a server right now, with what is known about them beyond the server's own list

| Field | Type | Required | Description |
|---|---|---|---|
| `user` | [ConnectedUser](#schema-connecteduser) | yes | Their connection, as the server's user list has it |
| `steam` | null or [SteamProfile](#schema-steamprofile) | yes |  |
| `minecraft` | null or [MinecraftProfile](#schema-minecraftprofile) | yes |  |
| `profileState` | string | yes | As string ProfilesAnswer.State, or `none` when they are not known by an id that can be looked up |
| `analyticsKey` | string? | yes | The key their analytics are filed under, when the caller may see who users are on this server and analytics has seen them; the way to their analytics card |
| `addressKind` | null or [AddressKind](#schema-addresskind) |  |  |
| `othersAtAddress` | integer (int32) |  | How many other users have connected from their address, when the caller holds `analytics.addresses` here and the address is one where that means something |
| `userKey` | string? |  | Their key, for a reader allowed to see addresses, so the users at their address can leave them out |
| `recoveredAddress` | string? |  | Where they really connected from, when the server saw a relay's address and the connection could be followed back through AMP's relays with certainty; the kind and the count are then this one's |

<a id="schema-connecteduser"></a>
#### ConnectedUser

A user currently connected to a shard's application (game server, voice server, etc.)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Display name reported by the application |
| `id` | string | yes | Stable application-side user identifier (e.g. UUID, SteamID), when the app provides one |
| `sessionId` | string | yes | Identifier for this particular connection, distinguishing repeat connects by the same user |
| `endpoint` | string? | yes | Remote address the user connected from, when known |
| `joinedAt` | string (date-time) | yes | When this user connected |
| `idKind` | string |  | What kind of id Id is, which decides where it may be compared (see UserIdKinds) Default `"name"`. |

<a id="schema-consolehistorypage"></a>
#### ConsoleHistoryPage

A page of console history, oldest-first, plus whether older lines remain to fetch

| Field | Type | Required | Description |
|---|---|---|---|
| `lines` | [ConsoleLineDto](#schema-consolelinedto)[] | yes | The console lines in this page, in ascending sequence order |
| `hasMore` | boolean | yes | True when older history exists before this page's first line |

<a id="schema-consoleinputbody"></a>
#### ConsoleInputBody

Request body carrying a single console command to send to a shard

| Field | Type | Required | Description |
|---|---|---|---|
| `command` | string | yes | The command string to write to the shard's stdin |
| `promptReply` | boolean |  | True when the command answers an interactive prompt (a Steam password or Guard code), so it is never audited in the clear Default `false`. |

<a id="schema-consolelinedto"></a>
#### ConsoleLineDto

One console history entry, ready for the UI. Two kinds:       • `line`   - ordinary console output from a stream.       • `marker` - a lifecycle event (start/stop/update) surfaced inline         so the console visually separates runs. int? ConsoleLineDto.State carries         the ApplicationState for state markers; bool? ConsoleLineDto.Success the         outcome for update-complete markers (with State null).     long ConsoleLineDto.Seq is a per-shard monotonic sequence assigned by the server-side history buffer - clients use it to merge the REST history snapshot with the live SignalR feed without duplicates.

| Field | Type | Required | Description |
|---|---|---|---|
| `streamId` | string | yes | Identifier of the source stream this line came from |
| `text` | string | yes | The line text (English fallback for localised markers) |
| `timestamp` | string (date-time) | yes | When the line was produced |
| `seq` | integer (int64) |  | Per-shard monotonic sequence used to merge history with the live feed Default `0`. |
| `kind` | string |  | `line` for ordinary output, `marker` for an inline lifecycle event, `notice` for a notice, `error` for a failure reported on the shard, or `info` for an information line whose Text is its title Default `"line"`. |
| `state` | integer (int32) |  | ApplicationState carried by a state marker; null otherwise |
| `success` | boolean? |  | Outcome carried by an update-complete marker; null otherwise |
| `code` | string? |  | i18n key for an AMP-authored marker message; null for app output or uncatalogued lines |
| `params` | map<string, string> |  | Parameters for the localised Code message; null when there are none |
| `level` | string? |  | Structured log level parsed from the console format regex; null when unmatched or unconfigured |
| `origin` | string? |  | Structured origin/source field parsed from the console format regex; null when unmatched |

<a id="schema-consolespec"></a>
#### ConsoleSpec

Parsed from the Console.* keys in a .kvp file. Describes how AMP reads and interprets the application's console output.

| Field | Type | Required | Description |
|---|---|---|---|
| `filterMatchRegex` | string |  | Regex to strip ANSI escape codes / noise from raw output |
| `filterMatchReplacement` | string |  | Replacement text substituted for each string ConsoleSpec.FilterMatchRegex match |
| `throwawayMessageRegex` | string |  | Lines matching this are discarded entirely before storage |
| `appReadyRegex` | string |  | When this matches, the app is considered Ready |
| `serverAuthURLPromptRegex` | string |  | Server authentication URL prompt (e.g. Hytale's OAuth device flow): when this matches a console line, the shard enters AwaitingUserAction and surfaces the URL the operator must visit. If the regex has a `url` named group, that's the URL; otherwise the whole match is used. |
| `serverAuthAckRegex` | string |  | Acknowledgement that server authentication completed - clears the prompt |
| `userJoinRegex` | string |  | Matches a console line announcing a user connecting. Named groups: `username` (required), `userid`, `sessionid`, and `endpoint` (or `ip`) for the address. |
| `userIdKind` | string |  | What kind of id the `userid` group captures (see UserIdKinds), which decides whether one person is recognised across servers. Empty means undeclared: a SteamID is still recognised by its shape, and anything else is only compared between shards of this template. Declare it when the id is global, and make it specific (`minecraft-uuid`). |
| `userLeaveRegex` | string |  | Matches a console line announcing a user disconnecting |
| `userChatRegex` | string |  | Matches a chat message line; typically captures the user and message text |
| `preConnectRegex` | string |  | For games that announce a connecting player across multiple lines. Fires before ConnectIPRegex; must capture an `id` group that correlates with either the `id` group in ConnectIPRegex or the `username`/`userid` in UserJoinRegex. An optional `userid` group supplies the user's id when the join line has none, matched up by the name in `id`: Minecraft's "UUID of player Steve is ..." line. |
| `connectIPRegex` | string |  | Captures the joining player's IP when it does not appear on the same line as UserJoinRegex.  Must include an `ip` capture group. Optional `id` group ties this line to a specific player (or PreConnect entry); without it, the IP is assigned to the most-recently announced pre-connect entry. |
| `updateAvailableRegex` | string |  | When this matches, the app is signalling that an update is available |
| `metricsRegex` | string |  | Matches console lines carrying performance metrics to be parsed and recorded |
| `downloadProgressRegex` | string |  | Matches download-progress output lines during an update (e.g. progress bars from a custom downloader). Named capture groups: `percent` (required), `progressValue` + `progressUnits` and `totalValue` + `totalUnits` (optional, used to compute transfer speed). When this matches, the line is consumed as a structured progress event and not published to the console stream (keeps the transcript clean). |
| `startupProgressRegex` | string |  | Matches console lines reporting how far through STARTING the application is, for apps whose              startup is slow but measurable (Minecraft's "Preparing spawn area: 45%", world generation, big              modded packs). Named capture groups: `PercentageComplete` (required, the percentage), and              `stage` (optional, what it is doing - shown beside the bar).               The line is NOT consumed: unlike a download progress bar, which redraws hundreds of times and would drown the transcript, a startup progress line is part of the server's own startup log and users expect to see it. Progress is reported live only and never persisted - it is meaningless the moment the app is up, and writing every tick to KV would be pure amplification. |
| `logRegex` | string |  | Regex that matches structured log lines emitted by the application. Standard named capture groups: `origin`, `level`, `message`. Any group may be omitted if the log format does not provide it. The input timestamp, if present in the raw line, should be consumed by the regex but not captured - AMP always records its own UTC receive time. |
| `suppressLogAtStart` | boolean |  | Suppress log output until this regex fires (reduces startup noise) |
| `activateLogRegex` | string |  | When bool ConsoleSpec.SuppressLogAtStart is set, this regex re-enables log output once it fires |
| `commandTriggers` | map<string, string> |  | Automation rules (AMP 2 `App.CommandTriggers`): a console-line REGEX → a COMMAND to send back to the app when it matches. The command is template-expanded (`{{Setting}}`/`{{$PortRef}}`) and then has the match's capture groups substituted - `{{0}}` = whole match, `{{1}}`… = numbered groups, `{{name}}` = named groups. A command may carry embedded newlines to send several commands. (Hytale uses these to drive its `/auth login device` flow.) |

<a id="schema-containersupport"></a>
#### ContainerSupport

The application's container (Docker) support and preference, per platform (combinable flags)

Type: integer

<a id="schema-contentbody"></a>
#### ContentBody

A file's text content, used for both reads and writes

| Field | Type | Required | Description |
|---|---|---|---|
| `content` | string | yes | The full UTF-8 text of the file. On write, a null value is treated as empty |

<a id="schema-correlationid"></a>
#### CorrelationId

| Field | Type | Required | Description |
|---|---|---|---|
| `value` | string (uuid) |  |  |

<a id="schema-countryusage"></a>
#### CountryUsage

Where a period's sessions came from

| Field | Type | Required | Description |
|---|---|---|---|
| `country` | string | yes |  |
| `sessions` | integer (int32) | yes |  |
| `sessionsPercent` | number/string (double) | yes |  |
| `minutes` | number/string (double) | yes |  |
| `minutesPercent` | number/string (double) | yes |  |
| `users` | integer (int32) | yes | How many distinct people that country sent, or null when the answer came from precomputed totals, which do not keep enough per country to say. Filtering to a shard or a country answers it exactly. |

<a id="schema-createcredentialbody"></a>
#### CreateCredentialBody

A credential to keep

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | [SharedCredentialKind](#schema-sharedcredentialkind) | yes | What it signs in to; SteamCmd today |
| `title` | string | yes | What everybody who may use it sees; blank takes the kind's default, such as "SteamCMD login" |
| `username` | string | yes | The account's username for a kind that has one (SteamCmd, Smtp); shown to the creator alone afterwards. Empty for a kind whose secret stands alone |
| `secret` | string | yes | The password, webhook URL or token; sealed at rest and never returned |
| `visibility` | [CredentialVisibility](#schema-credentialvisibility) |  | Private (the creator alone, the default) or Public (everybody in the tenant may use it) |

<a id="schema-createdenrollment"></a>
#### CreatedEnrollment

| Field | Type | Required | Description |
|---|---|---|---|
| `enrollment` | [EnrollmentDto](#schema-enrollmentdto) | yes | The stored record |
| `joinBlob` | string | yes | The `.ampbus` join file. Returned ONCE, on creation, and never recoverable: it contains a credential, and we keep only a hash of its secret. |

<a id="schema-createdinvite"></a>
#### CreatedInvite

Returned ONCE on creation/re-arm - the raw code is never retrievable again

| Field | Type | Required | Description |
|---|---|---|---|
| `invite` | [InviteDto](#schema-invitedto) | yes | The invite as stored |
| `token` | string | yes | The one-time secret code. Only a hash is kept, so this is the sole chance to capture it |

<a id="schema-createenrollmentbody"></a>
#### CreateEnrollmentBody

| Field | Type | Required | Description |
|---|---|---|---|
| `label` | string | yes | A name for the machine, shown as the spire's display name once it joins |
| `expiresInHours` | integer (int32) | yes | How long the join file stays usable (1-720, default 72) |
| `hosts` | string[]? |  | The addresses to put in the join file. The joining machine tries each and uses the first it can reach, so listing a LAN address AND a public one covers both cases without the operator having to know which network the machine is on. Empty means "whatever this bus reports", which is the LAN address it is bound to. |
| `entryNode` | boolean |  | True to add the machine as a public entry node instead of an ordinary spire: a relay and panel front door that hosts no shards. Changes what the file contains and how it is reached, so it is chosen here rather than by the machine. See docs/public-entry-nodes.md. Default `false`. |
| `publicHost` | string? |  | Required for an entry node, ignored otherwise: the public address that machine is reachable at. This end has to know it, because this end is the one that dials - so there is no arrangement in which the operator does not supply it. |

<a id="schema-creategatewaybody"></a>
#### CreateGatewayBody

Request to route a whole shard through a front spire gateway

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) | yes | Server to route wholesale through the gateway |
| `frontSpireId` | string (uuid) | yes | Publicly routable spire that fronts the server's traffic |

<a id="schema-createinvitebody"></a>
#### CreateInviteBody

Fields for creating an invite

| Field | Type | Required | Description |
|---|---|---|---|
| `userName` | string | yes | The username the invite will create. Required and must not already exist |
| `displayName` | string? | yes | Optional friendly name to suggest to the invitee |
| `roleIds` | string[]? | yes | Roles to assign the new account. Invalid or self-role ids are dropped, and at least one must remain |
| `expiresInHours` | integer (int32) | yes | Optional lifetime in hours. Clamped between 1 and 720 (30 days); defaults to 72 |

<a id="schema-createpublicationbody"></a>
#### CreatePublicationBody

Request to publish a single shard port on a front spire. There is deliberately no protocol field: a relay carries what the target speaks, so the protocol is read from the shard's own port and can never be set to something the far end isn't listening on.

| Field | Type | Required | Description |
|---|---|---|---|
| `frontSpireId` | string (uuid) | yes | Publicly routable spire that will listen on the public port |
| `publicPort` | integer (int32) | yes | Public port to bind on the front spire (1-65535) |
| `shardId` | string (uuid) | yes | Server whose port is being published |
| `portRef` | string | yes | Named port on the server to expose |
| `frontPublicHost` | string? |  | Adopted as the front spire's PublicHost when it has none, so publishing to a public spire can establish its dial host inline rather than via a detour through Spire settings. Ignored once that setting exists: it is the operator's, and every tunnel on that spire already dials it. |

<a id="schema-createrolebody"></a>
#### CreateRoleBody

Fields for creating a role

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Display name. Required and unique |
| `description` | string? | yes | Optional human description |
| `permissions` | string[]? | yes | Optional permission-node grants. You may only grant permissions you yourself hold |

<a id="schema-createshardbody"></a>
#### CreateShardBody

Request body for creating a shard from a template

| Field | Type | Required | Description |
|---|---|---|---|
| `templateSlug` | string | yes | Slug of the app template to instantiate. Required |
| `displayName` | string | yes | Human-readable name for the new shard |
| `description` | string | yes | Free-text description for the new shard |
| `spireId` | string? (uuid) | yes | Target spire to create the shard on. When null, the tenant's default local spire is used |
| `startNow` | boolean | yes | When true, the shard's first update runs straight away and the shard starts once that update has finished successfully; a failed or cancelled update starts nothing |
| `startOnBoot` | boolean | yes | When true, the shard is configured to start automatically when its spire boots |
| `credentialId` | string? (uuid) |  | The shared credential the new shard's updates sign in with, for a game that can only be downloaded by an account that owns it: one the caller may use (their own or a Public one), pointed at before the first update runs |

<a id="schema-createshardresult"></a>
#### CreateShardResult

Result of a successful shard creation

| Field | Type | Required | Description |
|---|---|---|---|
| `shard` | [DeployedShard](#schema-deployedshard) |  | The newly created shard's deployment record |
| `infraSettings` | [ShardInfraSettings](#schema-shardinfrasettings) |  | Infrastructure settings (container mode, resource limits) for the new shard |
| `appConfig` | [ShardConfig](#schema-shardconfig) |  | Application configuration seeded for the new shard |
| `ports` | [ShardPortAssignments](#schema-shardportassignments) |  | Ports assigned to the new shard |
| `portOutcomes` | [PortOutcome](#schema-portoutcome)[] |  | How each port came to be what it is: kept as requested, or moved and why. Only interesting when IReadOnlyDictionary<string, int>? CreateShardRequest.PreferredPorts was supplied, which is the import case; a fresh deployment preferred nothing, so every entry reads as "nothing discovered". |
| `overLicenceLimit` | boolean |  | The new shard took the installation past its licence's soft limit. It was still created (the limit warns rather than refuses until the hard stop just above it), so the user should be told rather than stopped. |

<a id="schema-createuserbody"></a>
#### CreateUserBody

Fields for creating a user directly (username plus password, the operator path)

| Field | Type | Required | Description |
|---|---|---|---|
| `userName` | string | yes | Login name. Required and unique |
| `password` | string | yes | Initial password. Must satisfy the configured password policy |
| `displayName` | string? | yes | Optional friendly name |
| `email` | string? | yes | Optional contact email |
| `roleIds` | string[]? | yes | Optional named roles to assign. Ids that don't exist or are hidden self-roles are dropped |
| `passwordExpires` | boolean? | yes | Optional. Whether the password is subject to age-based expiry. Defaults to false |
| `requirePasswordChange` | boolean? | yes | Optional. Whether the user must change their password on first sign-in. Defaults to false |

<a id="schema-credentialpropertiesoutput"></a>
#### CredentialPropertiesOutput

| Field | Type | Required | Description |
|---|---|---|---|
| `rk` | boolean |  |  |
| `authenticatorDisplayName` | string? |  |  |

<a id="schema-credentialprotectionpolicy"></a>
#### CredentialProtectionPolicy

Type: object enum: `userVerificationOptional`, `userVerificationOptionalWithCredentialIDList`, `userVerificationRequired`, `None`

<a id="schema-credentialvisibility"></a>
#### CredentialVisibility

Who may use a shared credential besides its creator

Type: object enum: `Private`, `Public`

<a id="schema-customportdef"></a>
#### CustomPortDef

A user-registered port's descriptive metadata (its number lives in Assignments)

| Field | Type | Required | Description |
|---|---|---|---|
| `ref` | string |  |  |
| `name` | string |  |  |
| `protocol` | [PortProtocol](#schema-portprotocol) |  |  |
| `createdAt` | string (date-time) |  |  |
| `createdByUserId` | string |  |  |

<a id="schema-databasesetuprequest"></a>
#### DatabaseSetupRequest

Connection details for the database (NATS) setup step

| Field | Type | Required | Description |
|---|---|---|---|
| `servers` | string[] | yes | One or more server URLs (an "advanced" manual field) |
| `prefix` | string? | yes | Subject/bucket prefix that scopes this install (an "advanced" manual field) |
| `credentials` | string? | yes | Optional NATS credentials (an "advanced" manual field) |
| `caCert` | string? |  | Optional CA certificate (PEM) for a self-signed `tls://` server (an "advanced" manual field) |
| `blob` | string? |  | A single `.ampbus` descriptor. When present it is decoded server-side and supersedes the manual fields, so there is one apply path and the manual fields are just the already-decoded case. |

<a id="schema-datastoreusage"></a>
#### DatastoreUsage

Scanned usage of a single datastore

| Field | Type | Required | Description |
|---|---|---|---|
| `datastoreId` | string (uuid) |  | Identifies which SpireDatastore this usage was scanned from |
| `totalUsedMb` | integer (int64) |  | Sum of all first-level directories (known shards + orphans), MB |
| `driveFreeMb` | integer (int64) |  | Free space on the drive hosting the datastore path, MB |
| `driveTotalMb` | integer (int64) |  | Total capacity of that drive, MB |
| `shards` | [ShardDiskUsage](#schema-sharddiskusage)[] |  | Per-directory breakdown: one entry per first-level directory (active shards, soft-deleted shards, and orphans) |

<a id="schema-datastoreusagereport"></a>
#### DatastoreUsageReport

Periodic disk usage scan result for all of a spire's datastores, written by the spire's reporter (on int SpireConfig.UsageScanIntervalMinutes + once at boot) so reads are cheap.  KV key: `amp.tn.{tenant}.sp.{spireId}.usage`

| Field | Type | Required | Description |
|---|---|---|---|
| `spireId` | string (uuid) |  | The spire whose datastores were scanned |
| `scannedAt` | string (date-time) |  | When the scan that produced this report ran |
| `datastores` | [DatastoreUsage](#schema-datastoreusage)[] |  | One usage entry per datastore the spire offers |

<a id="schema-deployedshard"></a>
#### DeployedShard

A concrete deployment of an AppSpec - an application that can be started, stopped, and managed on a specific SpireDefinition. This is the AMP3 equivalent of what AMP2 called an "Instance".  Durable, rarely-written record. KV key: `amp.tn.{tenant}.sr.{shardId}`

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `displayName` | string |  | User-chosen display name, e.g. "My Minecraft Server". Mutable - the friendly label shown in the UI. |
| `description` | string |  | Optional human-readable description of what this shard is for. Shown on the shard detail page and in listings. |
| `name` | string |  | Immutable, unique-per-tenant machine name. Used in log paths, NATS subject tokens, and anywhere a stable identifier is needed. Set once at creation, never changed. Must be NATS-subject-safe (lowercase alphanumeric + hyphen/underscore only). |
| `templateSlug` | string |  | The template this shard was created from, e.g. `"minecraft"`. Links to an AppSpec in the template catalogue. |
| `displayImageSource` | string |  | The template's Meta.DisplayImageSource ("steam:appid" / "url:…" / "internal:name"), copied at creation and refreshed on update. Denormalized so listings render artwork from the shard record alone - never gated on the (potentially slow) template-catalogue load. |
| `spireId` | string (uuid) |  | The spire (machine) this shard is assigned to run on |
| `datastoreId` | string (uuid) |  | The spire datastore this shard's files live in (see SpireDatastore). Empty = use the spire's default datastore. The agent's working directory is `{datastore.Path}/{WorkingDirName}`, resolved by the backend at launch. |
| `workingDirName` | string |  | Leaf directory name for this shard's files inside its datastore, e.g.             `"valheim-3f9a2b1c"`. Set once at creation and never changed. Unique             per shard (it embeds the shard id) so deleting a shard and recreating one             with the same display name can NEVER reuse the old data directory - a             privacy/security guarantee, and it also stops two same-named live shards             from fighting over one directory.             Empty on shards created before this field existed → the launcher falls back to string DeployedShard.Name for them. |
| `createdByUserId` | string |  |  |
| `createdAt` | string (date-time) |  |  |
| `installedAt` | string? (date-time) |  | When the application files were last successfully installed/updated |
| `installedVersion` | string? |  | Version string as reported after the last successful update |
| `startAfterUpdateTaskId` | string? (uuid) |  | The update task whose success starts this shard: set when a shard is created with "Start now", which runs its first update straight away, and cleared when that update ends. Kept on the record so a panel restart while the update runs still starts the shard when it finishes. Null when no start is waiting. |
| `tags` | string[] |  | User-defined labels for grouping and filtering in the UI |
| `suspended` | boolean |  | When true, the shard will not be started even if commanded to. Used for administrative holds - maintenance, billing suspension, etc. |
| `diskWarningAcked` | boolean |  | The operator has acknowledged this shard's disk-usage warning, so the dismissible notice stays hidden. Cleared automatically by the usage scan once the shard drops back under the warning threshold, so the next approach to the limit is announced afresh rather than silently. Only the first, advisory level can be acknowledged at all - see bool DiskLimitPolicy.IsDismissible(DiskLimitLevel level). |
| `deleted` | boolean |  | Soft-delete flag. Deleted shards are hidden from normal listings but their data is retained so the action can be undone immediately. Hard deletion is a separate, scheduled, operator-level operation. |
| `deletedAt` | string? (date-time) |  | When the shard was soft-deleted, if applicable |

<a id="schema-diagnosticsdto"></a>
#### DiagnosticsDto

A one-shot snapshot of this machine and process for the Updates and Support panel

| Field | Type | Required | Description |
|---|---|---|---|
| `ampVersion` | string | yes | Three-part AMP assembly version (e.g. "3.1.0") |
| `codename` | string | yes | The release codename this build ships under (e.g. "Neptune") |
| `buildCommit` | string | yes | Short git commit hash this build was produced from, if available |
| `configuration` | string | yes | Hosting environment name (Development, Production, etc.) |
| `operatingSystem` | string | yes | Human-readable OS name of the host |
| `architecture` | string | yes | CPU architecture in AMP's vocabulary (x86_64, aarch64, ...) |
| `hardwarePlatform` | string | yes | Detected hardware or hypervisor platform name |
| `cpuModel` | string | yes | CPU model string |
| `cpuTopology` | string | yes | Sockets/cores/threads summary, formatted as "1S/8C/2T" |
| `hostname` | string | yes | Machine host name |
| `totalRamMb` | integer (int64) | yes | Installed physical RAM in megabytes |
| `virtualization` | string | yes | Detected virtualization type, or "None" on bare metal |
| `natsStatus` | string | yes | "Connected" or "Disconnected" from an active bus probe |
| `uptimeSeconds` | integer (int64) | yes | Seconds since this process started |
| `auditOrphanWrites` | integer (int64) | yes | Audit events this process wrote outside every action since it started: each became a root of its own, and each marks a boundary that is missing |
| `auditRelaunchCarriedWrites` | integer (int64) | yes | Audit events this process wrote outside every action while continuing the flow a self-update's relaunch left it |
| `ipv6` | string | yes | Whether this machine's IPv6 reaches the internet, as its probe last judged it: Unknown, Unavailable, Working or BlackHoled |
| `ipv6Detail` | string | yes | The one-line reason behind the IPv6 verdict: what answered and what did not |
| `containerRuntime` | string | yes | The container runtime this machine's last check found (Podman or Docker), empty when none answered |
| `containerRuntimeVersion` | string | yes | The version that runtime reported, empty when none answered or it could not be read |
| `containerRuntimeVerdict` | string | yes | What the last check means for running servers in containers here: Unknown before the first check, then NotFound, Usable or Unusable |
| `containerRuntimeReason` | null or [ApiError](#schema-apierror) | yes |  |

<a id="schema-discoveredport"></a>
#### DiscoveredPort

A port number found in a server's own config file, and where it was read from

| Field | Type | Required | Description |
|---|---|---|---|
| `ref` | string | yes | The string PortRequirement.Ref this number belongs to |
| `port` | integer (int32) | yes | The number the config file held |
| `configFile` | string | yes | The file it came from |
| `address` | string | yes | The key within that file |

<a id="schema-discoveredserver"></a>
#### DiscoveredServer

One directory a scan believes is a game server, with everything a results row needs

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Absolute path on the spire that found it |
| `rootPath` | string | yes | The scan root it was found beneath. Carried so the results screen can group by root and show each candidate as the part of the path that differs, instead of repeating a long absolute path on every row where only the last segment is doing any work. |
| `source` | [CandidateSource](#schema-candidatesource) | yes | Where it was found: a legacy panel, a datastore orphan, a path the user typed |
| `matches` | [TemplateMatch](#schema-templatematch)[] | yes | Templates this could be, strongest first. Empty means found but unsupported |
| `evidence` | [MatchEvidence](#schema-matchevidence)[] | yes | What was found on disk, so the user can check the match rather than trust it |
| `suggestedName` | string? | yes | The name AMP would give it |
| `installedVersion` | string? | yes | The build already on disk, where the evidence said |
| `installedVersionIsBuildId` | boolean | yes | The version is an opaque build identifier rather than one a person would recognise, so a results row keeps it out of sight. See CandidateIdentification. |
| `unmatchedSteamAppIds` | string[] | yes | Steam apps found that no template covers |
| `recovery` | null or [ImportRecoveryReport](#schema-importrecoveryreport) | yes |  |
| `listingTruncated` | boolean | yes | A cap stopped the listing, so an absent marker is not proof of absence |
| `links` | integer (int32) | yes | Symbolic links the scan saw inside it, which an import does not copy through |
| `alreadyImportedShardId` | string? (uuid) |  | The live shard that carries this directory's UUID as its id, when there is one |
| `alreadyImportedAs` | string? |  | That shard's name, for the row to say so |

<a id="schema-discoveryresult"></a>
#### DiscoveryResult

Everything a scan produced, including what it could not do

| Field | Type | Required | Description |
|---|---|---|---|
| `servers` | [DiscoveredServer](#schema-discoveredserver)[] | yes | What was found |
| `unreadable` | [UnreadablePath](#schema-unreadablepath)[] | yes | Paths AMP could not read, which is the case the privilege tiers exist for |
| `truncatedRoots` | string[] | yes | Roots where a cap stopped the walk |
| `timedOut` | boolean | yes | The scan ran out of its budget |

<a id="schema-disklimitlevel"></a>
#### DiskLimitLevel

How a shard's measured disk usage stands against its configured limit. Ordered by severity, so callers can compare rather than switch (`level >= DiskLimitLevel.Critical`).

Type: object enum: `None`, `Warning`, `Critical`, `Exceeded`

<a id="schema-diskusagekind"></a>
#### DiskUsageKind

What a first-level directory in a datastore is, as the usage scan sees it

Type: object enum: `Shard`, `Orphan`, `Backups`

<a id="schema-effectivesetting"></a>
#### EffectiveSetting

A single app setting with its spec and current effective value. Returned by `GET /api/shards/{id}/settings` so the UI has everything it needs to render the field without a second request for the spec.

| Field | Type | Required | Description |
|---|---|---|---|
| `spec` | [SettingSpec](#schema-settingspec) | yes | The template's setting spec (field name, input type, allowed values, and constraints) |
| `value` | string | yes | The setting's current effective value, or the spec default when unset. Password values are masked |

<a id="schema-emittedvaluedto"></a>
#### EmittedValueDto

One value a trigger puts into the run context for its steps to use

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | What a step refers to it by, as `{@Name}`. Never translated |
| `label` | string |  | Template locale key naming it, when it came from a template-declared event Default `""`. |
| `description` | string |  | Template locale key explaining what it carries Default `""`. |

<a id="schema-enabledbody"></a>
#### EnabledBody

Request body to arm or disarm a schedule

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | yes | True to arm the schedule, false to disable it |

<a id="schema-endpointnote"></a>
#### EndpointNote

What is known to stand between this address and a working connection. Notes never suppress an address: an operator who HAS forwarded the port needs to see it, and the one who has not needs to be told why it does not work rather than being shown nothing.

Type: object enum: `NeedsForward`, `Cgnat`, `NoHairpin`, `ViaFront`, `Declared`, `NotReady`, `Paused`

<a id="schema-endpointreach"></a>
#### EndpointReach

Which side of the front door an address is for. Both are real answers to "where do I connect?" and which one is right depends on where the person asking is sitting, so AMP reports both rather than picking one and being wrong half the time.

Type: object enum: `Public`, `Local`

<a id="schema-endpointsource"></a>
#### EndpointSource

Where an address came from, which is the whole of how much it can be trusted: one the operator declared is taken at face value, one read off an adapter is a fact, and one an echo service reported is only evidence the machine sits behind something.

Type: object enum: `Advertised`, `Published`, `Gateway`, `SpirePublicHost`, `SpireAddress`, `ExternalProbe`, `LocalAddress`

<a id="schema-enrollmentdto"></a>
#### EnrollmentDto

An enrollment as the UI sees it. Deliberately carries no secret and no credential

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) | yes | Enrollment id |
| `label` | string | yes | What the admin called this machine |
| `createdByName` | string? | yes | Who issued it |
| `createdAt` | string (date-time) | yes | When it was issued |
| `expiresAt` | string (date-time) | yes | When it stops being redeemable |
| `status` | string | yes | pending \| awaitingapproval \| approved \| denied \| redeemed \| expired \| revoked |
| `redeemedAt` | string? (date-time) | yes | When a machine redeemed it |
| `redeemedSpireId` | string? (uuid) | yes | The spire that redeemed it |
| `revokedAt` | string? (date-time) | yes | When it was withdrawn |
| `revokedReason` | string? | yes | Why it was withdrawn |
| `role` | string | yes | standard \| publicentrynode - what the machine was invited to be |
| `entryNodeHost` | string? | yes | The public address an entry-node enrollment is waiting on, so the operator can see what this end is dialling and correct it if it is wrong. Null for an ordinary enrollment. |
| `entryNodeState` | string? | yes | waiting \| unreachable \| notpasted \| wrongsecret \| collected. How the last attempt to reach that machine went, and the whole reason the bootstrap goes over HTTP rather than being left to WireGuard: each of these needs a different thing done about it, and WireGuard would have shown all of them as nothing happening. |
| `entryNodeStateCode` | string? | yes | i18n key with more about that state, when there is more to say |
| `entryNodeStateParams` | map<string, string> | yes | Substitutions for that key |
| `entryNodeObservedHost` | string? | yes | The address the machine itself reports having, when it differs from what the operator typed. Null when they agree, or when it has not answered yet: this is a hint, not a fault |
| `entryNodeBlockedPort` | integer (int32) | yes | The tunnel port the machine reported it cannot bind, or null when it can. A number rather than a sentence: the sentence belongs at the end that knows what language to write it in. |
| `tunnelPhase` | string? | yes | Once the details are collected, how far the tunnel itself has got. Null before then, because      there is no tunnel record to have a phase yet.       This is the half of the story THIS end owns. Collecting the machine's details is where its involvement ends and ours begins - rendering our own config, and getting our own privileged agent to install our end - and without this the strip fell silent at exactly the point the remaining work became ours. A stopped agent here looks, from the other machine, precisely like a firewall over there. |
| `tunnelFault` | string? | yes | What is wrong at THIS end, when something is: our own system agent not running, or refusing the interface. Null when the fault is elsewhere or there is none. |
| `admission` | null or [AdmissionDto](#schema-admissiondto) | yes |  |

<a id="schema-entrynodesetupprogress"></a>
#### EntryNodeSetupProgress

The bring-up of a public entry node, broken into the stages that fail for different reasons.               It exists because "it has not worked yet" was, for most of this process, the only thing either end could say. A tunnel that never forms, a bus that is not listening on the tunnel address, a join file nobody pasted and a firewall eating the setup port all present identically at the end of the chain - as a connection that does not happen - and each needs something completely different done about it. Naming the rungs is what lets an operator see WHERE it stopped instead of only that it did.The rungs are ordered, and each is only meaningful once the one before it is done. The two in the middle are the ones that earn the whole thing: string EntryNodeSetupProgress.TunnelConnected answers "is the wire up", and string EntryNodeSetupProgress.BusReachable answers "is anything listening at the other end of it". Those two failing look the same from a distance and have nothing in common up close.

| Field | Type | Required | Description |
|---|---|---|---|
| `steps` | [EntryNodeSetupStep](#schema-entrynodesetupstep)[] | yes | The ladder, in order |
| `complete` | boolean | yes | True once this machine is fully part of the installation |

<a id="schema-entrynodesetupstep"></a>
#### EntryNodeSetupStep

One rung of the ladder

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | yes | Stable identifier, also the i18n key suffix for the label |
| `state` | [EntryNodeStepState](#schema-entrynodestepstate) | yes | How far this rung has got |
| `code` | string? |  | i18n key for what to say about it, when there is more than the state |
| `params` | map<string, string> |  | Substitutions for Code |

<a id="schema-entrynodestepstate"></a>
#### EntryNodeStepState

How far one stage of a public entry node's bring-up has got.               String-serialized deliberately. There is no global JsonStringEnumConverter in this application, so an unannotated enum reaches the browser as a bare number and anything matching on its NAME - a CSS attribute selector, a TypeScript union - silently matches nothing. The ladder is exactly that shape, so it rendered every rung as un-started whatever it really was.

Type: object enum: `Pending`, `Active`, `Done`, `Failed`

<a id="schema-erased"></a>
#### Erased

What an erasure removed, for the caller and the audit trail

| Field | Type | Required | Description |
|---|---|---|---|
| `records` | integer (int32) | yes |  |
| `days` | integer (int32) | yes |  |

<a id="schema-eventparamspec"></a>
#### EventParamSpec

How one of an event's capture groups is presented in the schedule editor

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string |  | Locale key (or literal text) naming the parameter |
| `description` | string |  | Locale key (or literal text) explaining what the parameter carries |

<a id="schema-eventspec"></a>
#### EventSpec

One event a template declares: a console-line regex whose NAMED capture groups become the event's parameters. The agent matches every console line against it and publishes an occurrence; the scheduler treats it exactly like a built-in trigger.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string |  | Stable identifier, unique within the template (e.g. "blockPlaced"). Stored in a schedule as `template.{Id}`, so renaming one orphans every schedule that used it. |
| `name` | string |  | Locale key (or literal text) for the event's name in the trigger picker |
| `description` | string |  | Locale key (or literal text) explaining when the event fires |
| `expression` | string |  | The console-line regex. Every named capture group becomes a parameter of the event; numbered groups are ignored, because a step has to be able to name what it is referring to. |
| `paramMap` | map<string, [EventParamSpec](#schema-eventparamspec)> |  | Display strings for the capture groups, keyed by group name. A group with no entry still emits its value - this only decides how it is labelled in the editor. |

<a id="schema-exitmethod"></a>
#### ExitMethod

How AMP asks a running application to shut down gracefully before it is force-killed

Type: integer

<a id="schema-expiredpurgebody"></a>
#### ExpiredPurgeBody

A retention purge the owning spire has started

| Field | Type | Required | Description |
|---|---|---|---|
| `purging` | integer (int32) | yes | How many shards it is purging |

<a id="schema-extractbody"></a>
#### ExtractBody

An archive (or a folder inside one) to unpack, and where it should land

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | The archive, or a folder/file within it. A path inside an archive extracts just that part |
| `destination` | string? | yes | Target directory. Optional; defaults to the directory holding the archive, i.e. extract in place |
| `overwrite` | boolean | yes | Whether files already at the destination are replaced. When false they are left alone and counted as skipped |

<a id="schema-extractresultdto"></a>
#### ExtractResultDto

What an extract did, broken down so nothing is skipped without a reason

| Field | Type | Required | Description |
|---|---|---|---|
| `extracted` | integer (int32) | yes | Files written to disk |
| `skippedOnDisk` | integer (int32) | yes | Skipped where the bytes are: already present without `Overwrite`, no longer in the archive, or refused by the traversal guard |
| `skippedDenied` | integer (int32) | yes | Refused because the caller lacks `files.write` on the destination |
| `skippedBlocked` | integer (int32) | yes | Refused by the tenant file-type policy |
| `skippedManaged` | integer (int32) | yes | Refused because AMP writes that file from the server's settings |
| `examples` | string[] | yes | A few of the skipped paths, so the user can be shown which rather than just how many |

<a id="schema-failurepolicy"></a>
#### FailurePolicy

Type: object enum: `Continue`, `Abort`, `None`

<a id="schema-fileacldto"></a>
#### FileAclDto

Who can do what with one path, and where each rule comes from

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | The path being described, relative to the shard working directory |
| `principals` | [AclPrincipal](#schema-aclprincipal)[] | yes | Roles and people with any access here, named ones first |
| `canEdit` | boolean | yes | Whether the caller may change these rules |

<a id="schema-fileaclupdatebody"></a>
#### FileAclUpdateBody

A set of ACL changes to apply to one path

| Field | Type | Required | Description |
|---|---|---|---|
| `changes` | [AclChange](#schema-aclchange)[] | yes | Each sets one principal's one verb to allow, deny, or inherit |

<a id="schema-fileentry"></a>
#### FileEntry

One entry in a shard directory listing (file or folder), relative to the shard root

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string |  | Path relative to the shard working dir, using forward slashes |
| `name` | string |  | Display name (last path segment) |
| `isDirectory` | boolean |  | True for a folder, false for a file |
| `isLink` | boolean |  | True when the entry is a link (a symbolic link or a junction) rather than the folder or file itself, so a walk of the tree can decline to descend through it |
| `size` | integer (int64) |  | File length in bytes; 0 for directories |
| `modified` | string (date-time) |  | Last write time (UTC) |

<a id="schema-firewallprotocol"></a>
#### FirewallProtocol

Type: object enum: `Tcp`, `Udp`

<a id="schema-firewallrule"></a>
#### FirewallRule

| Field | Type | Required | Description |
|---|---|---|---|
| `port` | integer (int32) |  |  |
| `protocol` | [FirewallProtocol](#schema-firewallprotocol) |  |  |
| `tag` | string |  |  |
| `label` | string |  |  |
| `scope` | [FirewallScope](#schema-firewallscope) |  |  |

<a id="schema-firewallscope"></a>
#### FirewallScope

Type: object enum: `Any`, `HostOnly`

<a id="schema-firewallstatusdto"></a>
#### FirewallStatusDto

The privileged system agent's firewall state on the local spire

| Field | Type | Required | Description |
|---|---|---|---|
| `running` | boolean | yes | False when no system agent is installed, or it has stopped |
| `status` | null or [NetworkStatus](#schema-networkstatus) | yes |  |
| `withheld` | [WithheldPortRule](#schema-withheldportrule)[] | yes | Server rules AMP did not publish because it holds their numbers itself; each names the server, the port and what holds it |

<a id="schema-gameusage"></a>
#### GameUsage

How popular one game was, across every server running it

| Field | Type | Required | Description |
|---|---|---|---|
| `game` | string | yes | The template's slug, which is what makes two servers the same game |
| `sessions` | integer (int32) | yes |  |
| `sessionsPercent` | number/string (double) | yes |  |
| `minutes` | number/string (double) | yes |  |
| `minutesPercent` | number/string (double) | yes |  |
| `users` | integer (int32) | yes | Distinct people: one person on two Rust servers is one Rust player |
| `servers` | integer (int32) | yes | How many of its servers had anyone on them |

<a id="schema-gatewaybindingstate"></a>
#### GatewayBindingState

Where a gateway binding is in its bring-up. Mirrors TunnelPhase in shape so the two read consistently: a binding rides a tunnel, and cannot be further along than the tunnel under it.

Type: object enum: `Provisioning`, `AwaitingShard`, `Connecting`, `Active`, `Disabled`, `Fault`

<a id="schema-gatewaydto"></a>
#### GatewayDto

A whole-shard gateway binding with its last reported status

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) | yes | Gateway binding identifier |
| `shardId` | string (uuid) | yes | Server routed through the gateway |
| `shardName` | string | yes | Display name of the routed server |
| `frontSpireId` | string (uuid) | yes | Publicly routable spire that fronts the server |
| `frontSpireName` | string | yes | Display name of the front spire |
| `enabled` | boolean | yes | Whether the binding is currently enabled |
| `egressAllowList` | string[] | yes | Private networks (IPv4 CIDRs) the server may reach through the front; everything else private is dropped |
| `createdAt` | string (date-time) | yes | When the binding was created |
| `status` | null or [ShardGatewayBindingStatus](#schema-shardgatewaybindingstatus) | yes |  |
| `runningWithoutRoute` | boolean | yes | The binding waits for its server, but the server runs already, launched without this route; a route engages when the server launches, so it starts routing once the server is restarted |

<a id="schema-gatewayeligibilitydto"></a>
#### GatewayEligibilityDto

Who may take part in gateway mode. Both lists are positive: absent means ineligible

| Field | Type | Required | Description |
|---|---|---|---|
| `containerShardIds` | string (uuid)[] | yes | Servers running in Podman, the one runtime gateway mode can route (decision 114 of docs/per-shard-identity.md) |
| `gatewayFrontSpireIds` | string (uuid)[] | yes | Publicly-routable spires that can front a gateway (Linux only) |

<a id="schema-gatewaypreflightbody"></a>
#### GatewayPreflightBody

Request to check a whole-server gateway route before creating it

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) | yes | The server that would be routed |
| `frontSpireId` | string (uuid) | yes | The public spire that would front it |

<a id="schema-gatewayroute"></a>
#### GatewayRoute

| Field | Type | Required | Description |
|---|---|---|---|
| `interfaceName` | string |  |  |
| `publicPort` | integer (int32) |  |  |
| `protocol` | [FirewallProtocol](#schema-firewallprotocol) |  |  |
| `targetIp` | string |  |  |
| `targetPort` | integer (int32) |  |  |
| `key` | [GatewayRouteKey](#schema-gatewayroutekey) |  |  |

<a id="schema-gatewayroutekey"></a>
#### GatewayRouteKey

| Field | Type | Required | Description |
|---|---|---|---|
| `protocol` | [FirewallProtocol](#schema-firewallprotocol) |  |  |
| `publicPort` | integer (int32) |  |  |

<a id="schema-hostarchitecture"></a>
#### HostArchitecture

CPU architecture filter - member names deliberately match the template JSON spellings ("UpdateSourceArch": "x86_64" / "aarch64") so the enum binds directly. Mirrors AMP 2's PlatformInfo.Architecture; defaults to All when a stage doesn't declare one.

Type: integer

<a id="schema-hostplatform"></a>
#### HostPlatform

Operating system family of a host, as a flags set so templates can target several at once. Member names match the template JSON spellings.

Type: integer

<a id="schema-httpscheck"></a>
#### HttpsCheck

One rung: a fixed key the client labels, and a coded outcome with its parameters

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | yes | One of string[] HttpsSetupService.CheckKeys |
| `state` | [HttpsCheckState](#schema-httpscheckstate) | yes |  |
| `code` | string? |  | A locale key saying what the rung found, when there is something to say |
| `params` | map<string, string> |  | Values the code interpolates |
| `detail` | string? |  | A library's or the agent's own words, shown under the sentence; never the whole message |

<a id="schema-httpscheckstate"></a>
#### HttpsCheckState

Where one rung of the HTTPS step's ladder stands. The names are the ones the setup ladder's styles already know

Type: integer

<a id="schema-httpsnamecheck"></a>
#### HttpsNameCheck

Where a domain name points, as the name screen asks before anything starts: the first three rungs alone, with nothing configured and nothing audited

| Field | Type | Required | Description |
|---|---|---|---|
| `hostName` | string? | yes | The name in its normalised form; null when it is outside the grammar |
| `outcome` | string | yes | One of HttpsNameOutcome |
| `checks` | [HttpsCheck](#schema-httpscheck)[] | yes | The name, resolve and address rungs as the step would run them |
| `reachability` | string | yes | How this machine reaches the internet, as NetworkReachability names it |
| `externalAddress` | string? | yes | The public IPv4 the record has to carry |
| `internalAddress` | string? | yes | This machine's own IPv4, the target of a port forward behind NAT |
| `addresses` | string[] | yes | What the name resolves to, for the live check's sentence |

<a id="schema-httpssetupprogress"></a>
#### HttpsSetupProgress

The step as it runs, polled by the wizard and the settings page

| Field | Type | Required | Description |
|---|---|---|---|
| `phase` | string | yes | `idle`, `running`, `done` (the front is configured) or `failed` (nothing was configured) |
| `hostName` | string? | yes |  |
| `checks` | [HttpsCheck](#schema-httpscheck)[] | yes |  |
| `url` | string? | yes | The panel's address through the front once it is configured |
| `certificateVerified` | boolean | yes | Whether the last probe saw a trusted certificate at the name |
| `updatedUtc` | string (date-time) | yes |  |

<a id="schema-httpsstate"></a>
#### HttpsState

What the HTTPS step and the settings page show before anything is asked

| Field | Type | Required | Description |
|---|---|---|---|
| `mode` | string | yes | `none`, `managed` (Caddy in front, configured by the step or the installer) or `ownProxy` (the escape hatch) |
| `hostName` | string? | yes |  |
| `url` | string? | yes |  |
| `certificateVerified` | boolean | yes |  |
| `agentPresent` | boolean | yes | The privileged agent answered within its window, so the front can be asked for |
| `agentSupported` | boolean | yes | The agent on this platform manages Caddy at all (Linux) |
| `panelExposed` | boolean | yes | The panel port is in the managed firewall, which a public front presupposes |
| `entryNode` | boolean | yes | A headless relay, which never gets a front |
| `externalAddress` | string? | yes | The public IPv4 this machine is seen from, for the record the operator has to create |
| `reachability` | string | yes | How this machine reaches the internet, as NetworkReachability names it: the port-forwarding advice hangs on it |
| `internalAddress` | string? | yes | This machine's own IPv4, where a port forward behind NAT has to point |
| `ipv6Address` | string? | yes | A global IPv6 address bound on this machine, when it has one, for the AAAA note; null when it has none |
| `trustedProxies` | string[] | yes | The addresses whose forwarded headers the panel honours |
| `serveFrontend` | boolean | yes | Whether the panel serves the web app itself (read at start; a change needs a restart) |
| `running` | boolean | yes | A setup is in progress |

<a id="schema-hubtokenresponse"></a>
#### HubTokenResponse

A ticket for the hubs and the instant it stops being accepted

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | Present as `access_token` when opening a hub WebSocket; accepted once |
| `expiresAt` | string (date-time) | yes | UTC instant after which the ticket is refused even if unused |

<a id="schema-iformfile"></a>
#### IFormFile

Type: string (binary)

<a id="schema-iformfilecollection"></a>
#### IFormFileCollection

Type: [IFormFile](#schema-iformfile)[]

<a id="schema-ignorestatusdto"></a>
#### IgnoreStatusDto

Snapshot-ignore status of a directory's entries: which are amp base-data vs user-excluded

| Field | Type | Required | Description |
|---|---|---|---|
| `amp` | string[] | yes | Entry paths excluded automatically as AMP base-data (from `.ampsnapshotignore`) |
| `user` | string[] | yes | Entry paths the user has excluded (from `.usersnapshotignore`). User exclusion wins, so no path appears in both lists |

<a id="schema-ignoretogglebody"></a>
#### IgnoreToggleBody

A request to add or remove paths from the user snapshot-exclusion list

| Field | Type | Required | Description |
|---|---|---|---|
| `paths` | string[] | yes | Paths to toggle, relative to the shard working directory |
| `ignored` | boolean | yes | True to exclude the paths from all snapshots, false to include them again |

<a id="schema-importchangerecord"></a>
#### ImportChangeRecord

What an import actually changed, kept on the shard afterwards.               Deliberately durable rather than a completion toast. Somebody importing forty servers will not read forty change summaries in one sitting, and the one they need is attached to the server that is not working three days later.

| Field | Type | Required | Description |
|---|---|---|---|
| `importedAt` | string (date-time) | yes | When the import ran |
| `sourcePath` | string | yes | Where the files were copied from, which still exists untouched |
| `source` | [CandidateSource](#schema-candidatesource) | yes | Which legacy panel or route it was found through |
| `recovered` | [ImportContent](#schema-importcontent) | yes | The parts that came across |
| `refused` | [ImportContent](#schema-importcontent) | yes | Parts the user asked for that this candidate could not supply |
| `portsMoved` | [PortOutcome](#schema-portoutcome)[] | yes | Ports that could not be kept, with old, new and why |
| `nameOrigin` | [NameSource](#schema-namesource) | yes | Where the display name came from |
| `settingsExcluded` | string[] | yes | Settings AMP kept authority over rather than taking from the file |
| `settingsMissing` | string[] | yes | Managed settings whose key was absent from the file |
| `filesSkipped` | [ImportSkippedFile](#schema-importskippedfile)[] | yes | Anything the copy could not or would not bring across, each with why |
| `hasAnythingToSay` | boolean |  | Whether this import has anything the user needs to know about. False for a clean import, which should not leave a notice on the shard demanding to be dismissed.  IReadOnlyList<string> ImportChangeRecord.SettingsMissing deliberately does not count. A config file almost never holds every key its template manages, so counting it would make this true on essentially every import and turn the notice into permanent furniture. Those settings simply take AMP's defaults, which is the normal outcome rather than a change worth flagging. They stay in the record for anyone who opens the detail. |
| `settingsRefused` | string[] |  | Settings the files did hold a value for that AMP refused to take, because the value names a path outside the shard or a binary for the agent to run. Unlike IReadOnlyList<string> ImportChangeRecord.SettingsExcluded, which holds the ports and bindings every import leaves to AMP, each of these is a value the user chose that the new server does not have, so any one of them is worth a notice. |

<a id="schema-importcontent"></a>
#### ImportContent

What an import brings across, as three things that compose rather than a ladder. Mirrors              CloneContent, which already splits a shard the same way for cloning.               They are separate because they are recovered separately and can be wanted separately. Whether settings and ports are available at all varies by game and is nothing the user did; whether they are wanted is entirely the user's call. Somebody moving a Minecraft world onto a machine with a different network setup may want the world and the settings and emphatically not the old ports.

Type: integer

<a id="schema-importrecoveryreport"></a>
#### ImportRecoveryReport

| Field | Type | Required | Description |
|---|---|---|---|
| `available` | [ImportContent](#schema-importcontent) | yes | The parts that can be recovered from this candidate |
| `settingsRecovered` | integer (int32) | yes | How many settings were read from the server's own config files |
| `portsDiscovered` | [DiscoveredPort](#schema-discoveredport)[] | yes | Ports read from config files, with where each came from |
| `portsNotDiscovered` | string[] | yes | Declared port refs nothing on disk described, which will be allocated fresh |
| `primaryPortRecovered` | boolean | yes | Whether the port people actually connect to survived. Called out separately because it is the one that decides whether a saved address still works: a moved RCON port is invisible to everyone except the operator, while a moved game port breaks every bookmark and forward pointing at it. |
| `settings` | [SettingsAvailability](#schema-settingsavailability) | yes | Why settings are or are not available |
| `defaultSelection` | [ImportContent](#schema-importcontent) |  | What the row is ticked with by default: everything available. The user unticks what they do not want rather than hunting for what they do. |
| `hasCaveats` | boolean |  | Whether this candidate has enough to say that a plain summary line would be misleading, so              the row offers its detail behind an expander instead of either burying it or shouting it.               True when ports came across but not all of them, when the port people connect on is one of the ones that moved, or when settings were read but some could not be. Each of those needs a sentence rather than a tick, and a row that needs a sentence should say so and let the user open it. |

<a id="schema-importrequest"></a>
#### ImportRequest

A batch of servers to check or import

| Field | Type | Required | Description |
|---|---|---|---|
| `items` | [ImportSelection](#schema-importselection)[] | yes | The selection |
| `enterOtherVolumes` | boolean |  | Copy through a volume mounted inside a server's folder rather than stopping at it. A server set up by hand may keep its worlds on a second disk; whether that disk belongs to it is the user's to say. A backup or a move never crosses one. Default `false`. |

<a id="schema-importselection"></a>
#### ImportSelection

One server the user wants to bring in

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | The candidate directory, exactly as the scan reported it |
| `templateSlug` | string | yes | The template to deploy it as. Required, because AMP never guesses for the user |
| `content` | [ImportContent](#schema-importcontent) |  | Which of the three parts to bring across. Anything unavailable is dropped, not faked |

<a id="schema-importskippedfile"></a>
#### ImportSkippedFile

One entry an import left behind. The reason is an ImportSkipReasons token whichever side did the copying, so the panel renders one list in the operator's language with the path as its parameter.

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Path relative to the server's root, with forward slashes |
| `reason` | string | yes | One of the ImportSkipReasons tokens |

<a id="schema-invitedto"></a>
#### InviteDto

An outstanding or completed invite and its current status

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Stable unique identifier for the invite |
| `userName` | string | yes | The username the invite will create. Fixed by the inviting admin |
| `displayName` | string? | yes | Optional friendly name suggested by the admin |
| `roleIds` | string[] | yes | Roles the created account will be given |
| `createdByName` | string? | yes | Name of the admin who issued the invite, if recorded |
| `createdAt` | string (date-time) | yes | When the invite was created |
| `expiresAt` | string (date-time) | yes | When the invite stops being redeemable |
| `accepted` | boolean | yes | True once the invite has been redeemed |
| `acceptedAt` | string? (date-time) | yes | When the invite was redeemed, or null if not yet |
| `status` | string | yes | Derived status: pending, expired, or accepted |

<a id="schema-invitepreview"></a>
#### InvitePreview

The small, safe subset of an invite shown anonymously before acceptance

| Field | Type | Required | Description |
|---|---|---|---|
| `userName` | string | yes | The username the invite will create |
| `displayName` | string? | yes | The suggested friendly name, if any |

<a id="schema-invitepreviewbody"></a>
#### InvitePreviewBody

The link code, carried in the body so it never lands in a request log

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | The one-time secret code from the invite link |

<a id="schema-ipbindingoption"></a>
#### IpBindingOption

One selectable bind-address choice for a shard or spire default, carrying the same classification facts the spire reports (ReportedIpInfo) so the UI can render coloured Int/Ext/DHCP/Static tags. Synthetic entries (all interfaces, localhost, auto-from-pool) are distinguished by string IpBindingOption.Role.

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string |  | The stored value: a literal IP, "0.0.0.0", "127.0.0.1", or the auto sentinel. |
| `role` | string |  | What kind of option this is: "all" (0.0.0.0), "localhost" (127.0.0.1),             "auto" (first from pool sentinel), or "address" (a concrete reported/pool IP). |
| `interfaceName` | string |  | Owning interface name for concrete addresses; empty for synthetic roles. |
| `isV6` | boolean |  | True for an IPv6 address. |
| `isInternal` | boolean |  | Private/link-local/ULA/CGNAT range. Only meaningful for the "address" role. |
| `isDhcp` | boolean? |  | DHCP/SLAAC (true) vs static (false); null when undeterminable or synthetic. |
| `isPrimary` | boolean |  | The address outgoing traffic uses by default (routes to the gateway). |
| `isTunnel` | boolean |  | Belongs to a VPN/tunnel interface. |

<a id="schema-javaruntime"></a>
#### JavaRuntime

A Java runtime found installed on a spire host.               Deliberately structured rather than pre-formatted: AMP 2 built display strings like "TEMURIN 21 (hotspot) [System Default]" inside the scan, which cannot be translated. Here the scan reports facts and the UI composes the label, so "System default" can be localised.

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string |  | Absolute path to the java executable. This is the value a setting stores |
| `majorVersion` | integer (int32) |  | Major version (21, 17, 8). Zero when the directory name did not identify one |
| `vendor` | string |  | Distribution: "Adoptium", "Oracle", "Zulu", "Corretto", "GraalVM", "OpenJDK". Empty when the install gives nothing to identify it by. |
| `kind` | string |  | "JDK" or "JRE" - kept as initialisms, never title-cased. Empty when unknown |
| `name` | string |  | The install directory's own name, used as a label when nothing else was recognised |
| `isDefault` | boolean |  | This is the java on PATH - the one the host would use by default |
| `isSystemDefault` | boolean |  | Not a fixed install but "whatever java the host resolves at launch". Its string JavaRuntime.Path is a `$PATH`-prefixed token the agent resolves each time, so the shard follows the host's default instead of pinning today's answer. |

<a id="schema-jobexecution"></a>
#### JobExecution

Immutable record of one firing of a ScheduleDefinition. Stored in NATS KV at `amp.tn.{tenant}.job.{executionId}`.  The NATS message that enqueued the firing uses a deterministic dedup ID (time triggers: `{scheduleId}.{canonicalFireTime unixMs}`; event triggers: `{scheduleId}.{EventId}`) so even if every node publishes, only one message enters the stream.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) |  |  |
| `scheduleId` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `shardId` | string (uuid) |  |  |
| `eventPayload` | string? |  | Raw event payload (JSON) for event-triggered runs; null for time triggers.             The consumer maps it into run-context values via the TriggerCatalog schema |
| `canonicalFireTime` | string (date-time) |  | The canonical time this firing was *supposed* to occur. Part of the dedup ID for time triggers - deterministic across all nodes. |
| `timeout` | string |  | Copied from the definition at publish so the worker can arm it without a lookup |
| `queuedAt` | string (date-time) |  |  |
| `startedAt` | string? (date-time) |  |  |
| `completedAt` | string? (date-time) |  |  |
| `status` | [JobStatus](#schema-jobstatus) |  |  |
| `attemptNumber` | integer (int32) |  |  |
| `workerNode` | string? |  | The node/worker that ran this execution |
| `errorMessage` | string? |  | Run-level failure summary (first unhandled error), for the trigger badge |
| `stepOutcomes` | [StepOutcome](#schema-stepoutcome)[] |  | Per-step results, keyed to the definition's step ids - the run journal |
| `duration` | integer? |  |  |

<a id="schema-jobstatus"></a>
#### JobStatus

Type: object enum: `Queued`, `Running`, `Succeeded`, `CompletedWithErrors`, `Failed`, `TimedOut`, `Skipped`, `None`

<a id="schema-licenceactivatebody"></a>
#### LicenceActivateBody

A licence key to activate, with the holder's acceptance of the current terms when the key needs one

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | yes | The licence key exactly as issued. AMP keys are GUIDs |
| `terms` | null or [TermsAcceptanceBody](#schema-termsacceptancebody) | yes |  |

<a id="schema-licenceagreementdto"></a>
#### LicenceAgreementDto

The migration agreement as the licensing service serves it

| Field | Type | Required | Description |
|---|---|---|---|
| `version` | integer (int32) | yes |  |
| `language` | string | yes | The language the text is in |
| `title` | string | yes |  |
| `markdown` | string | yes | The agreement body. Render it as text |
| `hash` | string | yes | Quote this in the acceptance |
| `fallbackFrom` | string? | yes | The language asked for, when the service had no translation and served English |
| `attestation` | string | yes | The sentence the accepter ticks, exactly as the service serves it; the acceptance echoes it verbatim |

<a id="schema-licencedto"></a>
#### LicenceDto

One activated licence and its current state

| Field | Type | Required | Description |
|---|---|---|---|
| `spireId` | string (uuid) | yes | The spire holding the licence's activation file; with the grade, the handle for removal |
| `spireName` | string | yes | That spire's display name |
| `gradeId` | string (uuid) | yes | The edition's stable grade identifier |
| `productName` | string | yes | The licensed product, e.g. AMP |
| `gradeName` | string | yes | The grade, e.g. Enterprise. Present it with the product as     "{product} {grade} Edition" - never show the grade on its own |
| `state` | string | yes | What the activation file says on its own: Valid, Expired, or Invalid (unreadable/moved-machine activation) |
| `standing` | string | yes | What counts: the file and the licensing service's last verdict together.     Active, Grace, Lapsed, Revoked, Disabled, Unrecognised, OutOfTouch, Invalid or TermsRequired; only Active and Grace grant capacity |
| `expires` | string? (date-time) | yes | When the licence lapses; null means lifetime |
| `graceEnds` | string? (date-time) | yes | When a licence in its grace period stops counting |
| `usage` | integer (int32) | yes | Installations that have activated this key, as last reported by the     licensing service. Only an online query knows this, so a licence read back from its stored     activation reports 0 |
| `limitKnown` | boolean | yes | The licensing service has told this installation the key's limit |
| `shardLimit` | integer (int32) | yes | The key's limit, shared by every installation using it; null means unlimited (or not known yet) |
| `keyShardsTotal` | integer (int32) | yes | Shards across every installation using this key, as of the last report |
| `issuedAt` | string? (date-time) | yes | When the licence was created, when the licensing service has said |
| `termsVersion` | integer (int32) | yes | The licence terms the key is held under, when the service has said |
| `requiredTermsVersion` | integer (int32) | yes | The terms AMP 3 requires. A key below it stands TermsRequired until its holder accepts them |

<a id="schema-licencekeybody"></a>
#### LicenceKeyBody

A licence key to query or activate. Never stored

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | yes | The licence key exactly as issued. AMP keys are GUIDs |

<a id="schema-licencepreviewdto"></a>
#### LicencePreviewDto

What a key is, previewed before activation

| Field | Type | Required | Description |
|---|---|---|---|
| `productName` | string | yes | The licensed product, e.g. AMP |
| `gradeName` | string | yes | The grade, e.g. Enterprise. Present it as "{product} {grade} Edition" |
| `expires` | string? (date-time) | yes | When the licence lapses; null means lifetime |
| `usage` | integer (int32) | yes | Installations that have already activated this key |
| `alreadyActive` | boolean | yes | True when this edition already has an activation here (activating again just refreshes it) |
| `eligible` | boolean | yes | Whether this key's grade may actually be activated on this build. Always     true on a release build; on a preview build, false for a grade the preview does not accept, so     the surface can refuse before activation rather than after |
| `termsRequired` | boolean | yes | The holder must accept the current licence terms (the AMP 2 to AMP 3     Licence Migration) before this key can be activated; the activation then carries the acceptance |
| `termsVersion` | integer (int32) | yes | The terms the key is held under |
| `requiredTermsVersion` | integer (int32) | yes | The terms AMP 3 requires, and the agreement version to present |
| `issuedAt` | string? (date-time) | yes | When the licence was created |

<a id="schema-licencestatusdto"></a>
#### LicenceStatusDto

The installation's complete licensing picture

| Field | Type | Required | Description |
|---|---|---|---|
| `licences` | [LicenceDto](#schema-licencedto)[] | yes | Every activated licence, including expired/invalid ones |
| `shardCount` | integer (int32) | yes | Shards deployed on this installation across every spire (soft-deleted excluded) |
| `otherInstallationShards` | integer (int32) | yes | Shards on other installations sharing this one's keys, which count against the same limit |
| `licensed` | boolean | yes | At least one licence currently grants capacity |
| `confirmed` | boolean | yes | The licensing service has vouched for the limit. Until it has, the licence is trusted and nothing is capped |
| `shardLimit` | integer (int32) | yes | The soft limit shared with those other installations; null means unlimited (or not confirmed) |
| `shardCeiling` | integer (int32) | yes | The hard stop just above the soft limit; null means unlimited (or not confirmed) |
| `lastVerifiedAt` | string? (date-time) | yes | When the licensing service last gave a verified answer |
| `usingMockClient` | boolean | yes | True when a development stub is standing in for the real licensing service, and always false in a shipped build, which carries no stub. Only the keys the stub is given work in that mode, so genuine keys are rejected as unknown; the UI warns rather than letting that look like a broken check |

<a id="schema-licencetermsacceptbody"></a>
#### LicenceTermsAcceptBody

The holder's acceptance of the current licence terms for a licence already activated, with its key

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | yes | The licence key the licence was activated with. AMP keeps no key, so it comes with every     acceptance; the licensing service verifies it against the activation |
| `termsVersion` | integer (int32) | yes | The version accepted; the required version the agreement was fetched for |
| `agreementHash` | string | yes | The hash of the agreement text shown. A hash that is no longer current is refused |
| `attestation` | string | yes | The attestation exactly as it was affirmed |
| `locale` | string | yes | The panel's language at the time |
| `clickedAt` | string (date-time) | yes | When the accepter committed |

<a id="schema-livenessevidence"></a>
#### LivenessEvidence

Why a candidate looks like it is still running

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | yes | i18n key for the reason |
| `message` | string | yes | English fallback naming the specific evidence |

<a id="schema-loginrequest"></a>
#### LoginRequest

Credentials for a standard login

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | yes | The account's username |
| `password` | string | yes | The account's password |
| `rememberMe` | boolean |  | The user ticked "stay signed in": issue a long-lived session that survives closing the browser. Left false, the session lasts the working day and renews while the app is open. Default `false`. |

<a id="schema-managedfiledto"></a>
#### ManagedFileDto

One file in this directory that AMP writes from the server's settings

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | The file, relative to the shard working directory |
| `kind` | string | yes | `generated` when AMP rewrites the whole file on every start (so it is read-only), `merged` when AMP only writes its own values into the app's own file |
| `importable` | boolean | yes | Whether editing a value AMP owns can be read back into the settings on save. When false on a merged file, the template's author has ruled that reading this file back is not appropriate, so AMP's values are rewritten over the user's on the next start |
| `source` | string | yes | `config` for a mapped config file, `template` when it is produced from a shipped template, `listfile` when a single list setting writes the whole file |
| `fieldName` | string? | yes | For a `listfile`, the setting that owns the file |

<a id="schema-matchconfidence"></a>
#### MatchConfidence

How strongly the evidence supports a template. Ordered, so the strongest reason wins

Type: integer

<a id="schema-matchevidence"></a>
#### MatchEvidence

One reason a template is being suggested, phrased for the user to check

| Field | Type | Required | Description |
|---|---|---|---|
| `detail` | string | yes | What was found, e.g. "a Steam app manifest for 2394010" |
| `relativePath` | string? | yes | Where it was found, relative to the candidate root |
| `weight` | [MatchConfidence](#schema-matchconfidence) | yes | How much this particular fact is worth |

<a id="schema-messagebusstatsdto"></a>
#### MessageBusStatsDto

Live message-bus health and JetStream account metrics

| Field | Type | Required | Description |
|---|---|---|---|
| `state` | string | yes | Connection state of the NATS client (Open, Closed, Reconnecting, ...) |
| `serverId` | string? | yes | First 8 chars of the connected server's id, or null when disconnected |
| `serverName` | string? | yes | Reported server name |
| `serverVersion` | string? | yes | Reported NATS server version |
| `clusterName` | string? | yes | Cluster name, when the server is part of one |
| `rttMs` | number/string? (double) | yes | Measured round-trip latency in milliseconds from an active ping |
| `jetStreamAvailable` | boolean | yes | True when JetStream is reachable on the account |
| `jsStreams` | integer (int32) | yes | JetStream stream count for the account |
| `jsConsumers` | integer (int32) | yes | JetStream consumer count for the account |
| `jsStorageBytes` | integer? (uint64) | yes | Bytes of JetStream file storage in use |
| `jsApiTotal` | integer? (uint64) | yes | Total JetStream API requests served for the account |
| `jsApiErrors` | integer? (uint64) | yes | JetStream API requests that returned an error |
| `service` | null or [BusServiceStatus](#schema-busservicestatus) | yes |  |

<a id="schema-metabody"></a>
#### MetaBody

Request body for editing a snapshot's metadata

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string? | yes | New friendly name for the snapshot node |
| `description` | string? | yes | New longer description for the snapshot node |
| `sticky` | boolean | yes | When true the snapshot is pinned and exempt from automatic retention pruning |

<a id="schema-metaconfigsubsection"></a>
#### MetaConfigSubsection

One config-file subsection: an optional string MetaConfigSubsection.Heading ([section]) plus a map of config-key → setting-field-name. bool MetaConfigSubsection.AutoMap emits every non-command-line setting automatically (no explicit mappings needed). Ported from AMP 2 `MetaConfigSubsection`.

| Field | Type | Required | Description |
|---|---|---|---|
| `heading` | string |  | Display heading for this group of mapped keys |
| `settingMappings` | map<string, string> |  | config-key to `SettingSpec.FieldName`. Value may also be a literal or shared-variable |
| `autoMap` | boolean |  | When true, unmapped keys in this subsection are auto-mapped by name |

<a id="schema-metricsmodel"></a>
#### MetricsModel

A point-in-time snapshot of a shard's resource usage, surfaced on the dashboard and live graphs

| Field | Type | Required | Description |
|---|---|---|---|
| `cpuUsagePercent` | number/string (float) |  | CPU usage as a percentage (0-100) |
| `memoryUsageMB` | integer (int64) |  | Resident memory usage in megabytes |
| `memoryLimitMB` | integer (int64) |  | Configured memory ceiling in megabytes, 0 when unlimited. Carried so a usage bar can show a real proportion: without it the UI has nothing to divide by, and a bar drawn against a guessed denominator states a fraction that is not true. |
| `activeConnections` | integer (int32) |  | Number of currently connected users |
| `networkInKBps` | integer (int32) |  | Inbound network throughput in kilobytes per second |
| `networkOutKBps` | integer (int32) |  | Outbound network throughput in kilobytes per second |
| `diskReadKBps` | integer (int32) |  | Disk read throughput in kilobytes per second |
| `diskWriteKBps` | integer (int32) |  | Disk write throughput in kilobytes per second |
| `taskQueueLength` | integer (int32) |  | Number of tasks currently queued for the shard |

<a id="schema-minecraftprofile"></a>
#### MinecraftProfile

What AMP learns about a Minecraft: Java Edition account from its UUID, from Mojang's session server: the name it goes by now, and its skin, whose face stands in for a profile picture. Mojang publishes nothing about bans and keeps no public profile page.

| Field | Type | Required | Description |
|---|---|---|---|
| `uuid` | string | yes | The account's UUID, in the usual dashed form |
| `name` | string | yes | The name it currently goes by |
| `skinHash` | string? | yes | The skin's texture hash, as Mojang's texture server files it; null for a default skin |
| `slim` | boolean | yes | The skin is for the slim (three-pixel-arm) model |
| `hasCape` | boolean | yes | The account wears a cape: given out for events and to migrated accounts, so a sign of an old or engaged account |

<a id="schema-misfirepolicy"></a>
#### MisfirePolicy

Type: object enum: `Skip`, `FireOnce`

<a id="schema-monitoringsettings"></a>
#### MonitoringSettings

Thresholds for the "running hot" resource-usage warnings on the overview. CPU is a plain usage percentage (warn as it climbs UP). RAM and disk are about HEADROOM REMAINING: warn when what is still free drops to/below the threshold. Each takes a QUANTITY: a "%" quantity is the free SHARE of the total (20% = under a fifth free), an absolute one (e.g. "16GB") is a free AMOUNT.  Disk adds a share of the disk to each tier and applies the LESSER of the two: a spire is short of storage when its free space is below both the floor (string MonitoringSettings.DiskWarning, string MonitoringSettings.DiskHigh) and that share of the disk (int MonitoringSettings.DiskWarningShare, int MonitoringSettings.DiskHighShare). The floor caps what a big disk is asked to keep free (16 GB, not 10% of 4 TB) and the share scales it down on a small one (2 GB on a 20 GB disk, not 16 GB). When the disk's total size is unknown the floor alone applies. A floor typed as a percentage is already a share, so the smaller of the two shares applies and the larger never fires on its own.  Two tiers per resource: the amber "warning" level and the red "high" level. Because RAM/disk measure remaining headroom, "high" is the LOWER figure (less left = worse).

| Field | Type | Required | Description |
|---|---|---|---|
| `cpuWarningPercent` | integer (int32) |  |  |
| `cpuHighPercent` | integer (int32) |  |  |
| `ramWarning` | string |  |  |
| `ramHigh` | string |  |  |
| `diskWarning` | string |  |  |
| `diskHigh` | string |  |  |
| `diskWarningShare` | integer (int32) |  | The share of the disk, in percent, that pairs with string MonitoringSettings.DiskWarning; the lesser of the two applies |
| `diskHighShare` | integer (int32) |  | The share of the disk, in percent, that pairs with string MonitoringSettings.DiskHigh; the lesser of the two applies |

<a id="schema-movebody"></a>
#### MoveBody

Source and destination for a move or copy

| Field | Type | Required | Description |
|---|---|---|---|
| `from` | string | yes | Source path, relative to the shard working directory |
| `to` | string | yes | Destination path, relative to the shard working directory |

<a id="schema-movetospirerequest"></a>
#### MoveToSpireRequest

Request body for a cross-spire move

| Field | Type | Required | Description |
|---|---|---|---|
| `targetSpireId` | string (uuid) | yes | The spire that should take ownership of the shard. Must differ from the shard's current spire |
| `targetDatastoreId` | string (uuid) | yes | The datastore on the destination spire to place the shard's files into |
| `bringBackups` | boolean |  | Copy the shard's snapshot archives to the destination too. Defaults to false: backups are often larger than the shard itself. Snapshots left behind stay on the source spire, and the timeline marks them as unrestorable from here. Default `false`. |
| `stopIfRunning` | boolean |  | Stop the server first when it is running, and wait for it to stop before the move is sent. Without it a running server is refused with `shards.error.moveNeedsStop`. Default `false`. |
| `startAfterMove` | boolean? |  | Start the server on the destination spire once the move has completed. Defaults to the value of StopIfRunning, so a server the move stopped comes back where it now lives. |

<a id="schema-mypermissions"></a>
#### MyPermissions

The caller's effective grants plus the full capability catalogue

| Field | Type | Required | Description |
|---|---|---|---|
| `grants` | string[] | yes | The caller's resolved grant strings, as the matcher parses them |
| `catalog` | [CapabilityDto](#schema-capabilitydto)[] | yes | Every known capability, so the UI can gate on the same matcher the backend enforces |
| `roleIds` | string[] | yes | The named roles the caller belongs to. Lets the role editor warn BEFORE a save that this is a role they are in, since nobody may revoke their own permissions. The hidden self-role is not listed - it is implicit and always theirs |

<a id="schema-namesource"></a>
#### NameSource

Where an imported server's name came from, so the change report can say when it was invented

Type: integer

<a id="schema-networkprobe"></a>
#### NetworkProbe

Result of Task<NetworkProbe> NetworkDetection.ProbeAsync(CancellationToken ct = default(CancellationToken)): the reachability verdict plus the two             addresses it was derived from (either may be null when detection was inconclusive)

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | [NetworkReachability](#schema-networkreachability) | yes |  |
| `internalIp` | string? | yes |  |
| `externalIp` | string? | yes |  |

<a id="schema-networkreachability"></a>
#### NetworkReachability

How reachable this machine is from the public internet - drives the first-run guidance             about port-forwarding vs tunnels. Serialized as its name (no global string-enum converter here)

Type: object enum: `Unknown`, `Direct`, `Nat`, `Cgnat`

<a id="schema-networkstatus"></a>
#### NetworkStatus

| Field | Type | Required | Description |
|---|---|---|---|
| `schema` | integer (int32) |  |  |
| `generation` | integer (int64) |  |  |
| `backend` | string |  |  |
| `healthy` | boolean |  |  |
| `applied` | [FirewallRule](#schema-firewallrule)[] |  |  |
| `rejected` | [RejectedRule](#schema-rejectedrule)[] |  |  |
| `wireGuardApplied` | [WireGuardInterfaceStatus](#schema-wireguardinterfacestatus)[] |  |  |
| `wireGuardRejected` | [RejectedInterface](#schema-rejectedinterface)[] |  |  |
| `wireGuardPeersApplied` | [WireGuardPeerStatus](#schema-wireguardpeerstatus)[] |  |  |
| `wireGuardPeersRejected` | [RejectedPeer](#schema-rejectedpeer)[] |  |  |
| `gatewayApplied` | [GatewayRoute](#schema-gatewayroute)[] |  |  |
| `gatewayRejected` | [RejectedGatewayRoute](#schema-rejectedgatewayroute)[] |  |  |
| `advisories` | string[] |  |  |
| `error` | string? |  |  |
| `updatedAt` | string (date-time) |  |  |
| `agentVersion` | string |  |  |

<a id="schema-newlinetype"></a>
#### NewLineType

Line-ending convention used when sending commands over a telnet/text admin channel

Type: integer

<a id="schema-originfacet"></a>
#### OriginFacet

One origin and how many events carry it

| Field | Type | Required | Description |
|---|---|---|---|
| `origin` | [AuditOrigin](#schema-auditorigin) | yes | The origin |
| `count` | integer (int32) | yes | Number of events with this origin |

<a id="schema-ownproxyrequest"></a>
#### OwnProxyRequest

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string | yes | The proxy's address, or its network in CIDR form |
| `apiOnly` | boolean |  | The proxy serves the web app itself, so the panel serves the API alone from its next start Default `false`. |

<a id="schema-passkeysummary"></a>
#### PasskeySummary

A passkey credential as the API returns it. The public key and RP ID never leave the server: neither is a secret exactly, but neither is any of the caller's business either.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) | yes | AMP's handle for the credential |
| `credentialId` | string | yes | The authenticator's own credential id, base64url. Returned because the Signal API needs it to tell the browser's password manager which passkeys are still accepted. Not a secret: the browser already holds it and presents it in every assertion, and this only ever goes back to the account that owns it. |
| `name` | string | yes | The user's label for it |
| `createdAt` | string (date-time) | yes | When it was registered |
| `lastUsedAt` | string? (date-time) | yes | When it last completed a sign-in, or null if never |
| `backupEligible` | boolean | yes | Whether it is a synced passkey rather than device-bound |
| `usable` | boolean | yes | Whether it still matches the installation's current RP ID |

<a id="schema-passwordpolicy"></a>
#### PasswordPolicy

The server's effective Identity password policy, so the UI can render matching live requirements

| Field | Type | Required | Description |
|---|---|---|---|
| `requiredLength` | integer (int32) | yes | Minimum password length |
| `requiredUniqueChars` | integer (int32) | yes | Minimum number of distinct characters |
| `requireUppercase` | boolean | yes | Whether at least one uppercase letter is required |
| `requireLowercase` | boolean | yes | Whether at least one lowercase letter is required |
| `requireDigit` | boolean | yes | Whether at least one digit is required |
| `requireNonAlphanumeric` | boolean | yes | Whether at least one non-alphanumeric character is required |

<a id="schema-patchpreferencesrequest"></a>
#### PatchPreferencesRequest

Partial-update request for user preferences. Only non-null fields are applied; null means "leave unchanged". Use UserPreferences for full reads.

| Field | Type | Required | Description |
|---|---|---|---|
| `theme` | string? |  |  |
| `locale` | string? |  |  |
| `favouriteAppSlugs` | string[]? |  |  |
| `pinnedShardIds` | string[]? |  |  |
| `notifyOnStateChange` | boolean? |  |  |
| `notifyOnUpdateAvailable` | boolean? |  |  |
| `compactShardHeader` | boolean? |  |  |
| `hideUnroutedShards` | boolean? |  |  |
| `hidePrivatePorts` | boolean? |  |  |
| `fileDetailsWidth` | integer (int32) |  |  |
| `ampExperience` | null or [UserExperienceLevel](#schema-userexperiencelevel) |  |  |
| `showTutorials` | boolean? |  |  |
| `tutorialPromptDismissed` | boolean? |  |  |
| `tutorialsCompleted` | string[]? |  |  |
| `accessibilityHintsEnabled` | boolean? |  |  |
| `onboardingCompleted` | boolean? |  |  |
| `reducedMotion` | boolean? |  |  |
| `analyticsEnabled` | boolean? |  |  |
| `streamerMode` | boolean? |  |  |
| `timeFormat` | null or [TimeFormat](#schema-timeformat) |  |  |

<a id="schema-patchshardbody"></a>
#### PatchShardBody

Body for patching a shard's own record; omitted fields are left unchanged

| Field | Type | Required | Description |
|---|---|---|---|
| `displayName` | string? | yes | The friendly name shown throughout the UI |
| `description` | string? | yes | Free-text note about what this shard is for |
| `tags` | string[]? | yes | Free-form labels for grouping and searching |

<a id="schema-pathbody"></a>
#### PathBody

A single shard-relative path

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Path relative to the shard working directory (forward-slash separated) |

<a id="schema-pathsbody"></a>
#### PathsBody

A set of shard-relative paths (files and/or folders)

| Field | Type | Required | Description |
|---|---|---|---|
| `paths` | string[] | yes | Paths relative to the shard working directory. Folders are included recursively |

<a id="schema-platformwarning"></a>
#### PlatformWarning

A standing condition of the installation as the overview shows it

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | The identity of this occurrence: the code and the parameters that make it a new one |
| `code` | string | yes | The translation code |
| `params` | map<string, string> | yes | The parameters the translation takes |
| `message` | string | yes | The English text, for a client with no translation for the code |
| `dismissable` | boolean | yes | True when the condition is informational and the person may dismiss it once read; false when it needs action and stands until it is resolved |

<a id="schema-portchangereason"></a>
#### PortChangeReason

What happened to one port an import wanted to keep

Type: integer

<a id="schema-portoutcome"></a>
#### PortOutcome

The fate of one port. int? PortOutcome.Requested is null when the import found nothing to preserve, which is a different thing from a discovered port that could not be kept and reads differently to the user.

| Field | Type | Required | Description |
|---|---|---|---|
| `ref` | string | yes | The port requirement's reference key |
| `requested` | integer (int32) | yes | The discovered port, if there was one |
| `assigned` | integer (int32) | yes | The port actually assigned |
| `reason` | [PortChangeReason](#schema-portchangereason) | yes | Why it ended up there |
| `moved` | boolean |  | This port moved from where the user had it, and therefore has to be reported |

<a id="schema-portprotocol"></a>
#### PortProtocol

Transport protocol a required port uses

Type: object enum: `TCP`, `UDP`, `Both`

<a id="schema-portpublicationstatus"></a>
#### PortPublicationStatus

Live publication state + traffic counters, written by the FRONT spire's reconciler under a separate status key (frequent writes never race user edits of the declaration).

| Field | Type | Required | Description |
|---|---|---|---|
| `publicationId` | string (uuid) |  |  |
| `state` | [PublicationState](#schema-publicationstate) |  |  |
| `targetSpireId` | string (uuid) |  | The concrete target the proxy currently relays to (diagnostics; may lag a move) |
| `resolvedPort` | integer (int32) |  |  |
| `bytesIn` | integer (int64) |  | Cumulative relayed bytes since the listener last started |
| `bytesOut` | integer (int64) |  |  |
| `activeFlows` | integer (int32) |  | Live TCP connections + UDP flows currently in the table |
| `tunnelPhase` | null or [TunnelPhase](#schema-tunnelphase) |  |  |
| `faultSpireId` | string? (uuid) |  | The spire at fault for a PublicationState.TunnelFault (or the end still connecting), so the UI can name it; else null |
| `error` | string? |  |  |
| `updatedAt` | string (date-time) |  |  |

<a id="schema-portreferences"></a>
#### PortReferences

Names which declared port a feature should use, by referencing a PortRequirement by its Ref. Lets features like remote admin or sleep-mode point at the right port without hard-coding a number.

| Field | Type | Required | Description |
|---|---|---|---|
| `adminPortRef` | string |  | Ref of the port used for remote administration |
| `primaryApplicationPortRef` | string |  | Ref of the port users connect to for the application itself |
| `universalSleepApplicationUDPPortRef` | string |  | Ref of the UDP port watched to wake a sleeping application |
| `universalSleepSteamQueryPortRef` | string |  | Ref of the Steam query port watched to wake a sleeping application |

<a id="schema-portrequirement"></a>
#### PortRequirement

A network port (or contiguous range) the application needs, with the role it serves. Ported from the AMP 2 specification.

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string |  | Human-readable name for this port (e.g. "Game Port") |
| `description` | string |  | Explanation of what this port is used for |
| `ref` | string |  | Reference key used to bind this port to a setting/template token (e.g. "PrimaryApplicationPort") |
| `port` | integer (int32) |  | The default port number |
| `protocol` | [PortProtocol](#schema-portprotocol) |  | Transport protocol this port uses |
| `offset` | integer (int32) |  | Fixed offset added to the base port when allocating (for derived ports) |
| `range` | integer (int32) |  | Number of consecutive ports to reserve starting at int PortRequirement.Port |
| `rangeSeparator` | string |  | What joins first and last in the `$<Ref>Range` template variable, for a game that spells a port span with something other than a hyphen. Only affects that variable. |
| `required` | boolean |  | Whether the app cannot run without this port |
| `childPorts` | [PortRequirement](#schema-portrequirement)[]? |  | Additional ports derived from and allocated alongside this one |

<a id="schema-preflightbatchblock"></a>
#### PreflightBatchBlock

Why the whole batch cannot proceed, whatever the individual items say

Type: integer

<a id="schema-preflightblock"></a>
#### PreflightBlock

One candidate that cannot be imported, and why

| Field | Type | Required | Description |
|---|---|---|---|
| `candidatePath` | string | yes | The directory |
| `reason` | [PreflightBlockReason](#schema-preflightblockreason) | yes | What is stopping it |
| `detail` | string | yes | The specific evidence, phrased for the user |

<a id="schema-preflightblockreason"></a>
#### PreflightBlockReason

Why one candidate cannot be imported right now

Type: integer

<a id="schema-preflightcandidate"></a>
#### PreflightCandidate

One candidate as the pre-flight sees it

| Field | Type | Required | Description |
|---|---|---|---|
| `candidatePath` | string | yes | The directory |
| `size` | [CandidateSize](#schema-candidatesize) | yes | How much disk it needs |
| `liveness` | [LivenessEvidence](#schema-livenessevidence)[] | yes | Evidence that it is still running, if any |
| `exists` | boolean |  | Whether it is still there Default `true`. |
| `unavailableReason` | string? |  | Why the source could not be examined at all, in the words of whatever was asked to examine it, when that was a reader other than this process and it declined or did not answer. Blocks the candidate as unreadable, with that reason shown. |
| `alreadyImportedAs` | string? |  | The name of the live shard that already carries this directory's id, when one does |

<a id="schema-preflightreport"></a>
#### PreflightReport

The answer: what will import, what will not, and what the shortfall is if the batch cannot go ahead.

| Field | Type | Required | Description |
|---|---|---|---|
| `ready` | [PreflightCandidate](#schema-preflightcandidate)[] | yes | Candidates that can be imported |
| `blocked` | [PreflightBlock](#schema-preflightblock)[] | yes | Candidates that cannot, each with its reason |
| `requiredBytes` | integer (int64) | yes | Total disk the ready candidates need |
| `availableBytes` | integer (int64) | yes | Free space on the destination |
| `batchBlock` | [PreflightBatchBlock](#schema-preflightbatchblock) | yes | Why the whole batch cannot proceed, if it cannot |
| `shortfallBytes` | integer (int64) | yes | How much more space is needed. Zero unless space is the problem |
| `licenceHeadroom` | integer (int32) | yes | How many more servers the licence allows, or null when unlimited |
| `sizesAreUnderstated` | boolean | yes | At least one size is a floor, so the space check is optimistic |
| `links` | integer (int32) | yes | Symbolic links across the ready candidates, none of which the copy follows |
| `canProceed` | boolean |  | Whether Import can be pressed |

<a id="schema-previewbody"></a>
#### PreviewBody

Request body to preview when a trigger would fire, without saving anything

| Field | Type | Required | Description |
|---|---|---|---|
| `trigger` | [Trigger](#schema-trigger) | yes | The trigger to project fire times for |

<a id="schema-previewstatus"></a>
#### PreviewStatus

What a preview build has to say about itself, carried on every status so the panel can show its countdown without a second endpoint. Null on a release build, which is how the client knows to render none of it.

| Field | Type | Required | Description |
|---|---|---|---|
| `expiresAt` | string? (date-time) | yes | When this build stops working |
| `daysRemaining` | integer (int32) | yes | Days left, measured against the verified time rather than this     machine's clock. Null until the first verification answers |
| `reason` | string? | yes | Why the build is blocked (`expired`, `clockWrong`,     `unreachable`, `unstamped`); null while it is running normally |
| `update` | null or [PreviewUpdateStatus](#schema-previewupdatestatus) | yes |  |

<a id="schema-previewupdatestatus"></a>
#### PreviewUpdateStatus

Where a preview build stands on moving to a newer build, as the client reads it.

| Field | Type | Required | Description |
|---|---|---|---|
| `state` | string | yes | `current` (nothing newer), `available` (a newer build exists), `installing`     (an expired build is staging it; the panel relaunches when done) or `failed` (it could not) |
| `version` | string? | yes | The newer version, when there is one |
| `stream` | string? | yes | The channel it was found on, when there is one |
| `reason` | string? | yes | The coded reason for `failed`, an `updates.error.*` code |

<a id="schema-principalkind"></a>
#### PrincipalKind

Discriminates how a TaskPrincipal was constructed. Stored on every audit event so security tooling can filter by kind without having to infer intent from the presence/absence of fields.

Type: integer

<a id="schema-problemdetails"></a>
#### ProblemDetails

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string? |  |  |
| `title` | string? |  |  |
| `status` | integer (int32) |  |  |
| `detail` | string? |  |  |
| `instance` | string? |  |  |

<a id="schema-processorinfo"></a>
#### ProcessorInfo

CPU topology + identity, mirrors AMP 2's ProcessorInfo

| Field | Type | Required | Description |
|---|---|---|---|
| `sockets` | integer (int32) |  | Number of physical CPU sockets (packages) on the board |
| `cores` | integer (int32) |  | Physical cores per socket |
| `threads` | integer (int32) |  | Logical threads per socket (cores times SMT/hyper-threading factor) |
| `modelName` | string |  | Marketing/model string, e.g. "AMD Ryzen 9 5950X" |
| `vendor` | string |  | CPU vendor string, e.g. "GenuineIntel", "AuthenticAMD" |
| `totalCores` | integer (int32) |  | Total physical cores across all sockets |
| `totalThreads` | integer (int32) |  | Total logical threads across all sockets |

<a id="schema-profiledto"></a>
#### ProfileDto

The authenticated caller's own profile

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | The user's unique identifier |
| `userName` | string? | yes | The login user name |
| `displayName` | string? | yes | The friendly display name, if set |
| `email` | string? | yes | The email address, if set |
| `emailConfirmed` | boolean | yes | Whether the email address has been confirmed |

<a id="schema-profilesanswer"></a>
#### ProfilesAnswer

What their game accounts say about some users

| Field | Type | Required | Description |
|---|---|---|---|
| `steam` | map<string, [SteamProfile](#schema-steamprofile)> | yes | By user key, for each one the caller may identify who is known by a Steam64 id and found on Steam |
| `minecraft` | map<string, [MinecraftProfile](#schema-minecraftprofile)> | yes | By user key, the same for Minecraft UUIDs found with Mojang |
| `state` | string | yes | How far the lookups got: `complete`; `pending` (some are still to be looked up, a few at a time); `keyRejected` (Steam refused the Web API key, so the slower way was used); `unreachable`; `off` (the lookups are turned off in the settings); or `notLicensed` (the licence does not include account profiles: AMP Professional Edition and above) |
| `isDemo` | boolean |  | Invented people, with invented profiles: there is nothing to link to Default `false`. |

<a id="schema-publicationdto"></a>
#### PublicationDto

A public-port to shard-port mapping with its last reported status

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) | yes | Publication identifier |
| `frontSpireId` | string (uuid) | yes | Publicly routable spire that listens on the public port |
| `publicPort` | integer (int32) | yes | Public port bound on the front spire |
| `protocol` | string | yes | Protocol name (TCP/UDP/Both) |
| `shardId` | string (uuid) | yes | Server whose port is published |
| `portRef` | string | yes | Named port on the server that is exposed |
| `enabled` | boolean | yes | Whether the publication is currently enabled |
| `createdAt` | string (date-time) | yes | When the publication was created |
| `status` | null or [PortPublicationStatus](#schema-portpublicationstatus) | yes |  |

<a id="schema-publicationstate"></a>
#### PublicationState

Where a publication is in its (continuously reconciled) lifecycle. String-serialized so the API and UI speak state NAMES (the app otherwise serializes enums as integers), which the canvas maps straight to CSS state classes + i18n keys.

Type: object enum: `Pending`, `Active`, `TunnelProvisioning`, `TunnelDown`, `ShardMissing`, `PortRefMissing`, `PortConflict`, `Disabled`, `Error`, `PortPrivate`, `TunnelFault`, `TunnelUnavailable`, `ShardStopped`

<a id="schema-publichostoption"></a>
#### PublicHostOption

One address a machine could be reached on, with what is known about it.

| Field | Type | Required | Description |
|---|---|---|---|
| `host` | string | yes | The address or name to dial |
| `kind` | string | yes | "internal" (RFC1918 and friends) or "external" - NOT whether it is reachable |
| `interfaceName` | string | yes | The adapter it sits on, or empty when it isn't one of the host's own |
| `isPrimary` | boolean | yes | True when the host routes out through this address |
| `suggested` | boolean | yes | True when this is the one to offer first; see HostPreference |
| `note` | string? |  | Why it may not work as-is: "needsForward" or "cgnat" |

<a id="schema-publickeycredentialtype"></a>
#### PublicKeyCredentialType

Type: object enum: `public-key`, `invalid`, `None`

<a id="schema-purgeorphanbody"></a>
#### PurgeOrphanBody

Names the orphan directory leaf to purge from a datastore

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | The plain directory leaf name (no separators or traversal) |

<a id="schema-rconipselectmethod"></a>
#### RCONIPSelectMethod

Which local address AMP connects to when talking to the app's remote console

Type: integer

<a id="schema-readyindicator"></a>
#### ReadyIndicator

How AMP decides a starting application has finished starting and is ready to accept users

Type: integer

<a id="schema-rearmbody"></a>
#### ReArmBody

Optional new lifetime when re-arming an invite

| Field | Type | Required | Description |
|---|---|---|---|
| `expiresInHours` | integer (int32) | yes | New lifetime in hours. Clamped between 1 and 720 (30 days); defaults to 72 |

<a id="schema-reauthrequest"></a>
#### ReauthRequest

The caller's password, offered again from a live session to vouch for a sensitive change

| Field | Type | Required | Description |
|---|---|---|---|
| `password` | string | yes | The account's current password |

<a id="schema-recoverycodesdto"></a>
#### RecoveryCodesDto

A freshly generated set. The only time the plaintext exists anywhere

| Field | Type | Required | Description |
|---|---|---|---|
| `codes` | string[] | yes | The codes, in the grouped form the user should write down |

<a id="schema-recoverystatusdto"></a>
#### RecoveryStatusDto

How many recovery codes the caller has left

| Field | Type | Required | Description |
|---|---|---|---|
| `remaining` | integer (int32) | yes | Unused codes still available |
| `total` | integer (int32) | yes | How many are issued in a full set, so the UI can show a proportion |
| `generatedAt` | string? (date-time) | yes | When the current set was issued, or null if there has never been one |
| `low` | boolean | yes | Whether the caller should be nudged to regenerate. Computed here rather than in the client so every surface agrees on what "running low" means. |

<a id="schema-registercompleterequest"></a>
#### RegisterCompleteRequest

The attestation coming back from the browser

| Field | Type | Required | Description |
|---|---|---|---|
| `flowId` | string (uuid) | yes | The handle returned by the begin call |
| `response` | [AuthenticatorAttestationRawResponse](#schema-authenticatorattestationrawresponse) | yes | The raw WebAuthn attestation response |
| `name` | string |  | The label to give the credential Default `""`. |

<a id="schema-registeredpasskeydto"></a>
#### RegisteredPasskeyDto

A passkey that was just registered, and the session to carry on with

| Field | Type | Required | Description |
|---|---|---|---|
| `passkey` | [PasskeySummary](#schema-passkeysummary) | yes | The registered passkey |
| `session` | null or [AuthResponse](#schema-authresponse) | yes |  |

<a id="schema-rejectedgatewayroute"></a>
#### RejectedGatewayRoute

| Field | Type | Required | Description |
|---|---|---|---|
| `route` | [GatewayRoute](#schema-gatewayroute) |  |  |
| `reason` | string |  |  |

<a id="schema-rejectedinterface"></a>
#### RejectedInterface

| Field | Type | Required | Description |
|---|---|---|---|
| `interface` | [WireGuardInterface](#schema-wireguardinterface) |  |  |
| `reason` | string |  |  |

<a id="schema-rejectedpeer"></a>
#### RejectedPeer

| Field | Type | Required | Description |
|---|---|---|---|
| `peer` | [WireGuardPeer](#schema-wireguardpeer) |  |  |
| `reason` | string |  |  |

<a id="schema-rejectedrule"></a>
#### RejectedRule

| Field | Type | Required | Description |
|---|---|---|---|
| `rule` | [FirewallRule](#schema-firewallrule) |  |  |
| `reason` | string |  |  |

<a id="schema-releasestate"></a>
#### ReleaseState

How complete and reliable a template is, shown to users choosing what to deploy

Type: integer

<a id="schema-renamerequest"></a>
#### RenameRequest

A new label for an existing passkey

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | The label. Trimmed, and capped at 64 characters |

<a id="schema-replacesecretbody"></a>
#### ReplaceSecretBody

A new secret for a credential

| Field | Type | Required | Description |
|---|---|---|---|
| `secret` | string | yes | The password, webhook URL or token; sealed at rest and never returned |

<a id="schema-reportedipinfo"></a>
#### ReportedIpInfo

A single IP address a spire host holds, with routing/origin facts

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string |  | The IP address in string form, with any IPv6 %zone scope suffix stripped |
| `isV6` | boolean |  | True when this is an IPv6 address, false for IPv4 |
| `isInternal` | boolean |  | Private/link-local/ULA range (RFC1918, 169.254/16, fc00::/7, fe80::/10) |
| `isDhcp` | boolean? |  | True = DHCP/SLAAC-assigned, false = statically configured, null = undeterminable on this platform |
| `isPrimary` | boolean |  | The address outgoing traffic uses by default (routes to the gateway) |
| `isTunnel` | boolean |  | Belongs to a VPN/tunnel interface (tun/tap/wg/tailscale/zerotier/ppp…) |
| `interfaceName` | string |  | Owning interface name, for tooltips/diagnostics |

<a id="schema-resetpasswordbody"></a>
#### ResetPasswordBody

New password for an administrative reset

| Field | Type | Required | Description |
|---|---|---|---|
| `newPassword` | string | yes | The replacement password. Must satisfy the configured password policy |

<a id="schema-retrypolicy"></a>
#### RetryPolicy

| Field | Type | Required | Description |
|---|---|---|---|
| `maxAttempts` | integer (int32) |  |  |
| `initialDelay` | string |  |  |
| `backoffMultiplier` | number/string (double) |  | Exponential backoff multiplier applied per attempt. 1.0 = fixed delay, 2.0 = doubles each time. |
| `maxDelay` | string |  |  |

<a id="schema-revokebody"></a>
#### RevokeBody

| Field | Type | Required | Description |
|---|---|---|---|
| `reason` | string? | yes | Why it's being withdrawn - kept on the tombstone for the audit trail |

<a id="schema-roledto"></a>
#### RoleDto

A named role and the permission-node grants it carries

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Stable unique identifier for the role |
| `name` | string? | yes | Display name of the role. Unique across the tenant |
| `description` | string? | yes | Optional human description |
| `permissions` | string[] | yes | The role's permission-node grants (see PermissionCatalog) |
| `ownerUserId` | string? | yes | Id of the user who created the role, if recorded |
| `createdAt` | string (date-time) | yes | When the role was created |

<a id="schema-routehealth"></a>
#### RouteHealth

Type: object enum: `Ok`, `Connecting`, `Fault`, `None`

<a id="schema-scanrequest"></a>
#### ScanRequest

Where to look for servers that were set up outside AMP

| Field | Type | Required | Description |
|---|---|---|---|
| `roots` | string[] | yes | Absolute paths on the target spire to search beneath. Ignored when Automatic is set |
| `automatic` | boolean |  | Search the roots the spire finds for itself instead. The paths are never sent by the caller, so the tool each result is attributed to is something the spire established rather than something it was told. Default `false`. |

<a id="schema-schedulebody"></a>
#### ScheduleBody

Request body to create or replace a schedule definition

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Display name for the schedule. Required (rejected if blank) |
| `description` | string? | yes | Optional free-text description; trimmed, stored as empty when omitted |
| `trigger` | [Trigger](#schema-trigger) | yes | What fires the schedule: a cron, interval, or event trigger |
| `steps` | [Step](#schema-step)[] | yes | The ordered step tree executed on each firing (tasks, conditions, switches) |
| `timeout` | integer? | yes | Per-run time budget. Clamped to between one minute and two hours; defaults to 30 minutes when omitted |
| `enabled` | boolean | yes | Whether the schedule is armed. Disabled schedules are stored but never fire |

<a id="schema-scheduledefinition"></a>
#### ScheduleDefinition

Durable definition of a shard's scheduled behaviour, stored in NATS KV at `amp.tn.{tenant}.sched.{id}`: a trigger (when) plus a step TREE (what) - see Step. Executions are separate JobExecution records; live health is the separate ScheduleStatus key so frequent status writes never race user edits of the definition.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `shardId` | string (uuid) |  | The shard this schedule belongs to (v1: schedules are shard-scoped) |
| `name` | string |  |  |
| `description` | string |  |  |
| `trigger` | [Trigger](#schema-trigger) |  |  |
| `steps` | [Step](#schema-step)[] |  | The behaviour tree executed on each firing |
| `retryPolicy` | [RetryPolicy](#schema-retrypolicy) |  |  |
| `misfirePolicy` | [MisfirePolicy](#schema-misfirepolicy) |  |  |
| `timeout` | string |  | Maximum wall-clock time allowed for a single execution (all lanes) before it is considered timed out. |
| `enabled` | boolean |  |  |
| `createdAt` | string (date-time) |  |  |
| `updatedAt` | string (date-time) |  |  |
| `createdByUserId` | string |  | Who originally created the schedule (audit/display only) |
| `runAsUserId` | string |  | The identity the schedule ACTS AS: its steps execute under this user's LIVE permissions, so a schedule can never perform something its author couldn't do directly. Set to the user on every create AND update (the last editor authors what runs, so editing someone else's schedule can't be an escalation path). A missing value fails guarded actions closed. |

<a id="schema-scheduledto"></a>
#### ScheduleDto

A schedule paired with its latest run status and next projected fire time

| Field | Type | Required | Description |
|---|---|---|---|
| `definition` | [ScheduleDefinition](#schema-scheduledefinition) | yes | The stored schedule definition. For a caller without `ScheduleWrite`, every parameter listed in Withheld reads string SchedulesController.WithheldPlaceholder instead of its value |
| `status` | null or [ScheduleStatus](#schema-schedulestatus) | yes |  |
| `nextFireTime` | string? (date-time) | yes | Next projected fire time, or null when the schedule is disabled |
| `withheld` | [WithheldParameter](#schema-withheldparameter)[] | yes | The step parameters whose values this response withholds; empty for a caller who holds `ScheduleWrite` |

<a id="schema-schedulestatus"></a>
#### ScheduleStatus

Last-run health for a schedule, written by the consumer after every run at `amp.tn.{tenant}.sched.{id}.status`. Separate from the definition so frequent status writes never race user edits. Drives the trigger list's warning badge - AMP 2 parity: a failure is visible ON the trigger itself.

| Field | Type | Required | Description |
|---|---|---|---|
| `scheduleId` | string (uuid) |  |  |
| `lastRunAt` | string? (date-time) |  |  |
| `lastRunStatus` | null or [JobStatus](#schema-jobstatus) |  |  |
| `lastError` | string? |  |  |
| `lastExecutionId` | string? (uuid) |  |  |
| `consecutiveFailures` | integer (int32) |  |  |
| `turnedOff` | null or [ApiError](#schema-apierror) |  |  |
| `refusal` | null or [StepRefusal](#schema-steprefusal) |  |  |
| `refusalAudited` | boolean |  | Whether a refused step has been audited since the schedule was last edited. A destination that is refused on every firing is recorded once, not once per firing. |

<a id="schema-secondfactorrequest"></a>
#### SecondFactorRequest

Second step of a sign-in that needs more than a password

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | yes | The account's username |
| `password` | string | yes | The same password from the first step, re-presented so this call stands on its own |
| `code` | string | yes | One recovery code |
| `rememberMe` | boolean |  | Carried through from the first step Default `false`. |

<a id="schema-serverrulesource"></a>
#### ServerRuleSource

What asked for a server-derived firewall rule

Type: object enum: `Shard`, `Publication`, `Gateway`

<a id="schema-serverusage"></a>
#### ServerUsage

How busy one server was

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) | yes |  |
| `sessions` | integer (int32) | yes |  |
| `sessionsPercent` | number/string (double) | yes |  |
| `minutes` | number/string (double) | yes |  |
| `minutesPercent` | number/string (double) | yes |  |
| `users` | integer (int32) | yes | Distinct people. Exact from the records; from the totals, the merged estimate held between what is known for certain, as the headline figure is. |

<a id="schema-sessionbucket"></a>
#### SessionBucket

One point of the activity graph: how many people were on at once during it

| Field | Type | Required | Description |
|---|---|---|---|
| `start` | string (date-time) | yes |  |
| `end` | string (date-time) | yes |  |
| `users` | integer (int32) | yes |  |

<a id="schema-sessiondto"></a>
#### SessionDto

One active login session belonging to the caller

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) | yes | The session's unique identifier |
| `createdAt` | string (date-time) | yes | When the session was established |
| `lastSeenAt` | string (date-time) | yes | When the session was last active |
| `expiresAt` | string (date-time) | yes | When the session expires |
| `remoteIp` | string | yes | The remote IP address the session was created from |
| `userAgent` | string | yes | The user agent string captured at creation |
| `isCurrent` | boolean | yes | Whether this is the session making the request |

<a id="schema-sessionsummary"></a>
#### SessionSummary

Everything one query answers

| Field | Type | Required | Description |
|---|---|---|---|
| `window` | [SessionWindow](#schema-sessionwindow) | yes |  |
| `current` | [SessionTotals](#schema-sessiontotals) | yes |  |
| `previous` | [SessionTotals](#schema-sessiontotals) | yes |  |
| `change` | map<string, number/string? (double)> | yes | Percentage change against the previous period, per figure; null where the previous period had nothing to compare against |
| `activity` | [SessionBucket](#schema-sessionbucket)[] | yes |  |
| `countries` | [CountryUsage](#schema-countryusage)[] | yes |  |
| `topUsers` | [TopUser](#schema-topuser)[] | yes |  |
| `busiest` | null or [SessionBucket](#schema-sessionbucket) | yes |  |
| `quietest` | null or [SessionBucket](#schema-sessionbucket) | yes |  |
| `servers` | [ServerUsage](#schema-serverusage)[] | yes | The servers the sessions were on, busiest first |
| `games` | [GameUsage](#schema-gameusage)[] | yes | The games those servers run, busiest first; empty when no game was given for them |
| `isDemo` | boolean |  | These numbers were invented, because this installation's licence does not include analytics, or because a developer asked for them. The page says so plainly; nothing here was ever recorded. Default `false`. |
| `serverInfo` | map<string, [AnalyticsServer](#schema-analyticsserver)> |  | Every server the answer could have covered, by id, for the filter and for drawing the breakdown. |
| `rule` | null or [ActivityRule](#schema-activityrule) |  |  |

<a id="schema-sessiontotals"></a>
#### SessionTotals

The headline numbers for one period

| Field | Type | Required | Description |
|---|---|---|---|
| `sessions` | integer (int32) | yes |  |
| `uniqueUsers` | integer (int32) | yes |  |
| `newUsers` | integer (int32) | yes |  |
| `minutes` | number/string (double) | yes |  |
| `bounceRatePercent` | number/string (double) | yes |  |
| `averageSessionMinutes` | number/string (double) | yes |  |
| `sessionsPerUser` | number/string (double) | yes |  |
| `longestSessionMinutes` | number/string (double) | yes |  |
| `activeUsers` | integer (int32) |  | People who were active users at the end of the period (see ActivityRule) Default `0`. |
| `activeRatePercent` | number/string (double) |  | Of everyone who visited in the rule's span before the end of the period, how many came often enough to count as active: the share of a server's recent visitors who keep coming back. Default `0`. |

<a id="schema-sessionview"></a>
#### SessionView

One user's time on one shard, as the pair of records describes it

| Field | Type | Required | Description |
|---|---|---|---|
| `sessionRef` | string (uuid) | yes |  |
| `shardId` | string (uuid) | yes |  |
| `userKey` | string | yes |  |
| `idKind` | string | yes |  |
| `country` | string | yes |  |
| `startedAt` | string (date-time) | yes |  |
| `endedAt` | string (date-time) | yes |  |
| `running` | boolean | yes | The session had not ended when the records were read |
| `displayName` | string? |  |  |

<a id="schema-sessionwindow"></a>
#### SessionWindow

The period a query asks about

| Field | Type | Required | Description |
|---|---|---|---|
| `from` | string (date-time) |  |  |
| `to` | string (date-time) |  |  |

<a id="schema-setportaccessbody"></a>
#### SetPortAccessBody

Request body for setting a port's public-access flag

| Field | Type | Required | Description |
|---|---|---|---|
| `public` | boolean | yes | True to mark the port publicly accessible, false to make it private |

<a id="schema-setshardcredentialbody"></a>
#### SetShardCredentialBody

Which shared credential the shard's updates sign in with

| Field | Type | Required | Description |
|---|---|---|---|
| `credentialId` | string (uuid) | yes | A credential the caller may use: their own or a Public one |

<a id="schema-setstreamrequest"></a>
#### SetStreamRequest

The channel to follow from now on, by its manifest name

| Field | Type | Required | Description |
|---|---|---|---|
| `stream` | string | yes | Mainline (retail), Preview, or Development where the installation is already on it |

<a id="schema-settingdeployment"></a>
#### SettingDeployment

Whether a setting applies to containerised shards, native ones, or both.               Evaluated per SHARD rather than per template: a template with DockerRequired=False can be deployed both ways on the same spire, so one spec has to present differently depending on how this particular shard was actually deployed.

Type: integer

<a id="schema-settingsactioninfo"></a>
#### SettingsActionInfo

One renderable settings inline action

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Stable action identifier the UI dispatches when the action is invoked |
| `category` | string | yes | The settings category the action is grouped under |

<a id="schema-settingsavailability"></a>
#### SettingsAvailability

What an import of one candidate could recover, and what it could not. Built before anything is copied, so the results screen can be honest while the user is still deciding rather than explaining afterwards.

Type: integer

<a id="schema-settingsmanifest"></a>
#### SettingsManifest

The full settings manifest the UI renders from: specs (metadata), current values keyed by node id, which secret nodes have a stored value, and the available inline actions. DisplayName/Description/EnumValues in the specs are TRANSLOCO KEYS - the frontend translates before rendering.

| Field | Type | Required | Description |
|---|---|---|---|
| `specs` | [SettingSpec](#schema-settingspec)[] | yes | Per-node metadata (input type, category, i18n keys, order) the UI renders each control from |
| `values` | map<string, string> | yes | Current node values keyed by node id, each transported as a string |
| `secretHasValue` | map<string, boolean> | yes | For secret nodes, whether a value is stored (the value itself is never sent) |
| `actions` | [SettingsActionInfo](#schema-settingsactioninfo)[] | yes | The inline actions available across the settings sections |
| `unavailable` | map<string, string> |  | Nodes this machine cannot take a value for, each with the locale key that says why: a node this build holds at a fixed value (PlatformSettingsLocks), or one the machine's role rules out. The UI shows such a node read-only with that sentence, and a write to it is refused. |

<a id="schema-settingspec"></a>
#### SettingSpec

One configurable setting exposed for an application: where its value comes from and goes (command line and/or config file), how it is validated, and how it is presented in the UI.

| Field | Type | Required | Description |
|---|---|---|---|
| `fieldName` | string |  | The setting's identifier, used as its config/command-line key unless string SettingSpec.ParamFieldName overrides it |
| `paramFieldName` | string |  | Optional alternate key to emit on disk / on the command line instead of string SettingSpec.FieldName |
| `effectiveKey` | string? |  | The setting's on-disk / on-command-line key: string SettingSpec.ParamFieldName when set, otherwise string SettingSpec.FieldName. The single definition used by config-file generation, template substitution, and command-line building so the key never derives differently across them. |
| `required` | boolean |  | The user must provide a value for this setting |
| `hidden` | boolean |  | The setting is not shown in the UI (still applied from its default/stored value) |
| `platform` | [HostPlatform](#schema-hostplatform) |  | Host platforms this setting applies to; mirrors a stage's UpdateSourcePlatform |
| `deployment` | [SettingDeployment](#schema-settingdeployment) |  | Whether this setting applies to containerised shards, native ones, or both |
| `conditionSetting` | string |  | Setting key that gates this one; shown only when its value matches string SettingSpec.ConditionValue. Mirrors a stage's UpdateSourceConditionSetting |
| `conditionValue` | string |  | Value that string SettingSpec.ConditionSetting must equal for this setting to be shown |
| `defaultValue` | string? |  | Value used when the user has not set one. Null when the template declares none, which is distinct from an empty default: a setting with no default and no value is left out of a generated config file, so the application applies its own default. |
| `meta` | string |  | Free-form metadata carried through from the manifest |
| `category` | string |  | Top-level grouping used to organise the settings UI |
| `subcategory` | string |  | Secondary grouping within string SettingSpec.Category |
| `special` | string |  | Special handling marker (e.g. "array:text", "array:number", "listfile", "fileexists") that changes how the value is written |
| `displayName` | string |  | Label shown for this setting in the UI |
| `description` | string |  | Help text explaining what the setting does |
| `keywords` | string |  | Extra search terms that help users find this setting |
| `inputType` | string |  | The input control to render (e.g. text, number, checkbox, enum) |
| `isSecret` | boolean |  | The value is a secret (token, API key, connection string, ...). Secret values are masked on read and never returned to the client in the clear, independent of string SettingSpec.InputType. A `password` input is treated as secret automatically; set this for secret-bearing settings that render as plain text. |
| `placeholder` | string |  | Placeholder text shown in an empty input field |
| `suffix` | string |  | Unit or suffix shown after the input (e.g. "MB", "seconds") |
| `order` | integer (int32) |  | Sort order within its category; lower values appear first |
| `readOnly` | boolean |  | Displayed but not editable (platform settings manifest) |
| `isFlagArgument` | boolean |  | The setting is a presence flag: it emits string SettingSpec.FlagValue when enabled rather than a key/value pair |
| `flagValue` | string |  | The literal text emitted on the command line when bool SettingSpec.IsFlagArgument is set and enabled |
| `includeInCommandLine` | boolean |  | Include this setting when building the command line |
| `includeInConfigFile` | boolean |  | Include this setting when generating config files |
| `skipIfEmpty` | boolean |  | Omit this setting entirely when its value is empty |
| `enumValues` | map<string, string> |  | Allowed values for an enum-style setting: stored value → display label |
| `valueSourceId` | string |  | Id of the VersionSource this setting's options come from, for a value list              that is read from an upstream manifest rather than fixed in the template.               The list is NOT delivered with the settings payload: the client asks for it when the control is actually opened. A page carrying a dozen version pickers would otherwise fan out a dozen upstream fetches nobody asked for. So a setting with this set arrives with its current value and this id, and nothing else. |
| `minValue` | number/string? (float) |  | Minimum allowed numeric value; null means no lower bound |
| `maxValue` | number/string? (float) |  | Maximum allowed numeric value; null means no upper bound. See bool SettingSpec.IsHostCap |
| `isHostCap` | boolean |  | True when MaxValue was imposed by the host's infra cap (e.g. MaxUsersCap), not by the application manifest. The UI uses this to show "Capped at X by the host" vs. a plain valid-range hint when the manifest just declares the setting's natural bounds. |
| `multiplier` | number/string? (float) |  | NOT IMPLEMENTED - parsed from the manifest and currently read by nothing. In AMP 2 this scales the STORED value on its way OUT to the config file / command line (store minutes, emit milliseconds); the UI never sees it. Implementing it means applying it in `ctx.ResolveFieldValue` AND inverting it in `ConfigFileImporter` - it is the one attribute that makes the write path non-idempotent, so without the divide, every import of a merged config file would inflate the value by the multiplier again. |
| `multipleOf` | number/string? (float) |  | The value should be a multiple of this number. Currently only drives the number input's `step` attribute in the UI - AMP 2 additionally rounds to the nearest multiple when writing, and uses whether this is an integer or a float to decide the JSON node's type. |
| `maxLength` | integer (int32) |  | Maximum string length; -1 means unlimited |
| `listValuePrefix` | string |  | Text prepended to each item when writing a list-valued setting |
| `listValueSeparator` | string |  | Separator placed between items when writing a list-valued setting |
| `excludeFromImport` | boolean |  | Do not import a value for this setting when importing an existing installation |

<a id="schema-settingssaveresult"></a>
#### SettingsSaveResult

What a save answers when something that followed from it could not be done; the change itself stands

| Field | Type | Required | Description |
|---|---|---|---|
| `notices` | [ApiError](#schema-apierror)[] | yes | Coded sentences for the user, each one a thing still to put right |

<a id="schema-setupadminrequest"></a>
#### SetupAdminRequest

Details for creating the initial administrator account during setup

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | yes | Username for the new administrator |
| `email` | string | yes | Email address for the new administrator |
| `password` | string | yes | Password for the new administrator; must satisfy the Identity password policy |

<a id="schema-setupnetwork"></a>
#### SetupNetwork

What the setup wizard's network step shows

| Field | Type | Required | Description |
|---|---|---|---|
| `probe` | [NetworkProbe](#schema-networkprobe) | yes | How this machine reaches the internet |
| `busExpandable` | boolean | yes | True when AMP manages the bus, so the step can offer to let other machines join |
| `busPublic` | boolean | yes | True when other machines can already reach the bus |

<a id="schema-setuprequest"></a>
#### SetupRequest

| Field | Type | Required | Description |
|---|---|---|---|
| `hostName` | string | yes | The domain name that points at this machine |

<a id="schema-setvisibilitybody"></a>
#### SetVisibilityBody

Who may use a credential

| Field | Type | Required | Description |
|---|---|---|---|
| `visibility` | [CredentialVisibility](#schema-credentialvisibility) | yes | Private or Public |

<a id="schema-sftpconnectdto"></a>
#### SftpConnectDto

How to reach a shard's files over SFTP

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | yes | False when the owning spire is not running an SFTP listener |
| `host` | string | yes | The address to connect to - the owning spire's, since that path has no bus hop |
| `port` | integer (int32) | yes | The listener's port |
| `userName` | string | yes | The caller's own AMP username, which is what they authenticate as |
| `folder` | string | yes | This shard's folder in the SFTP root |
| `hostKeyFingerprint` | string? | yes | The spire's SSH host key, for the user to check on first connect. Null when the owning spire is not this one - a host key is a machine secret and is not published across the cluster |
| `hasKeys` | boolean | yes | False when the caller has registered no SSH keys, so cannot connect yet |

<a id="schema-shardattention"></a>
#### ShardAttention

A shard currently blocked on the user (AwaitingUserAction), surfaced for the global attention toasts. Message carries the prompt detail (login URL, Steam Guard request) the UI classifies into a broad reason.

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) | yes | The shard awaiting user action |
| `name` | string | yes | The shard's display name, for the toast |
| `message` | string? | yes | Human-readable prompt detail the UI classifies into a broad reason |

<a id="schema-shardconfig"></a>
#### ShardConfig

User-supplied setting values for a DeployedShard. Each key is a string SettingSpec.FieldName from the app spec. Values are always stored as strings and coerced to the target type at apply time (matching AMP2 behaviour).  KV key: `amp.tn.{tenant}.sr.{shardId}.cfg`

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `values` | map<string, string> |  | User-configured values. Only contains settings that have been explicitly set - the effective value for any absent key is the spec's string? SettingSpec.DefaultValue. |
| `updatedAt` | string (date-time) |  |  |
| `updatedByUserId` | string |  |  |

<a id="schema-shardcontainermode"></a>
#### ShardContainerMode

How AMP should manage the container runtime for this shard. Always explicitly configured - there is no automatic mode. The template's ContainerSupport policy surfaces as a recommendation or requirement hint in the UI only.

Type: object enum: `Native`, `Docker`, `Podman`, `WslContainers`

<a id="schema-shardcredentialdto"></a>
#### ShardCredentialDto

The shared credential a shard's updates sign in with, as the caller may see it

| Field | Type | Required | Description |
|---|---|---|---|
| `required` | boolean | yes | The shard's game can only be downloaded by a Steam account that owns it, so an update needs a credential |
| `credential` | null or [SharedCredentialView](#schema-sharedcredentialview) | yes |  |
| `usable` | [SharedCredentialView](#schema-sharedcredentialview)[] | yes | The credentials the caller may point the shard at: their own and the tenant's Public ones |

<a id="schema-sharddiskusage"></a>
#### ShardDiskUsage

Disk usage of one directory inside a datastore. DiskUsageKind ShardDiskUsage.Kind says which case it is:   • Active shard - DiskUsageKind.Shard, bool ShardDiskUsage.Deleted false.   • Soft-deleted shard - DiskUsageKind.Shard, bool ShardDiskUsage.Deleted true     (the operator can Restore it or Delete it permanently).   • Orphan - DiskUsageKind.Orphan: data with no matching shard at all     (surface it, never silently omit).   • Backups - DiskUsageKind.Backups: every shard's snapshot archives.

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | [DiskUsageKind](#schema-diskusagekind) |  | What this directory is; only DiskUsageKind.Orphan rows may be purged as orphans |
| `shardId` | string (uuid) |  | Matched shard id; Guid Guid.Empty unless DiskUsageKind ShardDiskUsage.Kind is DiskUsageKind.Shard |
| `name` | string |  | Directory name under the datastore (matches DeployedShard.WorkingDirName, or Name on legacy shards) |
| `sizeMb` | integer (int64) |  | Size of this directory on disk, in megabytes |
| `deleted` | boolean |  | True when this directory belongs to a soft-deleted shard (restorable / purgeable) |
| `deletedAt` | string? (date-time) |  | When the shard was soft-deleted (drives retention purge); null unless bool ShardDiskUsage.Deleted |
| `displayName` | string |  | Display name of the matched shard (active or deleted); empty for true orphans |
| `limitMb` | integer (int64) |  | The shard's configured disk budget in MB at scan time, copied here so a reader of the report can judge long ShardDiskUsage.SizeMb without fetching every shard's infra settings. 0 = no limit (always the case for orphans, which belong to no shard). |
| `limitLevel` | [DiskLimitLevel](#schema-disklimitlevel) |  | Where long ShardDiskUsage.SizeMb stood against long ShardDiskUsage.LimitMb when this scan ran. Evaluated on the spire that owns the files, at the moment they were measured, so every reader - UI banner, start guard, enforcement - agrees on one verdict instead of each recomputing it from numbers of differing freshness. |

<a id="schema-shardendpoint"></a>
#### ShardEndpoint

A shard port together with every address it can be reached on. One per port that is worth connecting to: the application's primary port always, plus anything the operator has deliberately published.

| Field | Type | Required | Description |
|---|---|---|---|
| `portRef` | string | yes | The port reference this endpoint resolves (e.g. "PrimaryApplicationPort") |
| `name` | string | yes | The port's display name from the template, or the ref when it declares none |
| `port` | integer (int32) | yes | The port number assigned on the shard's own machine |
| `protocol` | [PortProtocol](#schema-portprotocol) | yes | Transport protocol the port serves |
| `isPrimary` | boolean | yes | True for the port the application's users actually connect to |
| `isPublic` | boolean | yes | False when the operator marked the port not publicly accessible, which suppresses its public address entirely - showing one would contradict the switch they set. |
| `addresses` | [ShardEndpointAddress](#schema-shardendpointaddress)[] | yes | Resolved addresses, public first |

<a id="schema-shardendpointaddress"></a>
#### ShardEndpointAddress

One resolved way to reach a shard's port, with everything the UI needs to present it honestly: the address itself, where it came from, and what is known to be in the way.

| Field | Type | Required | Description |
|---|---|---|---|
| `reach` | [EndpointReach](#schema-endpointreach) | yes | Whether this is the outside-world address or the same-network one |
| `host` | string | yes | Hostname or IP to dial |
| `port` | integer (int32) | yes | Port to dial; meaningless when ShowPort is false |
| `showPort` | boolean | yes | False only when the operator advertised a bare host and said it carries no port (a reverse proxy or an SRV record routes it), in which case appending one would send people to the wrong place. |
| `display` | string | yes | The address as it should be shown and copied ("host:port", or just "host") |
| `source` | [EndpointSource](#schema-endpointsource) | yes | Where the address came from |
| `uri` | string? | yes | The template's `EndpointURIFormat` applied to this address (e.g. `steam://connect/1.2.3.4:27015`), or null when the template declares none or the format resolved to something unsafe to hand a browser. |
| `notes` | [EndpointNote](#schema-endpointnote)[] | yes | What stands between this address and a working connection; empty when nothing does |
| `viaSpireId` | string? (uuid) |  | The front spire serving this address, when it is not the shard's own machine |
| `viaSpireName` | string? |  | That front spire's display name, so the UI can name it without a second lookup |
| `tunnelState` | null or [RouteHealth](#schema-routehealth) |  |  |

<a id="schema-shardgatewaybindingstatus"></a>
#### ShardGatewayBindingStatus

Live gateway-binding state, written by the front spire's reconciler under a separate KV key (frequent status writes never race the declaration).

| Field | Type | Required | Description |
|---|---|---|---|
| `bindingId` | string (uuid) |  |  |
| `state` | [GatewayBindingState](#schema-gatewaybindingstate) |  |  |
| `address` | string? |  | The address allocated to the shard on its tunnel, once there is one |
| `error` | string? |  |  |
| `updatedAt` | string (date-time) |  |  |

<a id="schema-shardinfracontext"></a>
#### ShardInfraContext

Infra settings bundled with the spire/template context the UI needs to render container-mode options and resource-limit controls correctly.

| Field | Type | Required | Description |
|---|---|---|---|
| `settings` | [ShardInfraSettings](#schema-shardinfrasettings) | yes | The shard's AMP-owned infra settings |
| `spireName` | string | yes | Display name of the spire this shard runs on |
| `spireIsLinux` | boolean | yes | True when the shard's spire runs Linux (drives the container option list) |
| `containerPolicy` | integer (int32) | yes | Numeric ContainerSupport flags from the template's ContainerPolicy |
| `logicalCores` | integer (int32) | yes | Logical CPU count on the spire (caps the CPU-limit slider) |
| `templateOs` | integer (int32) | yes | Numeric HostPlatform flags from the template's declared OS support. Used by the UI to enforce mandatory containerisation when the template is Linux-only but the spire runs Windows (Windows=1, Linux=2). |
| `supportsSleep` | boolean | yes | Whether this application's template declares that it can be put to sleep. False hides the sleep setting entirely rather than offering a switch that could never do anything - whether an app is safe to sleep depends on how its clients behave, which only the template maintainer knows. |
| `tracksUsers` | boolean | yes | Whether this application reports users connecting at all. Analytics and geo filtering both start from that, so without it their settings can do nothing, and the page says so. |
| `reportsAddresses` | boolean | yes | Whether it also reports the address a user connected from, which geo filtering and the country breakdown need. |

<a id="schema-shardinfrasaveresult"></a>
#### ShardInfraSaveResult

The infra settings as saved, returned by `PATCH /api/shards/{id}/infra`, with the one part of the save that can fall short without failing it.

| Field | Type | Required | Description |
|---|---|---|---|
| `clampRefused` | null or [ApiError](#schema-apierror) |  |  |
| `shardId` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `startOnBoot` | boolean |  | Start this shard automatically when the host Spire starts. |
| `autoRestartOnCrash` | boolean |  | If the application reaches the Ready state and then exits unexpectedly (no stop was requested) after running for at least 5 minutes, the agent will automatically restart it instead of stopping. Disable to opt out of this behaviour for shards that intentionally exit after finishing a task. |
| `startupOrder` | integer (int32) |  | Determines the order in which shards start on boot. Lower numbers start first. Shards with the same order start in parallel. Default 100; use lower values for dependencies. |
| `sleepMode` | boolean |  | Let this shard sleep while nobody is connected: AMP stops it, holds its ports so it still              appears online, and starts it again the moment someone tries to connect.               The operator's ONLY say in sleep mode, and the only part of it that belongs to them. Every timing - how long empty before sleeping, how long to doze, how slow a start disqualifies it - is a property of how the application's clients behave, so it is set by the template maintainer on ApplicationLifecycle. This switch does nothing at all unless that template declares SupportsUniversalSleep, and the UI hides it entirely when it does not. |
| `advertisedAddress` | string |  | The address to give people for this shard, overriding everything AMP works out for itself.              A hostname or IP ("play.example.com"), or one with a port pinned ("play.example.com:25565").              Empty = AMP resolves it (see `ShardEndpointResolver`).               It exists because the operator routinely knows something AMP cannot observe: a domain pointed at the machine, a proxy or SRV record in front of it, a load balancer. Declaring one IS them saying they have arranged for it to work, so it is reported as given rather than second-guessed. |
| `advertisedAddressIncludesPort` | boolean |  | Whether a bare string ShardInfraSettings.AdvertisedAddress still carries the shard's port. False when something in front routes it - an SRV record or a reverse proxy - and players connect to the host alone; appending a port then would send them somewhere nothing is listening. Ignored when the advertised value already pins a port of its own. |
| `containerMode` | [ShardContainerMode](#schema-shardcontainermode) |  | Per-shard container mode - always explicitly set |
| `containerImageOverride` | string? |  | Override the container image specified by the template. Null = use the template default. |
| `memoryLimitMb` | integer (int32) |  | Memory limit in MiB. 0 = unlimited (host default) |
| `memoryReservationMb` | integer (int32) |  | Memory reservation (soft limit) in MiB. Must be <= MemoryLimitMb when both non-zero. 0 = no reservation. |
| `memorySwapMb` | integer (int32) |  | Total memory + swap in MiB. Docker/Podman semantics: -1 = unlimited swap; 0 = runtime default (typically 2x MemoryLimitMb); >0 must be >= MemoryLimitMb. Not meaningful when MemoryLimitMb = 0. |
| `cpuLimit` | number/string (double) |  | CPU limit as a fractional number of cores. 0.0 = unlimited |
| `maxUsersCap` | integer (int32) |  | Hard ceiling on concurrent players, enforced by AMP regardless of the application's own `$MaxUsers` setting. 0 = no infra-level cap (the app setting or template default governs). Intended for slot-based hosting where the operator sells a fixed number of player slots and must prevent the tenant from raising the in-game limit above what was purchased. |
| `enableAnalytics` | boolean |  | "Enable Analytics": collect data about users who join the server (where available).               AMP 2's wording, and AMP 2's meaning: with this off the agent records nothing at all, and the shard contributes no sessions to analytics. |
| `userSpecificData` | boolean |  | "Store user-specific data": if enabled, you will be able to see which users spend the most              time on the server and other data related to specific users. However this requires that you              disclose the use of analytics to all users who join the server.               This is the personal-data switch, so in AMP 3 it also governs addresses: with it off the agent does not look a user's IP up at all, and every session is recorded with an unknown country. Names and raw ids are likewise left out. Turning it on does not reach back, so sessions already recorded stay as they were. |
| `minimumSessionTimeForRetention` | integer (int32) |  | "Minimum Session Time for Retention": the minimum session time in seconds for a session to be retained in the analytics data. This can be used to filter out very short sessions that may not be meaningful for analysis. |
| `enableGeoFiltering` | boolean |  | "Enable Geo-Filtering": block connections from certain countries.               Works the way AMP 2's did: when a user connects from a country this shard does not accept, the agent sends string ShardInfraSettings.GeoFilterKickCommand to the application's console. Cruder than a firewall, and better for it: it needs no privilege, works the same on every platform and inside a container, and the game itself tells the user why. It does need the application to log connections with an address and to accept a kick command. |
| `blockedCountries` | string[] |  | "Blocked Countries": countries to block connections from, as ISO 3166-1 alpha-2 codes. |
| `allowedCountries` | string[] |  | "Allowed Countries": countries to allow connections from, as ISO 3166-1 alpha-2 codes. An empty list implies that all countries are allowed except those in the block list. |
| `blockUnknownCountries` | boolean |  | Also block users whose country cannot be worked out. That includes everybody who arrives through a published port without gateway mode, because the address the game sees is the tunnel's. |
| `geoFilterKickMessage` | string |  | "Filtering Message": the message to display to users who are disconnected by geo filtering. {CountryCode} and {CountryName} are replaced with the user's country. |
| `geoFilterKickCommand` | string |  | "Kick Command": the console command that disconnects a filtered user. {UserName}, {UserID}, {Reason}, {CountryCode} and {CountryName} are replaced; {Reason} is the filtering message. |
| `diskLimitMb` | integer (int64) |  | Disk budget for this shard's working directory in MB. 0 = no limit.               Deliberately NOT in the resource-limits block above: those are container knobs handed to Docker/Podman and mean nothing to a native shard, whereas this is measured and enforced by AMP itself from the spire's periodic usage scan, so it works the same either way.Soft by design. AMP warns as the shard approaches the budget and stops it once it is meaningfully over (see DiskLimitPolicy); it never gives the application a full filesystem, because a failed write mid-save is how game servers lose worlds. The trade is that a shard can exceed this between scans - the budget is what AMP acts on, not a wall the application can feel. |
| `importConfigFilesFromSftp` | boolean |  | When a managed, importable configuration file is written over SFTP, read the change back              into this shard's settings instead of leaving it to be overwritten on the next start.               On is the useful default: bulk-editing config is most of what SFTP is for, and without this a write appears to succeed and is then silently lost the next time AMP regenerates the file. Off suits a shard whose files are being moved around wholesale - restoring a backup, syncing a config tree in from elsewhere - where parsing every arriving file back into settings is not what was meant.Per SHARD rather than per spire because it follows the application, not the machine: whether a given server's config should be read back depends on its template and how its owner works with it, and one spire hosts servers that differ. |
| `extraEnvironment` | map<string, string> |  | Additional environment variables injected into the process or container. Merged with any variables defined by the template, with these taking precedence on collision. |
| `updatedAt` | string (date-time) |  |  |
| `updatedByUserId` | string |  |  |

<a id="schema-shardinfrasettings"></a>
#### ShardInfraSettings

Infrastructure and lifecycle settings for a DeployedShard. These settings belong to AMP, not to the hosted application - they control how and when the shard runs rather than what it does.  Separated from ShardConfig (application settings) so the two concerns have distinct ownership, audit trails, and access-control surfaces.  KV key: `amp.tn.{tenant}.sr.{shardId}.infra`

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `startOnBoot` | boolean |  | Start this shard automatically when the host Spire starts. |
| `autoRestartOnCrash` | boolean |  | If the application reaches the Ready state and then exits unexpectedly (no stop was requested) after running for at least 5 minutes, the agent will automatically restart it instead of stopping. Disable to opt out of this behaviour for shards that intentionally exit after finishing a task. |
| `startupOrder` | integer (int32) |  | Determines the order in which shards start on boot. Lower numbers start first. Shards with the same order start in parallel. Default 100; use lower values for dependencies. |
| `sleepMode` | boolean |  | Let this shard sleep while nobody is connected: AMP stops it, holds its ports so it still              appears online, and starts it again the moment someone tries to connect.               The operator's ONLY say in sleep mode, and the only part of it that belongs to them. Every timing - how long empty before sleeping, how long to doze, how slow a start disqualifies it - is a property of how the application's clients behave, so it is set by the template maintainer on ApplicationLifecycle. This switch does nothing at all unless that template declares SupportsUniversalSleep, and the UI hides it entirely when it does not. |
| `advertisedAddress` | string |  | The address to give people for this shard, overriding everything AMP works out for itself.              A hostname or IP ("play.example.com"), or one with a port pinned ("play.example.com:25565").              Empty = AMP resolves it (see `ShardEndpointResolver`).               It exists because the operator routinely knows something AMP cannot observe: a domain pointed at the machine, a proxy or SRV record in front of it, a load balancer. Declaring one IS them saying they have arranged for it to work, so it is reported as given rather than second-guessed. |
| `advertisedAddressIncludesPort` | boolean |  | Whether a bare string ShardInfraSettings.AdvertisedAddress still carries the shard's port. False when something in front routes it - an SRV record or a reverse proxy - and players connect to the host alone; appending a port then would send them somewhere nothing is listening. Ignored when the advertised value already pins a port of its own. |
| `containerMode` | [ShardContainerMode](#schema-shardcontainermode) |  | Per-shard container mode - always explicitly set |
| `containerImageOverride` | string? |  | Override the container image specified by the template. Null = use the template default. |
| `memoryLimitMb` | integer (int32) |  | Memory limit in MiB. 0 = unlimited (host default) |
| `memoryReservationMb` | integer (int32) |  | Memory reservation (soft limit) in MiB. Must be <= MemoryLimitMb when both non-zero. 0 = no reservation. |
| `memorySwapMb` | integer (int32) |  | Total memory + swap in MiB. Docker/Podman semantics: -1 = unlimited swap; 0 = runtime default (typically 2x MemoryLimitMb); >0 must be >= MemoryLimitMb. Not meaningful when MemoryLimitMb = 0. |
| `cpuLimit` | number/string (double) |  | CPU limit as a fractional number of cores. 0.0 = unlimited |
| `maxUsersCap` | integer (int32) |  | Hard ceiling on concurrent players, enforced by AMP regardless of the application's own `$MaxUsers` setting. 0 = no infra-level cap (the app setting or template default governs). Intended for slot-based hosting where the operator sells a fixed number of player slots and must prevent the tenant from raising the in-game limit above what was purchased. |
| `enableAnalytics` | boolean |  | "Enable Analytics": collect data about users who join the server (where available).               AMP 2's wording, and AMP 2's meaning: with this off the agent records nothing at all, and the shard contributes no sessions to analytics. |
| `userSpecificData` | boolean |  | "Store user-specific data": if enabled, you will be able to see which users spend the most              time on the server and other data related to specific users. However this requires that you              disclose the use of analytics to all users who join the server.               This is the personal-data switch, so in AMP 3 it also governs addresses: with it off the agent does not look a user's IP up at all, and every session is recorded with an unknown country. Names and raw ids are likewise left out. Turning it on does not reach back, so sessions already recorded stay as they were. |
| `minimumSessionTimeForRetention` | integer (int32) |  | "Minimum Session Time for Retention": the minimum session time in seconds for a session to be retained in the analytics data. This can be used to filter out very short sessions that may not be meaningful for analysis. |
| `enableGeoFiltering` | boolean |  | "Enable Geo-Filtering": block connections from certain countries.               Works the way AMP 2's did: when a user connects from a country this shard does not accept, the agent sends string ShardInfraSettings.GeoFilterKickCommand to the application's console. Cruder than a firewall, and better for it: it needs no privilege, works the same on every platform and inside a container, and the game itself tells the user why. It does need the application to log connections with an address and to accept a kick command. |
| `blockedCountries` | string[] |  | "Blocked Countries": countries to block connections from, as ISO 3166-1 alpha-2 codes. |
| `allowedCountries` | string[] |  | "Allowed Countries": countries to allow connections from, as ISO 3166-1 alpha-2 codes. An empty list implies that all countries are allowed except those in the block list. |
| `blockUnknownCountries` | boolean |  | Also block users whose country cannot be worked out. That includes everybody who arrives through a published port without gateway mode, because the address the game sees is the tunnel's. |
| `geoFilterKickMessage` | string |  | "Filtering Message": the message to display to users who are disconnected by geo filtering. {CountryCode} and {CountryName} are replaced with the user's country. |
| `geoFilterKickCommand` | string |  | "Kick Command": the console command that disconnects a filtered user. {UserName}, {UserID}, {Reason}, {CountryCode} and {CountryName} are replaced; {Reason} is the filtering message. |
| `diskLimitMb` | integer (int64) |  | Disk budget for this shard's working directory in MB. 0 = no limit.               Deliberately NOT in the resource-limits block above: those are container knobs handed to Docker/Podman and mean nothing to a native shard, whereas this is measured and enforced by AMP itself from the spire's periodic usage scan, so it works the same either way.Soft by design. AMP warns as the shard approaches the budget and stops it once it is meaningfully over (see DiskLimitPolicy); it never gives the application a full filesystem, because a failed write mid-save is how game servers lose worlds. The trade is that a shard can exceed this between scans - the budget is what AMP acts on, not a wall the application can feel. |
| `importConfigFilesFromSftp` | boolean |  | When a managed, importable configuration file is written over SFTP, read the change back              into this shard's settings instead of leaving it to be overwritten on the next start.               On is the useful default: bulk-editing config is most of what SFTP is for, and without this a write appears to succeed and is then silently lost the next time AMP regenerates the file. Off suits a shard whose files are being moved around wholesale - restoring a backup, syncing a config tree in from elsewhere - where parsing every arriving file back into settings is not what was meant.Per SHARD rather than per spire because it follows the application, not the machine: whether a given server's config should be read back depends on its template and how its owner works with it, and one spire hosts servers that differ. |
| `extraEnvironment` | map<string, string> |  | Additional environment variables injected into the process or container. Merged with any variables defined by the template, with these taking precedence on collision. |
| `updatedAt` | string (date-time) |  |  |
| `updatedByUserId` | string |  |  |

<a id="schema-shardmetricssample"></a>
#### ShardMetricsSample

One metrics sample published by a shard agent on `amp.tn.{t}.sr.{id}.ev.metrics` (write-only for the agent, like lifecycle events). The webapi bridges samples to SignalR for live charts and keeps a short in-memory history for the detail view; durable long-term retention can later be layered on via a JetStream stream on the same subject.

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `timestamp` | string (date-time) |  |  |
| `agentVersion` | string? |  | The reporting agent's own build version (Nerdbank.GitVersioning stamp). A metrics sample is a liveness heartbeat, so carrying the version here keeps string? ShardState.AgentVersion fresh for a long-running shard that emits no further lifecycle events after its handshake. |
| `cpuPercent` | number/string (double) |  | Process-tree CPU usage in percent of one core ×100 (0–100 per host normalisation) |
| `memoryMb` | integer (int32) |  | Resident memory of the application process tree, MiB |
| `memoryLimitMb` | integer (int32) |  | Configured memory ceiling (0 = unlimited) so charts can show headroom |
| `ioReadKbps` | number/string (double) |  | Disk read throughput since the previous sample, KiB/s |
| `ioWriteKbps` | number/string (double) |  | Disk write throughput since the previous sample, KiB/s |
| `activeUsers` | integer (int32) |  | Connected users/players, from console join/leave tracking (-1 = unknown) |
| `maxUsers` | integer (int32) |  | Configured user/player ceiling (0 = unknown/unlimited) |
| `custom` | map<string, number/string (double)> |  | App-defined metrics extracted from console output via the template's `Console.MetricsRegex` - every named capture group whose value parses as a number lands here under the group's name (e.g. TPS, MSPT). |
| `listeningPorts` | integer (int32)[] |  | Of the shard's ASSIGNED ports, the ones currently observed listening (host listener table, protocol-aware). Drives the per-port open/closed indicators - AMP 2's port summary equivalent. |

<a id="schema-shardportassignments"></a>
#### ShardPortAssignments

The concrete port assignments for a DeployedShard. Each entry maps a string PortRequirement.Ref to the actual assigned port number. Written once at creation and updated only when ports are explicitly reassigned.  Kept separate from ShardConfig (user setting values) because ports are assigned by the system and have different ownership semantics.  KV key: `amp.tn.{tenant}.sr.{shardId}.ports`

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `assignments` | map<string, integer (int32)> |  | Maps each string PortRequirement.Ref to its assigned base port. For requirements with int PortRequirement.Range > 1, this is the lowest port in the range - consecutive ports are implied. USER-DEFINED custom ports live here too (described by IReadOnlyList<CustomPortDef> ShardPortAssignments.CustomPorts), so conflict checks, occupied-set collection, renumbering, and port publishing all treat them exactly like template ports. |
| `customPorts` | [CustomPortDef](#schema-customportdef)[] |  | Metadata for ports the USER registered beyond the app spec (e.g. a Dynmap web port). Their numbers sit in IReadOnlyDictionary<string, int> ShardPortAssignments.Assignments under string CustomPortDef.Ref. Unlike template ports these can be deleted again. |
| `privatePortRefs` | string[] |  | Refs marked NOT publicly accessible: excluded from port publishing and not opened in the system firewall. Absence = public. The template's primary application port is public even when listed here; bool ShardPortAssignments.IsPublic(string portRef, string? primaryRef) is the one answer. |
| `assignedAt` | string (date-time) |  |  |
| `assignedBySpireId` | string |  |  |

<a id="schema-shardportinfo"></a>
#### ShardPortInfo

One declared or custom port for a shard, joining template metadata to its allocated number

| Field | Type | Required | Description |
|---|---|---|---|
| `ref` | string | yes | Stable port reference key (from the template, or the custom port's ref) |
| `name` | string | yes | Human-readable port name, falling back to the ref when the template omits one |
| `description` | string | yes | Template description of the port's purpose; empty for custom or metadata-less ports |
| `protocol` | string | yes | Transport protocol as a string (for example TCP, UDP, or Both) |
| `port` | integer (int32) | yes | The port number currently assigned by the allocator |
| `isPublic` | boolean | yes | True when the port is marked publicly accessible |
| `custom` | boolean | yes | True when this is a user-registered custom port (deletable), false for a template-declared port |
| `primary` | boolean | yes |  |

<a id="schema-shardsleepstatus"></a>
#### ShardSleepStatus

The live operational state of a DeployedShard. Written frequently by the shard agent - kept in a separate KV key so high-frequency heartbeat writes don't collide with the durable definition.  KV key: `amp.tn.{tenant}.sr.{shardId}.state`

| Field | Type | Required | Description |
|---|---|---|---|
| `available` | boolean | yes | Whether sleep can be used for the current run |
| `reason` | string? |  | Why not, in English, when Available is false |
| `reasonCode` | string? |  | i18n key for Reason |
| `reasonParams` | map<string, string> |  | Parameters for ReasonCode |
| `reportedAt` | string? (date-time) |  | When the agent last settled the question |

<a id="schema-shardsnapshot"></a>
#### ShardSnapshot

One node in a shard's snapshot timeline (a DAG). Guid? ShardSnapshot.ParentId links to the snapshot this one derives from; a null parent is a root. Restoring an older node and snapshotting again forks a new branch off it - which is what produces the GitHub-style timeline with diverging lanes.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) |  |  |
| `parentId` | string? (uuid) |  | The snapshot this one derives from; null = a root (full, no ancestor) |
| `kind` | [SnapshotKind](#schema-snapshotkind) |  |  |
| `content` | [SnapshotContent](#schema-snapshotcontent) |  |  |
| `trigger` | [SnapshotTrigger](#schema-snapshottrigger) |  |  |
| `status` | [SnapshotStatus](#schema-snapshotstatus) |  |  |
| `name` | string |  | Short operator-facing name, e.g. "Pre-1.20 upgrade" |
| `description` | string |  | Longer free-text description of what this snapshot is / why it was taken |
| `sticky` | boolean |  | When true, automatic pruning/retention must never delete this snapshot |
| `failureReason` | string |  | Visible reason when SnapshotStatus ShardSnapshot.Status is SnapshotStatus.Failed |
| `skippedFiles` | [SkippedFile](#schema-skippedfile)[] |  | Files the capture could not read, so this snapshot does not contain them. A snapshot with any of these is still Complete - it is a real backup of everything else - but it is NOT a complete picture of the disk, and restoring it will not put these files back. |
| `skippedCount` | integer (int32) |  | How many files were skipped in total (see IReadOnlyList<SkippedFile> ShardSnapshot.SkippedFiles) |
| `archiveMissing` | boolean |  | The zip this node names is not where it should be, so the snapshot cannot be restored or              downloaded even though the timeline still knows about it. The usual cause is a cross-spire              move that left the backups behind: the graph lives in shared KV and travels with the shard,              the archives sit on a disk belonging to the spire it came from.               Stamped by the owning spire when it reads the graph, and cleared again by the same check if the file reappears. A spire that does not own the shard cannot see its disk and leaves the flag exactly as it found it, rather than declaring someone else's backups missing. |
| `createdAt` | string (date-time) |  |  |
| `createdByUserId` | string |  |  |
| `sizeMb` | integer (int64) |  | Actual on-disk size of this node's zip, MB (compressed) |
| `uncompressedMb` | integer (int64) |  | Uncompressed size of this node's captured contents, MB (the 0-compression estimate) |
| `fileCount` | integer (int32) |  | Number of file entries this node contributes (changed/added for a partial) |
| `ownerSpireId` | string (uuid) |  | The spire+run that is capturing this node; a Running node from a different run of             its owner is a crash leftover, failed on read. Another spire's node is never touched |
| `runId` | string (uuid) |  |  |
| `stage` | [SnapshotStage](#schema-snapshotstage) |  |  |
| `processedFiles` | integer (int32) |  | Files processed so far in the SnapshotStage.Processing phase |
| `totalFiles` | integer (int32) |  | Total files to process this phase (0 = unknown/indeterminate) |
| `datastoreId` | string (uuid) |  | Datastore the zip is stored in (the `Backups`-tagged one, or the primary) |
| `zipFileName` | string |  | Zip filename relative to the shard's backup root, i.e. `{Id}.zip` |

<a id="schema-shardsnapshotgraph"></a>
#### ShardSnapshotGraph

The complete snapshot timeline for one shard: every node, the current Guid? ShardSnapshotGraph.HeadId ("what's on disk now"), and the per-shard settings. Stored as a single KV record (`Ns.ShardSnapshots`) so timeline edits are atomic - mirrors the one-record-per-shard pattern of the usage report.

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `nodes` | [ShardSnapshot](#schema-shardsnapshot)[] |  | Every snapshot node in this shard's timeline (the DAG) |
| `headId` | string? (uuid) |  | The node whose state is currently laid down on disk; null before the first snapshot |
| `settings` | [ShardSnapshotSettings](#schema-shardsnapshotsettings) |  |  |
| `restoringId` | string? (uuid) |  | The node currently being restored onto disk; null when no restore is running |
| `restoreStage` | [SnapshotStage](#schema-snapshotstage) |  |  |
| `restoreProcessed` | integer (int32) |  |  |
| `restoreTotal` | integer (int32) |  |  |
| `restoreOwnerSpireId` | string (uuid) |  | The spire+run driving the current restore (for the same stale-reconciliation as capture) |
| `restoreRunId` | string (uuid) |  |  |

<a id="schema-shardsnapshotsettings"></a>
#### ShardSnapshotSettings

Per-shard guardrails for snapshot storage. Both limits are evaluated pre-flight against the <b>uncompressed</b> (0-compression) size estimate so a snapshot is refused with a visible reason before any zipping work happens. 0 = no limit. Cadence/retention ("cycling") is deliberately out of scope until the scheduler.

| Field | Type | Required | Description |
|---|---|---|---|
| `maxBackupSizeMb` | integer (int64) |  | Maximum uncompressed size of a single snapshot, MB. 0 = no limit |
| `maxTotalStorageMb` | integer (int64) |  | Maximum aggregate snapshot storage for this shard, MB. 0 = no limit |
| `stopWhenNoQuiesce` | boolean |  | When the app declares no quiescence command, stop the shard for the duration of the snapshot (consistent capture) rather than snapshotting it hot. Default true. |

<a id="schema-shardstate"></a>
#### ShardState

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `status` | [ApplicationState](#schema-applicationstate) |  |  |
| `revision` | integer (uint64) |  | The KV revision this copy of the record was read at, which orders any two copies a reader holds: a copy with a lower revision is older. Set by the reads that reach a browser (the state feed's push, the REST state read) from the entry they read. Only the entry's revision counts: a value left inside the JSON by an earlier read that was written back is meaningless, and no writer of the record reads it. |
| `metrics` | null or [MetricsModel](#schema-metricsmodel) |  |  |
| `lastHeartbeat` | string (date-time) |  |  |
| `activeTaskId` | string? (uuid) |  | If a ManagedTask is currently running against this shard, its ID |
| `processId` | integer (int32) |  | OS process ID of the running application. Null when stopped |
| `runningVersion` | string? |  | Version reported by the running process, if available |
| `agentVersion` | string? |  | Build version of the shard AGENT supervising this shard (distinct from string? ShardState.RunningVersion, which is the hosted app's version). Persisted from the agent's handshake/heartbeat so the spire knows which agent binary each live shard runs on - the input to stale-agent restart escalation and version GC. Null until an agent reports in. |
| `lastErrorMessage` | string? |  | Set when the shard transitions to a failed state (English fallback) |
| `lastErrorCode` | string? |  | Optional i18n key for string? ShardState.LastErrorMessage so the client can localise it |
| `lastErrorParams` | map<string, string> |  | Parameters for string? ShardState.LastErrorCode |
| `statusMessage` | string? |  | What the agent is doing WITHIN its current state, when it said. A state on its own is often too coarse to be reassuring: "Pre-Start" for a minute reads as hung, where "Connecting to the gateway spire" reads as working. Cleared by the next transition that carries no message, so it can never linger and describe something that finished. English fallback, like string? ShardState.LastErrorMessage. |
| `statusCode` | string? |  | Optional i18n key for string? ShardState.StatusMessage so the client can localise it |
| `statusParams` | map<string, string> |  | Parameters for string? ShardState.StatusCode |
| `startedAt` | string? (date-time) |  | When the application was last launched, from the agent's most recent Starting transition. Kept after the shard stops; null only for a shard that has never been started. |
| `sleep` | null or [ShardSleepStatus](#schema-shardsleepstatus) |  |  |
| `isRunning` | boolean |  |  |
| `isStopped` | boolean |  |  |
| `acceptsTraffic` | boolean |  | Whether anything on the far side will answer a connection, which is not the same question as              whether the application is running.               A sleeping shard has no application process at all, and yet its port must stay reachable: the whole point of sleep is that the agent holds the port, answers queries on the application's behalf and starts it again when somebody actually connects. Cutting off a sleeping shard's traffic removes the only thing that could ever wake it. |
| `isBusy` | boolean |  |  |

<a id="schema-sharedcredentialkind"></a>
#### SharedCredentialKind

What a shared credential signs in to. Open: a kind is added when a consumer exists for it

Type: object enum: `SteamCmd`, `Smtp`, `DiscordWebhook`, `ApiKey`

<a id="schema-sharedcredentialview"></a>
#### SharedCredentialView

A credential as a viewer may see it. string? SharedCredentialView.Username is set for the creator alone and int? SharedCredentialView.ShardCount for the creator and a manager; there is no secret here or anywhere.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) | yes |  |
| `title` | string | yes |  |
| `kind` | [SharedCredentialKind](#schema-sharedcredentialkind) | yes |  |
| `visibility` | [CredentialVisibility](#schema-credentialvisibility) | yes |  |
| `mine` | boolean | yes |  |
| `username` | string? | yes |  |
| `shardCount` | integer (int32) | yes |  |
| `createdAt` | string (date-time) | yes |  |
| `updatedAt` | string (date-time) | yes |  |

<a id="schema-skippedfile"></a>
#### SkippedFile

A file a capture could not read and therefore left out of the archive, with the reason the filesystem gave. Recorded on the snapshot so "what is NOT in this backup" is answerable without going to the logs - the usual cause being a file the running application holds open in a way that forbids reading it at all.

| Field | Type | Required | Description |
|---|---|---|---|
| `relPath` | string | yes |  |
| `reason` | string | yes |  |

<a id="schema-sleepwakeupmodes"></a>
#### SleepWakeupModes

Which events can wake a sleeping shard back up (combinable flags)

Type: integer

<a id="schema-snapshotcontent"></a>
#### SnapshotContent

What a snapshot captures - any combination of the three. The split mirrors how an install is layered: base data is re-downloadable, game state is what the server generates on top, and AMP config is the management-side settings.

Type: integer

<a id="schema-snapshotkind"></a>
#### SnapshotKind

Whether a snapshot is self-contained or a delta against its parent chain

Type: integer

<a id="schema-snapshotstage"></a>
#### SnapshotStage

Coarse phase of an in-flight capture/restore, mirroring the update flow's "gather then process" shape. Gathering is indeterminate (walking the tree, diffing against the parent); Processing has a real files-done/total percentage.

Type: integer

<a id="schema-snapshotstatus"></a>
#### SnapshotStatus

Lifecycle of a snapshot capture

Type: integer

<a id="schema-snapshottrigger"></a>
#### SnapshotTrigger

What caused a snapshot to be taken

Type: integer

<a id="schema-sourcesyncresult"></a>
#### SourceSyncResult

Result of a sync operation against a single TemplateSource.

| Field | Type | Required | Description |
|---|---|---|---|
| `sourceId` | string | yes | Id of the template source that was synced |
| `headSha` | string | yes | The source's HEAD commit sha after the sync |
| `changed` | boolean | yes | True when HEAD moved since the previous sync |
| `error` | string? |  | A failure message when the sync did not complete, otherwise null |

<a id="schema-specmetadata"></a>
#### SpecMetadata

Descriptive and platform metadata for a template: display info, authorship, supported OS/architecture, container policy, prerequisites, and versioning/release details.

| Field | Type | Required | Description |
|---|---|---|---|
| `displayName` | string |  | Friendly name of the application shown to users |
| `description` | string |  | Short description of the application |
| `created` | string (date-time) |  | When this template definition was created (UTC) |
| `updated` | string (date-time) |  | When this template definition was last updated (UTC) |
| `os` | [HostPlatform](#schema-hostplatform) |  | Host operating systems the application supports |
| `aarchSupport` | [Aarch64Support](#schema-aarch64support) |  | Whether the application runs on aarch64 hosts |
| `arch` | [Architecture](#schema-architecture) |  | CPU architecture the application targets |
| `author` | string |  | Author or maintainer of the template |
| `url` | string |  | Reference URL for the template or application |
| `displayImageSource` | string |  | Icon/image reference for the application (e.g. an internal: id or a URL) |
| `endpointURIFormat` | string |  | Format string for a client connect URI: {0}=endpoint address |
| `configManifest` | string |  | Filename of the settings config manifest |
| `metaConfigManifest` | string |  | Filename of the meta-config manifest describing config-file read/write; empty if none |
| `configRoot` | string |  | Filename of the root .kvp definition this template is built from |
| `specificDockerImage` | string |  | A specific Docker image to run the app in; empty uses the default base image |
| `dockerRequired` | boolean |  | The app must run inside a container |
| `dockerBaseReadOnly` | boolean |  | Mount the container's base image read-only |
| `containerPolicy` | [ContainerSupport](#schema-containersupport) |  | Container support/recommendation policy per platform |
| `containerPolicyReason` | string |  | Explanation shown alongside ContainerSupport SpecMetadata.ContainerPolicy when it constrains the user's choice |
| `extraSetupStepsURI` | string |  | URL to extra manual setup instructions the app requires |
| `prerequisites` | string[] |  | Prerequisite packages/components that must be present on the host |
| `extraContainerPackages` | string[] |  | Additional OS packages to install into the container image |
| `configReleaseState` | [ReleaseState](#schema-releasestate) |  | Completeness/reliability of this template |
| `noCommercialUsage` | boolean |  | The application forbids commercial use |
| `eulaLocation` | string |  | URL of the end-user licence a user must accept before this app will start, shown by the              acceptance dialog alongside the setting marked `Special=EULA`.               Presented as a link that opens in a new tab, never embedded: the licences that matter here (Minecraft's EULA, the Steam Subscriber Agreement) all refuse to be framed, and a licence the user cannot actually read is worse than no dialog at all. |
| `configVersion` | string |  | Version of this template definition |
| `releaseNotes` | string |  | Release notes for this template version |
| `breakingReleaseNotes` | string |  | Notes about breaking changes in this template version |
| `deprecatedReason` | string |  | If set, marks the template deprecated and explains why |
| `minAMPVersion` | string |  | Minimum AMP version required to use this template |
| `minAgentVersion` | string? |  | Minimum shard-agent version (semver) required to run this template. Checked at deploy and at start against the version the spire would launch; a spire whose newest staged agent is below this refuses with a clear error. Null/empty means no floor.  Distinct from string SpecMetadata.MinAMPVersion: that gates template compatibility with the AMP install as a whole, this gates the specific agent binary that supervises the app process (a template may need a newer agent for a capability the old agent lacks). |
| `appIsMultiIPAware` | boolean |  | The app understands binding to a specific address on multi-IP hosts |

<a id="schema-spirebehinddto"></a>
#### SpireBehindDto

A spire that did not follow the last update

| Field | Type | Required | Description |
|---|---|---|---|
| `spireId` | string (uuid) | yes | The spire |
| `name` | string | yes | Its display name |
| `version` | string? | yes | The AMP version it reports, or null if unknown |
| `online` | boolean | yes | Whether it can be told to update right now |
| `rolledBackFrom` | string? | yes | The newer version that spire tried and rolled back, as its heartbeat reports, or null when it reports none |
| `rolledBackKind` | string? | yes | Why that spire rolled back, as the marker's kind, or null when unknown |

<a id="schema-spireconfig"></a>
#### SpireConfig

Operator-controlled configuration for a spire - most importantly the set of datastores (on-disk locations) it offers for shard placement.  Kept separate from SpireDefinition (identity/heartbeat) because it is edited by operators and read by the launch path, with different ownership and write cadence.  KV key: `amp.tn.{tenant}.sp.{spireId}.cfg`

| Field | Type | Required | Description |
|---|---|---|---|
| `spireId` | string (uuid) |  |  |
| `tenantId` | string |  |  |
| `datastores` | [SpireDatastore](#schema-spiredatastore)[] |  | The datastores this spire offers for shard placement |
| `usageScanIntervalMinutes` | integer (int32) |  | How often this spire re-measures its datastores, in minutes.               Per spire because the right answer is a property of the machine's disks and how much is on them: a scan walks every file under every datastore, which is nothing on an NVMe holding four servers and minutes of thrashing on a spinning bulk store holding two hundred. It is also the resolution of everything built on the scan - a disk limit is noticed, at worst, one interval after it is crossed - so the operator is choosing directly between how current those numbers are and what measuring them costs. |
| `resolvedUsageScanInterval` | string |  | int SpireConfig.UsageScanIntervalMinutes as a usable interval, clamped to the supported range.         Always read the cadence through this: an out-of-range value in KV (hand-edited, or written         by an older build) must land on a sane cadence, never on a zero-delay scan loop. |
| `updatedAt` | string (date-time) |  |  |
| `updatedByUserId` | string |  |  |

<a id="schema-spiredatastore"></a>
#### SpireDatastore

A named on-disk location a spire can place shard working directories in. Each deployed shard is assigned to one datastore and gets its own subdirectory beneath string SpireDatastore.Path, which becomes the working directory of its agent when launched.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) |  | Stable identifier a deployed shard references to record which datastore it was placed in |
| `name` | string |  | Operator-facing name, e.g. "Fast NVMe", "Bulk HDD" |
| `path` | string |  | Absolute path on the spire host under which shard directories are created |
| `isDefault` | boolean |  | The datastore used when a shard does not specify one |
| `enabled` | boolean |  | When false, the datastore is hidden from new deployments |
| `softLimitMb` | integer (int64) |  | Soft usage limit in MB. Once the scanned usage reaches this, new shards cannot be created in this datastore (existing shards keep running and may still grow - it is a placement guardrail, not a quota). 0 = no limit. |
| `tags` | string[] |  | Operator/auto-applied labels for placement and presentation - e.g. the storage medium ("NVMe", "SSD", "HDD", auto-detected where possible) plus any free-form tags ("scratch", "backups", "fast"). |

<a id="schema-spiredefinition"></a>
#### SpireDefinition

Represents a physical or virtual machine that can host shard agents. Spires are the execution layer - a shard is assigned to a spire and runs there.  KV key: `amp.tn.{tenant}.sp.{spireId}`

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) |  | Stable unique identifier for this spire, used throughout the KV hierarchy and NATS subjects |
| `tenantId` | string |  | The tenant that owns this spire |
| `displayName` | string |  | Human-readable name, e.g. "Primary Server", "EU Node 1" |
| `description` | string |  | Optional operator description of what this spire is for |
| `isLocal` | boolean |  | The machine AMP itself runs on. Exactly one auto-seeded local spire exists per tenant; it cannot be deleted. |
| `platform` | [HostPlatform](#schema-hostplatform) |  | The OS this spire runs on - determines which executables and paths apply |
| `architecture` | [Architecture](#schema-architecture) |  | The CPU architecture this spire runs on, used to pick matching executables |
| `tags` | string[] |  | Arbitrary operator-defined tags used for feature availability, placement filtering, and discovery (e.g. "gpu", "eu-west", "high-mem"). |
| `ipPool` | string[] |  | Addresses this spire may bind shards to - each entry an individual IP or a CIDR block (e.g. "10.0.0.5", "192.168.1.0/24"). Empty = any reported IP. |
| `natsPrefix` | string |  | The NATS subject prefix this spire's agents listen on. Derived from the hierarchy: `amp.tn.{tenant}.sp.{spireId}`. Used by the orchestrator to address service requests to this spire. |
| `allocatablePortRange` | string |  | The port pool available for shard assignment on this spire. Stored as a serialised SparseRange string, e.g. `1024:65535`. Defaults to the full unprivileged range. Administrators can narrow this to avoid conflicts with host services. |
| `acceptsDeployments` | boolean |  | Whether new shards may be deployed here. Disabling does not affect shards already on the spire - it only removes it as a placement target. |
| `maxShards` | integer (int32) |  | Maximum number of shards allowed on this spire. 0 = unlimited |
| `defaultContainerMode` | [ShardContainerMode](#schema-shardcontainermode) |  | Container mode applied to new shards created on this spire. Follows the host's detected container runtime (see `ContainerModePolicy.SpireDefault`) until an operator sets it, on the settings page or through the installer's server-runtime answer (`SpireSeed`), after which bool SpireDefinition.DefaultContainerModeChosen keeps their choice. Individual shards can still be changed after creation. |
| `defaultContainerModeChosen` | boolean |  | Whether an operator has set ShardContainerMode SpireDefinition.DefaultContainerMode, through the settings surface or the installer's seed. Recorded rather than inferred from the value, because Native chosen on purpose and Native left over from a host that had no runtime look identical. Once set it stays set. |
| `preferredContainerMode` | [ShardContainerMode](#schema-shardcontainermode) |  | Container runtime used when containerisation is forced (a mandated-container app, or a containerised shard migrating in) rather than chosen. See `SpireSettings.PreferredContainerMode`. |
| `defaultShardIpBinding` | string |  | The IP binding stamped on new shards' `$ApplicationIPBinding` at creation time. A literal address, "0.0.0.0" (all interfaces), or string SpireDefinition.AutoFirstFromPool. Empty = use the app's own template default. Only materially affects multi-IP-aware apps; others always bind 0.0.0.0. Operators set this once per spire rather than repeating it on every shard. |
| `createdAt` | string (date-time) |  | When this spire was first registered |
| `panelPort` | integer (int32) |  | The web port this machine's panel listens on, reported by the machine itself on every heartbeat. Held back from every server placed there, because a server on it would take the panel's firewall rule. The well-known number until the machine has reported. |
| `busPort` | integer (int32) |  | The client port of the message bus this machine runs, reported by the machine itself; 0 once it has reported that it runs none. Held back like int SpireDefinition.PanelPort, and for the same reason with a worse outcome: the bus's rule is host-only, and a server's would not be. |
| `identityPublicKey` | string |  | The public half of the spire's persistent identity keypair (NKEY account public, e.g. "A..."), published by the spire itself on self-registration. Peers verify cross-spire principal-assertion tokens this spire signs against this key. Empty until the owning host has registered with an identity. |
| `role` | [SpireRole](#schema-spirerole) |  | What this machine was added to do. Stamped at enrollment from the join file and deliberately absent from SpireDefinition SpireDefinition.WithOperatorEditable(SpireDefinition patch) - the role decides whether the machine was ever issued a shard signing key, so editing it afterwards could only ever produce a record that disagreed with what the machine can actually do. |
| `publicHost` | string |  | The public hostname or IP this spire is reachable at from the internet (e.g. `vps.example.com` or `203.0.113.10`). SET = this spire is publicly routable: it can FRONT port publications (players connect here), it's the endpoint tunnels dial, and shards running ON it are already reachable at this address without any tunnel. EMPTY = private (behind NAT / no public IP): can only be a tunnel BACK end, never a front. Operator-set, since only the operator knows whether the machine truly has a routable address + open ports. |
| `sftpEnabled` | boolean |  | Whether this spire runs its SFTP listener. Safe to leave on: authentication is by registered public key only, so a listener nobody has given a key to cannot be entered. |
| `sftpPort` | integer (int32) |  | Port the SFTP listener binds. 2222 rather than 22 so it needs no privileged bind and does not collide with the host's own sshd. |
| `sftpBindAddress` | string |  | Address to bind the SFTP listener to. Empty or unparseable means all interfaces |
| `sftpHoneypot` | boolean |  | Catch SFTP scanners: advertise password authentication that is NEVER accepted, so a              scanner reveals the username it is trying, and ban the address outright when that username              is a known honeypot.               The two halves are ONE setting because neither is worth anything alone. SSH only sends a username once a client offers a credential we advertise; key-only means a password scanner is refused during method negotiation and its username never reaches us, so the honeypot list sits inert - it cannot fire on a name it never sees. Equally, advertising a password path with no honeypot behind it is pure cost. AMP2 has these as two independent settings and consequently has a combination that silently does nothing.No password is ever accepted, on any path, whatever this is set to: the advertisement is bait, not a credential route. The trade is that a user with no key registered gets prompted for a password that cannot work, rather than an immediate publickey refusal. |
| `sftpIdleTimeoutSeconds` | integer (int32) |  | Seconds an SFTP connection may sit idle before it is dropped. An abandoned session holds a permission snapshot and a slot against the per-user limit, so it has to end on its own. Floored at 30 wherever it is read - a shorter one would disconnect people mid-thought. |
| `sftpMaxSessionsPerUser` | integer (int32) |  | Concurrent SFTP connections one user may hold on this spire. Not a security boundary: it stops one misconfigured client from opening connections until the process runs out of them. Generous by default because ordinary clients legitimately open several at once (a graphical client browsing while transferring is two or three on its own). 0 = unlimited. |
| `shardSigningKeyPublic` | string |  | The public half of the SCOPED signing key this spire mints its own shards' credentials with. Recorded so withdrawing the machine can drop the key from the account as well as revoking its user: leave the key behind and a revoked machine can still mint itself a shard-shaped credential and reconnect. Empty for a spire that predates scoped keys, or one on an unmanaged bus. |
| `busUserPublicKey` | string |  | The public half of the bus key this spire authenticates with - the handle for revoking its access. Recorded because without it we could not withdraw a machine at all: we would know it was enrolled and have no idea which credential to cancel. Empty on an unmanaged bus. |
| `captureAgentOutput` | boolean |  | Diagnostic: capture each shard agent's stdout/stderr on this machine and republish it as console              output for the shard. Off by default and deliberately so - see              `ShardAgentLauncher` for why leaving it on couples an agent's lifetime to the webapi's.               It exists because an agent that dies before it can reach the bus has no other voice. Its output is inherited by whatever console launched the webapi, which on a remote spire is a machine you are not looking at - so the failure arrives as a bare exit code and nothing else. |
| `logProcessLaunches` | boolean |  | Diagnostic. Writes every process launch (command line, working directory, and the environment AMP set) into the shard's console. See `SpireSettings.LogProcessLaunches` for why this is a spire setting rather than the developer-build toggle it started as. |
| `isPubliclyRoutable` | boolean |  | Convenience: whether this spire can front publications / be reached directly |
| `isPublicEntryNode` | boolean |  | Convenience: this machine relays and fronts the panel, and never hosts shards |
| `canHostShards` | boolean |  | Whether this machine may host shards at all. A public entry node may not, ever, and that is structural rather than a preference: it was never issued a shard signing key, so a shard placed here could not be given a credential and its agent would be refused by the bus at connect. Checking this at placement is what turns that into a sentence instead of a mystery. |
| `acceptsNewShards` | boolean |  | Whether a new shard may be placed here: the machine is able to host at all, AND the operator is currently letting it. Two different reasons with the same effect, kept apart because only one of them is something the operator can change. |

<a id="schema-spireplatforminfo"></a>
#### SpirePlatformInfo

Hardware/OS facts a spire reports about itself, detected via Detection and published to JetStream for dissemination. Presentation/discoverability only - operators never edit this.

| Field | Type | Required | Description |
|---|---|---|---|
| `os` | [HostPlatform](#schema-hostplatform) |  | The operating system family the host runs |
| `architecture` | [Architecture](#schema-architecture) |  | The host CPU architecture (e.g. x86_64, aarch64) |
| `osName` | string |  | Pretty OS name, e.g. "Ubuntu 24.04" / "Windows 11 Pro" |
| `hardwareName` | string |  | Board/hardware platform name where available (DMI / device-tree) |
| `hostname` | string |  | The host's machine/network name |
| `cpu` | [ProcessorInfo](#schema-processorinfo) |  | Detected CPU topology and identity |
| `installedRamMb` | integer (int32) |  | Total physical RAM installed, MB |
| `virtualization` | [VirtualizationType](#schema-virtualizationtype) |  | The virtualization/container environment the host runs under, if any |
| `reportedIps` | [ReportedIpInfo](#schema-reportedipinfo)[] |  | All non-loopback IP addresses the host actually has, with per-address facts |
| `installedJava` | [JavaRuntime](#schema-javaruntime)[] |  | Java runtimes installed on this host, for templates that need one and are not containerised.               Reported by the spire rather than discovered on demand because the choice has to be made before there is anything running to ask: at shard creation there is no agent, and while a shard is stopped there may be none either. Containerised shards never consult this - their Java comes from the image, and the setting gates to the container branch instead. |
| `externalIp` | string |  | The address the internet sees this host as, or empty when it could not be determined. Every other fact here is observable from the machine itself; this one is not, and no other spire can work it out on this host's behalf - which is why it is reported rather than probed on demand. A host behind NAT has no reachable address among IReadOnlyList<ReportedIpInfo> SpirePlatformInfo.ReportedIps, so this is the only candidate a tunnel or a join file could offer it. Set by the reporter, not SpirePlatformInfo Detection.Detect(Action<string>? warn = null). |
| `reachability` | [NetworkReachability](#schema-networkreachability) |  | How this host reaches the internet, which is what says whether string SpirePlatformInfo.ExternalIp is usable: Direct means it is an address of this machine, Nat means it needs a port forward, and Cgnat means no forward can help and only an outbound tunnel will work. |
| `freeDiskMb` | integer (int64) |  | Free space on the drive the spire runs on, MB |
| `totalDiskMb` | integer (int64) |  | Total capacity of the drive the spire runs on, MB (0 = unknown) |
| `freeRamMb` | integer (int32) |  | Currently available physical RAM, MB |
| `loadAvgPercent` | integer (int32) |  | System load as a percentage (Linux 1-minute load average times 100); -1 when unavailable |
| `reportedAt` | string (date-time) |  | When this snapshot was taken |

<a id="schema-spireportentry"></a>
#### SpirePortEntry

One expected port on a spire, with its owner and live status

| Field | Type | Required | Description |
|---|---|---|---|
| `port` | integer (int32) | yes | The port number |
| `protocol` | string | yes | The port's protocol (for example TCP, UDP, or Both) |
| `ref` | string | yes | The port's template reference key |
| `name` | string | yes | The port's human-readable name, falling back to its ref |
| `shardId` | string (uuid) | yes | The id of the shard that owns the port |
| `shardName` | string | yes | The display name of the owning shard |
| `ipBinding` | string | yes | The IP address the port is bound to |
| `running` | boolean | yes | Whether the owning shard is currently running |
| `listening` | boolean | yes | Whether the port was seen listening in the owner's latest metrics sample |
| `conflict` | boolean | yes | Whether another shard claims the same port on an overlapping binding |

<a id="schema-spirerole"></a>
#### SpireRole

What a machine was added to do. Decided by the affordance the operator used to add it, stamped at enrollment, and never operator-editable afterwards: a role that could be edited is not a guarantee, and callers rely on this one.

Type: integer

<a id="schema-spirerosteritem"></a>
#### SpireRosterItem

One spire's entry in the cluster roster: its definition plus live liveness

| Field | Type | Required | Description |
|---|---|---|---|
| `spire` | [SpireDefinition](#schema-spiredefinition) | yes | The spire definition, with its local flag stamped |
| `online` | boolean | yes | Whether the spire is currently reporting heartbeats |
| `lastSeen` | string? (date-time) | yes | When the spire was last seen, or null if never |
| `version` | string? | yes | The AMP version the spire last reported, or null if it never has |
| `behind` | boolean | yes | True when the spire runs an older version than the installation and needs updating |
| `installationVersion` | string? | yes | The version the installation is on: the highest any spire reports |

<a id="schema-spirestartupentry"></a>
#### SpireStartupEntry

One shard's place in a spire's boot sequence

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) | yes | The shard's unique id |
| `shardName` | string | yes | The shard's display name |
| `startOnBoot` | boolean | yes | Whether the shard is started automatically when the spire boots |
| `order` | integer (int32) | yes | The shard's startup order (lower starts earlier) |

<a id="schema-spirestartupentrybody"></a>
#### SpireStartupEntryBody

A submitted startup entry; list position sets the order, so only the boot flag is carried

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) | yes | The shard to configure |
| `startOnBoot` | boolean | yes | Whether the shard should start automatically when the spire boots |

<a id="schema-spirestate"></a>
#### SpireState

Lightweight heartbeat state for a spire - written frequently by the spire itself

| Field | Type | Required | Description |
|---|---|---|---|
| `spireId` | string (uuid) |  | The spire this state belongs to |
| `online` | boolean |  | True while the spire is considered reachable (recent heartbeat) |
| `lastHeartbeat` | string (date-time) |  | Timestamp of the most recent heartbeat from the spire |
| `shardCount` | integer (int32) |  | Number of shards currently assigned to this spire |
| `cpuUsagePercent` | number/string (float) |  | Machine-wide CPU utilisation percentage at the last report |
| `memoryUsedMb` | integer (int64) |  | Physical memory in use on the host, MB |
| `memoryTotalMb` | integer (int64) |  | Total physical memory installed on the host, MB |
| `networkInKBps` | number/string (float) |  | Machine-level NIC throughput (all up, non-loopback/tunnel interfaces), averaged over the reporter interval. Spire-wide, not per-shard - true per-shard network accounting arrives with container mode. |
| `networkOutKBps` | number/string (float) |  | Machine-level outbound NIC throughput, averaged over the reporter interval. See float SpireState.NetworkInKBps |
| `diskActivePercent` | number/string? (float) |  | "Active time" of the drive backing the DEFAULT datastore, as a 0-100 percentage of the reporter interval the disk had at least one I/O in flight - the same figure Windows Task Manager shows as disk "Active time" / Linux iostat as "%util". A busy indicator, not true saturation (an SSD can read 100% while still having headroom). Null when it can't be sampled (no default datastore, counter unavailable, priming). |
| `busLatencyMs` | number/string (double) |  | Round-trip latency (ms) from this spire to the NATS bus, measured each heartbeat |
| `platform` | null or [SpirePlatformInfo](#schema-spireplatforminfo) |  |  |

<a id="schema-spiretunnelstatus"></a>
#### SpireTunnelStatus

Live tunnel health, written by the FRONT spire's reconciler (a separate key so frequent status writes never race the setup fields on the main record). "Up" is proved end-to-end: an AMP-to-AMP UDP echo across the tunnel addresses, not just interface presence - so it only goes green when the actual data path works.

| Field | Type | Required | Description |
|---|---|---|---|
| `tunnelId` | string (uuid) |  |  |
| `up` | boolean |  |  |
| `rttMs` | number/string (double) |  |  |
| `lastProbeAt` | string (date-time) |  |  |
| `phase` | [TunnelPhase](#schema-tunnelphase) |  | Where the tunnel is in its bring-up (drives the progress readout as it comes up) |
| `faultSpireId` | string? (uuid) |  | For a fault phase, which end is at fault (so the UI can name the spire); else null |
| `error` | string? |  | The reason for an AgentRejected phase, or null: the agent's refusal of a rendered config, or the at-fault spire's own refusal to render one (see string? SpireTunnel.FrontAgentError) |

<a id="schema-sshkeydto"></a>
#### SshKeyDto

One of the caller's registered SSH public keys

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | The key's unique identifier |
| `label` | string | yes | The user-supplied label |
| `keyType` | string | yes | Key type as in authorized_keys, e.g. `ssh-ed25519` |
| `fingerprint` | string | yes | SHA256 fingerprint, matching `ssh-keygen -lf` |
| `bits` | integer (int32) | yes | Key size in bits |
| `addedAt` | string (date-time) | yes | When the key was registered |
| `lastUsedAt` | string? (date-time) | yes | When the key last authenticated an SFTP session, or null if never |
| `expiresAt` | string? (date-time) | yes | When the key stops authenticating, or null if it does not expire |

<a id="schema-startmigrationrequest"></a>
#### StartMigrationRequest

Request body for starting a same-spire datastore migration

| Field | Type | Required | Description |
|---|---|---|---|
| `targetDatastoreId` | string (uuid) | yes | The datastore to copy the shard's working directory into |

<a id="schema-steamanonymousloginmode"></a>
#### SteamAnonymousLoginMode

App.SteamUpdateAnonymousLogin - whether SteamCMD downloads need a real Steam account (AMP 2 semantics; member names match template values).

Type: integer

<a id="schema-steambans"></a>
#### SteamBans

What Steam says about an account's bans. Bans are public whatever the profile's visibility, so these are known for private profiles too.

| Field | Type | Required | Description |
|---|---|---|---|
| `vacBanned` | boolean | yes | Whether the account has a VAC (Valve Anti-Cheat) ban on record |
| `vacBans` | integer (int32) | yes | How many VAC bans; null when only the yes or no is known (the community profile) |
| `gameBans` | integer (int32) | yes | How many bans a game's own developer has issued; null when not known |
| `daysSinceLastBan` | integer (int32) | yes | Days since the most recent VAC or game ban; null when not known or none |
| `communityBanned` | boolean? | yes | Banned from the Steam Community; null when not known |
| `tradeBanned` | boolean | yes | Banned from trading and the market |
| `any` | boolean |  | Whether there is any ban on record at all |

<a id="schema-steamprofile"></a>
#### SteamProfile

What AMP learns about a Steam account from its Steam64 id: the public profile, from the Steam Web API when a key is set and from the community profile page otherwise.

| Field | Type | Required | Description |
|---|---|---|---|
| `steamId` | string | yes | The Steam64 id |
| `personaName` | string? | yes | The name they currently go by on Steam |
| `avatarHash` | string? | yes | Their avatar, as the hash Steam's image servers file it under; null for Steam's default |
| `visibility` | [SteamVisibility](#schema-steamvisibility) | yes | Who may see the profile's details |
| `createdAt` | string? (date-time) | yes | When the account was made; only for public profiles |
| `countryCode` | string? | yes | The country they have put on the profile, lower-case ISO 3166-1; only for public profiles, and only when they set one |
| `bans` | null or [SteamBans](#schema-steambans) | yes |  |
| `limited` | boolean? | yes | A limited account: one that has never spent money on Steam, so it cannot add friends or use the market. Only the community profile says; null otherwise. |
| `source` | string | yes | Where it came from: string SteamProfiles.FromWebApi or string SteamProfiles.FromCommunity |
| `profileUrl` | string? |  | Their profile page, by id rather than custom address, so it survives a rename |

<a id="schema-steamvisibility"></a>
#### SteamVisibility

Who can see a Steam profile's details, as its owner has set it

Type: object enum: `Unknown`, `Private`, `FriendsOnly`, `Public`

<a id="schema-step"></a>
#### Step

Type: [StepTaskStep](#schema-steptaskstep) or [StepIfStep](#schema-stepifstep) or [StepSwitchStep](#schema-stepswitchstep) or [StepStopStep](#schema-stepstopstep)

<a id="schema-stepifstep"></a>
#### StepIfStep

Branches on condition rows over the run context. Fully nestable

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string enum: `if` |  |  |
| `conditions` | [Condition](#schema-condition)[] |  |  |
| `match` | [ConditionMatch](#schema-conditionmatch) |  |  |
| `then` | [Step](#schema-step)[] |  |  |
| `else` | [Step](#schema-step)[] |  |  |
| `id` | string |  | Stable identity within the schedule - the run journal keys outcomes on it |

<a id="schema-stepoutcome"></a>
#### StepOutcome

One step's outcome within a run - the journal entry the UI renders on the tree

| Field | Type | Required | Description |
|---|---|---|---|
| `stepId` | string |  |  |
| `lane` | string |  | "main", or "bg:{stepId}" for a background lane spawned by an un-awaited task |
| `status` | [StepStatus](#schema-stepstatus) |  |  |
| `detail` | string? |  | Human detail: task output, error text, or which branch/case matched and why |
| `startedAt` | string? (date-time) |  |  |
| `duration` | integer? |  |  |

<a id="schema-steprefusal"></a>
#### StepRefusal

A step AMP would not perform because a policy forbids it. The step is skipped rather than failed, the rest of the run carries on, and the refusal is shown on the schedule and audited once.

| Field | Type | Required | Description |
|---|---|---|---|
| `notice` | [ApiError](#schema-apierror) | yes | Why the step was refused, as a coded error the schedules tab translates |
| `stepId` | string |  | The refused step's id, set by the interpreter that ran it |

<a id="schema-stepstatus"></a>
#### StepStatus

Type: object enum: `Succeeded`, `Failed`, `Skipped`, `NotReached`

<a id="schema-stepstopstep"></a>
#### StepStopStep

Stops the CURRENT lane cleanly (background lanes already running finish)

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string enum: `stop` |  |  |
| `id` | string |  | Stable identity within the schedule - the run journal keys outcomes on it |

<a id="schema-stepswitchstep"></a>
#### StepSwitchStep

Evaluates string SwitchStep.Value once and runs the FIRST matching case (top-to-bottom), else IReadOnlyList<Step> SwitchStep.Default. Sugar for an if/else-if chain, presented as a switch.

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string enum: `switch` |  |  |
| `value` | string |  |  |
| `cases` | [SwitchCase](#schema-switchcase)[] |  |  |
| `default` | [Step](#schema-step)[] |  |  |
| `id` | string |  | Stable identity within the schedule - the run journal keys outcomes on it |

<a id="schema-steptaskstep"></a>
#### StepTaskStep

Executes one task from the `TaskCatalog`. Parameters are {@Var} templates resolved against the run context (the UI presents variables as chips; the braces syntax is wire-only).

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string enum: `task` |  |  |
| `taskType` | string |  | Catalog id of the task to run (e.g. "app.console.send", "shard.snapshot.create") |
| `parameters` | map<string, string> |  | Per-parameter {@Var} template values, keyed by the catalog's parameter names |
| `await` | boolean |  | For ASYNC task types only (catalog-declared): true = block this lane until the work completes (flow continues downwards); false = start the work and continue immediately, with IReadOnlyList<Step> TaskStep.OnSuccess/IReadOnlyList<Step> TaskStep.OnFailure running later in a concurrent background lane ("sideways"). Ignored for synchronous task types. |
| `onSuccess` | [Step](#schema-step)[] |  | Steps run after (and only if) the task succeeds |
| `onFailure` | [Step](#schema-step)[] |  | Steps run if the task fails. EMPTY = unhandled: FailurePolicy? TaskStep.FailureOverride (or the task type's default policy) applies (Continue → run is marked CompletedWithErrors; Abort → remaining steps are NotReached and the run is Failed). NON-empty = the user handled the failure (like catch): the branch runs and the failure alone doesn't fail the run. |
| `failureOverride` | null or [FailurePolicy](#schema-failurepolicy) |  |  |
| `id` | string |  | Stable identity within the schedule - the run journal keys outcomes on it |

<a id="schema-switchcase"></a>
#### SwitchCase

One case of a SwitchStep - any operator is allowed, not just equality

| Field | Type | Required | Description |
|---|---|---|---|
| `operator` | [ConditionOperator](#schema-conditionoperator) |  |  |
| `comparand` | string |  |  |
| `steps` | [Step](#schema-step)[] |  |  |

<a id="schema-takebody"></a>
#### TakeBody

Request body for taking a new snapshot

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | [SnapshotKind](#schema-snapshotkind) | yes | Whether this is a full snapshot or a delta against its parent |
| `content` | [SnapshotContent](#schema-snapshotcontent) | yes | Flags for which parts to capture: game state, base install data, and/or AMP configuration |
| `name` | string? | yes | Optional friendly name for the snapshot node |
| `description` | string? | yes | Optional longer description shown on the timeline |
| `sticky` | boolean | yes | When true the snapshot is pinned and exempt from automatic retention pruning |

<a id="schema-taskdescriptordto"></a>
#### TaskDescriptorDto

Describes one task type available to the editor

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Stable task type id |
| `category` | string | yes | Grouping used to organise tasks in the editor |
| `isAsync` | boolean | yes | True when the task runs asynchronously (its completion is awaited by the runner) |
| `defaultFailure` | string | yes | Default failure-handling mode as a string |
| `parameters` | [TaskParamDto](#schema-taskparamdto)[] | yes | The task's configurable parameters |

<a id="schema-taskparamdto"></a>
#### TaskParamDto

Describes one configurable parameter of a task

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Parameter name |
| `inputType` | string | yes | Input control hint the editor should render |
| `required` | boolean | yes | Whether a value must be supplied |

<a id="schema-taskprincipal"></a>
#### TaskPrincipal

Records who is responsible for an action and how that responsibility was established. Every PrincipalKind value maps to exactly one factory method so the fields are always populated consistently.

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | [PrincipalKind](#schema-principalkind) |  | How this principal was established. The primary discriminator for security queries - filter on `Kind == Impersonation` to find all impersonation events without false-positives from scheduled jobs. |
| `userId` | string? |  | The direct actor. Null for PrincipalKind.System and PrincipalKind.Scheduled where no human acted directly. |
| `userDisplayName` | string? |  | Display name captured at the time of the action for audit readability |
| `onBehalfOfUserId` | string? |  | The user whose context this action belongs to when the direct actor is             the system or a different user.             * Scheduled: the user who created the schedule. * Impersonation: the user being impersonated. |
| `onBehalfOfUserDisplayName` | string? |  | Display name of the on-behalf-of user, captured at action time |
| `systemSource` | string? |  | Human-readable description of the initiating system component for PrincipalKind.System and PrincipalKind.Scheduled principals, e.g. `"Daily Backup"` or `"Instance Health Check"`. |

<a id="schema-templatematch"></a>
#### TemplateMatch

A template the candidate might be, and why

| Field | Type | Required | Description |
|---|---|---|---|
| `templateSlug` | string | yes |  |
| `displayName` | string | yes |  |
| `confidence` | [MatchConfidence](#schema-matchconfidence) | yes |  |
| `evidence` | [MatchEvidence](#schema-matchevidence)[] | yes |  |

<a id="schema-templatesource"></a>
#### TemplateSource

Configuration for a single template source - one repository at one branch. Multiple sources can be registered; their catalogues are merged in priority order.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string |  | Stable identifier for this source. Used as the local directory name and as a prefix in slug collision resolution. Must be filesystem-safe. Example: "official", "community-fork", "my-private-templates" |
| `url` | string |  | Remote git URL (HTTPS or SSH), or `LOCAL/` for a development source.               A URL beginning LOCAL/ means "this directory is mine, leave it alone": AMP never clones, fetches or resets it, and reads whatever files are on disk. That is what makes it possible to write a template in an editor and reload it, instead of having to commit and push to a git remote for every change. Anything after the prefix names the directory under string TemplateRepositoryOptions.BasePath; with nothing after it, the source string TemplateSource.Id is used - so LOCAL/ alone is the shortest form that works. |
| `isLocal` | boolean |  | Whether this source is a local development directory. Checked in every place that would otherwise touch git, because running a hard reset over somebody's work-in-progress template would destroy exactly the thing this feature exists to support. |
| `localDirectoryName` | string? |  | Directory name for a local source: whatever follows `LOCAL/`, else the source id. Any path separators or traversal are stripped - the name must stay a single directory under BasePath. |
| `isArchive` | boolean |  | Whether this source is a downloadable archive (`.zip`, `.tgz`, `.tar.gz` or `.tar`) rather than a git repository. An archive source is fetched over HTTP(S) (or read from a `file://` URL), unpacked into its own directory under string TemplateRepositoryOptions.BasePath, and only re-downloaded when the server reports it has changed. It has no branch. |
| `branch` | string |  | Branch to track. Defaults to "main". Meaningless for an archive source. |
| `localPath` | string? |  | Optional override for the local clone path. When null, the path is derived as `{BasePath}/{Id}`. |
| `priority` | integer (int32) |  | Sources are merged in ascending priority order - higher number wins on slug collision. The official repos take 0 and 1; user repos should use 10+. |
| `disabled` | boolean |  | When true this source is skipped during sync/load without removing it from configuration. Useful for temporarily disabling a repo. |

<a id="schema-termsacceptancebody"></a>
#### TermsAcceptanceBody

The holder's acceptance of the current licence terms, as the panel collected it

| Field | Type | Required | Description |
|---|---|---|---|
| `termsVersion` | integer (int32) | yes | The version accepted; the required version the agreement was fetched for |
| `agreementHash` | string | yes | The hash of the agreement text shown. A hash that is no longer current is refused |
| `attestation` | string | yes | The attestation exactly as it was ticked |
| `locale` | string | yes | The panel's language at the time |
| `clickedAt` | string (date-time) | yes | When the accepter committed |

<a id="schema-timeformat"></a>
#### TimeFormat

Type: object enum: `System`, `Hours12`, `Hours24`, `None`

<a id="schema-topuser"></a>
#### TopUser

Who spent the most time

| Field | Type | Required | Description |
|---|---|---|---|
| `userKey` | string | yes |  |
| `displayName` | string? | yes |  |
| `sessions` | integer (int32) | yes |  |
| `minutes` | number/string (double) | yes |  |
| `percent` | number/string (double) | yes |  |
| `servers` | integer (int32) |  | How many servers they spent it on. More than one only for an id that means the same person everywhere, a SteamID for instance; a name is only ever compared within one server. Default `1`. |
| `online` | boolean |  | They have a session still open: connected right now Default `false`. |

<a id="schema-trigger"></a>
#### Trigger

Type: [TriggerCronTrigger](#schema-triggercrontrigger) or [TriggerIntervalTrigger](#schema-triggerintervaltrigger) or [TriggerEventTrigger](#schema-triggereventtrigger)

<a id="schema-triggercrontrigger"></a>
#### TriggerCronTrigger

Fires at times described by a cron expression. Uses Cronos for parsing - supports standard 5-field and extended 6-field (with seconds) expressions. DST-safe via string CronTrigger.TimeZoneId.

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string enum: `cron` |  |  |
| `expression` | string |  | Cronos-compatible cron expression, e.g. "0 2 * * MON" |
| `timeZoneId` | string |  | IANA time zone id for DST-correct scheduling (e.g. "Europe/London"). Defaults to UTC. Explicit per schedule - "server timezone" is ambiguous once there's more than one machine. |

<a id="schema-triggerdescriptordto"></a>
#### TriggerDescriptorDto

Describes one trigger type available to the editor

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Stable trigger type id |
| `category` | string | yes | Grouping used to organise triggers in the editor |
| `emits` | [EmittedValueDto](#schema-emittedvaluedto)[] | yes | Signals this trigger makes available to downstream conditions |
| `label` | string |  | For a template-declared event, the template locale KEY naming it; empty for AMP's own triggers, whose names live in AMP's own translations under `schedule.trigger.*`. Sent as a key rather than resolved here so switching language re-renders without refetching the catalog, exactly as setting labels already do. Default `""`. |
| `description` | string |  | The same, for the sentence explaining when it fires Default `""`. |

<a id="schema-triggereventtrigger"></a>
#### TriggerEventTrigger

Fires in response to a catalogued event on the bus. string EventTrigger.EventType is a `TriggerCatalog` id (e.g. "player.joined"); the catalog maps it to the concrete NATS subject for the schedule's shard and declares the values the event emits into the run context. The event's agent-stamped EventId drives firing dedup across observers.

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string enum: `event` |  |  |
| `eventType` | string |  | TriggerCatalog event id (e.g. "player.joined", "app.stateChanged") |

<a id="schema-triggerintervaltrigger"></a>
#### TriggerIntervalTrigger

Fires repeatedly on a fixed interval

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string enum: `interval` |  |  |
| `interval` | string |  |  |
| `anchor` | string (date-time) |  | Canonical anchor point for interval calculation - all nodes derive the same `floor(now / interval) * interval` bucket from this. Defaults to Unix epoch so intervals are globally aligned. |

<a id="schema-tunneldto"></a>
#### TunnelDto

A spire-to-spire WireGuard tunnel with both ends, addressing, and last reported status

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (uuid) | yes | Tunnel identifier |
| `frontSpireId` | string (uuid) | yes | Publicly routable spire that terminates the tunnel |
| `backSpireId` | string (uuid) | yes | Private spire that dials out to the front |
| `slot` | integer (int32) | yes | Deterministic slot (0-255) that drives the address pair and listen port |
| `frontEndpointHost` | string | yes | Public host the back spire dials, from the front spire's PublicHost setting - or, for a public entry node still being set up, from the tunnel itself, since that machine has no record yet |
| `interfaceName` | string | yes | WireGuard interface name shared by both ends |
| `listenPort` | integer (int32) | yes | UDP port the front spire listens on for this tunnel |
| `frontAddress` | string | yes | Tunnel-internal address assigned to the front end |
| `backAddress` | string | yes | Tunnel-internal address assigned to the back end |
| `front` | [TunnelEndDto](#schema-tunnelenddto) | yes | The front end's keys and system-agent state |
| `back` | [TunnelEndDto](#schema-tunnelenddto) | yes | The back end's keys and system-agent state |
| `status` | null or [SpireTunnelStatus](#schema-spiretunnelstatus) | yes |  |
| `createdAt` | string (date-time) | yes | When the tunnel record was created |
| `permanentForEntryNode` | boolean | yes | True for a public entry node's tunnel: it carries the bus rather than any route, so it exists because that machine does and is not reference-counted off publications like every other tunnel. The canvas needs to know, or it will describe a permanent wire as an unused one. |

<a id="schema-tunnelenddto"></a>
#### TunnelEndDto

One end (front or back) of a tunnel, with the state of the system agent that brings it up

| Field | Type | Required | Description |
|---|---|---|---|
| `spireId` | string (uuid) | yes | Identifier of the spire this end runs on |
| `spireName` | string | yes | Display name of the spire this end runs on |
| `keyPublished` | boolean | yes | True once this end has generated and published its WireGuard public key |
| `agentInstalled` | boolean | yes | True once the system agent has this end's WireGuard interface up |
| `agentReachable` | boolean | yes | True while this spire's system agent is present and reporting. False means no agent has run here or it has stopped - and with no manual path, this end cannot come up |
| `agentError` | string? | yes | Why this end is not up when something refused it: its agent (e.g. an OS-reserved listen port) or its spire, which could not render the config (e.g. WireGuard missing); else null |

<a id="schema-tunnelfrontdto"></a>
#### TunnelFrontDto

A spire that could be the public end of a tunnel, with the addresses it could terminate one on

| Field | Type | Required | Description |
|---|---|---|---|
| `spireId` | string (uuid) | yes | Identifier of the eligible spire |
| `displayName` | string | yes | Its display name |
| `declaredHost` | string | yes | Its PublicHost setting, or empty. When set it is simply what gets dialled and Hosts is not a choice to make; when empty, one of them is adopted as it. |
| `hosts` | [PublicHostOption](#schema-publichostoption)[] | yes | Candidate addresses, best-first, with the recommended one flagged |

<a id="schema-tunnelphase"></a>
#### TunnelPhase

Where a tunnel is in its (continuously reconciled) bring-up. A single ladder both the tunnel link on the canvas and every publication riding the tunnel report against, so "what is it doing right now" reads the same everywhere. String-serialized so the API and UI speak phase NAMES.

Type: object enum: `Provisioning`, `Connecting`, `Up`, `Degraded`, `AgentUnreachable`, `AgentRejected`, `Releasing`

<a id="schema-unreadablepath"></a>
#### UnreadablePath

Why a path could not be looked at, so the scan can say so instead of returning less

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | The absolute path |
| `code` | string | yes | i18n key describing the reason |
| `message` | string | yes | English fallback, with the operating system's own words where it gave any |

<a id="schema-updategatewayegressbody"></a>
#### UpdateGatewayEgressBody

Request to set which private networks a gateway-routed server may reach through its front

| Field | Type | Required | Description |
|---|---|---|---|
| `egressAllowList` | string[] | yes | IPv4 networks in CIDR form no wider than a /8, at most 32. Empty means the internet only, which is the default |

<a id="schema-updateprofilerequest"></a>
#### UpdateProfileRequest

Fields the caller may change on their own profile

| Field | Type | Required | Description |
|---|---|---|---|
| `displayName` | string? | yes | New display name; blank clears it |
| `email` | string? | yes | New email address; ignored if unchanged |

<a id="schema-updatepublicationbody"></a>
#### UpdatePublicationBody

Partial update to a publication; only non-null fields are applied

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean? | yes | When set, enables or disables the publication |
| `publicPort` | integer (int32) | yes | When set, rebinds the publication to a new public port (1-65535) |

<a id="schema-updaterolebody"></a>
#### UpdateRoleBody

Partial update for a role. Only non-null fields are applied

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string? | yes | New display name, or null to leave unchanged |
| `description` | string? | yes | New description, or null to leave unchanged |
| `permissions` | string[]? | yes | Replacement grant set, or null to leave unchanged. Every grant added or removed is subject to the delegation rule |

<a id="schema-updaterolledbackdto"></a>
#### UpdateRolledBackDto

An update this machine tried and amp3-host rolled back, as its reason record says

| Field | Type | Required | Description |
|---|---|---|---|
| `version` | string | yes | The version that was rolled back |
| `kind` | string | yes | Why, as the marker's kind: `crash-loop`, `never-ready`, `revert`, `payload`, or empty when the record does not say |
| `atUtc` | string (date-time) | yes | When it was rolled back |
| `reason` | string | yes | The reason as the supervisor logged it |

<a id="schema-updatestage"></a>
#### UpdateStage

A single step in an application's install/update sequence: what action to run, its inputs and target, the platforms it applies to, and how failures are handled. Ported from the AMP 2 specification.

| Field | Type | Required | Description |
|---|---|---|---|
| `updateSource` | [UpdateSteps](#schema-updatesteps) |  | The kind of step this stage performs - the runner dispatches on this |
| `updateStageName` | string |  | Short name for this stage, shown in progress output |
| `updateStageDescription` | string |  | Longer description of what this stage does |
| `updateSourcePlatform` | [HostPlatform](#schema-hostplatform) |  | Host platform(s) this stage runs on |
| `updateSourceArch` | [HostArchitecture](#schema-hostarchitecture) |  | CPU architecture filter (AMP 2 semantics: stages run only on matching hosts; default All) |
| `updateSourceData` | string |  | Where the update is coming from. Such as an URL, App ID, etc |
| `updateSourceArgs` | string |  | Extra arguments passed to the step (e.g. command-line args for an executable) |
| `updateSourceVersion` | string |  | Version or branch/tag to fetch, where the step supports it |
| `updateSourceExtra` | string |  | Additional step-specific data |
| `updateSourceTarget` | string |  | Where the update data is being saved to |
| `unzipUpdateSource` | boolean |  | Extract the fetched source after downloading it |
| `overwriteExistingFiles` | boolean |  | Overwrite files that already exist at the target |
| `forceDownloadPlatform` | null or [HostPlatform](#schema-hostplatform) |  |  |
| `updateSourceConditionSetting` | string |  | Setting key that gates this stage; the stage runs only when its value matches string UpdateStage.UpdateSourceConditionValue |
| `updateSourceConditionValue` | string |  | Value that string UpdateStage.UpdateSourceConditionSetting must equal for this stage to run |
| `deleteAfterExtract` | boolean |  | Delete the downloaded archive after extracting it |
| `runInBackground` | boolean |  | Start this stage's process and move on: the process is a helper the application uses (a database server, a dedicated server launcher), its output keeps reaching the console, and it is ended with the application's run, or when the update finishes for an update stage |
| `skipOnFailure` | boolean |  | Continue the update sequence even if this stage fails |
| `processToolOutput` | boolean |  | Pass the stage's output through the console's handlers. Always the case: every stage line goes through the template's console handlers whatever its value |
| `oneShot` | boolean |  | Run this stage only once (on first install) rather than on every update |

<a id="schema-updatestatusdto"></a>
#### UpdateStatusDto

The update posture for this install, projected for the Updates card.

| Field | Type | Required | Description |
|---|---|---|---|
| `currentVersion` | string | yes | The version this install is currently running |
| `stream` | string | yes | The release channel the installation follows (Mainline / Development / Preview) |
| `availableStreams` | string[] | yes | The channels this installation may be switched to, including the current one |
| `latestVersion` | string | yes | The newest version offered on the channel |
| `updateAvailable` | boolean | yes | True when a newer version than the current one is offered |
| `mandatory` | boolean | yes | True when the current version is below the channel's minimum (a forced upgrade) |
| `codename` | string | yes | Codename of the latest version |
| `releaseNotesUrl` | string | yes | Release-notes URL for the latest version |
| `supervised` | boolean | yes | True when running under amp3-host (a prerequisite for applying an update) |
| `canApply` | boolean | yes | True when an update is available, this install is supervised, and the caller may apply it |
| `canChangeStream` | boolean | yes | True when the caller may switch the installation to another channel |
| `note` | string? | yes | A non-fatal code explaining why no update is offered despite a successful check |
| `installationVersion` | string? | yes | The version the installation is on: the highest any spire reports |
| `spiresBehind` | [SpireBehindDto](#schema-spirebehinddto)[] | yes | Spires reporting an older version than the installation; each can be updated on its own |
| `canUpdateSpires` | boolean | yes | True when the caller may tell a lagging spire to update |
| `packageManaged` | boolean | yes | True when this machine's updates are the package manager's job (Updates:PackageManaged), so nothing here applies, switches channel or pushes to spires |
| `rolledBack` | null or [UpdateRolledBackDto](#schema-updaterolledbackdto) | yes |  |

<a id="schema-updatesteps"></a>
#### UpdateSteps

The kind of action an update/install stage performs; the stage runner dispatches on this

Type: integer

<a id="schema-updateuserbody"></a>
#### UpdateUserBody

Partial update for a user. Only non-null fields are applied

| Field | Type | Required | Description |
|---|---|---|---|
| `displayName` | string? | yes | New friendly name, or null to leave unchanged |
| `email` | string? | yes | New contact email, or null to leave unchanged |
| `roleIds` | string[]? | yes | Replacement role set, or null to leave unchanged. Invalid or self-role ids are dropped |
| `suspended` | boolean? | yes | New suspended state, or null to leave unchanged. Changing this requires the suspend capability |
| `passwordExpires` | boolean? | yes | New password-expiry flag, or null to leave unchanged |
| `requirePasswordChange` | boolean? | yes | New forced-change flag, or null to leave unchanged. Newly forcing a change revokes live sessions |

<a id="schema-uploadstartbody"></a>
#### UploadStartBody

Parameters for beginning a chunked upload

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Destination path for the assembled file, relative to the shard working directory |
| `size` | integer (int64) | yes | Expected total file size in bytes, used to preallocate the destination and validate completion |

<a id="schema-usagescanintervalbody"></a>
#### UsageScanIntervalBody

How often the spire should re-measure its datastores

| Field | Type | Required | Description |
|---|---|---|---|
| `minutes` | integer (int32) | yes | Scan cadence in minutes. Clamped to the supported range rather than rejected |

<a id="schema-useraddressesanswer"></a>
#### UserAddressesAnswer

The addresses one user has connected from

| Field | Type | Required | Description |
|---|---|---|---|
| `addresses` | [AddressUse](#schema-addressuse)[] | yes | Most recently used first, each with how many others the caller can see used it |
| `serverInfo` | map<string, [AnalyticsServer](#schema-analyticsserver)> | yes |  |
| `isDemo` | boolean |  |  Default `false`. |

<a id="schema-userdeleteddto"></a>
#### UserDeletedDto

The outcome of a user deletion that went through

| Field | Type | Required | Description |
|---|---|---|---|
| `warnings` | [ApiError](#schema-apierror)[] | yes | Parts of the account's data that could not be removed. The account itself is gone either way |

<a id="schema-userdetail"></a>
#### UserDetail

One user's time on the servers, as the caller may see it

| Field | Type | Required | Description |
|---|---|---|---|
| `userKey` | string | yes |  |
| `displayName` | string? | yes |  |
| `totals` | [SessionTotals](#schema-sessiontotals) | yes | The same figures as the summary, for this user alone |
| `servers` | [ServerUsage](#schema-serverusage)[] | yes | Where they spent it, busiest first |
| `sessions` | [SessionView](#schema-sessionview)[] | yes | Their sessions in the window, newest first |
| `firstSeen` | string? (date-time) | yes | When they first connected, on any server the caller may see user detail on |
| `lastSeen` | string? (date-time) | yes | When they most recently connected, on those same servers |
| `isActive` | boolean | yes | Whether they meet the active-user rule right now |
| `recentVisits` | integer (int32) | yes | How many sessions they began in the rule's span up to now |
| `rule` | [ActivityRule](#schema-activityrule) | yes | The active-user rule they were judged by |
| `serverInfo` | map<string, [AnalyticsServer](#schema-analyticsserver)> | yes | How to draw the servers above |
| `isDemo` | boolean |  | An invented user, from the demonstration data Default `false`. |

<a id="schema-userdto"></a>
#### UserDto

A user account and its current state

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Stable unique identifier for the account |
| `userName` | string? | yes | Login name. Unique across the tenant |
| `displayName` | string? | yes | Friendly name shown in the UI, if set |
| `email` | string? | yes | Contact email, if set |
| `roleIds` | string[] | yes | Named roles the user belongs to. Authority is the union of these roles plus the hidden self-role |
| `isAdmin` | boolean | yes | True when the account carries the administrator role claim |
| `suspended` | boolean | yes | True when the account is suspended and cannot sign in |
| `passwordExpires` | boolean | yes | True when the account's password is subject to age-based expiry |
| `requirePasswordChange` | boolean | yes | True when the user must set a new password on next sign-in |
| `createdAt` | string? (date-time) | yes | When the account was created, or null if never recorded |
| `lastLoginAt` | string? (date-time) | yes | When the user last signed in, or null if never |

<a id="schema-userexperiencelevel"></a>
#### UserExperienceLevel

Type: object enum: `New`, `UsedPreviousVersions`, `Experienced`, `None`

<a id="schema-userfacet"></a>
#### UserFacet

One acting user and how many events they produced

| Field | Type | Required | Description |
|---|---|---|---|
| `userId` | string | yes | Stable id of the acting user |
| `name` | string | yes | Display name of the user at query time |
| `count` | integer (int32) | yes | Number of events attributed to this user |

<a id="schema-usermatch"></a>
#### UserMatch

A user a search found

| Field | Type | Required | Description |
|---|---|---|---|
| `userKey` | string | yes |  |
| `displayName` | string? | yes | Their most recent name, when the caller may see names on every server they were found on |
| `userId` | string? | yes | Their raw id, a SteamID for instance, under the same condition |
| `idKind` | string | yes |  |
| `shards` | string (uuid)[] | yes | The servers the caller can see that they have been on |
| `lastSeen` | string (date-time) | yes | When they most recently connected, on any of the servers above |

<a id="schema-userpreferences"></a>
#### UserPreferences

Per-user preferences stored in NATS KV. All fields are optional - absent fields mean "use the system default".  KV key: `amp.tn.{tenant}.usr.{userId}.prefs`  Design principles:   - One record per user, updated with PATCH semantics (only changed fields written).   - All fields are nullable so callers can distinguish "explicitly set to X"     from "never set" without magic sentinel values.   - New preference fields are additive - no migration needed.

| Field | Type | Required | Description |
|---|---|---|---|
| `theme` | string? |  | User's preferred UI theme. Null = system default |
| `locale` | string? |  | User's preferred locale / language code, e.g. `"en-GB"` |
| `favouriteAppSlugs` | string[] |  | Template slugs the user has starred as favourites. Displayed at the top of the app catalogue browser. |
| `pinnedShardIds` | string[] |  | Shard ids the user has pinned to the Overview dashboard, in pin order. |
| `notifyOnStateChange` | boolean? |  | Whether to receive in-app notifications for shard state changes |
| `notifyOnUpdateAvailable` | boolean? |  | Whether to receive in-app notifications when updates are available |
| `compactShardHeader` | boolean? |  | Collapse the shard-detail header (hide the live graph) into a compact banner |
| `hideUnroutedShards` | boolean? |  | Hide servers with no route on the networking canvas. Off by default, because an unrouted server is the thing you drag onto a front to route it - but an installation past a certain size wants it on, and having to set it again on every visit is the sort of small friction that makes a screen tiring to use. |
| `hidePrivatePorts` | boolean? |  | Hide ports the application does not expose publicly on the networking canvas. On by default: a private port cannot be published, so on the screen whose whole job is publishing ports it is a row of things you are not allowed to touch. Off for the operator who wants the full picture of what a server has open. |
| `fileDetailsWidth` | integer (int32) |  | Width in CSS pixels of the file manager's details pane, as left by the user's last drag of the splitter. Null = the default width. The client clamps on read, so an out-of-range value stored here is harmless. |
| `ampExperience` | null or [UserExperienceLevel](#schema-userexperiencelevel) |  |  |
| `showTutorials` | boolean? |  | Whether guided tutorials are offered at all: the master switch behind every "Show Tutorial" button. Defaulted from UserExperienceLevel? UserPreferences.AmpExperience at onboarding (on for new / returning users, off for the experienced) and then user-adjustable from the profile dialog. |
| `tutorialPromptDismissed` | boolean? |  | Whether the user has answered "Never ask again" to the offer of the getting-started tutorial, or has already finished it. Suppresses the automatic prompt only; the per-screen "Show Tutorial" buttons are governed by bool? UserPreferences.ShowTutorials and stay available. Null/false means the prompt is still offered, which is what "Ask me next time" leaves behind. |
| `tutorialsCompleted` | string[] |  | Ids of the tutorials the user has run to the end, so a "Show Tutorial" button can present itself as a replay and the getting-started offer stops once it has been taken. |
| `accessibilityHintsEnabled` | boolean? |  | Whether to surface extra accessibility instructions in the UI (e.g. how to operate the keyboard-driven confirm slider). The accommodations themselves are always available; this only governs whether the explanatory hints are shown. Null = never asked. |
| `onboardingCompleted` | boolean? |  | Whether the user has completed the first-login onboarding wizard. Null/false means the wizard should be shown on next login, regardless of how the account was created (invite, API, setup). |
| `reducedMotion` | boolean? |  | Whether to reduce non-essential UI animation. This is IN ADDITION to the browser's prefers-reduced-motion media query, not a replacement: the OS setting is machine-wide and all-or-nothing, so this lets a user ask for calmer motion in AMP alone. Either source turning it on reduces motion. Null/false means follow the OS only. |
| `analyticsEnabled` | boolean? |  | Whether the user consents to anonymous product analytics (used later to drive Plausible). Opt-in: null/false means no analytics. Distinct from the system-level crash-report setting. |
| `streamerMode` | boolean? |  | Streamer Mode: blur every place the panel deliberately shows an IP address, so a stream or a recording of the screen never carries one. Console and log text is left alone. Null/false means addresses are shown plainly. |
| `timeFormat` | null or [TimeFormat](#schema-timeformat) |  |  |

<a id="schema-usersearch"></a>
#### UserSearch

What a user search found

| Field | Type | Required | Description |
|---|---|---|---|
| `users` | [UserMatch](#schema-usermatch)[] | yes |  |
| `serverInfo` | map<string, [AnalyticsServer](#schema-analyticsserver)> | yes |  |
| `isDemo` | boolean |  |  Default `false`. |
| `address` | null or [AddressSummary](#schema-addresssummary) |  |  |

<a id="schema-versioncandidate"></a>
#### VersionCandidate

One selectable version produced by a VersionSource

| Field | Type | Required | Description |
|---|---|---|---|
| `value` | string | yes | What gets stored in the setting |
| `display` | string | yes | What the user sees and filters on |
| `fields` | map<string, string> | yes | Named extras, including the reserved `downloadUrl` when a template was given |

<a id="schema-versioncandidatelist"></a>
#### VersionCandidateList

A cached candidate list, as stored and as served

| Field | Type | Required | Description |
|---|---|---|---|
| `candidates` | [VersionCandidate](#schema-versioncandidate)[] | yes | Versions the user can pick from, already ordered |
| `fetchedAt` | string (date-time) | yes | When this was last successfully read from upstream |
| `failureReason` | string? |  | Why the last refresh failed, when it did. Candidates may still be     populated - a stale list plus an explanation beats an empty one. |
| `fromCache` | boolean |  | Whether this was served from cache without going upstream |
| `age` | string |  | How old the data is, for the UI to say "this list may be out of date" |

<a id="schema-versionsortorder"></a>
#### VersionSortOrder

How candidates are ordered before they reach the user

Type: integer

<a id="schema-versionsource"></a>
#### VersionSource

A list of installable versions read from an upstream manifest, so a template can offer a real              dropdown instead of asking the user to type a version string.               Each candidate carries three things: the string VersionSource.Value that gets stored, the string VersionSource.Display string the user picks from, and a bag of named Dictionary<string, string> VersionSource.Fields. The selected candidate's fields become {{Setting.field}} tokens usable anywhere a token already is - another source's URL, a download URL template, an update stage's arguments. That one rule is what lets a single mechanism cover every upstream, including the ones that need a second request to find the actual download.See docs/version-sources.md for the reasoning, including why a value the upstream already publishes whole (Forge's 1.20.1-47.2.0) is kept whole rather than split.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string |  | Identifies this source; a setting names it to be fed from it |
| `url` | string |  | The single upstream document, for the common case. Ignored when List<VersionSourceDocument> VersionSource.Documents is set. |
| `documents` | [VersionSourceDocument](#schema-versionsourcedocument)[] |  | Several upstream documents merged into one list, in order |
| `format` | [VersionSourceFormat](#schema-versionsourceformat) |  | How the documents are encoded |
| `select` | string |  | Path selecting the candidate nodes (JSONPath or XPath per VersionSourceFormat VersionSource.Format) |
| `value` | string |  | Path WITHIN a candidate giving the stored value. Empty or `$` means the node itself. |
| `display` | string |  | Path within a candidate giving the display string. Empty falls back to the value. |
| `fields` | map<string, string> |  | Extra named values pulled from each candidate, as `name -> path within the candidate`. These become `{{Setting.name}}` tokens once a candidate is selected, which is how a second-hop URL (Mojang) or a download filename (Paper) reaches the update stage. |
| `where` | map<string, string> |  | Values a candidate must carry to enter the list, as `field name -> required value` over Dictionary<string, string> VersionSource.Fields. Applied as the list is read, so a candidate that fails it is never cached, never offered and never what string VersionSelection.Latest means: Mojang's manifest lists snapshots first, and the latest Vanilla Minecraft has to be the latest release. A candidate without the field does not match; values compare exactly. |
| `urlTemplate` | string |  | Builds the download URL from a candidate. Tokens are `{value}`, `{display}` and any Dictionary<string, string> VersionSource.Fields name. The result is exposed as the reserved `downloadUrl` field, so an update stage is just `FetchURL` of `{{Setting.downloadUrl}}` and no new stage type is needed. |
| `sort` | [VersionSortOrder](#schema-versionsortorder) |  | Ordering applied before the list is shown |
| `cacheMinutes` | integer (int32) |  | How long a fetched list stays fresh for DISPLAY. Resolution tolerates any age. |
| `dependsOn` | string[] |  | Settings whose values this source's URLs interpolate. Load-bearing in three places: it keys the cache, it invalidates this source when one of them changes, and it tells the UI which dependent dropdowns to clear. A source with dependencies is not evaluated until they all have values. |

<a id="schema-versionsourcedocument"></a>
#### VersionSourceDocument

One upstream document a VersionSource reads. Most sources have exactly one and declare it inline on the source; the list exists for upstreams that split the same logical list across several documents, which is how NeoForge's old and new artifact naming is handled without having to work out afterwards which naming a given version used.

| Field | Type | Required | Description |
|---|---|---|---|
| `url` | string |  | Where to fetch it. May carry `{{Setting}}` tokens (see List<string> VersionSource.DependsOn) |
| `urlTemplate` | string |  | Download URL for candidates from THIS document, overriding the source's own template. Empty means use the source's. |

<a id="schema-versionsourceformat"></a>
#### VersionSourceFormat

How a version manifest is encoded, which decides the path language used to read it

Type: integer

<a id="schema-virtualizationtype"></a>
#### VirtualizationType

The virtualization or container environment a host was detected to be running under, or VirtualizationType.None for bare metal. Reported for presentation and diagnostics.

Type: integer

<a id="schema-wireguardinterface"></a>
#### WireGuardInterface

| Field | Type | Required | Description |
|---|---|---|---|
| `interfaceName` | string |  |  |

<a id="schema-wireguardinterfacestatus"></a>
#### WireGuardInterfaceStatus

| Field | Type | Required | Description |
|---|---|---|---|
| `interfaceName` | string |  |  |
| `installed` | boolean |  |  |

<a id="schema-wireguardpeer"></a>
#### WireGuardPeer

| Field | Type | Required | Description |
|---|---|---|---|
| `interfaceName` | string |  |  |
| `publicKey` | string |  |  |
| `allowedIp` | string |  |  |

<a id="schema-wireguardpeerstatus"></a>
#### WireGuardPeerStatus

| Field | Type | Required | Description |
|---|---|---|---|
| `interfaceName` | string |  |  |
| `publicKey` | string |  |  |
| `allowedIp` | string |  |  |

<a id="schema-withheldparameter"></a>
#### WithheldParameter

A step parameter whose value a response withholds

| Field | Type | Required | Description |
|---|---|---|---|
| `stepId` | string | yes | The step that holds it |
| `parameter` | string | yes | The parameter's name, such as `url` |

<a id="schema-withheldportrule"></a>
#### WithheldPortRule

A server-derived firewall rule withheld because AMP itself holds the number. The server behind it is unreachable on that port until it is moved, and nothing outside this record says why.

| Field | Type | Required | Description |
|---|---|---|---|
| `shardId` | string (uuid) | yes | The server the rule was derived for |
| `shardName` | string | yes | Its display name |
| `source` | [ServerRuleSource](#schema-serverrulesource) | yes | What asked for the rule |
| `port` | integer (int32) | yes | The number withheld |
| `protocol` | [PortProtocol](#schema-portprotocol) | yes | TCP or UDP |
| `kind` | [AmpListenerKind](#schema-amplistenerkind) | yes | Which of AMP's own listeners holds the number |
