# SPDX-License-Identifier: AGPL-3.0-or-later
"""Draws the brand emoji set as SVG into docs/branding/emoji/. Render the PNGs with render_emoji.js.

Two families, both from the v4 mark:
- Server states are the folded p itself, with the folds recoloured: green online, red offline,
  mustard while it changes. Starting and stopping share mustard and differ by an arrow badge, so the
  colour says "in progress" and the arrow says which way.
- Everything else is a sheet of the same charcoal card with its top corner turned down in orange (the
  mark's corner fold), carrying one cream glyph. "Needs licence" turns the corner mustard, because it
  waits on a person.

Each emoji sits on a near-black tile, like the app icon. The charcoal card is too close to Discord's
dark-theme grey to read on its own at 22 px; on the tile it reads the same on dark and light. The names
match portcullis/bot/emojis.py; Portcullis finds them by name.

Usage, from the repo root: python3 docs/branding/emoji.py && node docs/branding/render_emoji.js
"""

from __future__ import annotations

from pathlib import Path

CARD = "#5C5752"
NIGHT = "#141414"
CREAM = "#EDE6DC"
ORANGE = "#E8710A"
GREEN = "#4ADE80"
RED = "#FF5A5F"
MUSTARD = "#F2B33D"

OUT = Path(__file__).parent / "emoji"

# The mark on its 64-unit grid, as in generate.py.
P_BODY = '<path d="M14 11 H42 L50 19 V37 H26 V41 H14 Z M26 21 V29 H38 V21 Z" fill="{card}" fill-rule="evenodd"/>'
P_CORNER = '<path d="M42 11 L50 19 H42 Z" fill="{fold}"/>'
P_TAIL = '<path d="M14 41 H26 L34 53 H22 Z" fill="{fold}"/>'

SHEET = '<path d="M8 8 H42 L56 22 V56 H8 Z" fill="{card}"/><path d="M42 8 L56 22 H42 Z" fill="{fold}"/>'
STROKE = f'fill="none" stroke="{CREAM}" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"'


def svg(name: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 64 64" role="img" '
            f'aria-label="{name}"><title>{name}</title>{body}</svg>\n')


TILE = f'<rect width="64" height="64" rx="14" fill="{NIGHT}"/>'


def scaled(body: str, factor: float) -> str:
    return f'<g transform="translate(32 32) scale({factor}) translate(-32 -32)">{body}</g>'


def mark(fold: str) -> str:
    return TILE + scaled((P_BODY + P_CORNER + P_TAIL).format(card=CARD, fold=fold), 1.12)


def badge(up: bool) -> str:
    """A mustard disc at the lower right with a dark arrow; the night ring parts it from the mark."""
    arrow = "M50 43 V57 M44.5 48.5 L50 43 L55.5 48.5" if up else "M50 43 V57 M44.5 51.5 L50 57 L55.5 51.5"
    return (f'<circle cx="50" cy="50" r="11.5" fill="{MUSTARD}" stroke="{NIGHT}" stroke-width="2.5"/>'
            f'<path d="{arrow}" fill="none" stroke="{NIGHT}" stroke-width="3.5" stroke-linecap="round" '
            'stroke-linejoin="round"/>')


def sheet(glyph: str, fold: str = ORANGE) -> str:
    return TILE + scaled(SHEET.format(card=CARD, fold=fold) + glyph, 0.86)


GLYPHS = {
    # >_ : a prompt, the console
    "console": f'<path d="M17 29 L25 36 L17 43 M29 45 H42" {STROKE}/>',
    # an arrow into a tray: a copy put away
    "backup": f'<path d="M32 25 V41 M25 34.5 L32 41 L39 34.5 M17 41 V48 H47 V41" {STROKE}/>',
    # two people, the nearer one in cream, the further one in the orange of the fold
    "players": (f'<circle cx="38" cy="30" r="5" fill="{ORANGE}"/>'
                f'<path d="M29 50 a9 8.5 0 0 1 18 0 Z" fill="{ORANGE}"/>'
                f'<circle cx="25" cy="33" r="6" fill="{CREAM}" stroke="{CARD}" stroke-width="2"/>'
                f'<path d="M14 52 a11 10 0 0 1 22 0 Z" fill="{CREAM}" stroke="{CARD}" stroke-width="2"/>'),
    # lines of text on a sheet: the licence that waits for someone
    "licence": f'<path d="M17 29 H35 M17 37 H45 M17 45 H39" {STROKE}/>',
}

EMOJIS = {
    "portcullis_online": mark(GREEN),
    "portcullis_offline": mark(RED),
    "portcullis_starting": mark(MUSTARD) + badge(up=True),
    "portcullis_stopping": mark(MUSTARD) + badge(up=False),
    "portcullis_licence": sheet(GLYPHS["licence"], fold=MUSTARD),
    "portcullis_backup": sheet(GLYPHS["backup"]),
    "portcullis_console": sheet(GLYPHS["console"]),
    "portcullis_players": sheet(GLYPHS["players"]),
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, body in EMOJIS.items():
        (OUT / f"{name}.svg").write_text(svg(name, body), encoding="utf-8")
        print(f"{OUT / name}.svg")


if __name__ == "__main__":
    main()
