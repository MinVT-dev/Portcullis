# SPDX-License-Identifier: AGPL-3.0-or-later
"""Server status banners: a PNG with one card per server, for ``/server banner`` and the image board.

Gatekeeper V3 drew a banner image per server. This draws them with Pillow's built-in font, so there
are no font files and nothing is downloaded. The state is always written as a word next to its dot,
and every text colour passes WCAG AA (4.5:1) on its card; the tests check both themes.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

from PIL import Image, ImageDraw, ImageFont

from portcullis.core.types import ServerState, ServerStatus

MAX_CARDS = 10
WIDTH, CARD_HEIGHT, GAP, PAD = 760, 112, 10, 18
NAME_SIZE, TEXT_SIZE = 26, 17
ELLIPSIS = "..."  # the built-in font may not have "…"

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Theme:
    background: RGB
    card: RGB
    text: RGB
    muted: RGB
    dots: dict[ServerState, RGB]


THEMES = {
    "dark": Theme(
        background=(0x1E, 0x1F, 0x22), card=(0x2B, 0x2D, 0x31), text=(0xF2, 0xF3, 0xF5), muted=(0xB5, 0xBA, 0xC1),
        dots={ServerState.RUNNING: (0x3B, 0xA5, 0x5D), ServerState.STOPPED: (0xF0, 0x6A, 0x6A),
              ServerState.BUSY: (0xF0, 0xB2, 0x32), ServerState.SLEEPING: (0x7A, 0xA7, 0xE8),
              ServerState.FAILED: (0xFF, 0x8A, 0x80), ServerState.SUSPENDED: (0x9A, 0xA0, 0xA6),
              ServerState.AWAITING_LICENCE: (0xF0, 0x9A, 0x4A), ServerState.UNKNOWN: (0x9A, 0xA0, 0xA6)}),
    "light": Theme(
        background=(0xEB, 0xED, 0xEF), card=(0xFF, 0xFF, 0xFF), text=(0x1E, 0x1F, 0x22), muted=(0x4E, 0x50, 0x58),
        dots={ServerState.RUNNING: (0x1A, 0x7F, 0x45), ServerState.STOPPED: (0xC6, 0x28, 0x28),
              ServerState.BUSY: (0x9A, 0x67, 0x00), ServerState.SLEEPING: (0x3B, 0x6F, 0xB6),
              ServerState.FAILED: (0x9B, 0x1C, 0x1C), ServerState.SUSPENDED: (0x5C, 0x5F, 0x66),
              ServerState.AWAITING_LICENCE: (0xA4, 0x4A, 0x00), ServerState.UNKNOWN: (0x5C, 0x5F, 0x66)}),
}


@dataclass(frozen=True)
class BannerCard:
    name: str
    state: ServerState
    players: int | None = None
    max_players: int | None = None
    cpu_percent: float | None = None
    memory_mb: int | None = None
    memory_limit_mb: int | None = None
    started_at: datetime | None = None
    address: str | None = None


def card_from(name: str, status: ServerStatus, address: str | None = None) -> BannerCard:
    running = status.state is ServerState.RUNNING
    return BannerCard(name=name, state=status.state, players=status.player_count if running else None,
                      max_players=status.max_players, cpu_percent=status.cpu_percent if running else None,
                      memory_mb=status.memory_mb if running else None, memory_limit_mb=status.memory_limit_mb,
                      started_at=status.started_at, address=address)


def _luminance(colour: RGB) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in colour)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: RGB, b: RGB) -> float:
    """WCAG 2 contrast ratio between two colours, from 1 to 21."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@cache
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def fit(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, width: float) -> str:
    """``text``, cut with an ellipsis so it is at most ``width`` pixels wide."""
    if font.getlength(text) <= width:
        return text
    while text and font.getlength(text + ELLIPSIS) > width:
        text = text[:-1]
    return text.rstrip() + ELLIPSIS


def _uptime(started: datetime, now: datetime) -> str:
    minutes = max(0, int((now - started).total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def details(card: BannerCard, now: datetime) -> str:
    parts = []
    if card.players is not None:
        parts.append(f"Players {card.players}" + (f"/{card.max_players}" if card.max_players else ""))
    if card.cpu_percent is not None:
        parts.append(f"CPU {card.cpu_percent:.0f}%")
    if card.memory_mb is not None:
        parts.append(f"Memory {card.memory_mb}" + (f"/{card.memory_limit_mb} MB" if card.memory_limit_mb else " MB"))
    if card.started_at is not None and card.state is ServerState.RUNNING:
        parts.append(f"Up {_uptime(card.started_at, now)}")
    return "  ·  ".join(parts)


def render_banner(cards: list[BannerCard], *, theme: str = "dark", now: datetime | None = None) -> bytes:
    """A PNG with one card per server, at most ``MAX_CARDS``."""
    palette = THEMES[theme]
    now = now or datetime.now(UTC)
    shown = cards[:MAX_CARDS] or [BannerCard(name="No servers yet", state=ServerState.UNKNOWN)]
    height = PAD + len(shown) * (CARD_HEIGHT + GAP) - GAP + PAD
    image = Image.new("RGB", (WIDTH, height), palette.background)
    draw = ImageDraw.Draw(image)
    name_font, text_font = _font(NAME_SIZE), _font(TEXT_SIZE)
    inner = WIDTH - 2 * PAD - 2 * PAD
    for index, item in enumerate(shown):
        top = PAD + index * (CARD_HEIGHT + GAP)
        left, right = PAD, WIDTH - PAD
        draw.rounded_rectangle((left, top, right, top + CARD_HEIGHT), radius=10, fill=palette.card)
        x = left + PAD
        state_word = item.state.label
        state_width = text_font.getlength(state_word)
        dot = 12
        state_left = right - PAD - state_width
        dot_left = state_left - 8 - dot
        draw.ellipse((dot_left, top + PAD + 7, dot_left + dot, top + PAD + 7 + dot),
                     fill=palette.dots.get(item.state, palette.dots[ServerState.UNKNOWN]))
        draw.text((state_left, top + PAD + 4), state_word, font=text_font, fill=palette.text)
        name = fit(item.name, name_font, dot_left - 16 - x)
        draw.text((x, top + PAD - 2), name, font=name_font, fill=palette.text)
        draw.text((x, top + PAD + 38), fit(details(item, now), text_font, inner), font=text_font, fill=palette.muted)
        if item.address:
            draw.text((x, top + PAD + 62), fit(item.address, text_font, inner), font=text_font, fill=palette.muted)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
