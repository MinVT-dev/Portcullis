# SPDX-License-Identifier: AGPL-3.0-or-later
"""Base class for the generated API groups. Hand-written; the other modules here are generated."""

from __future__ import annotations

from portcullis.amp.transport import AmpTransport


class ApiGroup:
    def __init__(self, transport: AmpTransport) -> None:
        self._t = transport
