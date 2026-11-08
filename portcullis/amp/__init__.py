# SPDX-License-Identifier: AGPL-3.0-or-later
"""Portcullis's adapter onto the AMP 3 client in ``packages/portcullis-api`` (import name ``portcullis_api``).

The generic client (auth, REST, SignalR, generated models) lives in that package so anyone can build
on it. This folder holds only the parts that map AMP onto Portcullis's own types: ``port.py``
(``AmpServerPort``) and ``events.py`` (hub events to ``portcullis.core.types``). Discord code still
never imports either (DESIGN.md section 6).
"""

from portcullis_api import (
    AmpClient,
    AmpError,
    AuthExpired,
    AuthFailed,
    BadRequest,
    Conflict,
    Credentials,
    HubConnection,
    HubEvent,
    HubTicket,
    LoginNotSupported,
    MissingCapability,
    NotFound,
    RateLimited,
    TokenManager,
    Unreachable,
)

__all__ = [
    "AmpClient",
    "AmpError",
    "AuthExpired",
    "AuthFailed",
    "BadRequest",
    "Conflict",
    "Credentials",
    "HubConnection",
    "HubEvent",
    "HubTicket",
    "LoginNotSupported",
    "MissingCapability",
    "NotFound",
    "RateLimited",
    "TokenManager",
    "Unreachable",
]
