# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""Servers waiting for their game licence (EULA), and sleeping servers, from the live spec of 2026-11-13."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from portcullis.amp.events import state_from
from portcullis.amp.port import AmpServerPort
from portcullis.bot.alerts import Alerts
from portcullis.bot.base import ALERT_CHANNEL
from portcullis.core.types import ServerId, ServerState, StateChanged
from portcullis_api.auth import Credentials
from portcullis_api.client import AmpClient
from portcullis_api.testing import PASSWORD, SHARD_ID, USERNAME, FakeAmp
from tests.fakes.discord import FakeInteraction
from tests.test_bot import SURVIVAL, Harness, admin, db, h, member, operator  # noqa: F401
from tests.test_ideas import add_channels

ALERTS = 900


def test_state_from_reads_the_new_flags() -> None:
    assert state_from({"awaitsLicence": True, "isStopped": False})[0] is ServerState.AWAITING_LICENCE
    assert state_from({"awaitsLicence": True}, suspended=True)[0] is ServerState.SUSPENDED
    assert state_from({"awaitsLicence": False, "isStopped": True})[0] is ServerState.STOPPED
    assert state_from({"isRunning": False, "acceptsTraffic": True})[0] is ServerState.SLEEPING
    assert state_from({"isRunning": True, "acceptsTraffic": True})[0] is ServerState.RUNNING
    assert state_from({"isStopped": True, "acceptsTraffic": False})[0] is ServerState.STOPPED
    assert ServerState.AWAITING_LICENCE.label == "Needs licence"


async def test_real_port_maps_the_licence_state_and_answers(base_url: str, fake: FakeAmp) -> None:
    fake.awaits_licence = True
    client = AmpClient(base_url, Credentials(USERNAME, PASSWORD), keep_session_renewed=False,
                       refresh_margin=timedelta(minutes=30))
    port = AmpServerPort(client)
    try:
        assert (await port.get_status(ServerId(SHARD_ID))).state is ServerState.AWAITING_LICENCE
        await port.answer_licence(ServerId(SHARD_ID), accept=False)
        await port.answer_licence(ServerId(SHARD_ID), accept=True)
    finally:
        await port.close()
        await client.close()
    assert fake.licence_answers == ["decline", "accept"]


async def test_status_offers_licence_buttons_by_role(h: Harness) -> None:
    h.port.servers[ServerId(SURVIVAL)].state = ServerState.AWAITING_LICENCE

    i = FakeInteraction(member())
    await h.m.server.server_status(i, "Survival")
    assert {f.name: f.value for f in i.embed.fields}["State"] == "📜 Needs licence"
    assert "view" not in i.replies[-1]

    i = FakeInteraction(operator())  # may power it: decline only
    await h.m.server.server_status(i, "Survival")
    assert [b.label for b in i.replies[-1]["view"].children] == ["Decline"]

    i = FakeInteraction(admin())
    await h.m.server.server_status(i, "Survival")
    assert [b.label for b in i.replies[-1]["view"].children] == ["Accept the licence…", "Decline"]


async def test_accept_needs_admin_and_a_confirmation(h: Harness) -> None:
    sid = ServerId(SURVIVAL)
    h.port.servers[sid].state = ServerState.AWAITING_LICENCE
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None

    i = FakeInteraction(operator())
    await h.m.server.answer_licence(i, row, accept=True)
    assert "Manage Server" in i.text and h.port.licence_answers == []

    i = FakeInteraction(admin())
    await h.m.server.answer_licence(i, row, accept=True)
    assert "Read the game's licence first" in i.text and h.port.licence_answers == []
    confirm = FakeInteraction(admin())
    await i.replies[-1]["view"].children[0].callback(confirm)
    assert h.port.licence_answers == [(sid, True)]
    assert "accepted the game licence" in confirm.text
    assert h.port.servers[sid].state is ServerState.STOPPED
    actions = [(e.action, e.result) for e in await h.ctx.audit.query()]
    assert ("server.licence.accept", "denied") in actions and ("server.licence.accept", "ok") in actions


async def test_decline_needs_power_and_no_confirmation(h: Harness) -> None:
    sid = ServerId(SURVIVAL)
    h.port.servers[sid].state = ServerState.AWAITING_LICENCE
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None
    i = FakeInteraction(member())
    await h.m.server.answer_licence(i, row, accept=False)
    assert "operator role" in i.text and h.port.licence_answers == []
    i = FakeInteraction(operator())
    await h.m.server.answer_licence(i, row, accept=False)
    assert h.port.licence_answers == [(sid, False)] and "declined" in i.text


async def test_licence_wait_is_alerted(h: Harness) -> None:
    channels = add_channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    alerts = Alerts(h.ctx)
    now = datetime.now(UTC)
    await alerts(StateChanged(server=ServerId(SURVIVAL), at=now, state=ServerState.STOPPED))
    await alerts(StateChanged(server=ServerId(SURVIVAL), at=now, state=ServerState.AWAITING_LICENCE))
    assert "waiting for its game licence" in channels[ALERTS].sent[-1]["content"]


async def test_snapshot_conflict_and_outage_read_as_try_later(base_url: str, fake: FakeAmp) -> None:
    import pytest

    from portcullis.core.types import TryLater

    client = AmpClient(base_url, Credentials(USERNAME, PASSWORD), keep_session_renewed=False)
    client.transport._retry_backoff = 0  # the 503 is retried only for reads; this is a delete
    port = AmpServerPort(client)
    snap = "22222222-2222-2222-2222-222222222222"
    try:
        await port.delete_backup(ServerId(SHARD_ID), snap)
        fake.snapshot_error = (409, None)
        with pytest.raises(TryLater, match="busy with another backup"):
            await port.delete_backup(ServerId(SHARD_ID), snap)
        fake.snapshot_error = (503, "30")
        with pytest.raises(TryLater, match="storage isn't available right now. AMP asked to wait 30 s"):
            await port.delete_backup(ServerId(SHARD_ID), snap)
    finally:
        await port.close()
        await client.close()
