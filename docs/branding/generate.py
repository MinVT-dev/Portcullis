"""Portcullis brand v2: geometric mark on a 64-unit grid, text as outlines.

Usage: python3 gen_v2.py OUT_DIR FONT_DIR
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

# Palette: sits next to CubeCoders' site (deep navy, near-white, cool grey,
# bright cyan) without reusing their marks.
NAVY = "#121A30"
NAVY_2 = "#1A2442"
WHITE = "#E9EFF8"
GREY = "#A6B2C9"
LINE = "#8CAADC"
CYAN = "#4AD6FF"
GREEN = "#4ADE80"
RED = "#FF5A5F"

# ---------------------------------------------------------------- the mark
# 64x64 grid, one weight (W = 5) for every member. The frame is the top half
# of a flat-topped hexagon (60-degree shoulders) standing on two walls: a
# gatehouse arch. Inside it, with a G = 3 gap, hangs the gate: a beam that
# follows the arch, three spiked bars and one rail.
W = 5.0
TAN30 = math.tan(math.radians(30))
G = 3.0
BASE = 58.0
SH = 8.0 * math.tan(math.radians(60))  # shoulder rise for an 8-unit run
FRAME = [(12, BASE), (12, 26), (20, 26 - SH), (44, 26 - SH), (52, 26), (52, BASE)]


def pts(points):
    return " ".join(f"{x:.3f},{y:.3f}" for x, y in points)


def offset(poly, t):
    """Offset an open polyline inward (towards the arch's inside) by t, with mitred corners."""
    lines = []
    for (x0, y0), (x1, y1) in zip(poly, poly[1:], strict=False):
        dx, dy = x1 - x0, y1 - y0
        n = math.hypot(dx, dy)
        nx, ny = -dy / n, dx / n  # left-hand normal: points into the arch for this path direction
        lines.append(((x0 + nx * t, y0 + ny * t), (dx, dy)))
    out = [(poly[0][0] + lines[0][0][0] - poly[0][0], poly[0][1])]
    for (p0, d0), (p1, d1) in zip(lines, lines[1:], strict=False):
        den = d0[0] * d1[1] - d0[1] * d1[0]
        u = ((p1[0] - p0[0]) * d1[1] - (p1[1] - p0[1]) * d1[0]) / den
        out.append((p0[0] + d0[0] * u, p0[1] + d0[1] * u))
    last = lines[-1][0]
    out.append((last[0], poly[-1][1]))
    return out


REGION = offset(FRAME, W / 2 + G)  # where the gate may draw
BEAM = offset(FRAME, W / 2 + G + W / 2)  # centre line of the gate's top beam
GX0, GX1 = REGION[0][0], REGION[-1][0]  # 17.5 .. 46.5
GATE_BARS = (GX0, 32 - W / 2, GX1 - W)  # bars at both edges and the centre


def mark(p, fg=WHITE, lowered=False, light=None):
    """Return SVG for the mark in 64-unit space. p prefixes ids."""
    d = 9.0 if lowered else 0.0  # lowered: bars reach the ground
    beam_pts = [(BEAM[1][0], BEAM[1][1] + 6)] + BEAM[1:-1] + [(BEAM[-2][0], BEAM[-2][1] + 6)]
    beam = f'<polyline points="{pts(beam_pts)}" fill="none" stroke="{fg}" stroke-width="{W}" stroke-linejoin="miter"/>'
    bars = "".join(f'<path d="M{x} 0 H{x + W} V{44 + d} L{x + W / 2} {49 + d} L{x} {44 + d} Z"/>' for x in GATE_BARS)
    rail = f'<rect x="{GX0}" y="{33 + d}" width="{GX1 - GX0}" height="{W}"/>'
    glow = ""
    if light and not lowered:
        glow = (
            f'<defs><linearGradient id="{p}g" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{light}" stop-opacity="0"/>'
            f'<stop offset="1" stop-color="{light}" stop-opacity="0.9"/></linearGradient></defs>'
            f'<rect x="{GX0}" y="45" width="{GX1 - GX0}" height="{BASE - 45}" fill="url(#{p}g)"/>'
        )
    ground = ""
    if light and lowered:
        sill_x, sill_w = FRAME[0][0] - W / 2, FRAME[-1][0] - FRAME[0][0] + W
        ground = f'<rect x="{sill_x}" y="{BASE + 1.5}" width="{sill_w}" height="2.5" fill="{light}"/>'
    return (
        f'<defs><clipPath id="{p}c"><polygon points="{pts(REGION)}"/></clipPath></defs>'
        f"{glow}"
        f'<g fill="{fg}" clip-path="url(#{p}c)">{beam}{bars}{rail}</g>'
        f'<polyline points="{pts(FRAME)}" fill="none" stroke="{fg}" stroke-width="{W}" stroke-linejoin="miter"/>'
        f"{ground}"
    )


# ---------------------------------------------------------------- text as paths
def text_path(font_file, text, size, x, y, tracking=0.0, fill=WHITE):
    """Outline `text` at baseline (x, y). Returns (svg, width)."""
    font = TTFont(FONTS / font_file)
    gs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    scale = size / font["head"].unitsPerEm
    cursor, out = 0.0, []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x + cursor, y)))
        out.append(pen.getCommands())
        cursor += hmtx[name][0] * scale + tracking
    width = cursor - tracking
    return f'<path fill="{fill}" d="{" ".join(out)}"/>', width


def svg(w, h, label, body, bg=None):
    rect = f'<rect width="{w}" height="{h}" fill="{bg}"/>' if bg else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{label}"><title>{label}</title>{rect}{body}</svg>\n'
    )


def place(inner, x, y, size):
    return f'<g transform="translate({x} {y}) scale({size / 64})">{inner}</g>'


# ---------------------------------------------------------------- assets
def icon(p, lowered=False, light=CYAN):
    # Full-bleed square: Discord crops avatars to a circle, so the mark sits
    # inside the middle 70%.
    body = (
        f'<defs><radialGradient id="{p}bg" cx="0.5" cy="0.35" r="0.75">'
        f'<stop offset="0" stop-color="{NAVY_2}"/><stop offset="1" stop-color="{NAVY}"/></radialGradient></defs>'
        f'<rect width="512" height="512" fill="url(#{p}bg)"/>'
        + place(mark(p, lowered=lowered, light=light), 512 * 0.15, 512 * 0.13, 512 * 0.7)
    )
    return body


def lockup(p, fg, sub, x, y, mark_size, text_size, tagline=None, tag_size=None):
    """Mark + wordmark. Returns (svg, width)."""
    m = place(mark(p, fg=fg), x, y, mark_size)
    # Exo 2 cap height is ~0.70 em. Centre the caps on the mark's visual body.
    base = y + mark_size * (BASE / 64)
    tx = x + mark_size * 1.0
    word, ww = text_path(
        "exo2.woff2", "Portcullis", text_size, tx, base - mark_size * 0.10, tracking=text_size * 0.01, fill=fg
    )
    out = m + word
    width = tx - x + ww
    if tagline:
        t, tw = text_path("roboto.woff2", tagline, tag_size, tx + text_size * 0.04, base + tag_size * 1.6, fill=sub)
        out += t
        width = max(width, tx - x + tw)
    return out, width


def iso_grid(w, h, step, opacity):
    """Faint isometric line lattice, the cube-edge angle used across the banner."""
    lines = []
    dx = step / TAN30
    n = int((w + h / TAN30) / dx) + 2
    for i in range(-n, n):
        x0 = i * dx
        lines.append(f"M{x0:.1f} 0 L{x0 + h / TAN30:.1f} {h}")
        lines.append(f"M{x0:.1f} 0 L{x0 - h / TAN30:.1f} {h}")
    return f'<path d="{" ".join(lines)}" stroke="{LINE}" stroke-opacity="{opacity}" stroke-width="1" fill="none"/>'


def pills(labels, x, y, size):
    out, cx = [], x
    for label in labels:
        t, tw = text_path("roboto.woff2", label, size, 0, 0, fill=WHITE)
        pw, ph = tw + size * 1.8, size * 2.1
        out.append(
            f'<g transform="translate({cx:.1f} {y})"><rect width="{pw:.1f}" height="{ph:.1f}" rx="{ph / 2:.1f}" '
            f'fill="#FFFFFF" fill-opacity="0.04" stroke="{LINE}" stroke-opacity="0.35" stroke-width="1.5"/>'
            f'<g transform="translate({size * 0.9:.1f} {ph / 2 + size * 0.36:.1f})">{t}</g></g>'
        )
        cx += pw + size * 0.7
    return "".join(out)


def backdrop(w, h, p, glow_x, glow_y):
    return (
        f'<defs><radialGradient id="{p}glow" cx="0.5" cy="0.5" r="0.5">'
        f'<stop offset="0" stop-color="{CYAN}" stop-opacity="0.20"/>'
        f'<stop offset="1" stop-color="{CYAN}" stop-opacity="0"/></radialGradient>'
        f'<linearGradient id="{p}fade" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0" stop-color="{NAVY}" stop-opacity="1"/>'
        f'<stop offset="0.55" stop-color="{NAVY}" stop-opacity="0.6"/>'
        f'<stop offset="1" stop-color="{NAVY}" stop-opacity="0"/></linearGradient></defs>'
        f'<rect width="{w}" height="{h}" fill="{NAVY}"/>'
        + iso_grid(w, h, 36, 0.07)
        + f'<rect width="{w}" height="{h}" fill="url(#{p}fade)"/>'
        + f'<ellipse cx="{glow_x}" cy="{glow_y}" rx="{w * 0.45}" ry="{h * 0.6}" fill="url(#{p}glow)"/>'
        + f'<rect y="{h - 4}" width="{w}" height="4" fill="{CYAN}"/>'
    )


TAG = "Your game servers, in Discord."
FEATURES = ["Server status", "Join & leave alerts", "Chat bridge", "Backups"]

files = {}
files["portcullis-mark.svg"] = svg(64, 64, "Portcullis mark", mark("m"))
files["portcullis-mark-dark.svg"] = svg(64, 64, "Portcullis mark (for light backgrounds)", mark("md", fg=NAVY))
files["portcullis-icon.svg"] = svg(512, 512, "Portcullis icon", icon("i"))
files["portcullis-status-online.svg"] = svg(512, 512, "Portcullis: server online", icon("on", light=GREEN))
files["portcullis-status-offline.svg"] = svg(
    512, 512, "Portcullis: server offline", icon("off", lowered=True, light=RED)
)

lk, lw = lockup("l", WHITE, GREY, 0, 0, 128, 92)
files["portcullis-logo.svg"] = svg(int(lw) + 4, 132, "Portcullis logo", lk)
lk, lw = lockup("ld", NAVY, "#4A5670", 0, 0, 128, 92)
files["portcullis-logo-dark.svg"] = svg(int(lw) + 4, 132, "Portcullis logo (for light backgrounds)", lk)

# 1280x640 banner (GitHub social preview, release posts)
lk, lw = lockup("b", WHITE, GREY, 0, 0, 200, 140, tagline=TAG, tag_size=38)
bx = (1280 - lw) / 2
body = backdrop(1280, 640, "b", 640, 260)
body += f'<g transform="translate({bx:.1f} 150)">{lk}</g>'
pill_svg = pills(FEATURES, 0, 0, 22)
# measure pills width by re-running layout
total = sum(text_path("roboto.woff2", f, 22, 0, 0)[1] + 22 * 1.8 for f in FEATURES) + 22 * 0.7 * (len(FEATURES) - 1)
body += f'<g transform="translate({(1280 - total) / 2:.1f} 470)">{pill_svg}</g>'
files["portcullis-banner.svg"] = svg(1280, 640, "Portcullis banner", body)

# 680x240 Discord profile banner: the avatar covers the bottom-left, so the
# lockup sits right of centre and the left third stays plain.
lk, lw = lockup("d", WHITE, GREY, 0, 0, 84, 60, tagline=TAG, tag_size=17)
body = backdrop(680, 240, "d", 470, 110)
body += f'<g transform="translate({680 - lw - 48:.1f} 52)">{lk}</g>'
files["portcullis-discord-banner.svg"] = svg(680, 240, "Portcullis Discord profile banner", body)

# Fallbacks for games with no art of their own (see docs/GAME_ASSETS.md). No text, so one
# image serves every game; the embed title carries the game's name. The banner is a Steam
# header at 2x (920x430), so a fallback sits the same size as real game banners in embeds.
body = backdrop(920, 430, "gb", 460, 215) + place(mark("gb"), 460 - 110, 215 - 118, 220)
files["portcullis-game-banner.svg"] = svg(920, 430, "Game banner placeholder", body)
files["portcullis-game-logo.svg"] = svg(512, 512, "Game logo placeholder", icon("gl", light=GREY))

for name, content in files.items():
    (OUT / name).write_text(content)
print("written", sorted(files))
