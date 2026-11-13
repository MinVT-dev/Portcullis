"""Portcullis brand v3: the gate tower. Text is converted to outlines.

Usage:
    python3 generate.py OUT_DIR FONT_DIR          # SVGs only
    python3 generate.py OUT_DIR FONT_DIR --png    # also png/, preview.png and the bot's fallback art

FONT_DIR holds the variable fonts from github.com/google/fonts, as named there:
``Sora[wght].ttf`` (ofl/sora) and ``Roboto[wdth,wght].ttf`` (ofl/roboto). Needs ``fonttools``;
``--png`` also needs ``cairosvg``. Run it from the repo root with OUT_DIR = docs/branding.
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
PNG = "--png" in sys.argv[3:]
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- palette
NAVY = "#0F1629"  # background and the gate opening
NAVY_2 = "#18223F"  # lifted background
WHITE = "#EEF3FB"  # the gate, text
GREY = "#A9B4CB"  # secondary text
LINE = "#8CAADC"  # hairlines, pill borders (low opacity)
SKY = "#5CE1FF"  # tower gradient, top
BLUE = "#4C6BFF"  # tower gradient, bottom
CYAN = "#4AD6FF"  # brand light under the gate
GREEN = "#4ADE80"  # online
RED = "#FF5A5F"  # offline
STONE = ("#C3CBDA", "#7D879C")  # neutral tower for the game placeholder

# ---------------------------------------------------------------- the mark
# 64-unit grid. A gate tower: a solid block with three merlons, a pointed arch cut through it,
# and the portcullis hanging inside the arch with a 2.5-unit gap all round so it reads as its own
# piece at 32 px. Raised, light shows under its spikes (cyan for the brand, green for online).
# Lowered, the spikes reach the ground and a red sill shows: offline.
L, R, TOP, NOTCH, BASE = 9.0, 55.0, 7.0, 13.0, 58.0
MERLON, GAP_X = 10.0, 6.5
ARCH = (20.0, 44.0, 35.0, 17.0)  # x0, x1, springline, radius of each arc
INSET = 2.5
BAR_W, BAR_X = 4.0, (25.5, 32.0, 38.5)
RAIL_Y, RAIL_H = 31.0, 4.0
SPIKE_TOP, SPIKE_TIP = 43.0, 47.5
DROP = 9.5  # how far the lowered gate drops: its tips then touch the ground


def arch(x0, x1, spring, r, base=BASE):
    """A pointed (two-centred) arch from the ground up. Returns the SVG path."""
    mid = (x0 + x1) / 2
    rise = math.sqrt(r * r - (r - (mid - x0)) ** 2)
    apex = spring - rise
    return f"M{x0} {base} V{spring} A{r} {r} 0 0 1 {mid} {apex:.3f} A{r} {r} 0 0 1 {x1} {spring} V{base} Z"


TOWER = (
    f"M{L} {BASE} V{TOP} H{L + MERLON} V{NOTCH} H{L + MERLON + GAP_X} V{TOP} "
    f"H{R - MERLON - GAP_X} V{NOTCH} H{R - MERLON} V{TOP} H{R} V{BASE} Z"
)
OPENING = arch(*ARCH)
INNER = arch(ARCH[0] + INSET, ARCH[1] - INSET, ARCH[2], ARCH[3] - INSET)


def mark(p, *, mono=None, light=None, lowered=False, stone=None):
    """The mark in 64-unit space. ``mono`` draws it in one colour with a see-through arch; otherwise the
    tower has the brand gradient (or ``stone``) and the arch is navy. ``p`` prefixes ids."""
    top, bottom = stone or (SKY, BLUE)
    defs = (
        f'<linearGradient id="{p}t" x1="0" y1="0" x2="0.35" y2="1">'
        f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>'
        f'<clipPath id="{p}a"><path d="{INNER}"/></clipPath>'
    )
    if light and not lowered:
        defs += (
            f'<linearGradient id="{p}l" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{light}" stop-opacity="0"/>'
            f'<stop offset="1" stop-color="{light}" stop-opacity="1"/></linearGradient>'
        )
    out = f"<defs>{defs}</defs>"
    out += f'<path fill-rule="evenodd" fill="{mono or f"url(#{p}t)"}" d="{TOWER} {OPENING}"/>'
    if not mono:
        out += f'<path fill="{NAVY}" d="{OPENING}"/>'
    d = DROP if lowered else 0.0
    gate = ""
    if light and not lowered:
        gate += (f'<rect x="{ARCH[0]}" y="{SPIKE_TOP}" width="{ARCH[1] - ARCH[0]}" height="{BASE - SPIKE_TOP}" '
                 f'fill="url(#{p}l)"/>')
    for cx in BAR_X:
        h = BAR_W / 2
        gate += f'<path d="M{cx - h} 0 H{cx + h} V{SPIKE_TOP + d} L{cx} {SPIKE_TIP + d} L{cx - h} {SPIKE_TOP + d} Z"/>'
    gate += f'<rect x="{ARCH[0]}" y="{RAIL_Y + d}" width="{ARCH[1] - ARCH[0]}" height="{RAIL_H}"/>'
    out += f'<g clip-path="url(#{p}a)" fill="{mono or WHITE}">{gate}</g>'
    if light and lowered:
        out += f'<rect x="{L}" y="{BASE + 1.5}" width="{R - L}" height="3" fill="{light}"/>'
    return out


# ---------------------------------------------------------------- text as paths
_fonts = {}


def font(name):
    if name not in _fonts:
        if name == "sora":
            _fonts[name] = instancer.instantiateVariableFont(TTFont(FONTS / "Sora[wght].ttf"), {"wght": 800})
        elif name == "roboto":
            _fonts[name] = instancer.instantiateVariableFont(TTFont(FONTS / "Roboto[wdth,wght].ttf"),
                                                             {"wght": 400, "wdth": 100})
        else:
            raise KeyError(name)
    return _fonts[name]


def text_path(face, text, size, x, y, tracking=0.0, fill=WHITE):
    """Outline ``text`` with its baseline at (x, y). Returns (svg, width)."""
    f = font(face)
    gs, cmap, hmtx = f.getGlyphSet(), f.getBestCmap(), f["hmtx"]
    scale = size / f["head"].unitsPerEm
    cursor, out = 0.0, []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x + cursor, y)))
        out.append(pen.getCommands())
        cursor += hmtx[name][0] * scale + tracking
    return f'<path fill="{fill}" d="{" ".join(out)}"/>', cursor - tracking


def svg(w, h, label, body, bg=None):
    rect = f'<rect width="{w}" height="{h}" fill="{bg}"/>' if bg else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{label}"><title>{label}</title>{rect}{body}</svg>\n'
    )


def place(inner, x, y, size):
    return f'<g transform="translate({x:.2f} {y:.2f}) scale({size / 64})">{inner}</g>'


# ---------------------------------------------------------------- assets
def icon(p, *, lowered=False, light=CYAN, stone=None, size=512):
    """Full-bleed square for avatars. Discord crops to a circle, so the mark stays inside the middle 66%."""
    return (
        f'<defs><radialGradient id="{p}bg" cx="0.5" cy="0.3" r="0.8">'
        f'<stop offset="0" stop-color="{NAVY_2}"/><stop offset="1" stop-color="{NAVY}"/></radialGradient></defs>'
        f'<rect width="{size}" height="{size}" fill="url(#{p}bg)"/>'
        + place(mark(p, lowered=lowered, light=light, stone=stone), size * 0.17, size * 0.15, size * 0.66)
    )


def lockup(p, fg, sub, x, y, mark_size, text_size, *, mono=None, tagline=None, tag_size=None):
    """Mark and wordmark side by side. Returns (svg, width)."""
    out = place(mark(p, mono=mono), x, y, mark_size)
    base = y + mark_size * (BASE / 64)
    tx = x + mark_size * (R + 7) / 64
    word, ww = text_path("sora", "Portcullis", text_size, tx, base - mark_size * 0.06,
                         tracking=-text_size * 0.01, fill=fg)
    out += word
    width = tx - x + ww
    if tagline:
        t, tw = text_path("roboto", tagline, tag_size, tx + text_size * 0.03, base + tag_size * 1.55, fill=sub)
        out += t
        width = max(width, tx - x + tw)
    return out, width


def dots(w, h, step, opacity):
    """A faint dot grid behind banners."""
    return (
        f'<defs><pattern id="dots{w}x{h}" width="{step}" height="{step}" patternUnits="userSpaceOnUse">'
        f'<circle cx="{step / 2}" cy="{step / 2}" r="1.2" fill="{LINE}" fill-opacity="{opacity}"/></pattern></defs>'
        f'<rect width="{w}" height="{h}" fill="url(#dots{w}x{h})"/>'
    )


def pills(labels, size):
    """Feature pills in a row. Returns (svg, width)."""
    out, cx = [], 0.0
    for label in labels:
        t, tw = text_path("roboto", label, size, 0, 0, fill=WHITE)
        pw, ph = tw + size * 1.8, size * 2.1
        out.append(
            f'<g transform="translate({cx:.1f} 0)"><rect width="{pw:.1f}" height="{ph:.1f}" rx="{ph / 2:.1f}" '
            f'fill="#FFFFFF" fill-opacity="0.05" stroke="{LINE}" stroke-opacity="0.35" stroke-width="1.5"/>'
            f'<g transform="translate({size * 0.9:.1f} {ph / 2 + size * 0.36:.1f})">{t}</g></g>'
        )
        cx += pw + size * 0.7
    return "".join(out), cx - size * 0.7


def backdrop(w, h, p, glow_x, glow_y):
    return (
        f'<defs><radialGradient id="{p}glow" cx="0.5" cy="0.5" r="0.5">'
        f'<stop offset="0" stop-color="{BLUE}" stop-opacity="0.30"/>'
        f'<stop offset="0.6" stop-color="{SKY}" stop-opacity="0.06"/>'
        f'<stop offset="1" stop-color="{SKY}" stop-opacity="0"/></radialGradient>'
        f'<linearGradient id="{p}bar" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0" stop-color="{SKY}"/><stop offset="1" stop-color="{BLUE}"/></linearGradient></defs>'
        f'<rect width="{w}" height="{h}" fill="{NAVY}"/>'
        + dots(w, h, 28, 0.16)
        + f'<ellipse cx="{glow_x}" cy="{glow_y}" rx="{w * 0.5}" ry="{h * 0.7}" fill="url(#{p}glow)"/>'
        + f'<rect y="{h - 5}" width="{w}" height="5" fill="url(#{p}bar)"/>'
    )


TAG = "Your game servers, in Discord."
FEATURES = ["Server status", "Join & leave alerts", "Chat bridge", "Backups"]

files = {}
files["portcullis-mark.svg"] = svg(64, 64, "Portcullis mark", mark("m"))
files["portcullis-mark-light.svg"] = svg(64, 64, "Portcullis mark, one colour (for dark backgrounds)",
                                         mark("ml", mono=WHITE))
files["portcullis-mark-dark.svg"] = svg(64, 64, "Portcullis mark, one colour (for light backgrounds)",
                                        mark("md", mono=NAVY))
files["portcullis-icon.svg"] = svg(512, 512, "Portcullis icon", icon("i"))
files["portcullis-status-online.svg"] = svg(512, 512, "Portcullis: server online", icon("on", light=GREEN))
files["portcullis-status-offline.svg"] = svg(512, 512, "Portcullis: server offline",
                                             icon("off", lowered=True, light=RED))

lk, lw = lockup("l", WHITE, GREY, 0, 2, 128, 96)
files["portcullis-logo.svg"] = svg(int(lw) + 4, 132, "Portcullis logo", lk)
lk, lw = lockup("ld", NAVY, "#4A5670", 0, 2, 128, 96)
files["portcullis-logo-dark.svg"] = svg(int(lw) + 4, 132, "Portcullis logo (for light backgrounds)", lk)

# 1280x640: GitHub social preview, release posts.
lk, lw = lockup("b", WHITE, GREY, 0, 0, 200, 150, tagline=TAG, tag_size=38)
body = backdrop(1280, 640, "b", 640, 250)
body += f'<g transform="translate({(1280 - lw) / 2:.1f} 140)">{lk}</g>'
pill_svg, pw = pills(FEATURES, 22)
body += f'<g transform="translate({(1280 - pw) / 2:.1f} 468)">{pill_svg}</g>'
files["portcullis-banner.svg"] = svg(1280, 640, "Portcullis banner", body)

# 680x240 Discord profile banner: the avatar covers the bottom left, so the lockup sits right of
# centre and the left third stays plain.
lk, lw = lockup("d", WHITE, GREY, 0, 0, 84, 62, tagline=TAG, tag_size=17)
body = backdrop(680, 240, "d", 480, 110)
body += f'<g transform="translate({680 - lw - 44:.1f} 50)">{lk}</g>'
files["portcullis-discord-banner.svg"] = svg(680, 240, "Portcullis Discord profile banner", body)

# Placeholders for games with no art (docs/GAME_ASSETS.md): no text, a neutral stone tower so they
# don't read as the bot's own avatar. The banner is a Steam header at 2x (920x430).
body = backdrop(920, 430, "gb", 460, 215) + place(mark("gb", stone=STONE), 460 - 115, 215 - 125, 230)
files["portcullis-game-banner.svg"] = svg(920, 430, "Game banner placeholder", body)
files["portcullis-game-logo.svg"] = svg(512, 512, "Game logo placeholder", icon("gl", light=None, stone=STONE))

for name, content in files.items():
    (OUT / name).write_text(content)
print("written", sorted(files))

if PNG:
    import cairosvg

    png_dir = OUT / "png"
    png_dir.mkdir(exist_ok=True)
    sizes = {
        "portcullis-icon": 1024, "portcullis-status-online": 256, "portcullis-status-offline": 256,
        "portcullis-banner": 1280, "portcullis-discord-banner": 1360, "portcullis-logo": 1200,
        "portcullis-logo-dark": 1200, "portcullis-mark": 512, "portcullis-mark-light": 512,
        "portcullis-mark-dark": 512,
    }
    for stem, width in sizes.items():
        cairosvg.svg2png(bytestring=files[stem + ".svg"].encode(), write_to=str(png_dir / f"{stem}.png"),
                         output_width=width)
    fallback = OUT.parent.parent / "portcullis" / "assets" / "fallback"
    if fallback.is_dir():
        cairosvg.svg2png(bytestring=files["portcullis-game-banner.svg"].encode(),
                         write_to=str(fallback / "game-banner.png"), output_width=920)
        cairosvg.svg2png(bytestring=files["portcullis-game-logo.svg"].encode(),
                         write_to=str(fallback / "game-logo.png"), output_width=512)

    def at(name, x, y, w):
        """Embed one generated SVG at (x, y), ``w`` wide. Returns (svg, height)."""
        inner = files[name].split(">", 1)[1].rsplit("</svg>", 1)[0]
        vb = files[name].split('viewBox="', 1)[1].split('"', 1)[0].split()
        s = w / float(vb[2])
        return f'<g transform="translate({x} {y}) scale({s:.5f})">{inner}</g>', float(vb[3]) * s

    # preview.png: every asset on one sheet.
    parts, y = [], 20
    g, h = at("portcullis-banner.svg", 20, y, 1280)
    parts.append(g)
    y += h + 20
    g, h2 = at("portcullis-discord-banner.svg", 20, y, 680)
    parts.append(g)
    for i, name in enumerate(("portcullis-icon.svg", "portcullis-status-online.svg",
                              "portcullis-status-offline.svg")):
        x, yy = 730, y + i * 82
        for s in (72, 48, 32, 16):
            inner, _ = at(name, x, yy + 36 - s / 2, s)
            parts.append(f'<defs><clipPath id="pc{i}{s}"><circle cx="{x + s / 2}" cy="{yy + 36}" r="{s / 2}"/>'
                         f'</clipPath></defs><g clip-path="url(#pc{i}{s})">{inner}</g>')
            x += s + 18
        label, _ = text_path("roboto", name[11:-4], 15, x + 4, yy + 41, fill=GREY)
        parts.append(label)
    y += max(h2, 3 * 82) + 20
    parts.append(f'<rect x="20" y="{y}" width="630" height="150" fill="{NAVY}"/>')
    parts.append(f'<rect x="670" y="{y}" width="630" height="150" fill="#FFFFFF"/>')
    for bx, logo, mono in ((20, "portcullis-logo.svg", "portcullis-mark-light.svg"),
                           (670, "portcullis-logo-dark.svg", "portcullis-mark-dark.svg")):
        for name, dx, dy, w in (("portcullis-mark.svg", 24, 31, 88), (mono, 128, 51, 48),
                                ("portcullis-mark.svg", 192, 59, 32), (logo, 250, 52, 350)):
            g, _ = at(name, bx + dx, y + dy, w)
            parts.append(g)
    y += 170
    sheet = svg(1320, int(y), "Portcullis brand preview", "".join(parts), bg="#2B2D31")
    cairosvg.svg2png(bytestring=sheet.encode(), write_to=str(OUT / "preview.png"))
    print("rendered png/, preview.png and the fallback art")
