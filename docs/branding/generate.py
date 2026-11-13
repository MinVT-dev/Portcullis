"""Portcullis brand v4, "Inside out": a lowercase p folded from charcoal card with an orange inside.

Usage: python3 generate.py OUT_DIR FONT_DIR

FONT_DIR holds Syne[wght].ttf from Google Fonts (SIL Open Font License). The script
makes the ExtraBold (800) and Regular (400) instances itself. All text is converted to
outlines, so the SVGs need no fonts. Needs fonttools.
"""

import math
import pathlib
import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

OUT = pathlib.Path(sys.argv[1])
FONTS = pathlib.Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

# Palette. Near-black and charcoal card, warm greys, one orange. The orange only ever
# appears where the card is folded, so it reads as the inside of the paper.
NIGHT = "#141414"
CARD = "#5C5752"  # the mark itself, on dark and on light
STONE = "#EFEAE3"  # light background
CREAM = "#EDE6DC"  # text on dark
ASH = "#8C857E"  # secondary text, neutral folds
SHADOW = "#1E1D1B"  # the faint background mark on banners
ORANGE = "#E8710A"
GREEN = "#4ADE80"
RED = "#FF5A5F"

# ---------------------------------------------------------------- the mark
# 64x64 grid, centred. One sheet folded twice: the top-right corner is turned down and
# the tail of the p is folded out to the side. The fold faces carry the colour.
BODY = "M14 11 H42 L50 19 V37 H26 V41 H14 Z M26 21 V29 H38 V21 Z"
CORNER = "M42 11 L50 19 H42 Z"
TAIL = "M14 41 H26 L34 53 H22 Z"


def mark(fg=CARD, fold=ORANGE):
    """SVG for the mark in 64-unit space."""
    return (
        f'<path d="{BODY}" fill="{fg}" fill-rule="evenodd"/>'
        f'<path d="{CORNER}" fill="{fold}"/><path d="{TAIL}" fill="{fold}"/>'
    )


def place(inner, x, y, size):
    return f'<g transform="translate({x:.2f} {y:.2f}) scale({size / 64:.5f})">{inner}</g>'


# ---------------------------------------------------------------- text as paths
def _instance(weight):
    font = TTFont(FONTS / "Syne[wght].ttf")
    return instancer.instantiateVariableFont(font, {"wght": weight})


BOLD, REGULAR = _instance(800), _instance(400)


def text_path(font, text, size, x, y, fill=CREAM):
    """Outline `text` with its baseline at (x, y). Returns (svg, width)."""
    gs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    scale = size / font["head"].unitsPerEm
    cursor, out = 0.0, []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x + cursor, y)))
        out.append(pen.getCommands())
        cursor += hmtx[name][0] * scale
    return f'<path fill="{fill}" d="{" ".join(out)}"/>', cursor


def svg(w, h, label, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{label}"><title>{label}</title>{body}</svg>\n'
    )


# ---------------------------------------------------------------- pieces
WORD = "portcullis"


def lockup(fg, text, sub, mark_size, tagline=None):
    """Mark + wordmark, top-left at (0, 0). The wordmark sits on the mark's bowl line
    (y 37 of 64), so the two p's share a baseline. Returns (svg, width)."""
    unit = mark_size / 64
    size = mark_size * 0.40
    base = 37 * unit
    tx = 50 * unit + mark_size * 0.10
    out = place(mark(fg), 0, 0, mark_size)
    word, ww = text_path(BOLD, WORD, size, tx, base, fill=text)
    out += word
    width = tx + ww
    if tagline:
        tsize = size * 0.42
        t, tw = text_path(REGULAR, tagline, tsize, tx + size * 0.02, base + size * 0.45 + tsize, fill=sub)
        out += t
        width = max(width, tx + tw)
    return out, width


def fold_dot(x, y, r, color=ORANGE):
    """A small folded-corner triangle, used as the list separator."""
    return f'<path d="M{x - r} {y - r} H{x + r} L{x - r} {y + r} Z" fill="{color}"/>'


def features(items, x, y, size, color=ASH):
    out, cx = [], x
    for i, label in enumerate(items):
        if i:
            out.append(fold_dot(cx + size * 0.75, y - size * 0.32, size * 0.16))
            cx += size * 1.5
        t, tw = text_path(REGULAR, label, size, cx, y, fill=color)
        out.append(t)
        cx += tw
    return "".join(out)


def icon(fold=ORANGE, size=512):
    """Full-bleed square for avatars and thumbnails. Flat near-black; Discord crops to a
    circle, so the mark stays inside the middle 75%."""
    return f'<rect width="{size}" height="{size}" fill="{NIGHT}"/>' + place(
        mark(fold=fold), size * 0.08, size * 0.08, size * 0.84
    )


# ---------------------------------------------------------------- assets
TAG = "Your game servers, in Discord."
FEATURES = ["Server status", "Join & leave alerts", "Chat bridge", "Backups"]

files = {}
files["portcullis-mark.svg"] = svg(64, 64, "Portcullis mark", mark())
files["portcullis-mark-dark.svg"] = svg(64, 64, "Portcullis mark (for light backgrounds)", mark())
files["portcullis-icon.svg"] = svg(512, 512, "Portcullis icon", icon())
files["portcullis-status-online.svg"] = svg(512, 512, "Portcullis: server online", icon(fold=GREEN))
files["portcullis-status-offline.svg"] = svg(512, 512, "Portcullis: server offline", icon(fold=RED))

lk, lw = lockup(CARD, CREAM, ASH, 128)
files["portcullis-logo.svg"] = svg(math.ceil(lw) + 4, 128, "Portcullis logo", lk)
lk, lw = lockup(CARD, NIGHT, "#5C5752", 128)
files["portcullis-logo-dark.svg"] = svg(math.ceil(lw) + 4, 128, "Portcullis logo (for light backgrounds)", lk)

# 1280x640: GitHub social preview and release posts. The lockup on the left; the mark
# again, huge and barely lighter than the background, bleeding off the right edge.
body = f'<rect width="1280" height="640" fill="{NIGHT}"/>' + place(mark(SHADOW, SHADOW), 820, 10, 720)
lk, lw = lockup(CARD, CREAM, ASH, 190, tagline=TAG)
body += f'<g transform="translate(100 190)">{lk}</g>'
body += features(FEATURES, 104, 456, 24)
files["portcullis-banner.svg"] = svg(1280, 640, "Portcullis banner", body)

# 680x240 Discord profile banner. The avatar overlaps the bottom-left, so the left third
# stays plain and the lockup sits on the right.
body = f'<rect width="680" height="240" fill="{NIGHT}"/>'
lk, lw = lockup(CARD, CREAM, ASH, 84, tagline=TAG)
body += f'<g transform="translate({680 - lw - 40:.1f} 72)">{lk}</g>'
files["portcullis-discord-banner.svg"] = svg(680, 240, "Portcullis Discord profile banner", body)

# Fallbacks for games with no art (see docs/GAME_ASSETS.md). No text, so one image serves
# every game. Grey folds, so it doesn't read as the bot's own avatar.
body = f'<rect width="920" height="430" fill="{NIGHT}"/>' + place(mark(fold=ASH), 460 - 160, 215 - 160, 320)
files["portcullis-game-banner.svg"] = svg(920, 430, "Game banner placeholder", body)
files["portcullis-game-logo.svg"] = svg(512, 512, "Game logo placeholder", icon(fold=ASH))

for name, content in files.items():
    (OUT / name).write_text(content)
print("written", sorted(files))
