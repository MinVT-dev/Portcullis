# SPDX-License-Identifier: AGPL-3.0-or-later
"""The generated API groups build the right requests and parse responses into models."""

from __future__ import annotations

import inspect

import aiohttp
import pytest

from portcullis.amp import models as m
from portcullis.amp.api import GROUPS, ShardsApi
from portcullis.amp.auth import TokenManager
from portcullis.amp.errors import BadRequest
from portcullis.amp.transport import AmpTransport, encode_query
from tests.fakes.amp_server import SHARD_ID, FakeAmp


def test_every_operation_is_generated() -> None:
    count = sum(1 for cls in GROUPS.values() for v in vars(cls).values() if inspect.iscoroutinefunction(v))
    assert count == 263


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
