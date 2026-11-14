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

GATE_UTF8 = (
    "╔═╤═╤═╤═╗",
    "╟─┼─┼─┼─╢",
    "╟─┼─┼─┼─╢",
    "║ ╵ ╵ ╵ ║",
)
GATE_ASCII = (
    "+-+-+-+-+",
    "|-+-+-+-|",
    "|-+-+-+-|",
    "| ' ' ' |",
)


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
        "╔═╤║╵·".encode(encoding)
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
    gate = GATE_UTF8 if unicode else GATE_ASCII
    side = [c(ORANGE, "PORTCULLIS") + f" {info.version}", "Discord bot for AMP 3 panels", "", ""]
    lines = [""] + [f"  {c(ORANGE, g)}   {s}".rstrip() for g, s in zip(gate, side, strict=True)] + [""]

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
