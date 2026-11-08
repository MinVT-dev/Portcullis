# SPDX-License-Identifier: MIT
"""Async Python client for the CubeCoders AMP 3 web API.

    from portcullis_api import AmpClient, Credentials

    async with AmpClient("https://panel.example:8443", Credentials("bot", password)) as amp:
        for shard in await amp.shards.get_shards():
            print(shard.display_name, shard.id)

* ``AmpClient``: HTTP session, login and renewal, every REST operation of the pinned spec as a
  typed async method (``amp.shards``, ``amp.scheduling``, ``amp.files_backups``, ...).
* ``portcullis_api.models``: pydantic v2 models for every schema in the spec.
* ``portcullis_api.live``: live shard events over SignalR (``watch_shard``).
* ``portcullis_api.testing``: an in-process fake panel for your own tests.

Not affiliated with or endorsed by CubeCoders. "AMP" is CubeCoders' product name.
"""

from portcullis_api.auth import Credentials, HubTicket, TokenManager
from portcullis_api.client import AmpClient
from portcullis_api.errors import (
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
from portcullis_api.live import DEFAULT_TENANT, watch_shard
from portcullis_api.redact import Redactor
from portcullis_api.signalr import HubConnection, HubError, HubEvent, HubGaveUp

__version__ = "0.1.0"
SPEC_BUILD = "3.0.0.6"  # the AMP build whose OpenAPI spec the client was generated from

__all__ = [
    "DEFAULT_TENANT",
    "SPEC_BUILD",
    "AmpClient",
    "AmpError",
    "AuthExpired",
    "AuthFailed",
    "BadRequest",
    "Conflict",
    "Credentials",
    "HubConnection",
    "HubError",
    "HubEvent",
    "HubGaveUp",
    "HubTicket",
    "LoginNotSupported",
    "MissingCapability",
    "NotFound",
    "RateLimited",
    "Redactor",
    "TokenManager",
    "Unreachable",
    "__version__",
    "watch_shard",
]
