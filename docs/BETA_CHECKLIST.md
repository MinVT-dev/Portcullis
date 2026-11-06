# AMP 3 beta: what to capture

For Min and k8t, for the day the beta is running. This is the list of things I need to turn the open questions in `docs/DESIGN.md` (section 10, Q-1 to Q-12) into decisions, in priority order. Everything here is read-only: nothing needs a change to your beta install.

## How to get it to me

This cloud environment probably cannot reach a local `http://localhost` or LAN address, and it should not be pointed at a live AMP login. Realistic routes, best first:

1. **A saved spec file.** Download the OpenAPI JSON and commit it, or attach it to the thread. A file works for any setup, needs no network access and can be redacted before sharing.
2. **Screenshots or pasted text** of the pages in the list below, when there is no spec file.
3. **A public HTTPS address** only if you are comfortable exposing it. If you do, make it a throwaway test instance with no real servers, and not behind your normal login. Never give me a working login.

## Getting the spec

The API browser is the Scalar page (`/scalar/v1` on the panel, for example `http://localhost:7278/scalar/v1`). Scalar has a download or "open API document" button for the underlying JSON. If it does not, find the address it loads the spec from (open the browser's network tab and look for a `.json` request). ASP.NET apps often serve it at `/openapi/v1.json`; that is a guess, not something I have confirmed for AMP 3.

Save it as `docs/amp3/openapi.json` in this repo, or attach it. If the spec is split by area, save each part.

## What to capture, in priority order

Tick each one when done. Where it says "spec", the answer should be readable from the saved spec; otherwise a screenshot or a short note is enough.

### 1. The spec itself and the auth model (Q-1, Q-2, Q-3, Q-10)

- [ ] The full OpenAPI JSON (see above).
- [ ] How a bot signs in without a person. Is there an API key or token, a service account, or OAuth client credentials? Or only passkey and username/password? Note where you create it in the panel (a screenshot of that page is ideal).
- [ ] How long a token lasts and how it refreshes (or "none").
- [ ] The list of capabilities (Q-2): the roles or permissions page, or the capability list in the spec.
- [ ] Whether you can create an account or role with only chosen capabilities on chosen shards, and whether the API tells you what the signed-in account can do (the old `Core/CurrentSessionHasPermission` idea) (Q-3).
- [ ] Any statement on rate limits, API versioning, or "this will change during the beta" (Q-10). The release notes or forum post are enough.

### 2. Events, console and chat (Q-4, Q-6)

This decides most of the design, so it is worth the most care.

- [ ] Is there a live stream for console output (WebSocket or server-sent events)? The spec path and one example message.
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

1. Read the spec and answer Q-1 to Q-12 in `docs/DESIGN.md`, marking each answered, partly answered or still open.
2. Fix the AMP interface layer (`gatekeeper/amp/`) to match the real API, and re-check the command list in Appendix B against which endpoints exist.
3. List what Mike still needs to answer.
