# SPDX-License-Identifier: AGPL-3.0-or-later
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
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets: set[str] = set()
        for s in secrets:
            self.add(s)

    def add(self, secret: str | None) -> None:
        # Very short values would mangle ordinary text; they are covered by the key rules instead.
        if secret and len(secret) >= 6:
            self._secrets.add(secret)

    def text(self, value: str) -> str:
        # Longest first, so a secret that contains another is masked whole.
        for secret in sorted(self._secrets, key=len, reverse=True):
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
    """Logging filter that runs every record's final message through a ``Redactor``."""

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
        return True
