# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""Brand emojis and their fallback, live card pacing and back-off, plain errors, /health, /setup start,
/server diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from portcullis.bot.base import STATE_COLOURS, state_text
from portcullis.bot.board import BOARD_CHANNEL, StatusBoard
from portcullis.bot.cards import PANEL_URL, SUPPORT_URL, bar, next_restart, status_embed
from portcullis.bot.emojis import EMOJI, SET, Emojis
from portcullis.bot.errors import plain_error
from portcullis.bot.livecard import CARD_KEY, LiveCards
from portcullis.bot.refresh import BACKOFF_MAX, DEFAULT_EVERY, EVERY_KEY, MAX_EVERY, MIN_EVERY, Pacer, clamp_every
from portcullis.bot.server import CONNECT_KEY
from portcullis.core.relay import state_line
from portcullis.core.types import DiskUsage, Schedule, ServerId, ServerState, ServerStatus, TryLater
from portcullis.db.repos import ServerRow
from tests.fakes.amp import FakeAmpError
from tests.fakes.discord import FakeChannel, FakeInteraction
from tests.test_bot import CREATIVE, SURVIVAL, Harness, admin, db, h, member, operator  # noqa: F401

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def http_error(status: int, retry_after: float | None = None) -> discord.HTTPException:
    kind = discord.NotFound if status == 404 else discord.HTTPException
    exc = kind(SimpleNamespace(status=status, reason="test"), "test")
    if retry_after is not None:
        exc.retry_after = retry_after  # type: ignore[attr-defined]
    return exc


# ------------------------------------------------------------------------------ emojis


def test_emoji_fallback_when_nothing_is_uploaded() -> None:
    emojis = Emojis()
    assert emojis["online"] == "🟢" and emojis["players"] == "👥"
    assert emojis["no such key"] == "❔"
    assert emojis.state(ServerState.RUNNING) == "🟢"
    assert emojis.state(ServerState.BUSY) == "🟡"
    assert emojis.state(ServerState.BUSY, going_down=True) == "🟠"
    assert emojis.state(ServerState.SLEEPING) == "💤"  # no brand emoji: the relay's icon
    assert len(emojis.missing()) == len(SET)
    # the shared set stays plain in tests, so every other test's "🟢 Online" holds
    assert state_text(ServerState.RUNNING) == "🟢 Online" and not EMOJI.found


def test_emoji_load_uses_only_valid_matches() -> None:
    emojis = Emojis()
    missing = emojis.load([
        SimpleNamespace(name="portcullis_online", id=111, animated=False),
        SimpleNamespace(name="portcullis_offline", id=222, animated=True),
        SimpleNamespace(name="portcullis_backup", id="333"),  # not an int id: ignored
        SimpleNamespace(name="portcullis_console", id=True),  # bool is not an id
        SimpleNamespace(name="Portcullis_Players", id=444),  # names must match exactly
        SimpleNamespace(name=None, id=555),
        SimpleNamespace(id=666),
    ])
    assert emojis["online"] == "<:portcullis_online:111>"
    assert emojis["offline"] == "<a:portcullis_offline:222>"
    assert emojis["backup"] == "💾" and emojis["console"] == "⌨️" and emojis["players"] == "👥"
    assert emojis.state(ServerState.STOPPED) == "<a:portcullis_offline:222>"
    assert "portcullis_backup" in missing and "portcullis_online" not in missing
    assert emojis.checked and emojis.error is None


async def test_emoji_fetch_failure_falls_back_and_never_raises() -> None:
    emojis = Emojis()
    emojis.load([SimpleNamespace(name="portcullis_online", id=1)])

    class Broken:
        async def fetch_application_emojis(self) -> list[Any]:
            raise http_error(503)

    missing = await emojis.fetch(Broken())
    assert emojis["online"] == "🟢"  # an earlier find is dropped too, so nothing points at a stale id
    assert len(missing) == len(SET) and emojis.error

    class Fine:
        async def fetch_application_emojis(self) -> list[Any]:
            return [SimpleNamespace(name=name, id=n + 1) for n, (name, _f) in enumerate(SET.values())]

    assert await emojis.fetch(Fine()) == [] and emojis.error is None
    assert emojis["players"].startswith("<:portcullis_players:")


def test_relay_state_line_takes_the_bot_icons() -> None:
    assert state_line("Survival", ServerState.RUNNING).startswith("🟢 **Survival**")
    emojis = Emojis()
    emojis.load([SimpleNamespace(name="portcullis_online", id=9)])
    line = state_line("Survival", ServerState.RUNNING, emojis.state)
    assert line == "<:portcullis_online:9> **Survival** is now Online"


# ------------------------------------------------------------------------------ pacing


def test_interval_is_held_to_two_to_five_minutes() -> None:
    assert clamp_every(None) == DEFAULT_EVERY and clamp_every("60") == DEFAULT_EVERY
    assert clamp_every(True) == DEFAULT_EVERY and clamp_every(float("nan")) == DEFAULT_EVERY
    assert clamp_every(10) == MIN_EVERY and clamp_every(60 * 60) == MAX_EVERY and clamp_every(240) == 240


def test_pacer_waits_the_interval_and_longer_when_idle() -> None:
    clock = Clock()
    pacer = Pacer(180, clock=clock)
    assert pacer.delay() == 180 and pacer.delay(idle=True) == MAX_EVERY
    pacer.edited()
    clock.now += 100
    assert pacer.delay() == 80 and pacer.delay(idle=True) == 200
    clock.now += 500
    assert pacer.delay() == 0


def test_pacer_backs_off_on_429_and_recovers() -> None:
    clock = Clock()
    pacer = Pacer(180, clock=clock)
    assert pacer.rate_limited() == 180 and pacer.backing_off
    assert pacer.rate_limited() == 360
    assert pacer.rate_limited() == 720
    assert pacer.rate_limited() == BACKOFF_MAX  # capped at 15 minutes
    assert pacer.delay() == BACKOFF_MAX and not pacer.early_ok()
    assert pacer.last_problem == "Discord rate limit"
    pacer.edited()
    assert pacer.failures == 0 and not pacer.backing_off and pacer.last_problem is None


def test_pacer_honours_discords_retry_after() -> None:
    clock = Clock()
    pacer = Pacer(120, clock=clock)
    assert pacer.rate_limited(retry_after=400) == 400  # longer than the doubling: Discord wins
    assert pacer.rate_limited(retry_after=5) == 240  # shorter: the doubling wins
    assert pacer.rate_limited(retry_after=99999) == BACKOFF_MAX
    assert pacer.failed("boom") == BACKOFF_MAX and pacer.last_problem == "boom"


def test_state_changes_never_pull_edits_closer_than_a_minute() -> None:
    clock = Clock()
    pacer = Pacer(180, clock=clock)
    assert pacer.early_ok()
    pacer.edited()
    clock.now += 30
    assert not pacer.early_ok()
    clock.now += 31
    assert pacer.early_ok()


# ------------------------------------------------------------------------------ live cards


@dataclass
class EditChannel:
    """A channel whose stored card can be missing or rate-limited."""

    id: int
    sent: list[dict[str, Any]] = field(default_factory=list)
    edits: list[dict[str, Any]] = field(default_factory=list)
    edit_error: BaseException | None = None

    def permissions_for(self, member: Any) -> SimpleNamespace:
        return SimpleNamespace(view_channel=True, send_messages=True)

    async def send(self, content: str | None = None, **kwargs: Any) -> SimpleNamespace:
        self.sent.append(kwargs)
        return SimpleNamespace(id=700 + len(self.sent))

    def get_partial_message(self, message_id: int) -> SimpleNamespace:
        async def edit(**kwargs: Any) -> None:
            if self.edit_error is not None:
                raise self.edit_error
            self.edits.append({"id": message_id, **kwargs})
        return SimpleNamespace(edit=edit)


def add_channel(h: Harness, channel: EditChannel) -> None:
    h.client.channels[channel.id] = channel  # type: ignore[assignment]
    h.client.cached.add(channel.id)


async def test_live_card_posts_then_edits_in_place(h: Harness) -> None:
    channel = EditChannel(77)
    add_channel(h, channel)
    cards = LiveCards(h.ctx, pacer=Pacer(clock=Clock()))
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None
    await h.ctx.settings.set(CONNECT_KEY, "play.example.com:27015", scope=SURVIVAL)
    await h.ctx.settings.set(PANEL_URL, "https://panel.example.com")
    h.port.servers[ServerId(SURVIVAL)].schedules = [
        Schedule("1", "Restart nightly", True, next_fire=datetime.now(UTC) + timedelta(hours=3)),
        Schedule("2", "Backup", True, next_fire=datetime.now(UTC) + timedelta(hours=1))]

    assert await cards.place(row, 77, by=40)
    [posted] = channel.sent
    embed = posted["embed"]
    fields = {f.name: f.value for f in embed.fields}
    assert embed.footer.text == "Updates every 3 minutes" and embed.timestamp is not None
    assert fields["State"] == "🟢 Online" and fields["Connect"] == "```\nplay.example.com:27015\n```"
    assert "Next restart" in fields and embed.colour.value == STATE_COLOURS[ServerState.RUNNING]
    assert [b.label for b in posted["view"].children] == ["Panel"]
    assert await h.ctx.settings.get(CARD_KEY, scope=SURVIVAL) == {"channel": 77, "message": 701}

    await cards.refresh_all()
    assert len(channel.sent) == 1 and channel.edits[-1]["id"] == 701  # edited, not reposted
    assert cards.pacer.last_edit is not None and cards.pacer.failures == 0


async def test_live_card_reposts_when_deleted(h: Harness) -> None:
    add_channel(h, EditChannel(77, edit_error=http_error(404)))
    await h.ctx.settings.set(CARD_KEY, {"channel": 77, "message": 5}, scope=SURVIVAL)
    cards = LiveCards(h.ctx, pacer=Pacer(clock=Clock()))
    await cards.refresh_all()
    assert len(h.client.channels[77].sent) == 1
    assert await h.ctx.settings.get(CARD_KEY, scope=SURVIVAL) == {"channel": 77, "message": 701}


async def test_live_cards_back_off_on_429_and_stop_the_pass(h: Harness) -> None:
    channel = EditChannel(77, edit_error=http_error(429, retry_after=30))
    add_channel(h, channel)
    for sid in (SURVIVAL, CREATIVE):
        await h.ctx.settings.set(CARD_KEY, {"channel": 77, "message": 5}, scope=sid)
    clock = Clock()
    cards = LiveCards(h.ctx, pacer=Pacer(clock=clock))
    await h.ctx.settings.set(EVERY_KEY, 120)

    statuses = 0
    real = h.port.get_status

    async def counting(server: ServerId) -> ServerStatus:
        nonlocal statuses
        statuses += 1
        return await real(server)

    h.port.get_status = counting  # type: ignore[method-assign]
    await cards.refresh_all()
    assert statuses == 1  # the second card wasn't tried after the 429
    assert cards.pacer.every == 120 and cards.pacer.backing_off and cards.pacer.delay() == 120
    await cards.refresh_all()
    assert cards.pacer.delay() == 240  # doubled
    channel.edit_error = None
    clock.now += 1000
    await cards.refresh_all()
    assert not cards.pacer.backing_off and len(channel.edits) == 2


async def test_live_cards_survive_amp_and_channel_failures(h: Harness) -> None:
    await h.ctx.settings.set(CARD_KEY, {"channel": 999, "message": 5}, scope=SURVIVAL)  # unknown channel
    cards = LiveCards(h.ctx, pacer=Pacer(clock=Clock()))
    await cards.refresh_all()
    assert cards.pacer.backing_off and cards.pacer.last_problem == "no card could be updated"

    async def down(server: ServerId) -> ServerStatus:
        raise FakeAmpError("down")

    h.port.get_status = down  # type: ignore[method-assign]
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None
    embed, _view = await cards.build(row)
    fields = {f.name: f.value for f in embed.fields}
    assert fields["State"] == "❔ Unknown" and "didn't answer" in fields["Message"]


async def test_live_card_nudge_only_on_a_state_change(h: Harness) -> None:
    cards = LiveCards(h.ctx, pacer=Pacer(clock=Clock()))
    cards._states = {SURVIVAL: ServerState.RUNNING}
    h.ctx.live.states = {ServerId(SURVIVAL): ServerState.RUNNING}
    cards.nudge()
    assert not cards._wake.is_set()
    h.ctx.live.states = {ServerId(SURVIVAL): ServerState.STOPPED}
    cards.nudge()
    assert cards._wake.is_set()
    assert cards.idle()  # nobody online
    h.ctx.live.players = {ServerId(SURVIVAL): {"Bob"}}
    assert not cards.idle()


async def test_board_holds_back_after_a_429(h: Harness) -> None:
    await h.ctx.settings.set(BOARD_CHANNEL, 55)
    await h.ctx.settings.set("board_message", 3)
    board = StatusBoard(h.ctx, every=120)

    async def limited(message_id: int) -> Any:
        raise http_error(429, retry_after=600)

    channel: FakeChannel = h.client.channels[55]
    channel.fetch_message = limited  # type: ignore[method-assign]
    await board.update()  # logged, not raised
    assert board._pending is None
    await board.changed()
    assert board._pending is not None  # waits instead of editing at once
    await board.close()


# ------------------------------------------------------------------------------ cards and errors


def test_status_embed_licence_and_restart_pick() -> None:
    row = ServerRow(shard_id=ServerId(SURVIVAL), amp_name="Survival", display_name=None, template="minecraft",
                    hidden=False, console_channel=None, chat_channel=None, event_channel=None, discord_role=None,
                    prefix=None, gone_at=None)
    embed = status_embed(row, ServerStatus(id=ServerId(SURVIVAL), state=ServerState.AWAITING_LICENCE))
    assert "licence (EULA)" in (embed.description or "") and embed.footer.text.startswith("Server id")
    schedules = [Schedule("a", "Restart", False, next_fire=NOW + timedelta(hours=1)),
                 Schedule("b", "Daily restart", True, next_fire=NOW + timedelta(hours=5)),
                 Schedule("c", "restart (old)", True, next_fire=NOW - timedelta(hours=1)),
                 Schedule("d", "Backup", True, next_fire=NOW + timedelta(minutes=5))]
    picked = next_restart(schedules, NOW)
    assert picked is not None and picked.id == "b"
    assert next_restart([], NOW) is None
    assert bar(31, 100) == "▰▰▰▱▱▱▱▱▱▱ 31%" and bar(5, 0).endswith("0%") and bar(200, 100).endswith("100%")


def amp_error(name: str, status: int | None = None, retry_after: float | None = None) -> Exception:
    """Shaped like portcullis_api's errors, which Discord code may not import."""
    cls = type(name, (Exception,), {})
    exc = cls(f"POST /api/Shards/x: [{status}] raw")
    exc.status = status  # type: ignore[attr-defined]
    exc.retry_after = retry_after  # type: ignore[attr-defined]
    return exc


def test_plain_errors() -> None:
    assert "isn't allowed" in plain_error(amp_error("MissingCapability", 403))
    assert "busy with another job" in plain_error(amp_error("Conflict", 409))
    assert "slow down. Try again in 30 seconds." in plain_error(amp_error("RateLimited", 429, 30))
    assert "Try again in 5 minutes." in plain_error(amp_error("RateLimited", 429, 300))
    assert "AMP_PASSWORD" in plain_error(amp_error("AuthFailed", 401))
    assert "didn't answer" in plain_error(amp_error("Unreachable", 503))
    assert "error of its own" in plain_error(amp_error("AmpError", 500))
    assert "took too long" in plain_error(TimeoutError())
    assert plain_error(TryLater("A backup is running.")) == "A backup is running."
    unknown = plain_error(FakeAmpError("weird `thing` @everyone"))
    assert unknown.startswith("What AMP said: `") and "@everyone" not in unknown
    for raw in (amp_error("MissingCapability", 403), amp_error("Conflict", 409), amp_error("NotFound", 404)):
        assert "/api/" not in plain_error(raw) and "[4" not in plain_error(raw)


# ------------------------------------------------------------------------------ commands


async def test_health_reports_each_part(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.admin.health(i)
    text = i.embed.description
    assert "✅ **AMP** · answering, 3 server(s)" in text
    assert "**Live status cards** · none yet" in text
    assert f"**Brand emojis** · 0 of {len(SET)} uploaded" in text
    assert i.embed.colour.value == 0x4ADE80  # all fine: green

    h.ctx.live_cards.pacer.rate_limited()
    await h.ctx.settings.set(CARD_KEY, {"channel": 55}, scope=SURVIVAL)
    i = FakeInteraction(member())
    await h.m.admin.health(i)
    assert "paused after Discord rate limit" in i.embed.description
    assert i.embed.colour.value == 0xF2B33D  # mustard


async def test_health_says_when_amp_is_down(h: Harness) -> None:
    async def down() -> list[Any]:
        raise TimeoutError

    h.port.list_servers = down  # type: ignore[method-assign]
    i = FakeInteraction(member())
    await h.m.admin.health(i)
    assert "❌ **AMP** · AMP took too long" in i.embed.description and i.embed.colour.value == 0xFF5A5F


async def test_setup_start_walks_the_steps(h: Harness) -> None:
    setup = h.m.setup
    i = FakeInteraction(member())
    await setup.setup_start(i)
    assert "Manage Server" in i.text

    i = FakeInteraction(admin())
    await setup.setup_start(i)
    fields = {f.name: f.value for f in i.embed.fields}
    assert fields["1 · Connection to AMP"].startswith("✅") and "3 server(s)" in fields["1 · Connection to AMP"]
    assert fields["2 · Alerts"].startswith("⬜") and fields["3 · Live status cards"].startswith("⬜")
    assert "view" in i.replies[-1]

    note = await setup.place_live_cards(FakeInteraction(admin()), 55)
    assert note == "Posted 2 card(s) in <#55>."  # the hidden server gets no card
    assert (await h.ctx.settings.get(CARD_KEY, scope=CREATIVE))["channel"] == 55
    assert await setup.set_live_every(FakeInteraction(admin()), 9) == "Cards update every 5 minutes from the next pass."
    assert await setup.save_links(FakeInteraction(admin()), "ftp://x", "") is not None
    assert await setup.save_links(FakeInteraction(admin()), "https://panel.example.com", "") is None
    assert await h.ctx.settings.get(PANEL_URL) == "https://panel.example.com"
    assert await h.ctx.settings.get(SUPPORT_URL) is None

    fields = {f.name: f.value for f in (await setup.wizard_embed(FakeInteraction(admin()))).fields}
    assert fields["3 · Live status cards"] == "✅ 2 card(s) in <#55>, updated every 5 minutes."
    assert fields["4 · Buttons under the cards (optional)"] == "✅ Panel set."
    assert "Stopped updating 2 card(s)" in await setup.remove_live_cards(FakeInteraction(admin()))


async def test_server_diagnostics(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.server.server_diagnostics(i, "Survival")
    assert "operator role" in i.text

    h.port.disk[ServerId(SURVIVAL)] = DiskUsage(size_mb=2048, limit_mb=8192)
    i = FakeInteraction(operator())
    await h.m.server.server_diagnostics(i, "Survival")
    fields = {f.name: f.value for f in i.embed.fields}
    assert fields["Disk"] == "▰▰▰▱▱▱▱▱▱▱ 25% · 2048 of 8192 MB"
    assert "AMP sent no CPU, memory figures" in i.embed.footer.text

    async def broken(server: ServerId) -> DiskUsage:
        raise FakeAmpError("no")

    h.port.disk_usage = broken  # type: ignore[method-assign]
    i = FakeInteraction(operator())
    await h.m.server.server_diagnostics(i, "Survival")
    assert "Disk" not in {f.name for f in i.embed.fields} and "disk" in i.embed.footer.text


@pytest.mark.parametrize("state", list(ServerState))
def test_every_state_has_a_colour_and_an_icon(state: ServerState) -> None:
    assert state in STATE_COLOURS
    assert Emojis().state(state) != "❔" or state is ServerState.UNKNOWN
