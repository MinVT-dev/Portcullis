# SPDX-License-Identifier: AGPL-3.0-or-later
"""The embeds that describe a server: the status card (``/server status`` and the live card in a
channel) and the diagnostics card. Kept apart from the commands so the live card, the commands and
``tools/render_embeds.py`` (the README mock-ups) all draw the same thing.

Layout, top to bottom: the game as the author line, the server name, one status line in words with
the brand emoji, then numbers in short inline fields, then anything that needs a sentence. The colour
bar follows the state, the same green, red and mustard the folds of the mark take.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

import discord

from portcullis.assets import game_art
from portcullis.bot.base import MUTED, STATE_COLOURS, card, state_text
from portcullis.bot.emojis import EMOJI, Emojis
from portcullis.bot.refresh import minutes
from portcullis.core.relay import escape
from portcullis.core.types import DiskUsage, Schedule, ServerState, ServerStatus
from portcullis.db.repos import ServerRow

PANEL_URL, SUPPORT_URL = "panel_url", "support_url"  # bot settings: link buttons under live cards
LICENCE_TEXT = ("AMP is holding this server until someone accepts the game's licence (EULA). "
                "An admin can accept it from `/server status`.")


def next_restart(schedules: Iterable[Schedule], now: datetime) -> Schedule | None:
    """The soonest enabled schedule with "restart" in its name. AMP doesn't mark a schedule's purpose,
    so the name is the only clue; one called "Nightly reboot" won't be found."""
    due = [s for s in schedules if s.enabled and s.next_fire is not None and s.next_fire > now
           and "restart" in s.name.lower()]
    return min(due, key=lambda s: s.next_fire) if due else None  # type: ignore[arg-type,return-value]


def players_text(status: ServerStatus, emojis: Emojis = EMOJI) -> str:
    count = f"{status.player_count}" + (f" / {status.max_players}" if status.max_players else "")
    return f"{emojis['players']} {count}"


def bar(used: float, limit: float, width: int = 10) -> str:
    """A text bar for a share, e.g. ``▰▰▰▱▱▱▱▱▱▱ 31%``. The number is always there too."""
    share = 0.0 if limit <= 0 else max(0.0, min(1.0, used / limit))
    filled = int(share * width + 0.5)  # half up, so 25% of 10 shows 3, not 2
    return "▰" * filled + "▱" * (width - filled) + f" {share * 100:.0f}%"


def status_embed(row: ServerRow, status: ServerStatus, address: str | None = None, *,
                 restart: Schedule | None = None, every: float | None = None, going_down: bool = False,
                 emojis: Emojis = EMOJI, now: datetime | None = None) -> discord.Embed:
    """``every``: this is a live card refreshed every so many seconds (it says so in the footer)."""
    state = status.state
    embed = card(title=row.name, colour=STATE_COLOURS.get(state, MUTED))
    art = game_art(row.template, name=row.amp_name)
    embed.set_author(name=art.name)
    embed.add_field(name="State", value=state_text(state, going_down=going_down))
    embed.add_field(name="Players", value=players_text(status, emojis))
    if status.started_at is not None and state is ServerState.RUNNING:
        embed.add_field(name="Up since", value=discord.utils.format_dt(status.started_at, "R"))
    if restart is not None and restart.next_fire is not None:
        embed.add_field(name="Next restart", value=discord.utils.format_dt(restart.next_fire, "R"))
    if state is ServerState.RUNNING:
        if status.cpu_percent is not None:
            embed.add_field(name="CPU", value=f"{status.cpu_percent:.0f}%")
        if status.memory_mb is not None:
            limit = f" / {status.memory_limit_mb} MB" if status.memory_limit_mb else " MB"
            embed.add_field(name="Memory", value=f"{status.memory_mb}{limit}")
    if address:
        embed.add_field(name="Connect", value=f"```\n{address}\n```", inline=False)
    if status.players:
        names = ", ".join(escape(p.name) for p in status.players)
        embed.add_field(name="Online now", value=names if len(names) <= 1000 else names[:999] + "…", inline=False)
    if state is ServerState.AWAITING_LICENCE:
        embed.description = f"{emojis['licence']} {LICENCE_TEXT}"
    elif status.message:
        embed.add_field(name="Message", value=escape(status.message)[:1000], inline=False)
    if art.thumbnail_url:
        embed.set_thumbnail(url=art.thumbnail_url)
    if every is not None:
        embed.set_footer(text=f"Updates every {minutes(every)}")
        embed.timestamp = now or discord.utils.utcnow()
    else:
        embed.set_footer(text=f"Server id {row.shard_id}")
    return embed


def diagnostics_embed(row: ServerRow, status: ServerStatus, disk: DiskUsage | None, *,
                      emojis: Emojis = EMOJI) -> discord.Embed:
    """Resources and the last error for one server, for the people who look after it."""
    state = status.state
    embed = card(title=f"Diagnostics: {row.name}", colour=STATE_COLOURS.get(state, MUTED))
    embed.set_author(name=game_art(row.template, name=row.amp_name).name)
    embed.add_field(name="State", value=state_text(state))
    embed.add_field(name="Players", value=players_text(status, emojis))
    if status.started_at is not None and state is ServerState.RUNNING:
        embed.add_field(name="Up since", value=discord.utils.format_dt(status.started_at, "R"))
    if status.cpu_percent is not None:
        embed.add_field(name="CPU", value=bar(status.cpu_percent, 100), inline=False)
    if status.memory_mb is not None:
        if status.memory_limit_mb:
            memory = f"{bar(status.memory_mb, status.memory_limit_mb)} · {status.memory_mb} of " \
                     f"{status.memory_limit_mb} MB"
        else:
            memory = f"{status.memory_mb} MB (no limit set)"
        embed.add_field(name="Memory", value=memory, inline=False)
    if disk is not None and disk.size_mb is not None:
        if disk.limit_mb:
            used = f"{bar(disk.size_mb, disk.limit_mb)} · {disk.size_mb} of {disk.limit_mb} MB"
        else:
            used = f"{disk.size_mb} MB (no limit set)"
        embed.add_field(name="Disk", value=used, inline=False)
    if state is ServerState.AWAITING_LICENCE:
        embed.add_field(name="Licence", value=f"{emojis['licence']} Waiting for someone to accept the game's "
                                              "licence (EULA)", inline=False)
    if status.last_error:
        embed.add_field(name="Last error", value=f"```\n{status.last_error[:900].replace('```', 'ˋˋˋ')}\n```",
                        inline=False)
    elif state is ServerState.FAILED:
        embed.add_field(name="Last error", value="AMP marked it failed but gave no reason.", inline=False)
    missing = [name for name, value in (("CPU", status.cpu_percent), ("memory", status.memory_mb),
                                        ("disk", disk.size_mb if disk else None)) if value is None]
    footer = f"Server id {row.shard_id}"
    if missing:
        footer += f" · AMP sent no {', '.join(missing)} figures"
    embed.set_footer(text=footer)
    return embed


def link_buttons(panel_url: object, support_url: object) -> discord.ui.View | None:
    """Panel and Support link buttons for a live card, or None when neither is set. Link buttons need
    no handler, so they keep working across restarts."""
    view = discord.ui.View(timeout=None)
    for label, url in (("Panel", panel_url), ("Support", support_url)):
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            view.add_item(discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link))
    return view if view.children else None
