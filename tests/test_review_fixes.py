# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""Regression tests for the full review: Discord's 3-second rule, the restart race, hidden servers,
imported settings, channels that are gone, and the preview-build expiry warning."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import discord
import pytest

from portcullis.amp.port import AmpServerPort
from portcullis.bot.alerts import BACKUP_CHECKED, BACKUP_DAYS, PREVIEW_CHECKED, preview_warning
from portcullis.bot.base import ALERT_CHANNEL, ALERT_ROLE, ROLE_GRANTS, ChannelUnavailable
from portcullis.bot.client import DiscordSink
from portcullis.bot.server import QUICK_KEY, SAY_KEY, quick_commands
from portcullis.core.relay import Relay, RelayTarget
from portcullis.core.types import ServerId
from portcullis_api.auth import Credentials
from portcullis_api.client import AmpClient
from portcullis_api.testing import PASSWORD, SHARD_ID, USERNAME, FakeAmp
from tests.fakes.amp import FakeAmpPort
from tests.fakes.discord import FakeChannel, FakeInteraction, FakeMember, FakeRole
from tests.test_bot import CREATIVE, SECRET, SURVIVAL, Harness, admin, db, h, member, operator  # noqa: F401

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
ALERTS = 55  # a channel the harness's client knows
POWER_ROLE = 77


def not_found() -> discord.NotFound:
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Channel")  # type: ignore[arg-type]


# ------------------------------------------------------------------------------ channels that are gone


class FlakySink:
    def __init__(self) -> None:
        self.attempts = 0
        self.failing = True
        self.sent: list[str] = []

    async def send(self, channel_id: int, text: str) -> None:
        self.attempts += 1
        if self.failing:
            raise RuntimeError("Unknown Channel")
        self.sent.append(text)


async def test_relay_leaves_a_failing_channel_alone_then_recovers() -> None:
    port, sink, now = FakeAmpPort(), FlakySink(), [1000.0]
    sid = port.add_server("3" * 32, "Survival")
    relay = Relay(port, sink, flush_interval=3600, send_retry_min=30, send_retry_max=100, clock=lambda: now[0])
    await relay.apply([RelayTarget(server=sid, name="Survival", game=None, console_channel=5,
                                   event_channel=None, chat_channel=None)])
    await asyncio.sleep(0.01)

    async def line(text: str) -> None:
        port.say(sid, text)
        await asyncio.sleep(0.01)
        await relay.flush()

    await line("one")
    assert sink.attempts == 1
    await line("two")  # inside the 30 s pause: not even tried
    assert sink.attempts == 1
    now[0] += 31
    await line("three")  # tried again, fails again, and the pause doubles
    assert sink.attempts == 2
    now[0] += 31
    await line("four")
    assert sink.attempts == 2
    now[0] += 30
    sink.failing = False
    await line("five")
    first = "\n".join(sink.sent)
    assert "some lines are missing" in first and "five" in first and "four" not in first
    count = len(sink.sent)
    await line("six")  # back to normal, and the note is not repeated
    later = "\n".join(sink.sent[count:])
    assert "missing" not in later and "six" in later
    await relay.close()


async def test_a_missing_channel_is_not_fetched_again_for_a_while(h: Harness) -> None:
    calls: list[int] = []

    async def fetch(channel_id: int) -> FakeChannel:
        calls.append(channel_id)
        raise not_found()

    h.client.fetch_channel = fetch  # type: ignore[method-assign]
    with pytest.raises(discord.NotFound):
        await h.ctx.channel(999)
    with pytest.raises(ChannelUnavailable):
        await h.ctx.channel(999)
    assert not await h.ctx.post_to(999, "hello")  # alerts and the audit feed go through here
    with pytest.raises(ChannelUnavailable):
        await DiscordSink(h.client, h.ctx.channel).send(999, "hello")  # type: ignore[arg-type]
    assert calls == [999]
    h.ctx.missing_channels[999] = 0.0  # the pause is over
    with pytest.raises(discord.NotFound):
        await h.ctx.channel(999)
    assert calls == [999, 999]
    assert (await h.ctx.channel(55)).id == 55  # known channels are untouched


# ------------------------------------------------------------------------------ Discord's 3-second rule


async def test_commands_that_wait_on_amp_answer_discord_first(h: Harness) -> None:
    await h.ctx.settings.set("whitelist_add", "whitelist add {name}", scope=SURVIVAL)
    await h.ctx.settings.set("kick", "kick {name} {reason}", scope=SURVIVAL)
    order: list[str] = []
    real_send = h.port.send_console
    current: list[FakeInteraction] = []

    async def send_console(server: ServerId, text: str) -> None:
        order.append("deferred" if current[-1].deferred is not None else "NOT deferred")
        await real_send(server, text)

    h.port.send_console = send_console  # type: ignore[method-assign]
    current.append(FakeInteraction(operator()))
    await h.m.community.whitelist_change(current[-1], "Survival", "Dana", add=True)
    assert "Added" in current[-1].text
    current.append(FakeInteraction(operator()))
    await h.m.community.moderate(current[-1], "Survival", "Dana", "kick", "being rude")
    assert order == ["deferred", "deferred"]
    assert h.port.sent[-1] == (SURVIVAL, "kick Dana being rude")


async def test_whitelist_decision_button_is_acknowledged_before_amp(h: Harness) -> None:
    await h.ctx.settings.set("whitelist_add", "whitelist add {name}", scope=SURVIVAL)
    req = await h.ctx.whitelist.create(SURVIVAL, 20, "Bob")
    press = FakeInteraction(operator())
    seen: list[bool] = []
    real_send = h.port.send_console

    async def send_console(server: ServerId, text: str) -> None:
        seen.append(press.response.is_done())
        await real_send(server, text)

    h.port.send_console = send_console  # type: ignore[method-assign]
    await h.m.community.decide(press, req.id, "approve")
    assert seen == [True]
    assert press.edits[-1]["view"] is None


async def test_stop_does_not_wait_long_for_the_player_count(h: Harness) -> None:
    async def slow(server: ServerId) -> None:
        await asyncio.sleep(30)

    h.port.get_status = slow  # type: ignore[method-assign,assignment]
    i = FakeInteraction(operator())
    started = asyncio.get_running_loop().time()
    await h.m.server.server_power(i, "Survival", "stop")
    assert asyncio.get_running_loop().time() - started < 2.9
    assert "someone may be playing" in i.text


# ------------------------------------------------------------------------------ warned restart


async def test_two_warned_restarts_at_once_start_one_countdown(h: Harness) -> None:
    await h.ctx.settings.set(SAY_KEY, "say {message}", scope=SURVIVAL)
    h.port.join(ServerId(SURVIVAL), "Steve")
    release = asyncio.Event()

    async def wait(seconds: float) -> None:
        await release.wait()

    h.m.server.sleep = wait
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None
    first, second = FakeInteraction(operator()), FakeInteraction(operator())
    await asyncio.gather(h.m.server.warned_restart(first, row), h.m.server.warned_restart(second, row))
    texts = sorted((first.text, second.text))
    assert "already counting down" in texts[0] and "Restarting" in texts[1]
    await asyncio.sleep(0.01)
    assert [line for _, line in h.port.sent].count("say Server restart in 5 minutes.") == 1
    assert h.m.server.restart_pending == set()
    release.set()
    await asyncio.gather(*h.m.server.restarts.values())
    assert h.m.server.restarts == {}


# ------------------------------------------------------------------------------ hidden servers


async def test_power_all_leaves_out_hidden_servers_the_member_cannot_see(h: Harness) -> None:
    await h.ctx.settings.set(ROLE_GRANTS, {"power": [POWER_ROLE]})
    i = FakeInteraction(FakeMember(21, roles=[FakeRole(POWER_ROLE)]))
    await h.m.server.power_all(i, "restart")
    assert "Creative" in i.text and "Secret" not in i.text
    i = FakeInteraction(operator())
    await h.m.server.power_all(i, "restart")
    assert "Secret" in i.text


async def test_player_lookup_does_not_name_hidden_servers(h: Harness) -> None:
    await h.ctx.players.joined(SECRET, "Steve")
    await h.ctx.players.left(SECRET, "Steve")
    await h.ctx.players.joined(CREATIVE, "Steve")
    i = FakeInteraction(member())
    await h.m.community.player_lookup(i, "Steve")
    shown = " ".join(f.value for f in i.embed.fields)
    assert "Creative" in shown and "Secret" not in shown and SECRET not in shown
    i = FakeInteraction(operator())
    await h.m.community.player_lookup(i, "Steve")
    assert "Secret" in " ".join(f.value for f in i.embed.fields)


# ------------------------------------------------------------------------------ settings from a file


async def test_import_refuses_console_templates_that_are_not_one_line(h: Harness) -> None:
    data = {"portcullis_export": 1, "servers": {SURVIVAL: {"prefix": "S", "hidden": "yes", "console_channel": "x"}},
            "settings": {SURVIVAL: {"whitelist_add": "whitelist add {name}\nop {name}", "kick": "kick {name}",
                                    "ban": 7, QUICK_KEY: [{"label": "Save", "command": "save\nstop"},
                                                          {"label": "Day", "command": "time set day"}]}}}
    _, _, problems = await h.m.admin.import_data(data, by=40)
    assert await h.ctx.settings.get("whitelist_add", scope=SURVIVAL) is None
    assert await h.ctx.settings.get("ban", scope=SURVIVAL) is None
    assert await h.ctx.settings.get("kick", scope=SURVIVAL) == "kick {name}"
    assert sum("one-line command" in p for p in problems) == 2
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None and row.prefix == "S" and not row.hidden and row.console_channel is None
    assert sum("not a valid value" in p for p in problems) == 2
    # A quick command with a line break is stored as sent, but never offered or run.
    assert quick_commands(await h.ctx.settings.get(QUICK_KEY, scope=SURVIVAL)) == [
        {"label": "Day", "command": "time set day"}]


async def test_setup_refuses_templates_with_control_characters(h: Harness) -> None:
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None
    i = FakeInteraction(admin())
    assert "one line" in (await h.m.setup.save_commands(i, row, {"whitelist_add": "whitelist add {name}\rop x"}) or "")
    assert "one line" in (await h.m.setup.save_mod_commands(i, row, {"kick": "kick {name}\x00"}) or "")
    assert await h.m.setup.save_mod_commands(i, row, {"kick": "kick {name} {reason}"}) is None


async def test_server_panel_survives_long_quick_commands(h: Harness) -> None:
    await h.ctx.settings.set(QUICK_KEY, [{"label": "L" * 40, "command": "*" * 200} for _ in range(5)],
                             scope=SURVIVAL)
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None
    embed = await h.m.setup.server_embed(row)
    assert all(len(f.value) <= 1024 for f in embed.fields)


# ------------------------------------------------------------------------------ the real port


@pytest.fixture
async def port(base_url: str, fake: FakeAmp) -> AsyncIterator[AmpServerPort]:
    client = AmpClient(base_url, Credentials(USERNAME, PASSWORD), keep_session_renewed=False)
    client.auth._clock = fake.clock
    p = AmpServerPort(client)
    yield p
    await p.close()
    await client.close()


async def test_console_lines_with_control_characters_never_reach_amp(port: AmpServerPort, fake: FakeAmp) -> None:
    for bad in ("say hi\nstop", "say hi\rstop", "say\x00", ""):
        with pytest.raises(ValueError, match="one line"):
            await port.send_console(ServerId(SHARD_ID), bad)
    assert fake.calls == []  # refused before any request, even the login


async def test_preview_expiry_is_read_from_health_status(port: AmpServerPort, fake: FakeAmp) -> None:
    assert await port.preview_expires_at() is None  # a release build has no preview block
    fake.preview = {"expiresAt": "2026-12-07T00:00:00Z", "daysRemaining": None, "reason": None}
    assert await port.preview_expires_at() == datetime(2026, 12, 7, tzinfo=UTC)


# ------------------------------------------------------------------------------ alerts


def test_preview_warning_wording() -> None:
    assert preview_warning(None, NOW) is None
    assert preview_warning(NOW + timedelta(days=30), NOW) is None
    assert "in 12 days" in (preview_warning(NOW + timedelta(days=12, hours=1), NOW) or "")
    assert "in 1 day**" in (preview_warning(NOW + timedelta(days=1, hours=1), NOW) or "")
    assert "less than a day" in (preview_warning(NOW + timedelta(hours=5), NOW) or "")
    assert "expired" in (preview_warning(NOW - timedelta(hours=1), NOW) or "")


async def test_preview_expiry_is_announced_once_a_day_and_pings_near_the_end(h: Harness) -> None:
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    await h.ctx.settings.set(ALERT_ROLE, 600)
    channel = h.client.channels[ALERTS]
    h.port.preview_expires = NOW + timedelta(days=12, hours=1)
    await h.m.alerts.check_preview(NOW)
    await h.m.alerts.check_preview(NOW + timedelta(hours=3))
    assert len(channel.sent) == 1 and "stops working in 12 days" in channel.sent[0]["content"]
    assert "<@&600>" not in channel.sent[0]["content"]
    await h.m.alerts.check_preview(NOW + timedelta(days=10))
    assert len(channel.sent) == 2 and channel.sent[1]["content"].startswith("<@&600>")
    h.port.preview_expires = None  # AMP was updated to a release build
    await h.m.alerts.check_preview(NOW + timedelta(days=11, hours=1))
    assert len(channel.sent) == 2


async def test_daily_checks_survive_an_unreadable_last_run_time(h: Harness) -> None:
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    await h.ctx.settings.set(BACKUP_DAYS, 3)
    await h.ctx.settings.set(BACKUP_CHECKED, "yesterday")  # e.g. from a hand-edited import
    await h.ctx.settings.set(PREVIEW_CHECKED, "2026-11-24T12:00:00")  # no time zone
    await h.m.alerts.tick(NOW)
    assert str(await h.ctx.settings.get(BACKUP_CHECKED)).startswith("2026-11-25")
    assert any("no backups" in (m.get("content") or "") for m in h.client.channels[ALERTS].sent)


# ------------------------------------------------------------------------------ events


async def test_cancelling_an_event_that_is_gone_says_so(h: Harness) -> None:
    i = FakeInteraction(operator())
    await h.m.events.cancel(i, 4242)
    assert "no longer exists" in i.text
