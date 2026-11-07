# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small SignalR client for AMP 3's hubs (JSON protocol over WebSockets).

Why our own client instead of a library: AMP's hub tickets are single-use, last 60-90 seconds and
are only accepted as ``?access_token=`` (API_REFERENCE.md section 5.3). Every connect and every
reconnect must fetch a new ticket, and it is not yet known whether AMP counts the negotiate call and
the WebSocket upgrade as one use or two. Owning the ~300 lines below lets us control exactly when a
ticket is fetched and try each variant (``mode``):

* ``direct``: skip negotiate and open ``/hubs/<hub>?access_token=T`` (one ticket per connect).
* ``negotiate``: ``POST /hubs/<hub>/negotiate?access_token=T1``, then open the socket with a second
  fresh ticket ``T2`` and the returned connection token.
* ``negotiate-reuse``: as ``negotiate`` but reuses ``T1`` for the socket, as browser clients do.

Protocol reference: https://github.com/dotnet/aspnetcore/blob/main/src/SignalR/docs/specs/HubProtocol.md
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlencode

import aiohttp

from portcullis.amp.auth import HubTicket
from portcullis.amp.errors import AmpError, AuthFailed, LoginNotSupported
from portcullis.amp.redact import Redactor

log = logging.getLogger(__name__)

RS = "\x1e"  # SignalR record separator

# Message types from the hub protocol spec.
INVOCATION, STREAM_ITEM, COMPLETION, STREAM_INVOCATION, CANCEL_INVOCATION, PING, CLOSE = 1, 2, 3, 4, 5, 6, 7

Mode = Literal["direct", "negotiate", "negotiate-reuse"]
MODES: tuple[Mode, ...] = ("direct", "negotiate", "negotiate-reuse")

TicketSource = Callable[[], Awaitable[HubTicket]]
LifecycleHook = Callable[[str, dict[str, Any]], Any]


class HubError(AmpError):
    """The hub refused the connection or an invocation failed."""


class HubClosed(HubError):
    """The connection ended. ``allow_reconnect`` mirrors the server's Close message."""

    def __init__(self, message: str, *, allow_reconnect: bool = True) -> None:
        super().__init__(message)
        self.allow_reconnect = allow_reconnect


@dataclass(frozen=True)
class HubEvent:
    hub: str
    target: str
    arguments: list[Any]
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def encode(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":")) + RS


def decode_frame(frame: str) -> list[dict[str, Any]]:
    out = []
    for part in frame.split(RS):
        if part.strip():
            out.append(json.loads(part))
    return out


def ws_base(http_base: str) -> str:
    if http_base.startswith("https://"):
        return "wss://" + http_base[len("https://"):]
    if http_base.startswith("http://"):
        return "ws://" + http_base[len("http://"):]
    return http_base


class HubConnection:
    """One logical connection to a hub that survives drops by reconnecting with a fresh ticket.

    Use ``run(handler)`` as a long-lived task. ``on_connected`` runs after every (re)connect, which is
    where you call ``JoinShard`` again: group membership does not survive a new connection.
    """

    def __init__(
        self,
        base_url: str,
        hub: str,
        session: aiohttp.ClientSession,
        ticket_source: TicketSource,
        *,
        mode: Mode = "direct",
        redactor: Redactor | None = None,
        on_connected: Callable[[HubConnection], Awaitable[None]] | None = None,
        on_lifecycle: LifecycleHook | None = None,
        keepalive: float = 15.0,
        server_timeout: float = 30.0,
        handshake_timeout: float = 15.0,
        invoke_timeout: float = 30.0,
        backoff: tuple[float, ...] = (0, 2, 5, 10, 30, 60),
        stable_after: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.hub = hub
        self.path = f"/hubs/{hub}"
        self._session = session
        self._ticket_source = ticket_source
        self.mode: Mode = mode
        self.redactor = redactor or Redactor()
        self._on_connected = on_connected
        self._on_lifecycle = on_lifecycle
        self.keepalive = keepalive
        self.server_timeout = server_timeout
        self.handshake_timeout = handshake_timeout
        self.invoke_timeout = invoke_timeout
        self.backoff = backoff
        self.stable_after = stable_after

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._next_id = 0
        self._buffered: list[dict[str, Any]] = []
        self._stopping = False

        # Health, for /status and diagnostics.
        self.connected = False
        self.connects = 0
        self.tickets_used = 0
        self.last_error: str | None = None
        self.connected_since: float | None = None

    # ------------------------------------------------------------------ helpers

    def _lifecycle(self, kind: str, **detail: Any) -> None:
        detail = self.redactor.data(detail)
        log.info("hub %s: %s %s", self.hub, kind, detail if detail else "")
        if self._on_lifecycle is not None:
            try:
                self._on_lifecycle(kind, detail)
            except Exception:  # noqa: BLE001 - a broken hook must not kill the connection
                log.exception("lifecycle hook failed")

    async def _ticket(self) -> str:
        ticket = await self._ticket_source()
        self.tickets_used += 1
        self.redactor.add(ticket.token)
        return ticket.token

    async def _negotiate(self, ticket: str) -> str:
        url = f"{self.base_url}{self.path}/negotiate?" + urlencode({"negotiateVersion": 1, "access_token": ticket})
        async with self._session.post(url, timeout=aiohttp.ClientTimeout(total=self.handshake_timeout)) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise HubError(f"negotiate returned {resp.status}", status=resp.status,
                               body=self.redactor.text(text[:300]), method="POST", path=f"{self.path}/negotiate")
        body = json.loads(text)
        if "error" in body:
            raise HubError(f"negotiate error: {body['error']}")
        transports = [t.get("transport") for t in body.get("availableTransports", [])]
        if transports and "WebSockets" not in transports:
            raise HubError(f"hub offers no WebSockets transport: {transports}")
        token = body.get("connectionToken") or body.get("connectionId")
        if not token:
            raise HubError("negotiate response had no connection token")
        self.redactor.add(token)
        return token

    async def _open(self) -> aiohttp.ClientWebSocketResponse:
        first = await self._ticket()
        query: dict[str, Any]
        if self.mode == "direct":
            query = {"access_token": first}
        else:
            connection_token = await self._negotiate(first)
            second = first if self.mode == "negotiate-reuse" else await self._ticket()
            query = {"id": connection_token, "access_token": second}
        url = f"{ws_base(self.base_url)}{self.path}?{urlencode(query)}"
        try:
            ws = await self._session.ws_connect(url, heartbeat=None, autoping=True,
                                                timeout=aiohttp.ClientWSTimeout(ws_close=10))
        except aiohttp.WSServerHandshakeError as exc:
            raise HubError(f"WebSocket upgrade refused: {exc.status}", status=exc.status,
                           path=self.path) from exc

        await ws.send_str(encode({"protocol": "json", "version": 1}))
        msg = await ws.receive(timeout=self.handshake_timeout)
        if msg.type != aiohttp.WSMsgType.TEXT:
            await ws.close()
            raise HubError(f"no handshake response (got {msg.type.name})", path=self.path)
        parts = msg.data.split(RS, 1)
        handshake = json.loads(parts[0]) if parts[0] else {}
        if handshake.get("error"):
            await ws.close()
            raise HubError(f"handshake refused: {handshake['error']}", path=self.path)
        self._buffered = decode_frame(parts[1]) if len(parts) > 1 else []
        return ws

    async def probe(self) -> None:
        """Open one connection with the current ``mode`` and close it again. Raises on failure."""
        ws = await self._open()
        self._buffered = []
        await ws.close()

    # ------------------------------------------------------------------ calls

    async def send(self, target: str, *args: Any) -> None:
        """Fire-and-forget invocation."""
        if self._ws is None or self._ws.closed:
            raise HubClosed("not connected")
        await self._ws.send_str(encode({"type": INVOCATION, "target": target, "arguments": list(args)}))

    async def invoke(self, target: str, *args: Any) -> Any:
        """Invoke a hub method and wait for its completion."""
        if self._ws is None or self._ws.closed:
            raise HubClosed("not connected")
        self._next_id += 1
        inv_id = str(self._next_id)
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[inv_id] = fut
        try:
            await self._ws.send_str(
                encode({"type": INVOCATION, "invocationId": inv_id, "target": target, "arguments": list(args)})
            )
            return await asyncio.wait_for(fut, self.invoke_timeout)
        finally:
            self._pending.pop(inv_id, None)

    # ------------------------------------------------------------------ loops

    async def _pinger(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while not ws.closed:
            await asyncio.sleep(self.keepalive)
            with contextlib.suppress(ConnectionError, RuntimeError):
                await ws.send_str(encode({"type": PING}))

    async def _dispatch(self, message: dict[str, Any], handler: Callable[[HubEvent], Awaitable[None]]) -> None:
        kind = message.get("type")
        if kind == INVOCATION:
            await handler(HubEvent(self.hub, str(message.get("target")), list(message.get("arguments") or [])))
        elif kind == COMPLETION:
            fut = self._pending.get(str(message.get("invocationId")))
            if fut is not None and not fut.done():
                if message.get("error"):
                    fut.set_exception(HubError(f"invocation failed: {message['error']}"))
                else:
                    fut.set_result(message.get("result"))
        elif kind == CLOSE:
            raise HubClosed(f"server closed the connection: {message.get('error') or 'no reason'}",
                            allow_reconnect=bool(message.get("allowReconnect", True)))
        # PING and stream messages need no action.

    async def _read(self, ws: aiohttp.ClientWebSocketResponse, handler: Callable[[HubEvent], Awaitable[None]]) -> None:
        for message in self._buffered:
            await self._dispatch(message, handler)
        self._buffered = []
        while True:
            try:
                msg = await ws.receive(timeout=self.server_timeout)
            except TimeoutError as exc:
                raise HubClosed(f"no message from the server for {self.server_timeout:.0f}s") from exc
            if msg.type == aiohttp.WSMsgType.TEXT:
                for message in decode_frame(msg.data):
                    await self._dispatch(message, handler)
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                raise HubClosed(f"socket closed (code {ws.close_code})")
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise HubClosed(f"socket error: {ws.exception()!r}")

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _session_once(self, handler: Callable[[HubEvent], Awaitable[None]]) -> None:
        """Connect, run ``on_connected``, then read until the connection drops."""
        ws = await self._open()
        self._ws = ws
        self.connected = True
        self.connects += 1
        self.connected_since = time.monotonic()
        self.last_error = None
        self._lifecycle("connected", mode=self.mode, connects=self.connects, tickets_used=self.tickets_used)
        pinger = asyncio.create_task(self._pinger(ws))
        reader = asyncio.create_task(self._read(ws, handler))
        try:
            if self._on_connected is not None:
                joined = asyncio.create_task(self._on_connected(self))
                done, _ = await asyncio.wait({joined, reader}, return_when=asyncio.FIRST_COMPLETED)
                if joined in done:
                    joined.result()  # raise if joining failed
                else:
                    joined.cancel()
            await reader
        finally:
            self.connected = False
            for task in (pinger, reader):
                task.cancel()
            await asyncio.gather(pinger, reader, return_exceptions=True)
            self._fail_pending(HubClosed("connection lost"))
            if not ws.closed:
                await ws.close()
            self._ws = None

    async def run(self, handler: Callable[[HubEvent], Awaitable[None]]) -> None:
        """Stay connected until ``close()`` is called. Each attempt uses a brand-new hub ticket."""
        self._stopping = False
        failures = 0
        while not self._stopping:
            try:
                await self._session_once(handler)
            except (AuthFailed, LoginNotSupported):
                raise  # wrong credentials: reconnecting will not fix it
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure means "reconnect"
                if self._stopping:
                    break
                self.last_error = self.redactor.text(str(exc))
                lasted = time.monotonic() - self.connected_since if self.connected_since else 0.0
                if lasted >= self.stable_after:
                    failures = 0
                self.connected_since = None
                delay = self.backoff[min(failures, len(self.backoff) - 1)]
                failures += 1
                self._lifecycle("disconnected", error=self.last_error, error_type=type(exc).__name__,
                                retry_in=delay)
                await asyncio.sleep(delay)

    async def events(self) -> AsyncIterator[HubEvent]:
        """Iterate events while ``run`` handles reconnects in the background.

        Use ``contextlib.aclosing(conn.events())`` so breaking out of the loop closes the connection.
        """
        queue: asyncio.Queue[HubEvent] = asyncio.Queue()

        async def put(event: HubEvent) -> None:
            await queue.put(event)

        task = asyncio.create_task(self.run(put))
        try:
            while True:
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    yield getter.result()
                else:
                    getter.cancel()
                    task.result()
                    return
        finally:
            await self.close()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def close(self) -> None:
        self._stopping = True
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
