# AMP 3 beta: what to capture

For Min and k8t, for the day the beta is running. This is the list of things I need to turn the open questions in `docs/DESIGN.md` (section 10, Q-1 to Q-14) into decisions, in priority order. Everything here is read-only: nothing needs a change to your beta install.

## How to get it to me

This cloud environment probably cannot reach a local `http://localhost` or LAN address, and it should not be pointed at a live AMP login. Realistic routes, best first:

1. **A saved spec file.** Download the OpenAPI JSON and commit it, or attach it to the thread. A file works for any setup, needs no network access and can be redacted before sharing.
2. **Screenshots or pasted text** of the pages in the list below, when there is no spec file.
3. **A public HTTPS address** only if you are comfortable exposing it. If you do, make it a throwaway test instance with no real servers, and not behind your normal login. Never give me a working login.

Tailscale addresses (the `100.x.x.x` ones) are private to your tailnet. I cannot join it and you should not add this environment to it, so those addresses are not a route to me.

## Getting the spec

**Do not assume the `/api` page is the spec.** Min says the AMP 3 `/api` page is built like the AMP 2 one, which is a guide. The AMP 2 page is a short how-to (how calls are made, how to sign in, an example) plus a handful of sign-in and setup calls when logged out. The full list only appears when logged in, or when AMP 2's discovery setting is on.

Treat that only as a hint about the **page format**. AMP 3 is an entirely new system with a new design, so nothing about AMP 2's calls, names, sign-in method or request style should be assumed for AMP 3. Capture what the AMP 3 page says and do not fill gaps from AMP 2.

If section 0 below shows there is no HTTP API browser at all, skip to the section 0 findings first. So for AMP 3, in this order:

1. **Look for a download or "open API document" button.** AMP 3 has a Scalar API browser (`/scalar/v1` on the panel, for example `http://localhost:7278/scalar/v1`). Scalar can usually export the underlying JSON.
2. **If the page only shows a short list, log in first** and look again. If the guide mentions a setting that shows the full list without login, use it on a throwaway test instance only.
3. **If there is no export,** find the address the page loads its spec from (browser network tab, look for a `.json` request). ASP.NET apps often serve it at `/openapi/v1.json`; that is a guess, not confirmed for AMP 3.
4. **If none of that works,** save the guide page itself as text, then a screenshot of every call list you can reach. That is still useful, but the real spec is better.

Save the spec as `docs/amp3/openapi.json` in this repo, or attach it. If it is split by area, save each part.

## What to capture, in priority order

Tick each one when done. Where it says "spec", the answer should be readable from the saved spec; otherwise a screenshot or a short note is enough.

### 0. Which transport the beta really uses (Q-13, Q-14)

New evidence from the beta VM changes what to look for first. The machine listens on `webapi` HTTP 8443, `webapi` on 2222 (not HTTP), `nats-server` on 4222 and `sshd` on 22, and Min reports there is no `/api` page. That means the REST and Scalar picture in `docs/DESIGN.md` may be wrong or incomplete. These are all read-only and need no credentials:

- [ ] Open the beta's web address on 8443 in a browser. What loads? Note the path of any docs, API browser or Scalar-style page and its address. Try `/scalar/v1` and `/openapi/v1.json` too, and say which return something and which are "not found".
- [ ] Is the API browser (seen on port 7278 in the October screenshot) still there? On which port?
- [ ] Release notes, forum posts, Mike's messages or in-panel help that mention **NATS**, "messaging", "event bus", a client library or an SDK. Copy the text.
- [ ] Which process owns each port (the Tailscale view already names them). If you can run it on the VM, `ss -ltnp` lists listening ports and their processes. Share the process names and port numbers only.
- [ ] The NATS server version (`nats-server --version`), if available, and whether port 4222 only listens on the machine itself or on the network too.
- [ ] Whatever the beta says 2222 is for. If nothing, say so; that is a question for Mike.

Please do **not** connect a NATS client to 4222, subscribe to subjects, or publish anything. A wildcard subscription would show all traffic, including secrets, and could change how the system behaves. If NATS is meant for clients, the subjects and the sign-in method should be documented; ask Mike for that and for read access to the docs rather than probing it.

### 1. The spec itself and the auth model (Q-1, Q-2, Q-3, Q-10)

- [ ] The full OpenAPI JSON (see above), or the guide page text if there is no spec.
- [ ] The guide page's description of how calls are made (HTTP verb, headers, body shape, how the session or token is sent). Do not assume it matches AMP 2.
- [ ] How a bot signs in without a person. Is there an API key or token, a service account, or OAuth client credentials? Or only passkey and username/password? Note where you create it in the panel (a screenshot of that page is ideal).
- [ ] How long a token lasts and how it refreshes (or "none").
- [ ] The list of capabilities (Q-2): the roles or permissions page, or the capability list in the spec.
- [ ] Whether you can create an account or role with only chosen capabilities on chosen shards, and whether the API tells you what the signed-in account can do (V3 used `Core/CurrentSessionHasPermission`; AMP 3 may differ) (Q-3).
- [ ] Any statement on rate limits, API versioning, or "this will change during the beta" (Q-10). The release notes or forum post are enough.

### 2. Events, console and chat (Q-4, Q-6)

This decides most of the design, so it is worth the most care.

- [ ] Is there a live stream for console output (WebSocket, server-sent events, or NATS messages)? The spec path and one example message.
- [ ] Does AMP itself emit player join, leave and chat events, or a player list? In AMP 2 Gatekeeper had to read the console and apply patterns itself.
- [ ] Webhooks: what events can trigger one, the payload shape, and how a request is signed so the bot can verify it.
- [ ] Console input: the endpoint that sends a command to a game server, and the capability it needs. This is what whitelist, kick and say commands will use.

### 3. Servers: status and power (Q-5, Q-6)

- [ ] List shards and instances: the response shape, including the field that says which template or game an instance runs.
- [ ] Start, stop, restart and kill endpoints.
- [ ] Live status and metrics: state, CPU, memory, player count, and TPS where a game has it.
- [ ] The online player list endpoint, if one exists.
- [ ] Game actions as endpoints, if any (whitelist, kick, ban).

### 4. Schedules and webhooks (Q-7)

- [ ] The list of triggers and tasks the scheduler offers. Is there a "send webhook" or "emit event" task?
- [ ] The schedule create, list and delete endpoints.

### 5. Files and backups (Q-8)

- [ ] Backup create, list, delete and restore endpoints, the capability each one needs, and whether a restore reports progress.
- [ ] File access endpoints, only if you spot them while you are in there.

### 6. Running Gatekeeper inside AMP (Q-9)

- [ ] Create an instance from the **Python App Runner** template (git repository, `requirements.txt`, entry script, `.env`). Does it start, does it install requirements, does it survive a restart?
- [ ] Note the Python version it provides. Gatekeeper wants 3.11 or newer.
- [ ] Can the app instance reach the panel API from inside AMP, and at what address?

### 7. Nice to have (Q-11, Q-12)

- [ ] Whether an API client can write to the audit log and name the Discord user (Q-11).
- [ ] What "Generate MCP" produces. A screenshot of the output is enough (Q-12).

## Samples from a running game

Two or three games give the most: one Minecraft server, one Source RCON game (for example ARK or Valheim), and one game that uses the plain AMP console (for example Palworld). For each:

- [ ] The template name and the game version.
- [ ] Ten lines of console output covering one player joining and one leaving, and one chat line if the game has chat.
- [ ] What the API returns for the player list at that moment.

This lets me compare what AMP gives with what the template patterns expect.

## What must never be shared

Do not paste, attach or screenshot any of these. If they appear in a capture, redact them first:

- API tokens, keys, session cookies and `Authorization` headers.
- Passwords, TOTP secrets and passkey or recovery codes.
- AMP licence keys.
- Game server passwords, RCON passwords and Steam credentials.
- Webhook signing secrets and Discord bot tokens.
- NATS credentials: user names and passwords, tokens, NKeys, JWTs, `.creds` files, and the contents of the `nats-server` config file (it usually holds them).
- Tailscale addresses (`100.x.x.x`), node names and auth keys. Port numbers and process names are fine.
- Public IP addresses of your real servers, and player names you do not have permission to share (use a test account).

The OpenAPI spec itself should hold no secrets. Search it before sharing anyway.

### How to redact

- **Screenshots:** cover the value with a solid box. Blurring is not enough. Check the browser address bar and the network tab too.
- **Text and JSON:** replace the value with `REDACTED` and keep the key, for example `"Authorization": "REDACTED"`.
- **Quick check before you send a file:** search it for `token`, `secret`, `password`, `apikey`, `licence`, `license` and `Bearer`. This lists any hits with line numbers:

  ```
  grep -n -i -E 'token|secret|password|api[_-]?key|licen[cs]e|bearer' openapi.json
  ```

  A hit in a field name is fine. A hit next to a real value is not.
- **If something leaks:** rotate it. Remove the token or change the password in AMP, then tell me so I can point out anything stored in the thread or a PR.

## What I will do with it

1. Read the spec and answer Q-1 to Q-14 in `docs/DESIGN.md`, marking each answered, partly answered or still open.
2. Fix the AMP interface layer (`gatekeeper/amp/`) to match the real API, and re-check the command list in Appendix B against which endpoints exist.
3. List what Mike still needs to answer.
