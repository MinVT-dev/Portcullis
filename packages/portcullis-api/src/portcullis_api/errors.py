# SPDX-License-Identifier: MIT
"""Errors raised by the AMP client.

AMP 3 returns three error body shapes (API_REFERENCE.md section 6): RFC 9110 ProblemDetails,
AMP's own ``ApiError`` (``{"code", "params", "message"}``) and the analytics ``{"error"}``.
``AmpError.from_response`` reads any of them.
"""

from __future__ import annotations

import json
from typing import Any


class AmpError(Exception):
    """Any error from AMP or from talking to it."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        body: Any = None,
        method: str | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.body = body
        self.method = method
        self.path = path

    def __str__(self) -> str:
        where = f"{self.method} {self.path}: " if self.method else ""
        status = f"[{self.status}] " if self.status is not None else ""
        code = f" ({self.code})" if self.code else ""
        return f"{where}{status}{self.message}{code}"

    @staticmethod
    def parse_body(text: str) -> tuple[str | None, str | None, Any]:
        """Return (code, message, parsed body) for any of AMP's error shapes."""
        if not text:
            return None, None, None
        try:
            body = json.loads(text)
        except ValueError:
            return None, text[:300], text
        if not isinstance(body, dict):
            return None, None, body
        if "code" in body and "message" in body:  # ApiError
            return body.get("code"), body.get("message"), body
        if "title" in body:  # ProblemDetails
            message = body.get("title")
            errors = body.get("errors")
            if isinstance(errors, dict) and errors:
                details = "; ".join(f"{k}: {', '.join(map(str, v))}" for k, v in errors.items())
                message = f"{message} {details}"
            return body.get("code"), message, body
        if "error" in body:  # analytics
            return body.get("error"), body.get("error"), body
        return None, None, body

    @classmethod
    def from_response(cls, status: int, text: str, method: str, path: str) -> AmpError:
        code, message, body = cls.parse_body(text)
        kind: type[AmpError]
        if status == 400:
            kind = BadRequest
        elif status == 401:
            kind = AuthExpired
        elif status == 403:
            kind = MissingCapability
        elif status == 404:
            kind = NotFound
        elif status == 409:
            kind = Conflict
        elif status == 429:
            kind = RateLimited
        elif status in (502, 503, 504):
            kind = Unreachable
        else:
            kind = AmpError
        return kind(
            message or f"HTTP {status}", status=status, code=code, body=body, method=method, path=path
        )


class BadRequest(AmpError):
    """400: validation failed."""


class AuthExpired(AmpError):
    """401 on a normal call: the token is missing, bad, expired or its session was revoked."""


class AuthFailed(AmpError):
    """Login itself failed: wrong username or password, or the account is suspended."""


class LoginNotSupported(AmpError):
    """Login succeeded but AMP wants a flow a bot can't do (2FA, passkey, forced password change)."""


class MissingCapability(AmpError):
    """403: the bot's AMP account lacks a permission node for this call."""


class NotFound(AmpError):
    """404: unknown id."""


class Conflict(AmpError):
    """409."""


class RateLimited(AmpError):
    """429 (AMP only documents this for failed logins)."""


class Unreachable(AmpError):
    """AMP, or a spire behind it, did not answer."""
