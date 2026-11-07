# AMP 3 API surface (fresh install trace, 7 Nov 2026, second pass)

Build: AMP 3.0.0 preview channel (`amp3-host 3.0.0.6+f3d1a77cea`), Ubuntu 24.04, rootless Podman. Licence: Patreon Supporter, 30-shard limit. One shard (Veloren) is deployed.

Method: everything was read-only and against this one throwaway VM. Sources were listening sockets, on-disk config (secrets redacted), the shipped OpenAPI spec, the panel's JavaScript bundle, the panel's network calls, and a live probe of every GET route that takes no IDs other than a shard or spire ID (106 paths, each tried with and without a token). No writes, no brute forcing, and no connection to the NATS bus. No tokens, keys or IDs are recorded here.

**Markers:** **[V]** = verified live on the VM. **[S]** = stated in the shipped OpenAPI spec. **[B]** = seen in the panel's JS bundle only. **[I]** = inferred.

Companion files in this folder:
- `API_REFERENCE.md` documents every operation (263) in full: parameters, bodies, responses, permission and live result.
- `openapi-3.0.0.6.json` is the raw spec.

## 0. Headline: there is a full OpenAPI spec

- **[V]** `webapi.json` sits next to the backend binary (`data/versions/3.0.0/webapi/`). It is OpenAPI 3.1.1 ("webapi | v1"). It has **219 paths, 263 operations and 389 schemas**, with real descriptions: permission needed, NATS subject used, and error codes.
- **[V]** The server does **not** serve it. `/openapi/v1.json`, `/openapi/v1.yaml` and `/swagger/v1/swagger.json` return 401 without a token and 404 with one. `/swagger`, `/scalar`, `/scalar/v1` and `/api/dev/options` return 404. It probably only shows in dev mode **[I]**. Gatekeeper can generate a client from the file directly.
- **[S]** The spec declares no `securitySchemes`. Auth is still Bearer JWT **[V]**.
- The panel bundle missed a set of routes that are in the spec. They include file upload (`upload-start/chunk/commit`), `files/download`, `files/zip`, `snapshots/{id}/download`, `Profile/sshkeys`, `Profile/sessions/others`, `networking/tunnel-fronts`, `networking/gateways/{id}/egress`, `networking/gateways/preflight`, `networking/spires/{id}/port-claims`, `Shards/{id}/ports/{ref}/access`, `Shards/{id}/settings/{field}/candidates`, `Audit/last/{action}/{targetType}/{targetId}`, `Tasks/{tenantId}/{taskId}/cancel`, `shards/{id}/import` and `csp-report`.

## 1. Processes and ports

| Port | Bound to | Owner | What it is |
|---|---|---|---|
| 8443/tcp | `*` | `webapi` (ASP.NET Kestrel) | Panel UI, REST under `/api`, SignalR under `/hubs` **[V]**. Plain HTTP unless HTTPS is set up. |
| 2222/tcp | `0.0.0.0` | `webapi` | Built-in SFTP **[V]**. It sends no banner on a raw connect. |
| 4222/tcp | `*` | `nats-server` 2.15.2 | Message bus. Its INFO banner shows `auth_required`, `tls_required`, JetStream, a 1 MB max payload and api_lvl 5 **[V]**. No HTTP monitoring port **[V]**. |
| 14004-14005/tcp | `0.0.0.0` | `rootlessport` | The Veloren shard's game ports **[V]**, published from container `amp3-shard-<shardId>`. Not opened in the Azure NSG. |

- Unix sockets **[V]**: `/run/podman/podman.sock` and the .NET diagnostics socket of `webapi` (`/tmp/dotnet-diagnostic-*`). The second is local-only and not an API.
- **[V]** Each shard runs in its own rootless Podman container from `docker.io/cubecoders/amp3base:debian`, which runs `ampstart v3` and then `/opt/amp/agent/shardagent` as uid 999.
- **[V]** systemd units: `amp3.service`, `amp3-bus.service` (NATS), and `amp3-sysagent.service` (root; firewall within 1024-65535, Caddy, WireGuard).
- **[V]** CubeCoders design docs ship in `/opt/cubecoders/amp3/docs/`: `packaging-and-containers.md`, `per-shard-identity.md` and `system-agent.md`, about 1 MB together.

## 2. Auth

- **[V]** `POST /api/Auth/login` takes `{username, password, rememberMe?}` and returns `{token, expiration}`. The token is an HS256 JWT, valid for about 8 hours, sent as `Authorization: Bearer`. Refresh it with `POST /api/Auth/renew`.
- **[S]** 2FA uses `/api/Auth/login/second-factor`. Passkeys use `/api/auth/passkey/begin|complete|abandon|reauth`. **[V]** `GET /api/auth/capabilities` (public) reports passkeys as not configured on a plain-HTTP install.
- **SignalR hub tokens [V]:**
  - `POST /api/auth/hub-token` returns `{token, expiresAt}`, valid for about 90 s.
  - **Each token works once.** A second negotiate with the same token gets 401, even on a different hub.
  - It works **only as `?access_token=`**. As an `Authorization` header it gets 401. The main JWT gets 401 on hubs.
  - Gatekeeper must fetch a fresh hub token for every hub connection and every reconnect.
- **[V]** There is no inbound API-key or service-account auth anywhere in the spec. A bot logs in as a normal user with a scoped role.
- **Permissions [V]:** `GET /api/me/permissions` returns `{grants, catalog}`. The catalog has **71 nodes** in three scopes:
  - **Shard:** `view`, `power.start|stop|restart|kill`, `update.run|abort`, `console.read|send`, `settings.read|write`, `infra.*`, `ports.*`, `files.read|write|delete` (path-scopable), `snapshots.*`, `schedule.read|write`, `analytics.read|users|addresses`, and more.
  - **Spire:** `view`, `shards.create`, `settings.*`, `startup.*`, `datastores.*`, `discover`, `delete`.
  - **Platform:** `audit.read`, `users.*`, `roles.*`, `network.*`, `licence.*`, `webhooks.internal`, `credentials.manage`, `platform.*`.
  - `dangerous` flags mark nodes such as `power.kill`, `delete`, `suspend` and `files.delete`.
- Minimal Gatekeeper role **[I]**: `view`, `power.start|stop|restart`, `console.read`, `console.send`, `analytics.read`, `analytics.users`, `schedule.read` on the target shards.

## 3. Realtime (SignalR)

| Hub | Negotiate | Join / leave **[B]** | Server events **[B]** |
|---|---|---|---|
| `/hubs/shard` | **[V]** 200 with a fresh hub token. Transports: WebSockets, SSE, LongPolling. | `JoinShard`, `LeaveShard` | `ConsoleLine`, `Metrics`, `UserJoined`, `UserLeft`, `UserRoster`, `ShardStateChanged`, `Lifecycle`, `ScheduleRun`, `Snapshot`, `Migration` |
| `/hubs/spire` | **[V]** 200 with a fresh token | `JoinSpire`, `LeaveSpire`, `JoinNetwork`, `LeaveNetwork` | `SpirePresence`, `SpireState`, `NetworkStatus` |
| `/hubs/tasks` | **[V]** 200 with a fresh token | none | `TaskUpsert` |

- Event payloads were not captured. That needs a live WebSocket client and a player joining, which is a good first spike for Gatekeeper.
- **[V]** The REST equivalents work. `GET /api/Shards/{id}/console/history?limit=N` returns lines shaped `{streamId, text, timestamp, seq, kind, state, code, params, level, origin}`. `GET /api/Shards/{id}/users` returns the current roster.

## 4. REST routes

See `API_REFERENCE.md` for the full reference. Groups by operation count:

| Group | Ops |
|---|---|
| Shards | 51 |
| Files & Backups | 30 |
| Authentication & Setup | 23 |
| Networking | 22 |
| Spires & Datastores | 19 |
| Users & Access | 18 |
| My Account | 14 |
| Analytics | 11 |
| System & Health | 10 |
| Scheduling | 10 |
| Profile | 7 |
| Licensing, Platform Settings, Spires, Templates | 6 each |
| Audit Log, Credentials, Tasks | 5 each |
| Import | 4 |
| Monitoring | 3 |
| Branding, Security | 1 each |

What the live probe showed:
- **[V]** 91 IDs-only GET routes were probed. With an admin token, most return 200. Three return 204 when there is nothing to report: `disk`, `migration` and `import`. `Analytics/addresses`, `Analytics/addresses/users`, `Analytics/connected/{id}` and `files/archive-info` return 400 without their required query parameters. `licence/terms/agreement` returns 404 when there is nothing pending.
- **[V]** Every probed `/api` route returns **401 without a token**, except these public ones: `/api/Health/status`, `/api/branding`, `/api/Auth/password-policy` and `/api/auth/capabilities`.
- **[V]** The three setup routes (`Auth/setup/code`, `setup/entrynode` and `setup/database/provision/status`) return 503 after setup is finished.
- **[S]** `POST /api/Shards/{id}/console` needs `console.send` and returns 202. The command is published to NATS `amp.tn.{tenant}.sr.{shardId}.ev.con.input` and audited. Anything that could be a secret is redacted from the audit entry.
- **[V]** Shard create body: `{"templateSlug","displayName","description","spireId","startNow","startOnBoot"}`. It returns 201 `{shard, infraSettings, appConfig, ports, portOutcomes, overLicenceLimit}`.

### Schedules and webhooks [V]

`GET /api/shards/{id}/schedules/catalog` lists the building blocks for schedules.

**Triggers:**
- `player.joined` and `player.left`, which emit `User` and `UserID`
- `app.stateChanged` (`NewState`)
- `app.updateComplete` (`Success`, `Version`)
- `app.error` (`Message`)
- cron and interval triggers, which come from the schema

**Tasks:**
- `power.start|stop|restart|kill|update`
- `app.consoleSend {command}`
- `backup.create {name}`
- `flow.wait {seconds}`
- **`flow.webhook {url, body}`**

**Common template variables:** `Time`, `TriggerId`, `TriggerName`, `ShardName`, `State`, `UserCount`, `MaxUsers`, `CPUUsage`, `RAMUsage`.

So **webhooks in AMP 3 are outbound schedule tasks**, not a subscription API. Gatekeeper could receive `player.joined` and `player.left` pushes this way, without keeping a SignalR connection open **[I]**. The cost is setting up one schedule per shard.

## 5. Message bus (NATS)

- **[V]** A single local server, with TLS, operator JWT auth and JetStream. The panel's Message Bus page shows 12 streams and 27 consumers once one shard exists (10 and 25 on a fresh install).
- **[V]** Streams and their subjects (`tn` = tenant, `sr` = shard, `sp` = spire **[I]**):

| Stream | Subjects | Notes |
|---|---|---|
| `console` | `amp.tn.*.sr.*.ev.con.main`, `.ev.con.update`, `.ev.con.agent`, `amp.tn.*.sr.*.ev.life` | 14 days, 200k per subject |
| `jobs` | `amp.tn.*.job.>` | workqueue |
| `audit` | `amp.tn.*.audit.>` | 1 year |
| `analytics` | `amp.tn.*.sr.*.an.>` | 2 years |
| `analytics-rollup` | `amp.tn.*.an.>` | |
| `analytics-addresses` | `amp.tn.*.sr.*.ip.>` | 90 days |
| `analytics-flows` | `amp.tn.*.sr.*.flow.>` | 6 hours |
| `log` | `amp.log.sr.*.log`, `amp.log.sp.>` | |
| `KV_amp`, `KV_amp_geo`, `KV_identity`, `KV_amp_sr_<shardId>` | `$KV.*` | key-value buckets |

- **[S]** Other subjects named in the spec: `...sr.{id}.ev.con.input` (console input), `...sr.{id}.ev.metrics`, `amp.tn.{t}.sched.{id}[.status]`, `amp.tn.{t}.usr.{user}...` and `amp.tn.{t}.sp.{spire}...`.
- Gatekeeper should **not** use the bus. It needs AMP's own credentials, and REST plus SignalR already cover everything Gatekeeper needs.

## 6. Not present or not exposed

- **[V]** There is **no AMP 2 compatibility API**. `/API/Core/GetStatus` and `/API/ADSModule/GetInstances` return 404, so the old GatekeeperV2 API client cannot be reused.
- **[V]** `/health`, `/healthz`, `/metrics` and `/.well-known/openid-configuration` return **200 with HTML**. That is the panel's catch-all page, not an endpoint. Don't mistake it for Prometheus or OIDC.
- **[V]** There is no webhook REST route (see section 4). Veloren's own metrics port (14005 `/metrics`) returned nothing.
- **[V]** The panel phones home with `POST https://metrics.c7rs.com/api/event` on every page view. **[V]** The backend fetches `cdn-downloads.c7rs.com/AMP3/manifest.json` (+ `.p7s`) to check for updates.

## 7. Gotchas

- **The preview build expires [V].** `GET /api/Health/status` (public) returns `{phase:"ready", message, preview:{expiresAt:"2026-12-07T15:58Z", daysRemaining:31, reason, update}, messageParams, plainText:true}`. `phase` is the liveness signal. `preview.update: null` means there is no newer build yet. `plainText: true` most likely means the panel is on HTTP, not HTTPS **[I]**. The response says nothing about shard state, so check shards with `GET /api/Shards/{id}/state`.

- **Uptime is wrong in the preview [V].** System Info showed "API Uptime 0m", and `/api/Support/diagnostics` returned `uptimeSeconds: 114`. At the time the backend process had been running for about 21 minutes with 0 restarts. Don't use this field as a health signal. Use `/api/Health/status` and request success instead.
- The Message Bus page shows "API Errors 20 / 4218". These are JetStream API calls inside AMP, not REST errors. A small count is expected (lookups for things that don't exist yet) **[I]**.
- **Preview UI bug [V]:** the deploy dialog asks for a Steam account whenever `steamUpdateAnonymousLogin` is 0, even for non-Steam games. Workaround: untick "Start now", or call `POST /api/shards`.
- Without HTTPS, logins are sent in clear text, and passkeys are unavailable.
- Azure: West Europe refused new resources on this subscription, D4s_v5 had no capacity in Germany West Central or North Europe, and Sweden Central worked.
