# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from portcullis_api.auth import Credentials, TokenManager
from portcullis_api.testing import PASSWORD, USERNAME, FakeAmp


@pytest.fixture(autouse=True)
def no_os_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read or write the real keyring of the machine they run on."""
    monkeypatch.setenv("PORTCULLIS_KEYRING", "off")


@pytest.fixture
def fake() -> FakeAmp:
    return FakeAmp()


@pytest.fixture
async def server(fake: FakeAmp) -> AsyncIterator[TestServer]:
    srv = TestServer(fake.app())
    await srv.start_server()
    yield srv
    await srv.close()


@pytest.fixture
def base_url(server: TestServer) -> str:
    return str(server.make_url("")).rstrip("/")


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
def tokens(base_url: str, session: aiohttp.ClientSession, fake: FakeAmp) -> TokenManager:
    return TokenManager(base_url, session, Credentials(USERNAME, PASSWORD), clock=fake.clock,
                        refresh_margin=timedelta(minutes=30))
