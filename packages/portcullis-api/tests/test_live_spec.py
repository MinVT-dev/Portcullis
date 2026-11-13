# SPDX-License-Identifier: MIT
"""What changed in the live spec of 2026-11-13: sign-in capabilities, ticket downloads, partial infra
patches and the licence (EULA) answers."""

from __future__ import annotations

from pathlib import Path

import aiohttp
import pytest

from portcullis_api import AmpClient, Credentials
from portcullis_api import models as m
from portcullis_api.auth import AuthCapabilities, TokenManager, login_marker
from portcullis_api.errors import AmpError, AuthExpired, LoginNotSupported
from portcullis_api.testing import PASSWORD, SHARD_ID, USERNAME, FakeAmp
from portcullis_api.transport import encode_body

SNAP = "22222222-2222-2222-2222-222222222222"


# ------------------------------------------------------------------ sign-in capabilities

async def test_login_reads_capabilities_first(tokens: TokenManager, fake: FakeAmp) -> None:
    await tokens.token()
    assert fake.capability_checks == 1
    assert tokens.capabilities is not None
    assert tokens.capabilities.passkey_policy == "Encouraged"
    assert tokens.capabilities.passkey_status == "NotConfigured"  # undocumented, but seen live: kept as is


async def test_required_passkeys_refuse_login_without_trying(tokens: TokenManager, fake: FakeAmp) -> None:
    assert fake.capabilities is not None
    fake.capabilities["passkeyPolicy"] = "Required"
    with pytest.raises(LoginNotSupported) as info:
        await tokens.token()
    assert "passkey" in str(info.value) and "Optional or Encouraged" in str(info.value)
    assert "login" not in fake.calls  # no password was sent
    with pytest.raises(LoginNotSupported):  # held off for the cooldown, without asking again
        await tokens.token()
    assert fake.capability_checks == 1


async def test_missing_capabilities_route_does_not_block_login(tokens: TokenManager, fake: FakeAmp) -> None:
    fake.capabilities = None  # an older build: 404
    await tokens.token()
    assert fake.calls == ["login"]
    assert tokens.capabilities is None


async def test_capability_check_can_be_turned_off(base_url: str, session: aiohttp.ClientSession,
                                                  fake: FakeAmp) -> None:
    assert fake.capabilities is not None
    fake.capabilities["passkeyPolicy"] = "Required"
    tokens = TokenManager(base_url, session, Credentials(USERNAME, PASSWORD), clock=fake.clock,
                          check_capabilities=False)
    await tokens.token()
    assert fake.capability_checks == 0


def test_capabilities_parse_tolerates_odd_bodies() -> None:
    assert AuthCapabilities.parse(None) is None
    caps = AuthCapabilities.parse({"PasskeyPolicy": "required", "passkeysAvailable": "yes"})
    assert caps is not None and caps.passkey_required and not caps.passkeys_available
    assert not AuthCapabilities.parse({"passkeyPolicy": "Sometimes"}).passkey_required  # type: ignore[union-attr]


@pytest.mark.parametrize(("body", "words"), [
    ({"passwordChangeRequired": True, "reason": "Expired"}, ("change its password", "Expired")),
    ({"PasskeyRequired": True}, ("passkey",)),
])
async def test_login_markers_explain_themselves(tokens: TokenManager, fake: FakeAmp, body: dict,
                                                words: tuple[str, ...]) -> None:
    fake.login_response = body
    with pytest.raises(LoginNotSupported) as info:
        await tokens.token()
    for word in words:
        assert word in str(info.value)


def test_unknown_marker_falls_back() -> None:
    assert login_marker({"somethingElse": True}) is None
    assert login_marker({"passwordChangeRequired": False}) is None


# ------------------------------------------------------------------ downloads

async def test_file_download_uses_a_fresh_ticket_each_time(base_url: str, fake: FakeAmp) -> None:
    async with AmpClient(base_url, Credentials(USERNAME, PASSWORD), keep_session_renewed=False) as amp:
        assert await amp.downloads.file(SHARD_ID, "server.properties") == b"motd=Hello\n"
        assert await amp.downloads.file(SHARD_ID, "server.properties") == b"motd=Hello\n"
    assert len(fake.download_tickets) == 2
    assert all(used for *_, used in fake.download_tickets.values())


async def test_zip_goes_through_the_ticket(base_url: str, fake: FakeAmp, tmp_path: Path) -> None:
    target = tmp_path / "files.zip"
    async with AmpClient(base_url, Credentials(USERNAME, PASSWORD), keep_session_renewed=False) as amp:
        size = await amp.downloads.zip(SHARD_ID, ["world", "server.properties"], dest=target)
        with pytest.raises(ValueError):
            await amp.downloads.zip(SHARD_ID, [])
    assert target.read_bytes().startswith(b"PK") and size == target.stat().st_size
    assert "zip:world,server.properties" in fake.calls


async def test_snapshot_download(base_url: str, fake: FakeAmp) -> None:
    async with AmpClient(base_url, Credentials(USERNAME, PASSWORD), keep_session_renewed=False) as amp:
        assert await amp.downloads.snapshot(SHARD_ID, SNAP) == fake.snapshot_zip


async def test_spent_ticket_is_refused(base_url: str, fake: FakeAmp) -> None:
    async with AmpClient(base_url, Credentials(USERNAME, PASSWORD), keep_session_renewed=False) as amp:
        ticket = await amp.files_backups.post_shards_files_zip_ticket(SHARD_ID, m.PathsBody(paths=["world"]))
        path = "/api/shards/{shardId}/files/zip"
        await amp.transport.download(path, path_params={"shardId": SHARD_ID}, params={"ticket": ticket.ticket},
                                     bearer=False)
        with pytest.raises(AuthExpired):
            await amp.transport.download(path, path_params={"shardId": SHARD_ID},
                                         params={"ticket": ticket.ticket}, bearer=False)


async def test_download_size_cap_removes_partial_file(base_url: str, fake: FakeAmp, tmp_path: Path) -> None:
    target = tmp_path / "big.bin"
    fake.files["big.bin"] = b"x" * 5000
    async with AmpClient(base_url, Credentials(USERNAME, PASSWORD), keep_session_renewed=False) as amp:
        with pytest.raises(AmpError, match="larger than"):
            await amp.downloads.file(SHARD_ID, "big.bin", dest=target, max_bytes=1000)
    assert not target.exists()


# ------------------------------------------------------------------ partial infra patch, licence

def test_infra_patch_sends_only_what_was_set() -> None:
    patch = m.ShardInfraPatch(memory_limit_mb=4096, start_on_boot=False)
    assert encode_body(patch) == {"memoryLimitMb": 4096, "startOnBoot": False}
    assert encode_body(m.ShardInfraPatch()) == {}


def test_infra_patch_operation_takes_the_patch_model() -> None:
    import inspect

    from portcullis_api.api import ShardsApi

    hint = inspect.signature(ShardsApi.patch_shards_infra).parameters["body"].annotation
    assert "ShardInfraPatch" in str(hint)


async def test_licence_state_and_answers(base_url: str, fake: FakeAmp) -> None:
    fake.awaits_licence = True
    async with AmpClient(base_url, Credentials(USERNAME, PASSWORD), keep_session_renewed=False) as amp:
        state = await amp.shards.get_shards_state(SHARD_ID)
        assert state.awaits_licence is True and not state.is_running
        await amp.shards.post_shards_eula_decline(SHARD_ID)
        await amp.shards.post_shards_eula_accept(SHARD_ID)
    assert fake.licence_answers == ["decline", "accept"]
