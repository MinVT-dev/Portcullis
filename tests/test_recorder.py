# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end: the recorder against the fake AMP, including a forced reconnect."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest

from tests.fakes.amp_server import PASSWORD, SHARD_ID, TENANT, USERNAME, FakeAmp, wait_for
from tools import record_events


async def test_recorder_writes_redacted_capture(fake: FakeAmp, base_url: str, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMP_PASSWORD", PASSWORD)
    args = argparse.Namespace(
        url=base_url, username=USERNAME, shard=[], extra_hub=[], mode="auto", duration=3.0,
        out=tmp_path, env_file=tmp_path / "missing.env", keep_host=False, keep_ips=False,
        quiet=True, verbose=False, join_attempts=3, no_inspect=False,
    )
    task = asyncio.create_task(record_events.run(args))
    await wait_for(lambda: len(fake.joined) == 1)
    await fake.broadcast("UserJoined", {"shardId": SHARD_ID, "name": "Steve", "address": "203.0.113.7"})
    await fake.drop_all()
    await wait_for(lambda: len(fake.joined) == 2)
    await fake.broadcast("ConsoleLine", {"text": "<Steve> hello", "seq": 1})
    assert await task == 0

    capture = next(tmp_path.iterdir())
    events = [json.loads(line) for line in (capture / "events.jsonl").read_text().splitlines()]
    assert [e["target"] for e in events] == ["UserJoined", "ConsoleLine"]
    assert events[0]["arguments"][0]["address"] == "<ip>"

    everything = "".join(p.read_text() for p in capture.iterdir())
    host = base_url.split("//", 1)[1]
    secrets = [PASSWORD, host, *fake.tickets, *fake.tokens]
    assert not [s for s in secrets if s in everything]

    lifecycle = [json.loads(line)["event"] for line in (capture / "lifecycle.jsonl").read_text().splitlines()]
    assert lifecycle.index("probe-ok") < lifecycle.index("connected")
    assert lifecycle.count("connected") == 2 and lifecycle.count("joined") == 2
    summary = (capture / "summary.txt").read_text()
    assert "shard:UserJoined: 1" in summary and "mode: direct" in summary


def recorder_args(base_url: str, tmp_path: Path, **kw) -> argparse.Namespace:
    values = dict(url=base_url, username=USERNAME, shard=[], extra_hub=[], mode="auto", duration=3.0,
                  out=tmp_path, env_file=tmp_path / "missing.env", keep_host=False, keep_ips=False,
                  quiet=True, verbose=False, join_attempts=3, no_inspect=False)
    values.update(kw)
    return argparse.Namespace(**values)


def lifecycle_of(capture: Path) -> list[dict]:
    return [json.loads(line) for line in (capture / "lifecycle.jsonl").read_text().splitlines()]


async def test_recorder_gives_up_when_join_always_fails(fake: FakeAmp, base_url: str, tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMP_PASSWORD", PASSWORD)
    fake.join_error = "Failed to invoke 'JoinShard' due to an error on the server."
    code = await asyncio.wait_for(record_events.run(recorder_args(base_url, tmp_path, duration=None)), 10)
    assert code == 3

    capture = next(tmp_path.iterdir())
    records = lifecycle_of(capture)
    kinds = [r["event"] for r in records]
    assert kinds.count("join-failed") == 3 and kinds.count("gave-up") == 1
    failed = [r for r in records if r["event"] == "join-failed"][:1]
    assert failed[0]["arguments"] == [TENANT, SHARD_ID]
    assert failed[0]["state"] == "online" and "error on the server" in failed[0]["error"]

    panel = (capture / "panel_hub_calls.txt").read_text()
    assert "invoke(`JoinShard`,e,t)" in panel


def test_noisy_events_are_echoed_only_when_they_change(tmp_path: Path) -> None:
    from portcullis.amp.signalr import HubEvent

    rec = record_events.Recorder(tmp_path, record_events.CaptureRedactor(scrub_ips=True), echo=True)
    state = {"status": 25, "isRunning": True, "revision": 1}
    seen = [
        rec._worth_echoing(HubEvent("shard", "Metrics", [SHARD_ID, {"cpuPercent": 3}])),
        rec._worth_echoing(HubEvent("shard", "ShardStateChanged", [TENANT, SHARD_ID, state])),
        rec._worth_echoing(HubEvent("shard", "ShardStateChanged", [TENANT, SHARD_ID, {**state, "revision": 2}])),
        rec._worth_echoing(HubEvent("shard", "ShardStateChanged", [TENANT, SHARD_ID, {**state, "isRunning": False}])),
        rec._worth_echoing(HubEvent("shard", "UserRoster", [SHARD_ID, []])),
        rec._worth_echoing(HubEvent("shard", "UserRoster", [SHARD_ID, []])),
        rec._worth_echoing(HubEvent("shard", "UserRoster", [SHARD_ID, [{"name": "Steve"}]])),
        rec._worth_echoing(HubEvent("shard", "ConsoleLine", [SHARD_ID, {"text": "hi"}])),
    ]
    assert seen == [False, True, False, True, True, False, True, True]


def test_versions_are_not_mistaken_for_ip_addresses() -> None:
    r = record_events.CaptureRedactor(scrub_ips=True)
    assert r.text("agent 3.0.0.6+f3d1a77cea from 192.168.1.20") == "agent 3.0.0.6+f3d1a77cea from <ip>"


async def test_recorder_rejects_unknown_shard(fake: FakeAmp, base_url: str, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("AMP_PASSWORD", PASSWORD)
    code = await record_events.run(recorder_args(base_url, tmp_path, shard=["0000"]))
    assert code == 1
    assert "can't see shard 0000" in capsys.readouterr().out
