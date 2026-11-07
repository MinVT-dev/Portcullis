# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in to AMP 3, keeping the session alive, and issuing SignalR hub tickets.

What AMP 3 does (docs/amp3-api/API_REFERENCE.md section 5):

* ``POST /api/Auth/login`` takes a username and password and returns ``{token, expiration}``. The token
  is a JWT that lasts about 8 hours. There are no API keys or service accounts, so the bot is an
  ordinary user with a small role.
* ``POST /api/Auth/renew`` (Bearer) reissues a token for the same session. A 401 means the session is
  gone and we must log in again.
* ``POST /api/auth/hub-token`` (Bearer) returns ``{token, expiresAt}``: a ticket for the SignalR hubs
  that is accepted once and lives 60-90 seconds. Every connect and every reconnect needs a new one,
  so tickets are never cached here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

from portcullis.amp.errors import AmpError, AuthExpired, AuthFailed, LoginNotSupported, Unreachable
from portcullis.amp.redact import Redactor

log = logging.getLogger(__name__)

LOGIN_PATH = "/api/Auth/login"
RENEW_PATH = "/api/Auth/renew"
LOGOUT_PATH = "/api/Auth/logout"
HUB_TOKEN_PATH = "/api/auth/hub-token"

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class SessionToken:
    token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class HubTicket:
    token: str = field(repr=False)
    expires_at: datetime


_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_instant(value: Any) -> datetime | None:
    """Parse an ISO 8601 instant from AMP (.NET may send 7 fractional digits)."""
    if not isinstance(value, str) or not value:
        return None
    text = _FRACTION.sub(r"\1", value.strip())
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def jwt_expiry(token: str) -> datetime | None:
    """Read the ``exp`` claim without verifying the signature (we only use it as a fallback)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        return datetime.fromtimestamp(int(exp), UTC)
    except (IndexError, KeyError, ValueError, TypeError):
        return None


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


class TokenManager:
    """Owns the bot's AMP session token.

    * ``token()`` returns a valid token, logging in or renewing first when needed. Renewal starts
      ``refresh_margin`` before expiry, so callers never hold a token that is about to die.
    * ``invalidate(token)`` is called by the transport after a 401: the next ``token()`` renews,
      and falls back to a full login if renewal is refused.
    * ``hub_ticket()`` fetches a brand-new single-use SignalR ticket on every call.
    * ``run_renewal()`` is an optional background loop that renews on time even when the bot is idle.

    Concurrent callers share one in-flight login or renewal.
    """

    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession,
        credentials: Credentials,
        *,
        redactor: Redactor | None = None,
        refresh_margin: timedelta = timedelta(minutes=30),
        clock: Clock = utcnow,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._credentials = credentials
        self.redactor = redactor or Redactor()
        self.redactor.add(credentials.password)
        self.refresh_margin = refresh_margin
        self._clock = clock
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._current: SessionToken | None = None
        self._stale = False
        self._lock = asyncio.Lock()
        # Counters, handy for /admin diagnostics and for tests.
        self.logins = 0
        self.renewals = 0
        self.hub_tickets_issued = 0

    # ------------------------------------------------------------------ state

    @property
    def current(self) -> SessionToken | None:
        return self._current

    def _needs_refresh(self) -> bool:
        if self._current is None or self._stale:
            return True
        return self._clock() >= self._current.expires_at - self.refresh_margin

    # ------------------------------------------------------------------ HTTP

    async def _post(self, path: str, *, json_body: Any = None, bearer: str | None = None) -> tuple[int, str]:
        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        url = self.base_url + path
        try:
            async with self._session.post(url, json=json_body, headers=headers, timeout=self._timeout) as resp:
                return resp.status, await resp.text()
        except (aiohttp.ClientConnectionError, TimeoutError) as exc:
            raise Unreachable(f"AMP did not answer: {type(exc).__name__}", method="POST", path=path) from exc

    def _session_from(self, body: Any, path: str) -> SessionToken:
        if not isinstance(body, dict) or not isinstance(body.get("token"), str) or not body["token"]:
            keys = sorted(body.keys()) if isinstance(body, dict) else type(body).__name__
            raise LoginNotSupported(
                "AMP did not issue a token. The bot's account must not need a second factor, a passkey "
                f"or a password change. Response fields: {keys}",
                method="POST",
                path=path,
                body=self.redactor.data(body),
            )
        token = body["token"]
        self.redactor.add(token)
        expires = parse_instant(body.get("expiration")) or jwt_expiry(token)
        if expires is None:
            log.warning("AMP gave no usable expiry for the session token; assuming one hour")
            expires = self._clock() + timedelta(hours=1)
        return SessionToken(token, expires)

    # ------------------------------------------------------------------ flows

    async def login(self) -> SessionToken:
        body = {
            "username": self._credentials.username,
            "password": self._credentials.password,
            "rememberMe": False,
        }
        status, text = await self._post(LOGIN_PATH, json_body=body)
        if status == 200:
            self._current = self._session_from(_json_or_none(text), LOGIN_PATH)
            self._stale = False
            self.logins += 1
            log.info("Logged in to AMP as %s; token valid until %s",
                     self._credentials.username, self._current.expires_at.isoformat())
            return self._current
        err = AmpError.from_response(status, self.redactor.text(text), "POST", LOGIN_PATH)
        if status in (400, 401):
            raise AuthFailed(
                f"AMP refused the login for {self._credentials.username!r}: {err.message}",
                status=status, code=err.code, method="POST", path=LOGIN_PATH,
            )
        raise err

    async def renew(self) -> SessionToken:
        """Renew the current session; log in again if AMP says the session is gone."""
        if self._current is None:
            return await self.login()
        status, text = await self._post(RENEW_PATH, bearer=self._current.token)
        if status == 200:
            self._current = self._session_from(_json_or_none(text), RENEW_PATH)
            self._stale = False
            self.renewals += 1
            log.info("Renewed AMP session; token valid until %s", self._current.expires_at.isoformat())
            return self._current
        if status == 401:
            log.info("AMP refused to renew the session (revoked or expired); logging in again")
            return await self.login()
        raise AmpError.from_response(status, self.redactor.text(text), "POST", RENEW_PATH)

    async def _refresh(self) -> SessionToken:
        if self._current is None:
            return await self.login()
        return await self.renew()

    async def token(self) -> str:
        if not self._needs_refresh():
            assert self._current is not None
            return self._current.token
        async with self._lock:
            if self._needs_refresh():  # someone else may have refreshed while we waited
                await self._refresh()
            assert self._current is not None
            return self._current.token

    async def invalidate(self, token: str) -> None:
        """Mark ``token`` as rejected. A no-op if it was already replaced."""
        if self._current is not None and self._current.token == token:
            self._stale = True

    async def hub_ticket(self) -> HubTicket:
        """Fetch a fresh single-use SignalR ticket. Never reuse the result."""
        for attempt in (1, 2):
            bearer = await self.token()
            status, text = await self._post(HUB_TOKEN_PATH, bearer=bearer)
            if status == 200:
                body = _json_or_none(text)
                if not isinstance(body, dict) or not body.get("token"):
                    raise AmpError("hub-token response had no token", status=200, method="POST",
                                   path=HUB_TOKEN_PATH)
                ticket = str(body["token"])
                self.redactor.add(ticket)
                expires = parse_instant(body.get("expiresAt")) or self._clock() + timedelta(seconds=60)
                self.hub_tickets_issued += 1
                return HubTicket(ticket, expires)
            if status == 401 and attempt == 1:
                await self.invalidate(bearer)
                continue
            raise AmpError.from_response(status, self.redactor.text(text), "POST", HUB_TOKEN_PATH)
        raise AuthExpired("hub-token kept returning 401", status=401, method="POST", path=HUB_TOKEN_PATH)

    async def logout(self) -> None:
        if self._current is None:
            return
        try:
            await self._post(LOGOUT_PATH, bearer=self._current.token)
        except AmpError as exc:
            log.debug("Logout failed: %s", exc)
        self._current = None

    async def run_renewal(
        self, *, min_sleep: float = 5.0, retry_delays: tuple[float, ...] = (30, 60, 120, 300)
    ) -> None:
        """Keep the session renewed until cancelled. Errors are logged and retried, never raised."""
        failures = 0
        while True:
            try:
                await self.token()
                failures = 0
                assert self._current is not None
                due = self._current.expires_at - self.refresh_margin
                wait = max(min_sleep, (due - self._clock()).total_seconds())
            except AuthFailed:
                raise  # wrong password: retrying won't help and may trip AMP's 429
            except AmpError as exc:
                wait = retry_delays[min(failures, len(retry_delays) - 1)]
                failures += 1
                log.warning("Could not renew the AMP session (%s); retrying in %ss", exc, wait)
            await asyncio.sleep(wait)
