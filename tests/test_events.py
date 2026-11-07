# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hub payload parsing. These shapes are guesses until real captures exist; the tests pin the
lenient behaviour so the guesses can be swapped for fixtures safely."""

from __future__ import annotations

from datetime import UTC, datetime

from portcullis.amp.events import parse_hub_event, state_from
from portcullis.amp.signalr import HubEvent
from portcullis.core.types import ConsoleLine, OtherEvent, PlayerJoined, PlayerLeft, ServerState, StateChanged

SID = "7d4b6a52-9a7e-4e0e-9a38-1d1f0d3c2b11"
OTHER = "aaaaaaaa-9a7e-4e0e-9a38-1d1f0d3c2b11"
AT = datetime(2026, 11, 7, 12, tzinfo=UTC)


def ev(target: str, *args) -> HubEvent:  # noqa: ANN002
    return HubEvent("shard", target, list(args), AT)


def test_console_line_with_shard_in_payload() -> None:
    e = parse_hub_event(ev("ConsoleLine", {"shardId": SID, "text": "hello", "seq": "42", "kind": "line",
                                           "timestamp": "2026-11-07T12:00:01.1234567Z"}), [SID, OTHER])
    assert isinstance(e, ConsoleLine) and e.text == "hello" and e.seq == 42 and e.server == SID
    assert e.at.second == 1


def test_shard_id_as_first_argument_and_pascal_case() -> None:
    e = parse_hub_event(ev("UserJoined", SID.upper(), {"Name": "Steve", "Id": "76561198000000000"}), [SID, OTHER])
    assert isinstance(e, PlayerJoined) and e.player.name == "Steve" and e.server == SID


def test_single_joined_shard_is_assumed() -> None:
    e = parse_hub_event(ev("UserLeft", {"user": {"name": "Alex"}}), [SID])
    assert isinstance(e, PlayerLeft) and e.player.name == "Alex"


def test_unattributable_event_is_dropped() -> None:
    assert parse_hub_event(ev("UserLeft", {"name": "Alex"}), [SID, OTHER]) is None


def test_state_and_unknown_events() -> None:
    e = parse_hub_event(ev("ShardStateChanged", {"shardId": SID, "status": 25, "isRunning": True}), [SID])
    assert isinstance(e, StateChanged) and e.state is ServerState.RUNNING and e.raw_status == 25
    other = parse_hub_event(ev("Metrics", {"shardId": SID, "cpuPercent": 3}), [SID])
    assert isinstance(other, OtherEvent) and other.name == "Metrics"
    malformed = parse_hub_event(ev("UserJoined", {"shardId": SID}), [SID])
    assert isinstance(malformed, OtherEvent)


def test_state_mapping() -> None:
    assert state_from({"isBusy": True, "isRunning": True})[0] is ServerState.BUSY
    assert state_from({"isStopped": True})[0] is ServerState.STOPPED
    assert state_from({"isStopped": True, "lastErrorCode": "app.crashed"})[0] is ServerState.FAILED
    assert state_from({"isRunning": True}, suspended=True)[0] is ServerState.SUSPENDED
    assert state_from({"status": 99}) == (ServerState.UNKNOWN, 99)
