"""Portcullis brand v3: a pointed gate arch with a real lattice, lit from below.

Usage: python3 generate.py OUT_DIR FONT_DIR

FONT_DIR holds ChakraPetch-Bold.ttf and ChakraPetch-Regular.ttf from Google Fonts
(SIL Open Font License). Text is converted to outlines, so the SVGs need no fonts.
Needs fonttools.
"""

import math
import pathlib
import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = pathlib.Path(sys.argv[1])
FONTS = pathlib.Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

BOLD = "ChakraPetch-Bold.ttf"
TEXT = "ChakraPetch-Regular.ttf"

# Palette. Night navy and stone white, with torchlight amber as the one accent.
# Amber (not cyan) keeps clear distance from CubeCoders' own colours.
NIGHT = "#0E1424"
NIGHT_2 = "#18213A"
STONE = "#EEF2FA"
MIST = "#9AA6BD"
LINE = "#8FA3C8"
TORCH = "#FFB547"
EMBER = "#FF7A2F"
GREEN = "#4ADE80"
RED = "#FF5A5F"

# ---------------------------------------------------------------- the mark
# 64x64 grid. A pointed (two-centred) arch, 6.5 units thick, with a lattice of
# three spiked bars and two rails hanging inside it. Raised, light shows under
# the spikes; lowered, the spikes reach the ground and a sill lights up.
FRAME_W = 6.5
SPRING = 30.0  # where the walls turn into the arch
R_OUT = 26.0
ARCH = f"M10 58 V{SPRING} A{R_OUT} {R_OUT} 0 0 1 32 4.3 A{R_OUT} {R_OUT} 0 0 1 54 {SPRING} V58"
INNER = "M16 58 V31 A20 20 0 0 1 32 11.5 A20 20 0 0 1 48 31 V58 Z"
BAR_X = (22.5, 32.0, 41.5)
BAR_W = 5.5
RAIL_H = 5.0


def mark(p, fg=STONE, light=TORCH, lowered=False):
    """SVG for the mark in 64-unit space. p prefixes ids so several marks can share a file."""
    d = 10.0 if lowered else 0.0
    bars = "".join(
        f'<path d="M{x - BAR_W / 2} 0 H{x + BAR_W / 2} V{42 + d} L{x} {48 + d} L{x - BAR_W / 2} {42 + d} Z"/>'
        for x in BAR_X
    )
    rails = "".join(f'<rect x="0" y="{y + d}" width="64" height="{RAIL_H}"/>' for y in (20, 32))
    defs = f'<clipPath id="{p}c"><path d="{INNER}"/></clipPath>'
    glow = ""
    if light and not lowered:
        defs += (
            f'<linearGradient id="{p}g" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{light}" stop-opacity="0"/>'
            f'<stop offset="1" stop-color="{light}"/></linearGradient>'
        )
        glow = f'<rect x="0" y="42" width="64" height="16" fill="url(#{p}g)" clip-path="url(#{p}c)"/>'
    sill = ""
    if light and lowered:
        sill = f'<rect x="6.75" y="59.5" width="50.5" height="3" rx="1.5" fill="{light}"/>'
    return (
        f"<defs>{defs}</defs>{glow}"
        f'<g fill="{fg}" clip-path="url(#{p}c)">{bars}{rails}</g>'
        f'<path d="{ARCH}" fill="none" stroke="{fg}" stroke-width="{FRAME_W}" stroke-linejoin="round"/>'
        f"{sill}"
    )


def place(inner, x, y, size):
    return f'<g transform="translate({x:.2f} {y:.2f}) scale({size / 64:.5f})">{inner}</g>'


# ---------------------------------------------------------------- text as paths
_fonts = {}


def text_path(font_file, text, size, x, y, tracking=0.0, fill=STONE):
    """Outline `text` with its baseline at (x, y). Returns (svg, width)."""
    font = _fonts.setdefault(font_file, TTFont(FONTS / font_file))
    gs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    scale = size / font["head"].unitsPerEm
    cursor, out = 0.0, []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x + cursor, y)))
        out.append(pen.getCommands())
        cursor += hmtx[name][0] * scale + tracking
    return f'<path fill="{fill}" d="{" ".join(out)}"/>', cursor - tracking


def svg(w, h, label, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{label}"><title>{label}</title>{body}</svg>\n'
    )


# ---------------------------------------------------------------- pieces
CAP = 0.70  # Chakra Petch cap height, in em
WORD = "PORTCULLIS"


def lockup(p, fg, sub, mark_size, tagline=None, accent=TORCH):
    """Mark + wordmark, top-left at (0, 0). Returns (svg, width)."""
    text_size = mark_size * 0.62
    tracking = text_size * 0.06
    gap = mark_size * 0.22
    m = place(mark(p, fg=fg, light=accent), 0, 0, mark_size)
    # Centre the caps (or caps + tagline) on the arch body, y 10..58 of 64.
    body_mid = mark_size * (34 / 64)
    cap_h = text_size * CAP
    base = body_mid - mark_size * 0.04 if tagline else body_mid + cap_h / 2
    tx = mark_size * (58 / 64) + gap
    word, ww = text_path(BOLD, WORD, text_size, tx, base, tracking=tracking, fill=fg)
    out, width = m + word, tx + ww
    if tagline:
        tag_size = text_size * 0.42
        t, tw = text_path(TEXT, tagline, tag_size, tx + text_size * 0.03, base + tag_size * 1.75, fill=sub)
        out += t
        width = max(width, tx + tw)
    return out, width


def lattice_bg(w, h, cell, opacity):
    """Faint portcullis lattice across the background."""
    d = " ".join(f"M{x} 0 V{h}" for x in range(0, w + cell, cell))
    d += " " + " ".join(f"M0 {y} H{w}" for y in range(0, h + cell, cell))
    return f'<path d="{d}" stroke="{LINE}" stroke-opacity="{opacity}" stroke-width="1" fill="none"/>'


def backdrop(w, h, p, gx, gy, glow=TORCH, grid=48):
    """Night background, faint lattice, warm light rising from below the focal point."""
    return (
        f'<defs><radialGradient id="{p}glow" cx="0.5" cy="0.5" r="0.5">'
        f'<stop offset="0" stop-color="{glow}" stop-opacity="0.22"/>'
        f'<stop offset="0.6" stop-color="{EMBER if glow == TORCH else glow}" stop-opacity="0.05"/>'
        f'<stop offset="1" stop-color="{glow}" stop-opacity="0"/></radialGradient>'
        f'<linearGradient id="{p}v" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{NIGHT}" stop-opacity="0.85"/>'
        f'<stop offset="1" stop-color="{NIGHT}" stop-opacity="0"/></linearGradient></defs>'
        f'<rect width="{w}" height="{h}" fill="{NIGHT}"/>'
        + lattice_bg(w, h, grid, 0.06)
        + f'<rect width="{w}" height="{h}" fill="url(#{p}v)"/>'
        + f'<ellipse cx="{gx}" cy="{gy}" rx="{w * 0.42}" ry="{h * 0.55}" fill="url(#{p}glow)"/>'
    )


def big_gate(p, x, y, size, opacity=0.08):
    """A huge, faint gate silhouette used as a background motif."""
    return f'<g opacity="{opacity}">{place(mark(p, fg=STONE, light=None), x, y, size)}</g>'


def icon(p, light=TORCH, lowered=False, size=512):
    """Full-bleed square for avatars and thumbnails. Discord crops to a circle, so the
    mark stays inside the middle 66%."""
    s = size
    return (
        f'<defs><radialGradient id="{p}bg" cx="0.5" cy="0.4" r="0.7">'
        f'<stop offset="0" stop-color="{NIGHT_2}"/><stop offset="1" stop-color="{NIGHT}"/></radialGradient>'
        f'<radialGradient id="{p}lt" cx="0.5" cy="0.5" r="0.5">'
        f'<stop offset="0" stop-color="{light}" stop-opacity="0.28"/>'
        f'<stop offset="1" stop-color="{light}" stop-opacity="0"/></radialGradient></defs>'
        f'<rect width="{s}" height="{s}" fill="url(#{p}bg)"/>'
        f'<ellipse cx="{s / 2}" cy="{s * 0.86}" rx="{s * 0.46}" ry="{s * 0.24}" fill="url(#{p}lt)"/>'
        + place(mark(p, light=light, lowered=lowered), s * 0.17, s * 0.15, s * 0.66)
    )


def features(items, x, y, size, color=STONE):
    """Inline feature list separated by small amber diamonds. Returns (svg, width)."""
    out, cx = [], x
    for i, label in enumerate(items):
        if i:
            r = size * 0.18
            mx, my = cx + size * 0.7, y - size * 0.33
            out.append(f'<path d="M{mx} {my - r} L{mx + r} {my} L{mx} {my + r} L{mx - r} {my} Z" fill="{TORCH}"/>')
            cx += size * 1.4
        t, tw = text_path(TEXT, label, size, cx, y, fill=color)
        out.append(t)
        cx += tw
    return "".join(out), cx - x


# ---------------------------------------------------------------- assets
TAG = "Your game servers, in Discord."
FEATURES = ["Server status", "Join & leave alerts", "Chat bridge", "Backups"]

files = {}
files["portcullis-mark.svg"] = svg(64, 64, "Portcullis mark", mark("m"))
files["portcullis-mark-dark.svg"] = svg(64, 64, "Portcullis mark (for light backgrounds)", mark("md", fg=NIGHT))
files["portcullis-icon.svg"] = svg(512, 512, "Portcullis icon", icon("i"))
files["portcullis-status-online.svg"] = svg(512, 512, "Portcullis: server online", icon("on", light=GREEN))
files["portcullis-status-offline.svg"] = svg(
    512, 512, "Portcullis: server offline", icon("off", light=RED, lowered=True)
)

lk, lw = lockup("l", STONE, MIST, 128)
files["portcullis-logo.svg"] = svg(math.ceil(lw) + 4, 132, "Portcullis logo", lk)
lk, lw = lockup("ld", NIGHT, "#4A5670", 128)
files["portcullis-logo-dark.svg"] = svg(math.ceil(lw) + 4, 132, "Portcullis logo (for light backgrounds)", lk)

# 1280x640: GitHub social preview and release posts. Lockup on the left, a huge
# faint gate on the right, warm light rising behind it.
body = backdrop(1280, 640, "b", 960, 560)
body += big_gate("bg", 700, 40, 640, 0.07)
lk, lw = lockup("b", STONE, MIST, 168, tagline=TAG)
bx = 96
body += f'<g transform="translate({bx} 160)">{lk}</g>'
body += f'<rect x="{bx + 4}" y="384" width="72" height="4" fill="{TORCH}"/>'
body += features(FEATURES, bx + 4, 434, 24, color=MIST)[0]
files["portcullis-banner.svg"] = svg(1280, 640, "Portcullis banner", body)

# 680x240 Discord profile banner. The avatar overlaps the bottom-left, so the
# left third stays plain and the lockup sits on the right.
body = backdrop(680, 240, "d", 520, 230, grid=24)
body += big_gate("dg", 430, -10, 260, 0.06)
lk, lw = lockup("d", STONE, MIST, 72, tagline=TAG)
body += f'<g transform="translate({680 - lw - 40:.1f} 70)">{lk}</g>'
files["portcullis-discord-banner.svg"] = svg(680, 240, "Portcullis Discord profile banner", body)

# Fallbacks for games with no art (see docs/GAME_ASSETS.md). No text, so one image
# serves every game. Neutral grey light so it doesn't read as the bot's own avatar.
body = backdrop(920, 430, "gb", 460, 400, glow=MIST) + place(mark("gb", light=MIST), 460 - 115, 215 - 125, 230)
files["portcullis-game-banner.svg"] = svg(920, 430, "Game banner placeholder", body)
files["portcullis-game-logo.svg"] = svg(512, 512, "Game logo placeholder", icon("gl", light=MIST))

for name, content in files.items():
    (OUT / name).write_text(content)
print("written", sorted(files))
