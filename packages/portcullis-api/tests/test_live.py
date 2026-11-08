# SPDX-License-Identifier: MIT
"""watch_shard: joins with the tenant, filters other shards' events, leaves when the caller stops."""

from __future__ import annotations

import asyncio
import contextlib

import portcullis_api
from portcullis_api import AmpClient, Credentials, watch_shard
from portcullis_api.live import shard_tenant
from portcullis_api.testing import PASSWORD, SHARD_ID, TENANT, USERNAME, FakeAmp, wait_for

OTHER = "00000000-0000-4000-8000-000000000000"


async def test_shard_tenant_reads_the_shard(base_url: str, fake: FakeAmp) -> None:
    async with AmpClient(base_url, Credentials(USERNAME, PASSWORD)) as amp:
        assert await shard_tenant(amp, SHARD_ID) == TENANT


async def test_watch_shard_yields_own_events_and_skips_others(base_url: str, fake: FakeAmp) -> None:
    async def produce() -> None:
        await wait_for(lambda: fake.joined == [SHARD_ID])
        await fake.broadcast("ShardStateChanged", TENANT, OTHER, {"isRunning": True})
        await fake.broadcast("Metrics", SHARD_ID, {"cpuUsagePercent": 5})
        await fake.broadcast("ConsoleLine", {"text": "hello", "streamId": "main"})

    seen = []
    async with AmpClient(base_url, Credentials(USERNAME, PASSWORD)) as amp:
        producer = asyncio.create_task(produce())
        async with contextlib.aclosing(watch_shard(amp, SHARD_ID)) as events:
            async for event in events:
                seen.append(event.target)
                if len(seen) == 2:
                    break
        await producer
        await wait_for(lambda: fake.left == [SHARD_ID])
    assert seen == ["Metrics", "ConsoleLine"]


def test_public_surface() -> None:
    for name in portcullis_api.__all__:
        assert hasattr(portcullis_api, name), name
    assert portcullis_api.SPEC_BUILD == "3.0.0.6"


def test_package_never_imports_the_bot() -> None:
    from pathlib import Path

    root = Path(portcullis_api.__file__).parent
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from portcullis." not in text and "import portcullis." not in text, path
