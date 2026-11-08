# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""Status banners: the picture, its colours, /server banner and the board's image style."""

from __future__ import annotations

import io
import time
from datetime import UTC, datetime, timedelta

from PIL import Image, ImageFont

from portcullis.bot.banners import (
    ELLIPSIS,
    MAX_CARDS,
    THEMES,
    WIDTH,
    BannerCard,
    card_from,
    contrast_ratio,
    details,
    fit,
    render_banner,
)
from portcullis.bot.board import BOARD_CHANNEL, BOARD_MESSAGE, BOARD_STYLE
from portcullis.core.types import ServerId, ServerState, ServerStatus
from tests.fakes.amp import FakeAmpError
from tests.fakes.discord import FakeInteraction
from tests.test_bot import SURVIVAL, Harness, admin, db, h, member, operator  # noqa: F401

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def picture(png: bytes) -> Image.Image:
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    image = Image.open(io.BytesIO(png))
    image.load()
    return image


def test_every_state_renders_in_both_themes() -> None:
    cards = [BannerCard(name=state.label, state=state, players=1, max_players=10, cpu_percent=5.0, memory_mb=512,
                        memory_limit_mb=1024, started_at=NOW - timedelta(hours=2), address="play.example.com")
             for state in ServerState]
    for theme in THEMES:
        image = picture(render_banner(cards, theme=theme, now=NOW))
        assert image.width == WIDTH and image.height > 100 * len(cards)


def test_at_most_ten_cards_and_an_empty_list_still_renders() -> None:
    one = picture(render_banner([BannerCard("A", ServerState.RUNNING)]))
    many = picture(render_banner([BannerCard(str(n), ServerState.RUNNING) for n in range(15)]))
    ten = picture(render_banner([BannerCard(str(n), ServerState.RUNNING) for n in range(MAX_CARDS)]))
    assert many.height == ten.height > one.height
    assert picture(render_banner([])).height == one.height


def test_long_text_is_cut_to_fit() -> None:
    font = ImageFont.load_default(size=26)
    cut = fit("A very long server name " * 10, font, 300)
    assert cut.endswith(ELLIPSIS) and font.getlength(cut) <= 300
    assert fit("Short", font, 300) == "Short"


def test_details_line() -> None:
    running = BannerCard("S", ServerState.RUNNING, players=3, max_players=20, cpu_percent=12.4, memory_mb=2048,
                         memory_limit_mb=4096, started_at=NOW - timedelta(hours=1, minutes=5))
    assert details(running, NOW) == "Players 3/20  ·  CPU 12%  ·  Memory 2048/4096 MB  ·  Up 1h 5m"
    assert details(BannerCard("S", ServerState.STOPPED), NOW) == ""
    stopped = card_from("S", ServerStatus(id=ServerId("x"), state=ServerState.STOPPED, cpu_percent=1.0))
    assert stopped.players is None and stopped.cpu_percent is None


def test_colours_pass_wcag() -> None:
    assert round(contrast_ratio((0, 0, 0), (255, 255, 255)), 1) == 21.0
    for name, theme in THEMES.items():
        for text in (theme.text, theme.muted):
            assert contrast_ratio(text, theme.card) >= 4.5, name
        for state, dot in theme.dots.items():  # the dot is a graphic next to the state word: 3:1
            assert contrast_ratio(dot, theme.card) >= 3.0, (name, state)
        assert set(theme.dots) == set(ServerState)


def test_render_is_quick() -> None:
    cards = [BannerCard(f"Server {n}", ServerState.RUNNING, players=n, cpu_percent=1.0, memory_mb=1)
             for n in range(MAX_CARDS)]
    render_banner(cards)
    started = time.perf_counter()
    render_banner(cards)
    assert time.perf_counter() - started < 1.0  # about 0.1 s in practice; generous for slow CI runners


async def test_banner_command(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.server.server_banner(i)
    [sent] = i.replies
    assert sent["ephemeral"] is True and sent["file"].filename == "servers.png"
    image = picture(sent["file"].fp.read())
    assert image.height < 400  # Survival and Creative; Secret is hidden from members

    i = FakeInteraction(member())
    await h.m.server.server_banner(i, "Survival", theme="light", public=True)
    assert i.replies[0]["ephemeral"] is True and "Only operators" in i.text

    i = FakeInteraction(operator())
    await h.m.server.server_banner(i, "Survival", public=True)
    assert i.replies[0]["ephemeral"] is False and i.text == ""

    i = FakeInteraction(member())
    await h.m.server.server_banner(i, "Nope")
    assert "don't know a server" in i.text


async def test_banner_shows_unreachable_servers_as_unknown(h: Harness) -> None:
    async def broken(server: ServerId) -> ServerStatus:
        raise FakeAmpError("down")

    h.port.get_status = broken  # type: ignore[method-assign]
    i = FakeInteraction(member())
    await h.m.server.server_banner(i, "Survival")
    picture(i.replies[0]["file"].fp.read())


async def test_board_image_style(h: Harness) -> None:
    await h.ctx.live.seed([await h.port.get_status(ServerId(SURVIVAL))], [ServerId(SURVIVAL)])
    setup = h.m.setup
    assert "embed or image" in (await setup.save_bot_options(FakeInteraction(admin()), "", "", "", "gif") or "")
    await setup.set_board_channel(FakeInteraction(admin()), 55)
    channel = h.client.channels[55]
    assert "embed" in channel.sent[-1]

    assert await setup.save_bot_options(FakeInteraction(admin()), "", "", "", "image") is None
    assert await h.ctx.settings.get(BOARD_STYLE) == "image"
    edit = channel.sent[-1]
    assert edit["edit"] == await h.ctx.settings.get(BOARD_MESSAGE) and edit["embed"] is None
    [file] = edit["attachments"]
    picture(file.fp.read())
    embed = await setup.bot_embed()
    assert "(image)" in {f.name: f.value for f in embed.fields}["Live server board"]

    await h.ctx.settings.delete(BOARD_MESSAGE)
    await h.ctx.board.update()
    assert "file" in channel.sent[-1]

    assert await setup.save_bot_options(FakeInteraction(admin()), "", "", "", "embed") is None
    assert await h.ctx.settings.get(BOARD_STYLE) is None
    assert channel.sent[-1]["attachments"] == [] and channel.sent[-1]["embed"] is not None
    assert await h.ctx.settings.get(BOARD_CHANNEL) == 55
