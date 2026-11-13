# SPDX-License-Identifier: MIT
"""An in-process fake of the parts of AMP 3 that auth and realtime depend on.

It follows the documented behaviour (docs/amp3-api/API_REFERENCE.md sections 5 and 8): username and
password login, renew, single-use hub tickets accepted only as ``?access_token=``, and a SignalR JSON
hub at /hubs/shard. It also serves the sign-in capabilities, ticket-based downloads (files, zips and
snapshots) and the licence (EULA) answers of the live spec of 2026-11-13. It is a model of what we were
told, not a recording of the real thing.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import WSMsgType, web

RS = "\x1e"
USERNAME = "portcullis"
PASSWORD = "correct-horse-battery"
SHARD_ID = "7d4b6a52-9a7e-4e0e-9a38-1d1f0d3c2b11"
TENANT = "_root"  # the tenant id a real single-tenant AMP 3 reports


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def fake_jwt(n: int) -> str:
    def b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256'})}.{b64({'n': n, 'iss': 'amp-auth'})}.c2lnbmF0dXJlLXNpZw"


@dataclass
class Session:
    sid: int
    revoked: bool = False


@dataclass
class FakeAmp:
    clock: Clock = field(default_factory=Clock)
    token_lifetime: timedelta = timedelta(hours=8)
    # How negotiate treats the ticket: "consume" (a second use is refused) or "peek" (not consumed).
    negotiate_consumes: bool = True
    login_response: dict[str, Any] | None = None  # override to simulate PasswordChangeRequired etc.
    # Retry-After values (None = no header) for the next login or shard-list calls, which answer 429.
    rate_limits: list[str | None] = field(default_factory=list)
    # The ``preview`` block of GET /api/Health/status; None is a release build, which has none.
    preview: dict[str, Any] | None = None
    # GET /api/auth/capabilities. ``passkeyStatus`` NotConfigured is what a real panel answered.
    capabilities: dict[str, Any] | None = field(default_factory=lambda: {
        "passkeysAvailable": False, "passkeyStatus": "NotConfigured", "passkeyMessageKey": "auth.passkey.none",
        "rpId": None, "expectedOrigin": None, "passkeyPolicy": "Encouraged"})
    capability_checks: int = 0
    # Files a download can return, by shard-relative path.
    files: dict[str, bytes] = field(default_factory=lambda: {"server.properties": b"motd=Hello\n",
                                                             "world/level.dat": b"\x00LEVEL"})
    snapshot_zip: bytes = b"PK\x03\x04snapshot"
    download_tickets: dict[str, tuple[Session, str, Any, bool]] = field(default_factory=dict)
    awaits_licence: bool = False
    # (status, Retry-After) for the next snapshot delete: 409 and 503 are documented since 2026-11-13.
    snapshot_error: tuple[int, str | None] | None = None
    licence_answers: list[str] = field(default_factory=list)

    tokens: dict[str, tuple[Session, datetime]] = field(default_factory=dict)
    tickets: dict[str, tuple[Session, bool]] = field(default_factory=dict)  # ticket -> (session, used)
    connection_tokens: set[str] = field(default_factory=set)
    sockets: list[web.WebSocketResponse] = field(default_factory=list)
    joined: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    roster_forbidden: bool = False
    refused_tickets: int = 0
    muted: list[web.WebSocketResponse] = field(default_factory=list)  # half-open sockets: never answer
    join_error: str | None = None  # when set, JoinShard with the right arguments fails with this error
    join_attempts: list[list[Any]] = field(default_factory=list)  # every JoinShard argument list received
    panel_script: str = "o=>o.invoke(`JoinShard`,e,t)"  # served as the panel's JavaScript
    _seq: itertools.count = field(default_factory=lambda: itertools.count(1))

    # ------------------------------------------------------------------ helpers

    def _issue(self, session: Session) -> dict[str, Any]:
        token = fake_jwt(next(self._seq))
        expires = self.clock() + self.token_lifetime
        self.tokens[token] = (session, expires)
        return {"token": token, "expiration": expires.isoformat().replace("+00:00", "Z")}

    def _bearer(self, request: web.Request) -> Session | None:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        entry = self.tokens.get(header[7:])
        if entry is None:
            return None
        session, expires = entry
        if session.revoked or self.clock() >= expires:
            return None
        return session

    def revoke_all(self) -> None:
        for session, _ in self.tokens.values():
            session.revoked = True

    def expire_all(self) -> None:
        for token, (session, _) in list(self.tokens.items()):
            self.tokens[token] = (session, self.clock() - timedelta(seconds=1))

    def _take_ticket(self, ticket: str | None, consume: bool = True) -> bool:
        entry = self.tickets.get(ticket or "")
        if entry is None or entry[1] or entry[0].revoked:
            self.refused_tickets += 1
            return False
        if consume:
            self.tickets[ticket] = (entry[0], True)  # type: ignore[index]
        return True

    # ------------------------------------------------------------------ handlers

    def _rate_limited(self) -> web.Response | None:
        if not self.rate_limits:
            return None
        wait = self.rate_limits.pop(0)
        return web.json_response({"title": "Too Many Requests", "status": 429}, status=429,
                                 headers={"Retry-After": wait} if wait is not None else None)

    async def login(self, request: web.Request) -> web.Response:
        self.calls.append("login")
        if (limited := self._rate_limited()) is not None:
            return limited
        body = await request.json()
        if body.get("username") != USERNAME or body.get("password") != PASSWORD:
            return web.json_response({"title": "Unauthorized", "status": 401}, status=401)
        if self.login_response is not None:
            return web.json_response(self.login_response)
        return web.json_response(self._issue(Session(next(self._seq))))

    async def renew(self, request: web.Request) -> web.Response:
        self.calls.append("renew")
        session = self._bearer(request)
        if session is None:
            return web.json_response({"title": "Unauthorized", "status": 401}, status=401)
        return web.json_response(self._issue(session))

    async def logout(self, request: web.Request) -> web.Response:
        self.calls.append("logout")
        return web.Response(status=204)

    async def hub_token(self, request: web.Request) -> web.Response:
        self.calls.append("hub-token")
        session = self._bearer(request)
        if session is None:
            return web.Response(status=401)
        ticket = f"hubticket-{next(self._seq)}-{'x' * 20}"
        self.tickets[ticket] = (session, False)
        expires = (self.clock() + timedelta(seconds=90)).isoformat().replace("+00:00", "Z")
        # AMP declares text/plain for this body; the client must not rely on the content type.
        return web.Response(text=json.dumps({"token": ticket, "expiresAt": expires}), content_type="text/plain")

    async def auth_capabilities(self, request: web.Request) -> web.Response:
        self.capability_checks += 1
        if self.capabilities is None:
            return web.Response(status=404)
        return web.json_response(self.capabilities)

    def _ticket(self, request: web.Request, kind: str, what: Any) -> web.Response:
        session = self._bearer(request)
        if session is None:
            return web.Response(status=401)
        ticket = f"dlticket-{next(self._seq)}-{'y' * 16}"
        self.download_tickets[ticket] = (session, kind, what, False)
        expires = (self.clock() + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
        return web.json_response({"ticket": ticket, "expiresAt": expires})

    def _spend(self, request: web.Request, kind: str) -> Any:
        """The ticket's payload, or None if it is missing, used, for another download or its session ended."""
        ticket = request.query.get("ticket", "")
        entry = self.download_tickets.get(ticket)
        if entry is None or entry[3] or entry[1] != kind or entry[0].revoked:
            return None
        self.download_tickets[ticket] = (entry[0], entry[1], entry[2], True)
        return entry[2]

    async def download_ticket(self, request: web.Request) -> web.Response:
        body = await request.json()
        if body.get("path") not in self.files:
            return web.json_response({"title": "Not Found", "status": 404}, status=404)
        return self._ticket(request, "file", body["path"])

    async def zip_ticket(self, request: web.Request) -> web.Response:
        body = await request.json()
        paths = body.get("paths") or []
        if not paths:
            return web.json_response({"title": "Bad Request", "status": 400, "code": "files.error.nothingSelected"},
                                     status=400)
        return self._ticket(request, "zip", list(paths))

    async def snapshot_ticket(self, request: web.Request) -> web.Response:
        return self._ticket(request, "snapshot", request.match_info["snap"])

    async def download(self, request: web.Request) -> web.StreamResponse:
        if "ticket" not in request.query:  # the Bearer form, with ?path=
            if self._bearer(request) is None:
                return web.Response(status=401)
            path = request.query.get("path")
        else:
            path = self._spend(request, "file")
        if path is None:
            return web.json_response({"title": "Unauthorized", "status": 401}, status=401)
        self.calls.append(f"download:{path}")
        return web.Response(body=self.files[path], content_type="application/octet-stream")

    async def zip(self, request: web.Request) -> web.StreamResponse:
        if "ticket" not in request.query:
            return web.json_response({"title": "Bad Request", "status": 400,
                                      "code": "files.error.downloadTicketRequired"}, status=400)
        paths = self._spend(request, "zip")
        if paths is None:
            return web.json_response({"title": "Unauthorized", "status": 401}, status=401)
        self.calls.append("zip:" + ",".join(paths))
        # Not a real zip: the client must not care what the bytes are.
        return web.Response(body=b"PK\x03\x04" + "|".join(paths).encode(), content_type="application/zip")

    async def snapshot_download(self, request: web.Request) -> web.StreamResponse:
        if "ticket" in request.query:
            snap = self._spend(request, "snapshot")
            if snap is None or snap != request.match_info["snap"]:
                return web.json_response({"title": "Unauthorized", "status": 401}, status=401)
        elif self._bearer(request) is None:
            return web.Response(status=401)
        self.calls.append("snapshot-download:" + request.match_info["snap"])
        return web.Response(body=self.snapshot_zip, content_type="application/zip")

    async def snapshot_delete(self, request: web.Request) -> web.Response:
        if not self._authed(request):
            return web.Response(status=401)
        if self.snapshot_error is not None:
            status, wait = self.snapshot_error
            return web.json_response({"title": "Snapshot store busy", "status": status}, status=status,
                                     headers={"Retry-After": wait} if wait else None)
        self.calls.append("snapshot-delete:" + request.match_info["snap"])
        return web.Response(status=204)

    async def eula(self, request: web.Request) -> web.Response:
        if not self._authed(request):
            return web.Response(status=401)
        answer = request.match_info["answer"]
        self.licence_answers.append(answer)
        self.awaits_licence = False
        return web.Response(status=204)

    async def health(self, request: web.Request) -> web.Response:
        self.calls.append("health")
        return web.json_response({"phase": "ready", "message": None, "preview": self.preview})

    async def shards(self, request: web.Request) -> web.Response:
        self.calls.append("shards")
        if self._bearer(request) is None:
            return web.Response(status=401, headers={"WWW-Authenticate": "Bearer"})
        if (limited := self._rate_limited()) is not None:
            return limited
        return web.json_response([{"id": SHARD_ID, "tenantId": TENANT, "name": "Survival",
                                   "unknownFutureField": 1}])

    async def console(self, request: web.Request) -> web.Response:
        if self._bearer(request) is None:
            return web.Response(status=401)
        body = await request.json()
        if "command" not in body:
            return web.json_response(
                {"title": "One or more validation errors occurred.", "status": 400,
                 "errors": {"Command": ["The Command field is required."]}}, status=400)
        self.calls.append(f"console:{body['command']}")
        return web.Response(status=202)

    async def forbidden(self, request: web.Request) -> web.Response:
        return web.json_response({"code": "auth.error.forbidden", "params": None,
                                  "message": "Missing schedule.read"}, status=403)

    async def always_401(self, request: web.Request) -> web.Response:
        self.calls.append("always-401")
        return web.Response(status=401)

    def _authed(self, request: web.Request) -> bool:
        return self._bearer(request) is not None

    async def shard(self, request: web.Request) -> web.Response:
        if not self._authed(request):
            return web.Response(status=401)
        if request.match_info["id"] != SHARD_ID:
            return web.json_response({"title": "Not Found", "status": 404}, status=404)
        return web.json_response({"id": SHARD_ID, "tenantId": TENANT, "name": "survival",
                                  "displayName": "Survival",
                                  "templateSlug": "minecraft", "suspended": False, "tags": ["smp"]})

    async def state(self, request: web.Request) -> web.Response:
        if not self._authed(request):
            return web.Response(status=401)
        if self.awaits_licence:
            return web.json_response({"shardId": SHARD_ID, "isRunning": False, "isStopped": False,
                                      "isBusy": False, "awaitsLicence": True,
                                      "statusMessage": "Waiting for the licence to be accepted"})
        return web.json_response({"shardId": SHARD_ID, "status": 20, "isRunning": True, "isStopped": False,
                                  "isBusy": False, "statusMessage": "Running",
                                  "metrics": {"cpuUsagePercent": "12.5", "memoryUsageMB": 2048,
                                              "memoryLimitMB": 4096}})

    async def users(self, request: web.Request) -> web.Response:
        if not self._authed(request):
            return web.Response(status=401)
        if self.roster_forbidden:
            return web.json_response({"title": "Forbidden", "status": 403}, status=403)
        return web.json_response([{"name": "Steve", "id": "uuid-steve", "sessionId": "s1",
                                   "endpoint": None, "idKind": "steam",
                                   "joinedAt": "2026-11-07T12:00:00Z"}])

    async def history(self, request: web.Request) -> web.Response:
        if not self._authed(request):
            return web.Response(status=401)
        limit = int(request.query.get("limit", "300"))
        lines = [{"streamId": "stdout", "text": f"line {i}", "timestamp": "2026-11-07T12:00:00Z", "seq": i}
                 for i in range(1, 6)][-limit:]
        return web.json_response({"lines": lines, "hasMore": False})

    async def power(self, request: web.Request) -> web.Response:
        if not self._authed(request):
            return web.Response(status=401)
        self.calls.append("power:" + request.match_info["action"])
        return web.Response(status=202)

    async def snapshots(self, request: web.Request) -> web.Response:
        if not self._authed(request):
            return web.Response(status=401)
        nodes = [
            {"id": "11111111-1111-1111-1111-111111111111", "name": "old", "status": 2, "sizeMb": "10",
             "createdAt": "2026-11-01T00:00:00Z"},
            {"id": "22222222-2222-2222-2222-222222222222", "name": "new", "status": 2, "sizeMb": 12,
             "createdAt": "2026-11-06T00:00:00Z", "sticky": True},
        ]
        return web.json_response({"shardId": SHARD_ID, "nodes": nodes})

    async def negotiate(self, request: web.Request) -> web.Response:
        self.calls.append("negotiate")
        if request.headers.get("Authorization"):
            return web.Response(status=401)
        if not self._take_ticket(request.query.get("access_token"), consume=self.negotiate_consumes):
            return web.Response(status=401)
        conn = f"conn-{next(self._seq)}"
        self.connection_tokens.add(conn)
        return web.json_response({"negotiateVersion": 1, "connectionId": conn, "connectionToken": conn,
                                  "availableTransports": [{"transport": "WebSockets",
                                                           "transferFormats": ["Text", "Binary"]}]})

    async def hub(self, request: web.Request) -> web.StreamResponse:
        self.calls.append("ws")
        conn = request.query.get("id")
        if conn is not None and conn not in self.connection_tokens:
            return web.Response(status=404)
        if not self._take_ticket(request.query.get("access_token")):
            return web.Response(status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        handshook = False
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            for part in filter(None, msg.data.split(RS)):
                data = json.loads(part)
                if not handshook:
                    handshook = True
                    await ws.send_str("{}" + RS)
                    continue
                if ws in self.muted:
                    continue
                if data.get("type") == 1 and data.get("target") == "JoinShard":
                    args = data.get("arguments") or []
                    self.join_attempts.append(args)
                    reply: dict[str, Any] = {"type": 3, "invocationId": data["invocationId"]}
                    # Real AMP 3 runs without SignalR's detailed errors, so a wrong argument count, a
                    # wrong type and an exception inside the method all come back as this one message.
                    if self.join_error is not None or args != [TENANT, SHARD_ID]:
                        reply["error"] = (self.join_error
                                          or "Failed to invoke 'JoinShard' due to an error on the server.")
                    else:
                        self.joined.append(args[1])
                        reply["result"] = {"feeds": ["core", "console", "snapshots", "schedule"], "error": None}
                    await ws.send_str(json.dumps(reply) + RS)
                elif data.get("type") == 1 and data.get("target") == "LeaveShard":
                    self.left.append(data["arguments"][-1])
                elif data.get("type") == 6:
                    await ws.send_str(json.dumps({"type": 6}) + RS)
        if ws in self.sockets:
            self.sockets.remove(ws)
        return ws

    # ------------------------------------------------------------------ test controls

    async def panel_index(self, request: web.Request) -> web.Response:
        return web.Response(text='<html><script type="module" src="/assets/index-abc.js"></script></html>',
                            content_type="text/html")

    async def panel_asset(self, request: web.Request) -> web.Response:
        if request.match_info["name"] == "index-abc.js":
            return web.Response(text='import("./hub-def.js");', content_type="application/javascript")
        if request.match_info["name"] == "hub-def.js":
            return web.Response(text=self.panel_script, content_type="application/javascript")
        return web.Response(status=404)

    async def broadcast(self, target: str, *args: Any) -> None:
        for ws in list(self.sockets):
            await ws.send_str(json.dumps({"type": 1, "target": target, "arguments": list(args)}) + RS)

    async def send_close(self, error: str = "Server shutting down", allow_reconnect: bool = True) -> None:
        for ws in list(self.sockets):
            await ws.send_str(json.dumps({"type": 7, "error": error, "allowReconnect": allow_reconnect}) + RS)

    def mute_all(self) -> None:
        self.muted.extend(self.sockets)

    async def drop_all(self) -> None:
        for ws in list(self.sockets):
            await ws.close()

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/Auth/login", self.login)
        app.router.add_post("/api/Auth/renew", self.renew)
        app.router.add_post("/api/Auth/logout", self.logout)
        app.router.add_post("/api/auth/hub-token", self.hub_token)
        app.router.add_get("/api/auth/capabilities", self.auth_capabilities)
        app.router.add_post("/api/shards/{id}/files/download-ticket", self.download_ticket)
        app.router.add_post("/api/shards/{id}/files/zip-ticket", self.zip_ticket)
        app.router.add_get("/api/shards/{id}/files/download", self.download)
        app.router.add_get("/api/shards/{id}/files/zip", self.zip)
        app.router.add_post("/api/shards/{id}/snapshots/{snap}/download-ticket", self.snapshot_ticket)
        app.router.add_get("/api/shards/{id}/snapshots/{snap}/download", self.snapshot_download)
        app.router.add_delete("/api/shards/{id}/snapshots/{snap}", self.snapshot_delete)
        app.router.add_post("/api/Shards/{id}/eula/{answer:accept|decline}", self.eula)
        app.router.add_get("/api/Shards", self.shards)
        app.router.add_get("/api/Health/status", self.health)
        app.router.add_post("/api/Shards/{id}/console", self.console)
        app.router.add_get("/api/Shards/{id}/schedules", self.forbidden)
        app.router.add_get("/api/always-401", self.always_401)
        app.router.add_get("/api/Shards/{id}", self.shard)
        app.router.add_get("/api/Shards/{id}/state", self.state)
        app.router.add_get("/api/Shards/{id}/users", self.users)
        app.router.add_get("/api/Shards/{id}/console/history", self.history)
        app.router.add_post("/api/Shards/{id}/{action:start|stop|restart|sleep|wake|update}", self.power)
        app.router.add_get("/api/shards/{id}/snapshots", self.snapshots)
        app.router.add_post("/hubs/shard/negotiate", self.negotiate)
        app.router.add_get("/hubs/shard", self.hub)
        app.router.add_get("/", self.panel_index)
        app.router.add_get("/assets/{name}", self.panel_asset)
        return app


async def wait_for(predicate: Any, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)
