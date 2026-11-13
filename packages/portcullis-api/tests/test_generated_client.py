# SPDX-License-Identifier: MIT
"""The generated API groups build the right requests and parse responses into models."""

from __future__ import annotations

import inspect

import aiohttp
import pytest

from portcullis_api import models as m
from portcullis_api.api import GROUPS, ShardsApi
from portcullis_api.auth import TokenManager
from portcullis_api.errors import BadRequest
from portcullis_api.testing import SHARD_ID, FakeAmp
from portcullis_api.transport import AmpTransport, encode_query


def test_every_operation_is_generated() -> None:
    count = sum(1 for cls in GROUPS.values() for v in vars(cls).values() if inspect.iscoroutinefunction(v))
    assert count == 269  # live spec of 2026-11-13 (263 in 3.0.0.6)


async def test_list_shards_parses_models_and_ignores_unknown_fields(
    tokens: TokenManager, base_url: str, session: aiohttp.ClientSession
) -> None:
    api = ShardsApi(AmpTransport(base_url, session, tokens))
    shards = await api.get_shards()
    assert isinstance(shards[0], m.DeployedShard)
    assert str(shards[0].id) == SHARD_ID and shards[0].name == "Survival"


async def test_console_send_serialises_model_by_alias(
    tokens: TokenManager, base_url: str, session: aiohttp.ClientSession, fake: FakeAmp
) -> None:
    api = ShardsApi(AmpTransport(base_url, session, tokens))
    result = await api.post_shards_console(SHARD_ID, m.ConsoleInputBody(command="say hi"))
    assert result is None
    assert "console:say hi" in fake.calls
    with pytest.raises(BadRequest) as info:
        await api.post_shards_console(SHARD_ID, {})
    assert "Command" in str(info.value)


def test_query_encoding() -> None:
    assert encode_query({"a": True, "b": None, "c": [1, 2], "d": 5}) == [("a", "true"), ("c", "1"), ("c", "2"),
                                                                        ("d", "5")]
