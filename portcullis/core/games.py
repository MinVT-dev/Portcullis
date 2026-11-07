# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-game knowledge that AMP doesn't give us: how chat looks in the console, and which console
lines to drop.

Join and leave come from AMP's own ``UserJoined`` / ``UserLeft`` events where AMP tracks players
(verified for Unturned). Chat has no AMP event, so it is parsed from the game's ``main`` console
stream with a per-game pattern. Patterns here come from real captures only; a game without a
verified pattern gets no chat relay rather than a guessed one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from portcullis.core.types import ConsoleLine, ServerId


@dataclass(frozen=True)
class ChatMessage:
    server: ServerId
    player: str
    text: str
    channel: str | None = None  # the game's own chat scope, e.g. Unturned's World, Area or Group

    @property
    def is_command(self) -> bool:
        """A player typing ``/help`` shows up as chat; relays should skip these."""
        return self.text.startswith("/")


@dataclass(frozen=True)
class GameProfile:
    key: str
    name: str
    chat: re.Pattern[str] | None = None
    tracks_players: bool = True  # whether AMP's roster and UserJoined/UserLeft work for this game
    duplicate_lines: bool = False  # AMP delivers every main line twice for this game
    notes: tuple[str, ...] = field(default=())

    def parse_chat(self, line: ConsoleLine) -> ChatMessage | None:
        if self.chat is None or line.stream != "main" or line.kind != "line":
            return None
        match = self.chat.match(line.text)
        if match is None:
            return None
        groups = match.groupdict()
        return ChatMessage(server=line.server, player=groups["player"], text=groups["text"],
                           channel=groups.get("channel"))


# Unturned 3, captured 2026-11-07 on AMP 3.0.0.6:
#   [World] Alice [Alice]: "hello world"
# The bracketed second name is the character name. Scopes seen: World; Area and Group are the
# game's other chat modes.
UNTURNED = GameProfile(
    key="unturned",
    name="Unturned",
    chat=re.compile(r'^\[(?P<channel>World|Area|Group)\] (?P<player>.+?) \[(?P<character>.*)\]: "(?P<text>.*)"$'),
    duplicate_lines=True,
    notes=("Joins also log 'Connecting: PlayerID: <steam64> Name: <name> Character: <name>'.",),
)

# Arma Reforger, captured 2026-11-07: AMP's roster stays empty with a player in game, and the
# console carries no player names or chat (BattlEye RCON holds them and AMP doesn't forward it).
ARMA_REFORGER = GameProfile(key="arma-reforger", name="Arma Reforger", tracks_players=False)

GENERIC = GameProfile(key="generic", name="Other game")

PROFILES: dict[str, GameProfile] = {p.key: p for p in (UNTURNED, ARMA_REFORGER)}


def profile_for(module: str | None) -> GameProfile:
    """Match an AMP template or shard name such as ``unturned`` or ``arma-reforger``."""
    key = (module or "").lower()
    for name, profile in PROFILES.items():
        if name in key:
            return profile
    return GENERIC


class LineDeduper:
    """Drops AMP's doubled console lines.

    For some games (Unturned) AMP sends each ``main`` line twice with consecutive ``seq`` numbers,
    and the second copy can arrive minutes late, with the next output. A line is a duplicate when
    its text equals the previous line on the same stream and its seq is exactly one higher.
    """

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], tuple[str, int | None]] = {}

    def is_duplicate(self, line: ConsoleLine) -> bool:
        key = (line.server, line.stream)
        previous = self._last.get(key)
        self._last[key] = (line.text, line.seq)
        if previous is None or previous[0] != line.text:
            return False
        prev_seq = previous[1]
        duplicate = line.seq is None or prev_seq is None or line.seq == prev_seq + 1
        if duplicate:
            del self._last[key]  # one copy per line: a third identical line is real output
        return duplicate
