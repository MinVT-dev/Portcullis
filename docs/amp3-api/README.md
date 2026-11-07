# AMP 3 API documentation

These docs map the AMP 3 API that Gatekeeper will talk to. They cover the 3.0.0 preview (`amp3-host 3.0.0.6+f3d1a77cea`) and were checked on a throwaway test VM on 7 Nov 2026.

| File | What it is |
|---|---|
| [API_REFERENCE.md](API_REFERENCE.md) | **The full reference.** It covers headers, auth, errors, permissions, SignalR, schedules/webhooks and the message bus. It documents every one of the 263 REST operations: parameters, body, responses, permission and live result. It ends with all 389 data types. Generated from the spec and merged with live probe results. |
| [API_SURFACE.md](API_SURFACE.md) | A short summary: ports, auth, realtime, bus, what's missing and gotchas, each marked verified, spec, bundle or inferred. |
| [openapi-3.0.0.6.json](openapi-3.0.0.6.json) | The OpenAPI 3.1.1 spec. It ships on disk with AMP at `data/versions/3.0.0/webapi/webapi.json` and is not served over HTTP. Use it to generate a client. |

**This applies to one preview build.** The build expires on 7 Dec 2026 (`/api/Health/status`), and the API may change between previews. Re-check against `webapi.json` on each new build.

**Licence note:** the spec, and the descriptions quoted from it in API_REFERENCE.md, are CubeCoders' material, not ours under AGPL. This repo is private. Remove these files, or get CubeCoders' permission, before making the repo public.

No passwords, tokens, licence keys, SSH keys or test-VM addresses are stored here.
