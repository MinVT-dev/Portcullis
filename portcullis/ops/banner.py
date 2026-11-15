# SPDX-License-Identifier: AGPL-3.0-or-later
"""The welcome banner printed when the bot starts (and by ``--check``).

It shows the version, whether the panel answered, a summary of the settings with every secret
left out, which optional features are on, and how the last run ended. Brand orange is used where
the console supports colour. Consoles that can't show box-drawing characters (an old Windows code
page such as cp437 or cp1252) get a plain ASCII version, and ``NO_COLOR`` or a redirected output
drops the colour. ``PORTCULLIS_BANNER=off`` turns it off.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TextIO
from urllib.parse import urlsplit

from portcullis.config import Settings
from portcullis.ops.logs import supports_colour
from portcullis.ops.settings import OpsSettings

ORANGE = "\x1b[38;2;232;113;10m"  # brand orange #E8710A
DIM = "\x1b[2m"
GREEN = "\x1b[38;2;74;222;128m"
AMBER = "\x1b[38;2;242;179;61m"
RESET = "\x1b[0m"

GREY = "\x1b[38;2;92;87;82m"  # the mark's charcoal card #5C5752
BOLD = "\x1b[1m"

# The brand mark ("Inside out"): a lowercase p folded from a card. '#' is the charcoal card, 'o' the
# orange folds (the turned corner top right and the tail), '.' is empty. Two pixel rows become one
# text row with the half blocks, so the five lines below are ten pixels tall.
MARK = (
    "#######o.",
    "#######oo",
    "###...###",
    "###...###",
    "#########",
    "#########",
    "###......",
    "ooo......",
    ".ooo.....",
    "..ooo....",
)
MARK_WIDTH = len(MARK[0])


def mark_lines(*, unicode: bool = True, colour: bool = False) -> list[str]:
    """The brand mark as five text rows, all ``MARK_WIDTH`` cells wide (trailing blanks kept).

    Colour: charcoal and orange, with the half blocks carrying both colours where a fold meets the card.
    Without colour the folds are shaded (``▒``) so they still read as folds. Without Unicode it is
    plain ASCII: ``#`` for the card and ``+`` for the folds.
    """
    rows: list[str] = []
    for top_row, bottom_row in zip(MARK[::2], MARK[1::2], strict=True):
        line = ""
        for top, bottom in zip(top_row, bottom_row, strict=True):
            pair = {top, bottom} - {"."}
            if not unicode:
                line += " " if not pair else ("#" if "#" in pair else "+")
            elif not pair:
                line += " "
            elif colour:
                line += _coloured_cell(top, bottom)
            elif "o" in pair:
                line += "▓" if "#" in pair else "▒"
            else:
                line += "█" if top == bottom else ("▀" if top == "#" else "▄")
        rows.append(line)
    return rows


_COLOUR = {"#": (92, 87, 82), "o": (232, 113, 10)}


def _coloured_cell(top: str, bottom: str) -> str:
    def fg(pixel: str) -> str:
        r, g, b = _COLOUR[pixel]
        return f"\x1b[38;2;{r};{g};{b}m"

    def bg(pixel: str) -> str:
        r, g, b = _COLOUR[pixel]
        return f"\x1b[48;2;{r};{g};{b}m"

    if top == bottom:
        return f"{fg(top)}█{RESET}"
    if bottom == ".":
        return f"{fg(top)}▀{RESET}"
    if top == ".":
        return f"{fg(bottom)}▄{RESET}"
    return f"{fg(top)}{bg(bottom)}▀{RESET}"


@dataclass
class BootInfo:
    version: str
    panel: str  # "reachable", or a short reason it isn't
    panel_ok: bool
    last_run: str
    warnings: list[str] = field(default_factory=list)


def panel_address(url: str) -> str:
    """The panel's scheme, host and port, without any user:password@ someone put in the URL."""
    parts = urlsplit(url)
    host = parts.hostname or "?"
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        port = ""
    return f"{parts.scheme}://{host}{port}"


def can_draw(stream: TextIO) -> bool:
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "╔═╤║╵·█▀▄▒▓".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def features(ops: OpsSettings) -> list[str]:
    on = ["live cards", f"panel guard ({ops.panel_retries} retries, pause after {ops.panel_trip_after} failures)"]
    on.append("restart recovery" if ops.state_file else "restart recovery off")
    on.append(f"metrics on {ops.metrics_host}:{ops.metrics_port}" + (" with token" if ops.metrics_token else "")
              if ops.metrics else "metrics off")
    on.append("update check (notify only)" if ops.update_check == "notify" else "update check off")
    return on


def render(settings: Settings, ops: OpsSettings, info: BootInfo, *, unicode: bool = True,
           colour: bool = False) -> str:
    def c(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if colour else text

    dot = "·" if unicode else "-"
    mark = mark_lines(unicode=unicode, colour=colour)
    side = ["", c(BOLD, "portcullis") + " " + c(DIM, info.version), "Discord bot for AMP 3 panels", "", ""]
    lines = [""] + [f"  {m}   {t}".rstrip() for m, t in zip(mark, side, strict=True)] + [""]

    owners = len(settings.owner_ids)
    roles = len(settings.operator_role_ids)
    panel_state = c(GREEN, info.panel) if info.panel_ok else c(AMBER, info.panel)
    discord_line = (f"server {settings.guild_id} {dot} {owners} owner{'s' * (owners != 1)} {dot} "
                    f"{roles} operator role{'s' * (roles != 1)} {dot} "
                    + ("token set" if settings.discord_token else "no token"))
    logs = (f"{ops.log_file} ({ops.log_format}, {ops.log_max_mb} MB x {ops.log_keep + 1} files)"
            if ops.log_file else "console only")
    rows = [
        ("Panel", f"{panel_address(settings.amp_url)} {dot} user {settings.amp_username} {dot} {panel_state}"),
        ("Discord", discord_line),
        ("Database", str(settings.database)),
        ("Logs", f"{settings.log_level} {dot} {logs}"),
        ("Last run", info.last_run),
        ("Features", ", ".join(features(ops))),
    ]
    lines += [f"  {c(DIM, f'{name:<9}')} {value}" for name, value in rows]
    for warning in info.warnings:
        lines.append(f"  {c(AMBER, 'Warning  ')} {warning}")
    lines.append("")
    return "\n".join(lines)


def show(settings: Settings, ops: OpsSettings, info: BootInfo, stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    if not ops.banner:
        return
    text = render(settings, ops, info, unicode=can_draw(stream), colour=supports_colour(stream))
    try:
        print(text, file=stream, flush=True)
    except (UnicodeEncodeError, OSError):  # a console that misreports its encoding: plain ASCII once
        try:
            print(render(settings, ops, info, unicode=False), file=stream, flush=True)
        except (UnicodeEncodeError, OSError):
            pass
