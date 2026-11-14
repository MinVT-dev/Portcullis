# SPDX-License-Identifier: AGPL-3.0-or-later
"""Uptime, player, CPU and memory graphs as PNG images, in the brand palette.

Drawn with Pillow (already a dependency for banners), so graphs need no extra package. Everything is
drawn at twice the size and scaled down, which smooths the lines. Time labels use the bot's time zone
(``/setup bot``). Gaps in the data (Portcullis was off) are left as gaps, never drawn as zero.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

# docs/branding/README.md
NIGHT, CREAM, ASH, ORANGE = "#141414", "#EDE6DC", "#8C857E", "#E8710A"
GREEN, RED, MUSTARD, GRID = "#4ADE80", "#FF5A5F", "#F2B33D", "#2C2926"
# Extra lines when several servers share one graph; orange first, then colours that stay apart on Night.
LINE_COLOURS = (ORANGE, "#5EA8F2", GREEN, MUSTARD, "#C58AF9", CREAM)

WIDTH, HEIGHT, SCALE = 1000, 380, 2
PAD_L, PAD_R, PAD_T, PAD_B = 64, 24, 70, 46

Style = Literal["line", "area", "bars"]


@dataclass(frozen=True)
class Series:
    label: str
    colour: str
    points: Sequence[tuple[int, float | None]]  # (Unix seconds, value); None means no data for that step


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size * SCALE)


def nice_max(value: float) -> float:
    """A round top for the y axis at or above ``value``."""
    if value <= 0:
        return 1.0
    for top in (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200, 250, 300, 400, 500, 1000):
        if value <= top:
            return float(top)
    return float(int(value * 1.1) + 1)


def time_label(ts: int, span: int, tz: tzinfo) -> str:
    when = datetime.fromtimestamp(ts, tz)
    if span <= 2 * 86400:
        return when.strftime("%H:%M")
    if span <= 8 * 86400:
        return when.strftime("%a")
    return when.strftime("%d %b")


def ticks(since: int, until: int, tz: tzinfo) -> list[int]:
    """Label positions on round local times inside ``since``..``until``."""
    span = until - since
    hours = 4 if span <= 2 * 86400 else 24 if span <= 8 * 86400 else 24 * 5
    start = datetime.fromtimestamp(since, tz).replace(minute=0, second=0, microsecond=0)
    if hours >= 24:
        start = start.replace(hour=0)
    out: list[int] = []
    when = start
    while int(when.timestamp()) <= until and len(out) < 40:
        ts = int(when.timestamp())
        if since <= ts <= until and (hours < 24 or (when.date().toordinal() % (hours // 24)) == 0) \
                and (hours >= 24 or when.hour % hours == 0):
            out.append(ts)
        when = datetime.fromtimestamp(ts + 3600, tz).replace(minute=0, second=0, microsecond=0)
    return out


def render(title: str, subtitle: str, series: Sequence[Series], *, since: int, until: int, step: int, tz: tzinfo,
           style: Style = "line", unit: str = "", y_max: float | None = None) -> bytes:
    """One graph as PNG bytes."""
    w, h = WIDTH * SCALE, HEIGHT * SCALE
    image = Image.new("RGB", (w, h), NIGHT)
    draw = ImageDraw.Draw(image)
    left, right, top, bottom = PAD_L * SCALE, w - PAD_R * SCALE, PAD_T * SCALE, h - PAD_B * SCALE
    values = [v for s in series for _, v in s.points if v is not None]
    peak = y_max if y_max is not None else nice_max(max(values, default=0.0))
    span = max(1, until - since)

    def x(ts: float) -> float:
        return left + (right - left) * (ts - since) / span

    def y(value: float) -> float:
        return bottom - (bottom - top) * min(max(value, 0.0), peak) / peak

    # Title, subtitle, legend
    draw.text((left, 16 * SCALE), title, fill=CREAM, font=_font(22))
    draw.text((left, 44 * SCALE), subtitle, fill=ASH, font=_font(14))
    legend_x = right
    for item in reversed(series if len(series) > 1 else []):
        text_w = draw.textlength(item.label, font=_font(14))
        legend_x -= text_w
        draw.text((legend_x, 44 * SCALE), item.label, fill=CREAM, font=_font(14))
        legend_x -= 18 * SCALE
        draw.rounded_rectangle((legend_x, 47 * SCALE, legend_x + 12 * SCALE, 59 * SCALE), 3 * SCALE, fill=item.colour)
        legend_x -= 16 * SCALE

    # Grid and y labels
    for i in range(5):
        value = peak * i / 4
        gy = y(value)
        draw.line((left, gy, right, gy), fill=GRID, width=SCALE)
        label = f"{value:.0f}{unit}" if peak >= 4 or value == int(value) else f"{value:.1f}{unit}"
        draw.text((left - 10 * SCALE, gy), label, fill=ASH, font=_font(13), anchor="rm")

    # x labels on round local times: every 4 hours, every day, or every 5 days
    for ts in ticks(since, until, tz):
        draw.line((x(ts), bottom, x(ts), bottom + 5 * SCALE), fill=ASH, width=SCALE)
        draw.text((x(ts), bottom + 12 * SCALE), time_label(ts, span, tz), fill=ASH, font=_font(13), anchor="mt")

    if not values:
        draw.text(((left + right) / 2, (top + bottom) / 2), "No data recorded for this period yet", fill=ASH,
                  font=_font(16), anchor="mm")

    for item in series:
        points = [(ts, v) for ts, v in item.points if since <= ts < until]
        if style == "bars":
            gap = max(1.0, (right - left) * step / span * 0.15)
            for ts, v in points:
                if v is None:
                    continue
                x0, x1 = x(ts) + gap / 2, x(ts + step) - gap / 2
                if x1 - x0 < SCALE:
                    x1 = x0 + SCALE
                draw.rectangle((x0, y(peak), x1, bottom), fill=RED if v < peak else GRID)
                draw.rectangle((x0, y(v), x1, bottom), fill=item.colour)
            continue
        runs: list[list[tuple[float, float]]] = []
        last_ts: int | None = None
        for ts, v in points:
            if v is None:
                last_ts = None
                continue
            centre = x(ts + step / 2)
            if last_ts is None or ts - last_ts > step * 1.5:
                runs.append([])
            runs[-1].append((centre, y(v)))
            last_ts = ts
        for run in runs:
            if style == "area" and len(run) > 1:
                fill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                ImageDraw.Draw(fill).polygon([(run[0][0], bottom), *run, (run[-1][0], bottom)],
                                             fill=_rgba(item.colour, 70))
                image.paste(fill, (0, 0), fill)
                draw = ImageDraw.Draw(image)
            if len(run) == 1:
                cx, cy = run[0]
                draw.ellipse((cx - 3 * SCALE, cy - 3 * SCALE, cx + 3 * SCALE, cy + 3 * SCALE), fill=item.colour)
            else:
                draw.line(run, fill=item.colour, width=3 * SCALE, joint="curve")

    draw.line((left, bottom, right, bottom), fill=ASH, width=SCALE)
    out = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    out.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _rgba(colour: str, alpha: int) -> tuple[int, int, int, int]:
    value = colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha
