# SPDX-License-Identifier: MIT
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
* ``GET /api/auth/capabilities`` (anonymous) says what the panel offers at sign-in, including its
  ``passkeyPolicy`` (``Optional``, ``Encouraged`` or ``Required``). It is read before each login: under
  ``Required`` a password-only account gets at most an enrolment-only session, which a bot can't use, so
  the login is refused up front with a message that says why. The answer is evaluated per caller origin,
  so the bot may see a different ``passkeyStatus`` than a browser does; only the policy is acted on.
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

from portcullis_api.errors import (
    AmpError,
    AuthExpired,
    AuthFailed,
    LoginNotSupported,
    RateLimited,
    Unreachable,
    parse_retry_after,
)
from portcullis_api.redact import Redactor

log = logging.getLogger(__name__)

LOGIN_PATH = "/api/Auth/login"
RENEW_PATH = "/api/Auth/renew"
LOGOUT_PATH = "/api/Auth/logout"
HUB_TOKEN_PATH = "/api/auth/hub-token"
CAPABILITIES_PATH = "/api/auth/capabilities"

# passkeyPolicy values the spec documents. Anything else is kept and logged, never acted on.
PASSKEY_POLICIES = ("Optional", "Encouraged", "Required")

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


@dataclass(frozen=True)
class AuthCapabilities:
    """What the panel offers at sign-in (``GET /api/auth/capabilities``), as seen from this client.

    ``passkey_status`` is AMP's verdict for this caller's origin. The spec only gives ``IpAddressHost``
    as an example; ``NotConfigured`` has been seen live. Unknown values are kept as they are.
    """

    passkeys_available: bool
    passkey_status: str | None
    passkey_policy: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def parse(cls, body: Any) -> AuthCapabilities | None:
        if not isinstance(body, dict):
            return None

        def text(key: str) -> str | None:
            value = body.get(key, body.get(key[:1].upper() + key[1:]))
            return value if isinstance(value, str) and value else None

        available = body.get("passkeysAvailable", body.get("PasskeysAvailable"))
        return cls(passkeys_available=available is True, passkey_status=text("passkeyStatus"),
                   passkey_policy=text("passkeyPolicy"), raw=body)

    @property
    def passkey_required(self) -> bool:
        return (self.passkey_policy or "").strip().lower() == "required"


# 200 bodies from login that carry no token. The spec names the markers but not their exact casing.
_LOGIN_MARKERS = {
    "passwordchangerequired": ("AMP wants the bot's account to change its password before it can sign in. "
                               "Sign in to the panel as that account once, set a new password, and put "
                               "the new one in the bot's settings."),
    "passkeyrequired": ("AMP wants the bot's account to sign in with a passkey, which a bot can't do. Give "
                        "the bot an account without a passkey, or set the panel's passkey policy to "
                        "Optional or Encouraged."),
    "secondfactorrequired": ("AMP wants a second factor for the bot's account, which a bot can't give. "
                             "Turn two-factor sign-in off for that account."),
    "twofactorrequired": ("AMP wants a second factor for the bot's account, which a bot can't give. "
                          "Turn two-factor sign-in off for that account."),
}


def login_marker(body: Any) -> str | None:
    """The explanation for a token-less login answer, when it names a flow we recognise."""
    if not isinstance(body, dict):
        return None
    for key, value in body.items():
        explanation = _LOGIN_MARKERS.get(str(key).lower())
        if explanation and value not in (None, False, "", 0):
            reason = body.get("reason", body.get("Reason"))
            return f"{explanation} (AMP said: {reason})" if isinstance(reason, str) and reason else explanation
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
        login_cooldown: timedelta = timedelta(seconds=60),
        check_capabilities: bool = True,
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
        # After AMP refuses the login, the same error is raised without asking again for this long.
        # Every caller (each REST call, each hub reconnect) would otherwise retry the login, and AMP
        # answers repeated failed logins with 429 and can lock the account out.
        self.login_cooldown = login_cooldown
        self._refused: tuple[datetime, AmpError] | None = None
        # Read GET /api/auth/capabilities before each login (see the module docstring). Turn it off only
        # for a panel that answers it wrongly; a failed read never blocks the login.
        self.check_capabilities = check_capabilities
        self.capabilities: AuthCapabilities | None = None
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
        status, text, _ = await self._post_with_wait(path, json_body=json_body, bearer=bearer)
        return status, text

    async def _post_with_wait(self, path: str, *, json_body: Any = None,
                              bearer: str | None = None) -> tuple[int, str, float | None]:
        """Like ``_post``, plus the seconds a ``Retry-After`` header asked for (None when there was none)."""
        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        url = self.base_url + path
        try:
            async with self._session.post(url, json=json_body, headers=headers, timeout=self._timeout) as resp:
                return resp.status, await resp.text(), parse_retry_after(resp.headers.get("Retry-After"))
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise Unreachable(f"AMP did not answer: {type(exc).__name__}", method="POST", path=path) from exc

    def _session_from(self, body: Any, path: str) -> SessionToken:
        if not isinstance(body, dict) or not isinstance(body.get("token"), str) or not body["token"]:
            keys = sorted(body.keys()) if isinstance(body, dict) else type(body).__name__
            explained = login_marker(body)
            raise LoginNotSupported(
                explained or "AMP did not issue a token. The bot's account must not need a second factor, "
                f"a passkey or a password change. Response fields: {keys}",
                method="POST",
                path=path,
                body=self.redactor.data(body),
            )
        token = body["token"]
        self.redactor.add(token, short_lived=True)
        expires = parse_instant(body.get("expiration")) or jwt_expiry(token)
        if expires is None:
            log.warning("AMP gave no usable expiry for the session token; assuming one hour")
            expires = self._clock() + timedelta(hours=1)
        return SessionToken(token, expires)

    async def _get_anonymous(self, path: str) -> tuple[int, str]:
        url = self.base_url + path
        try:
            async with self._session.get(url, headers={"Accept": "application/json"},
                                         timeout=self._timeout) as resp:
                return resp.status, await resp.text()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise Unreachable(f"AMP did not answer: {type(exc).__name__}", method="GET", path=path) from exc

    # ------------------------------------------------------------------ flows

    async def read_capabilities(self) -> AuthCapabilities | None:
        """Read what the panel offers at sign-in. None when the panel doesn't answer it usefully
        (an older build without the route, or a body we can't read)."""
        status, text = await self._get_anonymous(CAPABILITIES_PATH)
        if status != 200:
            log.debug("GET %s answered %s; skipping the passkey policy check", CAPABILITIES_PATH, status)
            return None
        caps = AuthCapabilities.parse(_json_or_none(text))
        if caps is not None:
            if caps.passkey_policy is not None and caps.passkey_policy not in PASSKEY_POLICIES:
                log.info("AMP reported an undocumented passkeyPolicy %r; treating it as not Required",
                         caps.passkey_policy)
            self.capabilities = caps
        return caps

    async def _check_capabilities(self) -> None:
        """Refuse to log in when the panel requires passkeys. Any failure to read the answer is ignored:
        the login itself then decides."""
        if not self.check_capabilities:
            return
        try:
            caps = await self.read_capabilities()
        except AmpError as exc:
            if isinstance(exc, Unreachable):
                raise  # the login would fail the same way; say so once
            log.debug("Could not read %s: %s", CAPABILITIES_PATH, exc)
            return
        if caps is not None and caps.passkey_required:
            refused = LoginNotSupported(
                "This AMP panel requires passkey sign-in (passkeyPolicy is Required). A bot can't use a "
                "passkey, and a password-only account would only get an enrolment session. Set the policy "
                "to Optional or Encouraged in the panel's sign-in settings, then start the bot again.",
                method="GET", path=CAPABILITIES_PATH, body=caps.raw,
            )
            self._refused = (self._clock() + self.login_cooldown, refused)
            raise refused

    async def login(self) -> SessionToken:
        body = {
            "username": self._credentials.username,
            "password": self._credentials.password,
            "rememberMe": False,
        }
        if self._refused is not None:
            until, refused = self._refused
            left = (until - self._clock()).total_seconds()
            if left > 0:
                raise type(refused)(refused.message, status=refused.status, code=refused.code,
                                    method=refused.method or "POST", path=refused.path or LOGIN_PATH,
                                    retry_after=left if isinstance(refused, RateLimited) else None)
            self._refused = None
        await self._check_capabilities()
        status, text, wait = await self._post_with_wait(LOGIN_PATH, json_body=body)
        if status == 200:
            self._current = self._session_from(_json_or_none(text), LOGIN_PATH)
            self._stale = False
            self.logins += 1
            log.info("Logged in to AMP as %s; token valid until %s",
                     self._credentials.username, self._current.expires_at.isoformat())
            return self._current
        err = AmpError.from_response(status, self.redactor.text(text), "POST", LOGIN_PATH)
        if status in (400, 401):
            refused = AuthFailed(
                f"AMP refused the login for {self._credentials.username!r}: {err.message}",
                status=status, code=err.code, method="POST", path=LOGIN_PATH,
            )
            self._refused = (self._clock() + self.login_cooldown, refused)
            raise refused
        if status == 429:
            # AMP is rate-limiting logins. Asking again before the wait is over only extends it, so hold
            # every caller off for as long as AMP said (at least the cooldown, at most an hour).
            hold = min(max(wait or 0.0, self.login_cooldown.total_seconds()), 3600.0)
            limited = RateLimited(f"AMP is rate-limiting logins: {err.message}", status=429, code=err.code,
                                  method="POST", path=LOGIN_PATH, retry_after=hold)
            self._refused = (self._clock() + timedelta(seconds=hold), limited)
            raise limited
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
                self.redactor.add(ticket, short_lived=True)
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
                wait = max(retry_delays[min(failures, len(retry_delays) - 1)], exc.retry_after or 0.0)
                failures += 1
                log.warning("Could not renew the AMP session (%s); retrying in %ss", exc, wait)
            await asyncio.sleep(wait)
