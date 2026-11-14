# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small local HTTP endpoint for health checks and Prometheus. Off unless ``PORTCULLIS_METRICS=on``.

* ``GET /healthz``: JSON. Status 200 while Discord is connected and the panel answers, 503 when
  either is down, so Docker, Uptime Kuma or a load balancer can check it.
* ``GET /metrics``: Prometheus text format (no extra library needed).

It listens on 127.0.0.1:9464 by default, so only the same machine can reach it. Binding it to
another address needs ``PORTCULLIS_METRICS_TOKEN`` (start-up refuses otherwise); every request must
then carry ``Authorization: Bearer <token>``. Responses never contain secrets, server addresses,
player names or Discord ids: only counts and states.
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

log = logging.getLogger(__name__)


@dataclass
class Snapshot:
    version: str
    uptime: float
    discord_ready: bool
    discord_latency: float | None
    panel_reachable: bool
    panel_circuit: str  # closed, open, half-open
    panel_calls: int = 0
    panel_failures: int = 0
    panel_retries: int = 0
    panel_rejected: int = 0
    panel_trips: int = 0
    servers: dict[str, int] = field(default_factory=dict)  # state -> count
    players: int = 0
    cards_backing_off: bool = False

    @property
    def healthy(self) -> bool:
        return self.discord_ready and self.panel_reachable


def snapshot_of(bot: Any, guard: Any, version: str, started: float) -> Snapshot:
    """Read a snapshot off the running bot and ``GuardedPort``. Duck-typed so tests can pass stand-ins."""
    states: dict[str, int] = {}
    live = getattr(bot, "live", None)
    for state in (getattr(live, "states", None) or {}).values():
        name = str(getattr(state, "value", state))
        states[name] = states.get(name, 0) + 1
    summary = live.summary() if live is not None else None
    latency = getattr(bot, "latency", None)
    ready = bool(bot.is_ready()) if hasattr(bot, "is_ready") else False
    cards = getattr(getattr(bot, "ctx", None), "live_cards", None)
    stats = guard.stats
    return Snapshot(
        version=version, uptime=time.monotonic() - started, discord_ready=ready,
        discord_latency=latency if isinstance(latency, float) and math.isfinite(latency) else None,
        panel_reachable=guard.reachable, panel_circuit=guard.breaker.state(),
        panel_calls=stats.calls, panel_failures=stats.failures, panel_retries=stats.retries,
        panel_rejected=stats.rejected, panel_trips=stats.trips, servers=states,
        players=summary.players if summary is not None else 0,
        cards_backing_off=bool(getattr(getattr(cards, "pacer", None), "backing_off", False)),
    )


def health_json(snap: Snapshot) -> dict[str, Any]:
    return {
        "status": "ok" if snap.healthy else "degraded",
        "version": snap.version,
        "uptime_seconds": round(snap.uptime),
        "discord": {"connected": snap.discord_ready,
                    "latency_ms": round(snap.discord_latency * 1000) if snap.discord_latency is not None else None},
        "panel": {"reachable": snap.panel_reachable, "circuit": snap.panel_circuit},
        "servers": snap.servers,
        "players_online": snap.players,
    }


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def prometheus(snap: Snapshot) -> str:
    out: list[str] = []

    def metric(name: str, kind: str, help_text: str, values: list[tuple[str, float]]) -> None:
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} {kind}")
        for labels, value in values:
            out.append(f"{name}{labels} {value:g}")

    def gauge(name: str, help_text: str, value: float, labels: str = "") -> None:
        metric(name, "gauge", help_text, [(labels, value)])

    def counter(name: str, help_text: str, value: float) -> None:
        metric(name, "counter", help_text, [("", value)])

    gauge("portcullis_info", "Build information.", 1, f'{{version="{escape(snap.version)}"}}')
    gauge("portcullis_uptime_seconds", "Seconds since the bot started.", round(snap.uptime, 1))
    gauge("portcullis_discord_connected", "1 while the Discord gateway is connected.", int(snap.discord_ready))
    if snap.discord_latency is not None:
        gauge("portcullis_discord_latency_seconds", "Discord gateway heartbeat latency.",
              round(snap.discord_latency, 4))
    gauge("portcullis_panel_reachable", "1 while the AMP panel answers.", int(snap.panel_reachable))
    gauge("portcullis_panel_circuit_open", "1 while the bot has paused calls to an unresponsive panel.",
          int(snap.panel_circuit != "closed"))
    counter("portcullis_panel_requests_total", "Calls made to the panel.", snap.panel_calls)
    counter("portcullis_panel_failures_total", "Panel calls that got no answer (timeouts, 5xx).",
            snap.panel_failures)
    counter("portcullis_panel_retries_total", "Panel reads tried again after no answer.", snap.panel_retries)
    counter("portcullis_panel_rejected_total", "Calls refused at once while the panel was paused.",
            snap.panel_rejected)
    counter("portcullis_panel_outages_total", "Times the panel stopped answering.", snap.panel_trips)
    metric("portcullis_servers", "gauge", "Servers by state.",
           [(f'{{state="{escape(state)}"}}', count) for state, count in sorted(snap.servers.items())])
    gauge("portcullis_players_online", "Players online across running servers.", snap.players)
    gauge("portcullis_live_cards_backing_off", "1 while live card edits are slowed after a Discord limit.",
          int(snap.cards_backing_off))
    return "\n".join(out) + "\n"


Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class MetricsServer:
    def __init__(self, snapshot: Callable[[], Snapshot], *, host: str = "127.0.0.1", port: int = 9464,
                 token: str | None = None) -> None:
        self.snapshot = snapshot
        self.host = host
        self.port = port
        self.token = token
        self._runner: web.AppRunner | None = None

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth])
        app.router.add_get("/healthz", self._health)
        app.router.add_get("/metrics", self._metrics)
        return app

    @web.middleware
    async def _auth(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        if self.token:
            given = request.headers.get("Authorization", "")
            if not hmac.compare_digest(given.encode(), f"Bearer {self.token}".encode()):
                return web.json_response({"error": "unauthorized"}, status=401,
                                         headers={"WWW-Authenticate": "Bearer"})
        return await handler(request)

    async def _health(self, _request: web.Request) -> web.Response:
        snap = self.snapshot()
        return web.Response(text=json.dumps(health_json(snap)), content_type="application/json",
                            status=200 if snap.healthy else 503)

    async def _metrics(self, _request: web.Request) -> web.Response:
        return web.Response(body=prometheus(self.snapshot()).encode(),
                            headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"})

    async def start(self) -> bool:
        """Start listening. Returns False (and logs why) if the port is taken, without stopping the bot."""
        runner = web.AppRunner(self.app(), access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.host, self.port).start()
        except OSError as exc:
            await runner.cleanup()
            log.warning("The metrics endpoint can't listen on %s:%s (%s). Pick another PORTCULLIS_METRICS_PORT; "
                        "the bot runs on without it.", self.host, self.port, exc.strerror or exc)
            return False
        self._runner = runner
        log.info("Health and metrics at http://%s:%s/healthz and /metrics", self.host, self.port)
        return True

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
