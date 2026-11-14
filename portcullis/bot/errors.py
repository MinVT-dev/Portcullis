# SPDX-License-Identifier: AGPL-3.0-or-later
"""What went wrong, in words a Discord member can act on, instead of AMP's raw error text.

Discord code may not import the AMP client (DESIGN.md section 6), so errors are read by shape: the
client's errors carry an HTTP ``status``, a ``retry_after`` and a class name (``AuthFailed``,
``LoginNotSupported``, ``Unreachable``). The raw text still goes to the audit log and the bot's log.
"""

from __future__ import annotations

import math

from portcullis.bot.base import code
from portcullis.core.types import TryLater

# Read by class name, because the classes live in portcullis_api.
_BY_NAME = {
    "AuthFailed": "AMP turned down the bot's login. Check AMP_USERNAME and AMP_PASSWORD in the bot's .env, "
                  "and that the account isn't suspended.",
    "LoginNotSupported": "The bot's AMP account needs two-factor or a passkey to log in, which a bot can't do. "
                         "Give it an account without them (docs/SETUP.md).",
    "AuthExpired": "AMP ended the bot's session. It logs in again by itself; try once more in a minute.",
}
_BY_STATUS = {
    400: "AMP didn't accept the request as sent. If this keeps happening with normal input, tell an admin.",
    403: "The bot's AMP account isn't allowed to do that. An admin can add the permission to its role in AMP "
         "(`/admin diagnostics` lists what it has).",
    404: "AMP doesn't know that server or item any more. It may have been deleted or moved in the panel.",
    409: "AMP is busy with another job on this server (an update, a backup or a restart). Try again when it's done.",
    429: "AMP asked the bot to slow down.",
    502: "AMP, or the machine running this server, didn't answer.",
    503: "AMP, or the machine running this server, didn't answer.",
    504: "AMP, or the machine running this server, took too long to answer.",
}


def _wait(seconds: object) -> str:
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or not math.isfinite(seconds):
        return ""
    if seconds <= 0:
        return ""
    if seconds < 90:
        return f" Try again in {max(1, round(seconds))} seconds."
    return f" Try again in {round(seconds / 60)} minutes."


def plain_error(exc: BaseException) -> str:
    """One or two sentences for the member. Never AMP's raw message, paths or status codes."""
    if isinstance(exc, TryLater):
        return str(exc) or "AMP can't do that right now. Try again in a few minutes."
    if isinstance(exc, TimeoutError):
        return "AMP took too long to answer. The panel may be busy or restarting; try again in a minute."
    by_name = _BY_NAME.get(type(exc).__name__)
    if by_name:
        return by_name
    status = getattr(exc, "status", None)
    wait = _wait(getattr(exc, "retry_after", None))
    if isinstance(status, int) and not isinstance(status, bool):
        if status in _BY_STATUS:
            return _BY_STATUS[status] + wait
        if status >= 500:
            return "AMP hit an error of its own. Check the panel's log, then try again." + wait
    if type(exc).__name__ == "Unreachable" or isinstance(exc, ConnectionError):
        return "The bot can't reach AMP right now. The panel may be down or restarting." + wait
    if isinstance(exc, ValueError):
        return "The bot can't send that: a console command must be one line without control characters."
    # Nothing we recognise: say so, and show AMP's words as a quoted detail rather than as the message.
    detail = str(exc) or type(exc).__name__
    detail = detail if len(detail) <= 200 else detail[:199] + "…"
    return f"What AMP said: {code(detail)}"
