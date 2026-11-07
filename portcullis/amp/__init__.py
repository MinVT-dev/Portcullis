# SPDX-License-Identifier: AGPL-3.0-or-later
"""The only package that knows AMP URLs, auth and wire formats (DESIGN.md section 6).

Nothing outside ``portcullis.amp`` should import the generated ``models`` or ``api`` modules.
"""

from portcullis.amp.auth import Credentials, HubTicket, TokenManager
from portcullis.amp.client import AmpClient
from portcullis.amp.errors import (
    AmpError,
    AuthExpired,
    AuthFailed,
    BadRequest,
    Conflict,
    LoginNotSupported,
    MissingCapability,
    NotFound,
    RateLimited,
    Unreachable,
)
from portcullis.amp.signalr import HubConnection, HubEvent

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
