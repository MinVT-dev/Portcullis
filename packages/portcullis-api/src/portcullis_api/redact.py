# SPDX-License-Identifier: MIT
"""Remove secrets from anything we log or write to disk.

Two layers:

* known values: the live session token, every hub ticket and the password are registered with
  ``Redactor.add`` the moment we see them and are replaced wherever they appear;
* patterns: anything shaped like a JWT, a ``Bearer`` header, an ``access_token=`` query value or a
  JSON key whose name says it is a secret, in case a value was never registered.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

MASK = "<redacted>"

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)(bearer\s+)[^\s\"',]+")
_QUERY = re.compile(r"(?i)((?:access_token|token|password)=)[^&\s\"']+")

_SECRET_KEY_PARTS = ("token", "password", "secret", "apikey", "api_key", "recoverycode", "cookie")
# Keys that contain "token" but are not secrets.
_SAFE_KEYS = {"tokentype", "expiresat", "expiration"}


def is_secret_key(key: str) -> bool:
    k = key.lower().replace("-", "").replace("_", "")
    if k in _SAFE_KEYS:
        return False
    return any(part.replace("_", "") in k for part in _SECRET_KEY_PARTS)


class Redactor:
    """``add`` keeps a secret for good (a password). ``add(..., short_lived=True)`` is for session
    tokens and hub tickets: a long-running bot sees a new one on every renewal and reconnect, so only
    the newest ``max_short_lived`` are kept. Older ones have expired, and the JWT and query patterns
    still mask them."""

    def __init__(self, secrets: Iterable[str] = (), *, max_short_lived: int = 256) -> None:
        self._secrets: set[str] = set()
        self._short_lived: OrderedDict[str, None] = OrderedDict()
        self._max_short_lived = max_short_lived
        self._ordered: tuple[str, ...] | None = ()
        for s in secrets:
            self.add(s)

    def add(self, secret: str | None, *, short_lived: bool = False) -> None:
        # Very short values would mangle ordinary text; they are covered by the key rules instead.
        if not secret or len(secret) < 6 or secret in self._secrets:
            return
        if short_lived:
            self._short_lived[secret] = None
            self._short_lived.move_to_end(secret)
            while len(self._short_lived) > self._max_short_lived:
                self._short_lived.popitem(last=False)
        else:
            self._short_lived.pop(secret, None)
            self._secrets.add(secret)
        self._ordered = None

    def _known(self) -> tuple[str, ...]:
        # Longest first, so a secret that contains another is masked whole. Sorted once per change,
        # not once per log line.
        if self._ordered is None:
            self._ordered = tuple(sorted({*self._secrets, *self._short_lived}, key=len, reverse=True))
        return self._ordered

    def text(self, value: str) -> str:
        for secret in self._known():
            if secret in value:
                value = value.replace(secret, MASK)
        value = _JWT.sub(MASK, value)
        value = _BEARER.sub(lambda m: m.group(1) + MASK, value)
        value = _QUERY.sub(lambda m: m.group(1) + MASK, value)
        return value

    def url(self, value: str) -> str:
        """Like ``text`` but also masks ``id=`` (a SignalR connection token) in query strings."""
        value = self.text(value)
        return re.sub(r"([?&]id=)[^&\s]+", lambda m: m.group(1) + MASK, value)

    def data(self, value: Any) -> Any:
        """Return a redacted deep copy of JSON-like data."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {
                k: (MASK if isinstance(k, str) and is_secret_key(k) and v not in (None, "") else self.data(v))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.data(v) for v in value]
        return value


class RedactingFilter(logging.Filter):
    """Logging filter that runs every record's final message, and its traceback, through a ``Redactor``.

    The traceback matters: aiohttp's errors print the request URL, and a hub URL carries its ticket as
    ``?access_token=``."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging crash the bot
            return True
        record.msg = self.redactor.text(message)
        record.args = None
        try:
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)  # type: ignore[arg-type]
            if record.exc_text:
                record.exc_text = self.redactor.text(record.exc_text)
                record.exc_info = None  # handlers print exc_text; without this they would format it again
            if record.stack_info:
                record.stack_info = self.redactor.text(record.stack_info)
        except Exception:  # noqa: BLE001 - never let logging crash the bot
            record.exc_info, record.exc_text = None, "<traceback dropped: it could not be redacted>"
        return True
