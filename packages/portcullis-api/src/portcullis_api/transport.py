# SPDX-License-Identifier: MIT
"""HTTP transport for the AMP 3 REST API: auth header, one retry after a 401, retries for safe calls,
a concurrency cap, typed parsing and redacted logging."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from enum import Enum
from functools import lru_cache
from typing import Any
from urllib.parse import quote
from uuid import UUID

import aiohttp
from pydantic import BaseModel, TypeAdapter

from portcullis_api.auth import TokenManager
from portcullis_api.errors import AmpError, AuthExpired, Unreachable

log = logging.getLogger(__name__)

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@lru_cache(maxsize=512)
def _adapter(tp: Any) -> TypeAdapter[Any]:
    return TypeAdapter(tp)


def _query_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (UUID, int, float)):
        return str(value)
    return value


def encode_query(params: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            out.extend((key, _query_value(v)) for v in value if v is not None)
        else:
            out.append((key, _query_value(value)))
    return out


def encode_body(body: Any) -> Any:
    if body is None:
        return None
    if isinstance(body, BaseModel):
        return body.model_dump(mode="json", by_alias=True, exclude_unset=True)
    if isinstance(body, list):
        return [encode_body(b) for b in body]
    return body


def fill_path(template: str, values: Mapping[str, Any]) -> str:
    path = template
    for key, value in values.items():
        if value is None:
            raise ValueError(f"path parameter {key!r} is required")
        path = path.replace("{" + key + "}", quote(str(_query_value(value)), safe=""))
    return path


class AmpTransport:
    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession,
        auth: TokenManager,
        *,
        timeout: float = 30.0,
        max_concurrency: int = 8,
        safe_retries: int = 2,
        retry_backoff: float = 1.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session
        self.auth = auth
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._sem = asyncio.Semaphore(max_concurrency)
        self._safe_retries = safe_retries
        self._retry_backoff = retry_backoff

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]],
        json_body: Any,
        form: Mapping[str, str] | None,
        timeout: aiohttp.ClientTimeout,
    ) -> tuple[int, str, str]:
        token = await self.auth.token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        url = self.base_url + path
        started = time.monotonic()
        async with self._sem, self._session.request(
            method, url, params=params, json=json_body, data=form, headers=headers, timeout=timeout
        ) as resp:
            text = await resp.text()
        log.debug("%s %s -> %s in %.0f ms", method, path, resp.status, (time.monotonic() - started) * 1000)
        return resp.status, text, token

    async def request(
        self,
        method: str,
        path: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        form: Mapping[str, str] | None = None,
        response_type: Any = None,
        timeout: float | None = None,
    ) -> Any:
        """Call AMP and return the parsed body (validated as ``response_type`` when given)."""
        method = method.upper()
        real_path = fill_path(path, path_params or {})
        query = encode_query(params)
        json_body = encode_body(body)
        client_timeout = aiohttp.ClientTimeout(total=timeout) if timeout else self._timeout
        retries = self._safe_retries if method in _SAFE_METHODS else 0
        reauthed = False
        attempt = 0
        while True:
            try:
                status, text, token = await self._send(
                    method, real_path, params=query, json_body=json_body, form=form, timeout=client_timeout
                )
            except (aiohttp.ClientConnectionError, TimeoutError) as exc:
                if attempt < retries:
                    attempt += 1
                    await asyncio.sleep(self._retry_backoff * attempt)
                    continue
                raise Unreachable(f"AMP did not answer: {type(exc).__name__}", method=method,
                                  path=path) from exc
            if status == 401 and not reauthed:
                # Token expired or session revoked: renew (or log in again) and retry once.
                reauthed = True
                await self.auth.invalidate(token)
                continue
            if status in (502, 503, 504) and attempt < retries:
                attempt += 1
                await asyncio.sleep(self._retry_backoff * attempt)
                continue
            break

        if status >= 400:
            err = AmpError.from_response(status, self.auth.redactor.text(text), method, path)
            if status == 401:
                raise AuthExpired(err.message, status=401, method=method, path=path)
            raise err
        if not text:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            data = text
        if response_type is None:
            return data
        return _adapter(response_type).validate_python(data)
