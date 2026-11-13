# SPDX-License-Identifier: MIT
"""Token refresh: login, renew ahead of expiry, fall back to login, single-use hub tickets."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import aiohttp
import pytest

from portcullis_api.auth import Credentials, TokenManager, jwt_expiry, parse_instant
from portcullis_api.errors import (
    AuthExpired,
    AuthFailed,
    LoginNotSupported,
    MissingCapability,
    RateLimited,
    parse_retry_after,
)
from portcullis_api.testing import PASSWORD, SHARD_ID, USERNAME, FakeAmp
from portcullis_api.transport import AmpTransport


async def test_logs_in_once_and_reuses_token(tokens: TokenManager, fake: FakeAmp) -> None:
    first = await tokens.token()
    second = await tokens.token()
    assert first == second
    assert tokens.logins == 1 and tokens.renewals == 0
    assert fake.calls == ["login"]


async def test_renews_inside_margin_not_before(tokens: TokenManager, fake: FakeAmp) -> None:
    first = await tokens.token()
    fake.clock.advance(hours=7, minutes=29)  # 31 min left: still outside the 30 min margin
    assert await tokens.token() == first
    fake.clock.advance(minutes=2)  # 29 min left
    second = await tokens.token()
    assert second != first
    assert (tokens.logins, tokens.renewals) == (1, 1)
    assert fake.calls == ["login", "renew"]


async def test_renew_refused_falls_back_to_login(tokens: TokenManager, fake: FakeAmp) -> None:
    await tokens.token()
    fake.revoke_all()
    fake.clock.advance(hours=7, minutes=45)
    await tokens.token()
    assert fake.calls == ["login", "renew", "login"]
    assert tokens.logins == 2


async def test_fully_expired_token_is_replaced(tokens: TokenManager, fake: FakeAmp) -> None:
    await tokens.token()
    fake.clock.advance(hours=9)  # the bot was asleep past expiry: renew gets 401, so log in again
    await tokens.token()
    assert fake.calls == ["login", "renew", "login"]


async def test_concurrent_callers_share_one_refresh(tokens: TokenManager, fake: FakeAmp) -> None:
    await tokens.token()
    fake.clock.advance(hours=7, minutes=45)
    results = await asyncio.gather(*(tokens.token() for _ in range(20)))
    assert len(set(results)) == 1
    assert fake.calls.count("renew") == 1


async def test_wrong_password_raises_auth_failed(base_url: str, session: aiohttp.ClientSession, fake: FakeAmp) -> None:
    tm = TokenManager(base_url, session, Credentials(USERNAME, "wrong-password"), clock=fake.clock)
    with pytest.raises(AuthFailed) as info:
        await tm.token()
    assert "wrong-password" not in str(info.value)
    assert fake.calls == ["login"]


@pytest.mark.parametrize("marker", [{"passwordChangeRequired": True, "reason": "expired"}, {"passkeyRequired": True}])
async def test_login_without_token_is_not_supported(tokens: TokenManager, fake: FakeAmp, marker: dict) -> None:
    fake.login_response = marker
    with pytest.raises(LoginNotSupported) as info:
        await tokens.token()
    assert "bot" in str(info.value) and ("password" in str(info.value) or "passkey" in str(info.value))


async def test_hub_tickets_are_fresh_every_time(tokens: TokenManager, fake: FakeAmp) -> None:
    a = await tokens.hub_ticket()
    b = await tokens.hub_ticket()
    assert a.token != b.token
    assert fake.calls == ["login", "hub-token", "hub-token"]
    assert tokens.hub_tickets_issued == 2
    assert a.token not in repr(a)  # never printed by accident


async def test_hub_ticket_after_revoked_session_logs_in_again(tokens: TokenManager, fake: FakeAmp) -> None:
    await tokens.token()
    fake.revoke_all()
    ticket = await tokens.hub_ticket()
    assert ticket.token
    assert fake.calls == ["login", "hub-token", "renew", "login", "hub-token"]


async def test_run_renewal_renews_before_expiry(base_url: str, session: aiohttp.ClientSession, fake: FakeAmp) -> None:
    # Real clock here: tokens live 1.2 s and renewal starts 1 s before expiry.
    fake.token_lifetime = timedelta(seconds=1.2)
    fake.clock = type(fake.clock)()
    from datetime import UTC, datetime
    fake.clock.now = datetime.now(UTC)

    def ticking() -> datetime:
        fake.clock.now = datetime.now(UTC)
        return fake.clock.now

    tm = TokenManager(base_url, session, Credentials(USERNAME, PASSWORD), clock=ticking,
                      refresh_margin=timedelta(seconds=1))
    task = asyncio.create_task(tm.run_renewal(min_sleep=0.05))
    try:
        for _ in range(100):
            if tm.renewals >= 2:
                break
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
    assert tm.logins == 1
    assert tm.renewals >= 2


# --------------------------------------------------------------------------- transport


async def test_transport_retries_once_after_401(tokens: TokenManager, fake: FakeAmp, base_url: str,
                                               session: aiohttp.ClientSession) -> None:
    t = AmpTransport(base_url, session, tokens)
    assert (await t.request("GET", "/api/Shards"))[0]["id"] == SHARD_ID
    fake.revoke_all()  # e.g. an admin killed the bot's session in the panel
    shards = await t.request("GET", "/api/Shards")
    assert shards[0]["name"] == "Survival"
    assert fake.calls == ["login", "shards", "shards", "renew", "login", "shards"]


async def test_transport_gives_up_after_second_401(tokens: TokenManager, fake: FakeAmp, base_url: str,
                                                  session: aiohttp.ClientSession) -> None:
    t = AmpTransport(base_url, session, tokens)
    with pytest.raises(AuthExpired):
        await t.request("GET", "/api/always-401")
    # one refresh attempt, not a loop
    assert fake.calls == ["login", "always-401", "renew", "always-401"]


async def test_403_becomes_missing_capability(tokens: TokenManager, base_url: str,
                                              session: aiohttp.ClientSession) -> None:
    t = AmpTransport(base_url, session, tokens)
    with pytest.raises(MissingCapability) as info:
        await t.request("GET", "/api/Shards/{id}/schedules", path_params={"id": SHARD_ID})
    assert info.value.code == "auth.error.forbidden"


def test_parse_instant_handles_dotnet_precision() -> None:
    parsed = parse_instant("2026-11-07T20:00:00.1234567Z")
    assert parsed is not None and parsed.tzinfo is not None and parsed.microsecond == 123456


def test_jwt_expiry_fallback() -> None:
    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps({"exp": 1794000000}).encode()).decode().rstrip("=")
    exp = jwt_expiry(f"eyJhbGciOiJIUzI1NiJ9.{payload}.sig")
    assert exp is not None and int(exp.timestamp()) == 1794000000


async def test_refused_login_is_not_retried_until_the_cooldown_passes(
    base_url: str, session: aiohttp.ClientSession, fake: FakeAmp
) -> None:
    tm = TokenManager(base_url, session, Credentials(USERNAME, "wrong-password"), clock=fake.clock)
    for _ in range(5):  # five callers, as five hub reconnects would be
        with pytest.raises(AuthFailed):
            await tm.token()
    assert fake.calls == ["login"]  # AMP was asked once, so its failed-login limit is not tripped
    fake.clock.advance(seconds=61)
    with pytest.raises(AuthFailed):
        await tm.token()
    assert fake.calls == ["login", "login"]


def test_parse_retry_after_reads_seconds_and_dates() -> None:
    from datetime import UTC, datetime
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after("Wed, 11 Nov 2026 12:02:00 GMT", now=now) == 120.0
    assert parse_retry_after("Wed, 11 Nov 2026 11:00:00 GMT", now=now) == 0.0  # already past
    assert parse_retry_after("999999999") == 86400.0  # capped at a day
    assert parse_retry_after(None) is None and parse_retry_after("soon") is None and parse_retry_after("nan") is None


async def test_short_rate_limit_on_a_read_is_waited_out(tokens: TokenManager, fake: FakeAmp, base_url: str,
                                                        session: aiohttp.ClientSession) -> None:
    t = AmpTransport(base_url, session, tokens)
    await tokens.token()
    fake.rate_limits = ["0"]
    assert (await t.request("GET", "/api/Shards"))[0]["id"] == SHARD_ID
    assert fake.calls == ["login", "shards", "shards"]


async def test_long_rate_limit_is_raised_with_the_wait(tokens: TokenManager, fake: FakeAmp, base_url: str,
                                                       session: aiohttp.ClientSession) -> None:
    t = AmpTransport(base_url, session, tokens)
    await tokens.token()
    fake.rate_limits = ["120"]
    with pytest.raises(RateLimited) as info:
        await t.request("GET", "/api/Shards")
    assert info.value.retry_after == 120.0 and "try again in 120 s" in str(info.value)
    assert fake.calls == ["login", "shards"]  # not retried: the wait is longer than the transport will sit out


async def test_rate_limited_login_holds_every_caller_off(base_url: str, session: aiohttp.ClientSession,
                                                         fake: FakeAmp) -> None:
    tm = TokenManager(base_url, session, Credentials(USERNAME, PASSWORD), clock=fake.clock)
    fake.rate_limits = ["300"]
    for _ in range(4):
        with pytest.raises(RateLimited) as info:
            await tm.token()
    assert fake.calls == ["login"]  # asking again during the wait would only extend it
    assert info.value.retry_after == 300.0
    fake.clock.advance(seconds=299)
    with pytest.raises(RateLimited):
        await tm.token()
    fake.clock.advance(seconds=2)
    assert await tm.token()
    assert fake.calls == ["login", "login"]


async def test_unrecognised_tokenless_login_lists_the_fields(tokens: TokenManager, fake: FakeAmp) -> None:
    fake.login_response = {"somethingNew": True}
    with pytest.raises(LoginNotSupported) as info:
        await tokens.token()
    assert "second factor" in str(info.value) and "somethingNew" in str(info.value)
