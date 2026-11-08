# SPDX-License-Identifier: AGPL-3.0-or-later
"""Game profiles, pinned to lines from real AMP 3 captures (player names replaced)."""

from __future__ import annotations

from datetime import UTC, datetime

from portcullis.amp.events import parse_hub_event
from portcullis.core.games import ARMA_REFORGER, GENERIC, UNTURNED, LineDeduper, profile_for
from portcullis.core.types import ConsoleLine, PlayerJoined
from portcullis_api.signalr import HubEvent

SID = "6366e687-3bbd-46fa-9195-5e789b3a91aa"
AT = datetime(2026, 11, 7, 19, 11, tzinfo=UTC)


def line(text: str, seq: int | None = None, stream: str = "main", kind: str = "line") -> ConsoleLine:
    return ConsoleLine(server=SID, at=AT, text=text, seq=seq, kind=kind, stream=stream)


def test_unturned_chat() -> None:
    msg = UNTURNED.parse_chat(line('[World] Alice [Alice]: "hello world"'))
    assert msg is not None and (msg.player, msg.text, msg.channel) == ("Alice", "hello world", "World")
    assert not msg.is_command
    quoted = UNTURNED.parse_chat(line('[Group] Bob Smith [Bobby]: "say "hi" back"'))
    assert quoted is not None and quoted.player == "Bob Smith" and quoted.text == 'say "hi" back'


def test_unturned_player_command_is_flagged() -> None:
    msg = UNTURNED.parse_chat(line('[World] Alice [Alice]: "/vehicle"'))
    assert msg is not None and msg.is_command


def test_non_chat_lines_are_ignored() -> None:
    for text in ("Connecting: PlayerID: 76561190000000000 Name: Alice Character: Alice",
                 "Failed to give vehicle with cheats disabled.",
                 "BoxCollider does not support negative scale or size."):
        assert UNTURNED.parse_chat(line(text)) is None
    assert UNTURNED.parse_chat(line('[World] Alice [Alice]: "x"', stream="agent")) is None
    assert GENERIC.parse_chat(line('[World] Alice [Alice]: "x"')) is None


def test_profile_lookup() -> None:
    assert profile_for("unturned") is UNTURNED
    assert profile_for("Arma-Reforger") is ARMA_REFORGER and not ARMA_REFORGER.tracks_players
    assert profile_for("minecraft-java-edition") is GENERIC and profile_for(None) is GENERIC


def test_deduper_drops_amps_doubled_lines() -> None:
    d = LineDeduper()
    assert not d.is_duplicate(line('[World] Alice [Alice]: "hi"', 5978))
    assert d.is_duplicate(line('[World] Alice [Alice]: "hi"', 5979))
    # The late copy (minutes later, next seq) is still a duplicate.
    assert not d.is_duplicate(line("Failed to give vehicle with cheats disabled.", 5986))
    assert d.is_duplicate(line("Failed to give vehicle with cheats disabled.", 5987))
    # Same text with a seq gap is a real repeat.
    assert not d.is_duplicate(line("Autosave", 10))
    assert not d.is_duplicate(line("Autosave", 40))


def test_live_user_joined_shape() -> None:
    payload = {"name": "Alice", "id": "76561190000000000", "sessionId": "", "endpoint": None,
               "joinedAt": "2026-11-07T19:10:59.1888017+00:00", "idKind": "steam64"}
    e = parse_hub_event(HubEvent("shard", "UserJoined", [SID, payload], AT), [SID])
    assert isinstance(e, PlayerJoined) and e.server == SID
    assert e.player.name == "Alice" and e.player.id == "76561190000000000" and e.player.joined_at is not None


def test_deduper_drops_only_one_copy() -> None:
    d = LineDeduper()
    assert [d.is_duplicate(line("tick", s)) for s in (1, 2, 3, 4)] == [False, True, False, True]


def test_unturned_relay_hooks() -> None:
    from portcullis.core.games import game_hooks

    hooks = game_hooks("unturned")
    assert hooks.chat is not None and hooks.drop is not None
    assert hooks.chat(line('[World] Alice [Alice]: "hi"')) == ("Alice", "hi")
    assert hooks.chat(line('[World] Alice [Alice]: "/help"')) is None
    assert [hooks.drop(line("x", s)) for s in (1, 2)] == [False, True]
    # Each server gets its own deduper.
    assert game_hooks("unturned").drop(line("x", 2)) is False
    other = game_hooks("minecraft")
    assert other.chat is None and other.drop is None
