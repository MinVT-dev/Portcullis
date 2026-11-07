# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end: the recorder against the fake AMP, including a forced reconnect."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest

from tests.fakes.amp_server import PASSWORD, SHARD_ID, USERNAME, FakeAmp, wait_for
from tools import record_events


async def test_recorder_writes_redacted_capture(fake: FakeAmp, base_url: str, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMP_PASSWORD", PASSWORD)
    args = argparse.Namespace(
        url=base_url, username=USERNAME, shard=[], extra_hub=[], mode="auto", duration=3.0,
        out=tmp_path, env_file=tmp_path / "missing.env", keep_host=False, keep_ips=False,
        quiet=True, verbose=False,
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
    assert lifecycle[0] == "probe-ok"
    assert lifecycle.count("connected") == 2 and lifecycle.count("joined") == 2
    summary = (capture / "summary.txt").read_text()
    assert "shard:UserJoined: 1" in summary and "mode: direct" in summary
