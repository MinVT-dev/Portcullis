# SPDX-License-Identifier: AGPL-3.0-or-later
"""The brand emoji set, with a plain Unicode fallback for each one.

The emojis are Discord *application* emojis: an admin uploads the PNGs from ``docs/branding/emoji/``
to the bot's page in the Discord Developer Portal (``docs/EMOJIS.md``). Portcullis looks them up by
name when it starts. Any that are missing, renamed or unreadable fall back to the Unicode emoji
listed here, so an embed never shows a broken ``:name:`` and never fails because of an emoji.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from portcullis.core.relay import STATE_ICONS
from portcullis.core.types import ServerState

log = logging.getLogger(__name__)

# key -> (application emoji name in the Developer Portal, Unicode fallback)
SET: dict[str, tuple[str, str]] = {
    "online": ("portcullis_online", "🟢"),
    "offline": ("portcullis_offline", "🔴"),
    "starting": ("portcullis_starting", "🟡"),
    "stopping": ("portcullis_stopping", "🟠"),
    "licence": ("portcullis_licence", "📜"),
    "backup": ("portcullis_backup", "💾"),
    "console": ("portcullis_console", "⌨️"),
    "players": ("portcullis_players", "👥"),
}
# States drawn with a brand emoji. The rest keep their Unicode icon from the relay.
STATE_KEYS = {
    ServerState.RUNNING: "online",
    ServerState.STOPPED: "offline",
    ServerState.BUSY: "starting",
    ServerState.AWAITING_LICENCE: "licence",
}


def _name(emoji: Any) -> str | None:
    name = getattr(emoji, "name", None)
    return name if isinstance(name, str) else None


class Emojis:
    """Emoji text by key. ``found`` holds the ones Discord has; everything else is the fallback."""

    def __init__(self) -> None:
        self.found: dict[str, str] = {}
        self.checked = False  # set after the first lookup, found or not
        self.error: str | None = None  # why the last lookup failed, for /health

    def __getitem__(self, key: str) -> str:
        if key in self.found:
            return self.found[key]
        entry = SET.get(key)
        return entry[1] if entry else "❔"

    def state(self, state: ServerState, *, going_down: bool = False) -> str:
        """The icon for a server state. ``going_down``: a busy server Portcullis is stopping or restarting."""
        key = STATE_KEYS.get(state)
        if state is ServerState.BUSY and going_down:
            key = "stopping"
        if key is None:
            return STATE_ICONS.get(state, "❔")
        return self[key]

    def missing(self) -> list[str]:
        return [SET[k][0] for k in SET if k not in self.found]

    def load(self, emojis: Iterable[Any]) -> list[str]:
        """Take the bot's application emojis. Returns the brand names that weren't among them."""
        by_name = {_name(e): e for e in emojis}
        found: dict[str, str] = {}
        for key, (name, _fallback) in SET.items():
            emoji = by_name.get(name)
            emoji_id = getattr(emoji, "id", None)
            if emoji is None or isinstance(emoji_id, bool) or not isinstance(emoji_id, int):
                continue
            prefix = "a" if getattr(emoji, "animated", False) is True else ""
            found[key] = f"<{prefix}:{name}:{emoji_id}>"
        self.found = found
        self.checked = True
        self.error = None
        return self.missing()

    async def fetch(self, client: Any) -> list[str]:
        """Look the set up through Discord. Never raises: on any failure every emoji uses its fallback."""
        try:
            emojis = await client.fetch_application_emojis()
        except Exception as exc:  # noqa: BLE001 - emojis are cosmetic; the fallback always works
            self.found = {}
            self.checked = True
            self.error = str(exc) or type(exc).__name__
            log.warning("could not read the bot's application emojis, using plain emojis: %s", self.error)
            return self.missing()
        missing = self.load(emojis)
        if missing:
            log.info("brand emojis not uploaded yet, using plain ones for: %s (docs/EMOJIS.md)", ", ".join(missing))
        return missing


# The one set the bot uses. Tests that load emojis make their own ``Emojis()``.
EMOJI = Emojis()
