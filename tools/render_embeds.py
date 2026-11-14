# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mock-ups of Portcullis embeds, drawn from the real embed code with sample data, as Discord-like HTML.

Every server, player, address and number here is made up, and each picture says so in its caption. Game
logos are drawn as the bot's own placeholder, so no game art ends up in the repository.

Usage, from the repo root:
    python3 tools/render_embeds.py /tmp/embeds_html
    NODE_PATH=$(npm root -g) node tools/screenshot_embeds.js /tmp/embeds_html docs/images/embeds
"""

from __future__ import annotations

import asyncio
import base64
import html
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portcullis.bot import admin as admin_module  # noqa: E402
from portcullis.bot.admin import AdminModule  # noqa: E402
from portcullis.bot.cards import diagnostics_embed, link_buttons, status_embed  # noqa: E402
from portcullis.bot.emojis import EMOJI, SET, Emojis  # noqa: E402
from portcullis.bot.refresh import Pacer  # noqa: E402
from portcullis.bot.setup import SetupModule  # noqa: E402
from portcullis.core.live import Summary  # noqa: E402
from portcullis.core.types import DiskUsage, Player, Schedule, ServerId, ServerState, ServerStatus  # noqa: E402
from portcullis.db.repos import ServerRow  # noqa: E402

NOW = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)
EMOJI_DIR = ROOT / "docs" / "branding" / "emoji"
AVATAR = ROOT / "docs" / "branding" / "png" / "portcullis-avatar-512.png"
PLACEHOLDER = ROOT / "portcullis" / "assets" / "fallback" / "game-logo.png"
CHANNELS = {55: "server-status", 56: "alerts"}


def data_uri(path: Path) -> str:
    kind = "svg+xml" if path.suffix == ".svg" else "png"
    return f"data:image/{kind};base64,{base64.b64encode(path.read_bytes()).decode()}"


def row(name: str, template: str, shard: str) -> ServerRow:
    return ServerRow(shard_id=ServerId(shard), amp_name=name, display_name=None, template=template, hidden=False,
                     console_channel=None, chat_channel=None, event_channel=None, discord_role=None, prefix=None,
                     gone_at=None)


def uploaded() -> Emojis:
    """The emoji set as if every PNG were uploaded, with made-up ids the page swaps for the images."""
    emojis = Emojis()
    emojis.load(SimpleNamespace(name=name, id=1000 + i, animated=False) for i, (name, _f) in enumerate(SET.values()))
    return emojis


# ------------------------------------------------------------------------------ Discord markdown, just enough


def relative(ts: int) -> str:
    delta = ts - NOW.timestamp()
    span = abs(delta)
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if span >= size:
            n = round(span / size)
            words = f"{n} {unit}{'' if n == 1 else 's'}"
            return f"in {words}" if delta > 0 else f"{words} ago"
    return "just now"


def markdown(text: str) -> str:
    blocks: list[str] = []

    def keep(m: re.Match[str]) -> str:
        blocks.append(f"<pre>{html.escape(m.group(1).strip(chr(10)))}</pre>")
        return f"\x00{len(blocks) - 1}\x00"

    text = re.sub(r"```(?:\w*\n)?(.*?)```", keep, text, flags=re.S)
    out = html.escape(text)
    out = re.sub(r"&lt;a?:(\w+):\d+&gt;",
                 lambda m: f'<img class="emoji" src="{data_uri(EMOJI_DIR / (m.group(1) + ".svg"))}" alt="">', out)
    out = re.sub(r"&lt;t:(\d+):R&gt;", lambda m: f'<span class="ts">{relative(int(m.group(1)))}</span>', out)
    out = re.sub(r"&lt;#(\d+)&gt;", lambda m: f'<span class="mention">#{CHANNELS.get(int(m.group(1)), "channel")}'
                                             "</span>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\\([_*~|`>\\])", r"\1", out)
    out = out.replace("\n", "<br>")
    return re.sub(r"\x00(\d+)\x00", lambda m: blocks[int(m.group(1))], out)


# ------------------------------------------------------------------------------ one message


def embed_html(embed: discord.Embed) -> str:
    d = embed.to_dict()
    colour = f"#{d.get('color', 0x8C857E):06X}"
    parts = []
    if "author" in d:
        parts.append(f'<div class="author">{html.escape(d["author"]["name"])}</div>')
    if "title" in d:
        parts.append(f'<div class="title">{markdown(d["title"])}</div>')
    if d.get("description"):
        parts.append(f'<div class="desc">{markdown(d["description"])}</div>')
    if d.get("fields"):
        cells, inline_run = [], 0
        for f in d["fields"]:
            inline = f.get("inline", False)
            inline_run = inline_run + 1 if inline else 0
            span = "inline" if inline else "full"
            cells.append(f'<div class="field {span}"><div class="fname">{markdown(f["name"])}</div>'
                         f'<div class="fvalue">{markdown(f["value"])}</div></div>')
        parts.append(f'<div class="fields">{"".join(cells)}</div>')
    footer = []
    if "footer" in d:
        footer.append(html.escape(d["footer"]["text"]))
    if "timestamp" in d:
        footer.append("Today at 20:00")
    if footer:
        parts.append(f'<div class="footer">{" • ".join(footer)}</div>')
    thumb = f'<img class="thumb" src="{data_uri(PLACEHOLDER)}" alt="">' if "thumbnail" in d else ""
    return (f'<div class="embed" style="border-left-color:{colour}"><div class="body">{"".join(parts)}</div>'
            f"{thumb}</div>")


def buttons_html(view: discord.ui.View | None) -> str:
    if view is None:
        return ""
    items = "".join(f'<span class="button">{html.escape(b.label or "")} <span class="ext">↗</span></span>'
                    for b in view.children if isinstance(b, discord.ui.Button))
    return f'<div class="buttons">{items}</div>'


def message(embed: discord.Embed, view: discord.ui.View | None = None, *, command: str | None = None) -> str:
    used = f'<div class="used"><span class="slash">used</span> {html.escape(command)}</div>' if command else ""
    return (f'<div class="message">{used}<img class="avatar" src="{data_uri(AVATAR)}" alt="">'
            f'<div class="content"><div class="head"><span class="name">Portcullis</span><span class="app">APP'
            f'</span><span class="when">Today at 20:00</span></div>{embed_html(embed)}{buttons_html(view)}'
            "</div></div>")


STYLE = """
body{margin:0;background:#313338;font-family:"gg sans","Noto Sans","DejaVu Sans",sans-serif;color:#DBDEE1}
#shot{width:640px;padding:16px 0 18px}
.caption{margin:0 16px 10px;padding:6px 10px;border-radius:4px;background:#2B2D31;font-size:12px;color:#B5BAC1}
.caption b{color:#F2B33D}
.message{position:relative;padding:4px 16px 4px 72px;margin-top:10px}
.used{font-size:13px;color:#949BA4;margin:0 0 4px}
.slash{color:#949BA4}
.avatar{position:absolute;left:16px;width:40px;height:40px;border-radius:50%}
.head{display:flex;align-items:baseline;gap:6px}
.name{font-weight:600;color:#F2F3F5;font-size:16px}
.app{background:#5865F2;color:#fff;font-size:10px;font-weight:600;padding:1px 4px;border-radius:3px}
.when{font-size:12px;color:#949BA4}
.embed{display:flex;max-width:520px;margin-top:4px;background:#2B2D31;border-left:4px solid;border-radius:4px;
  padding:8px 16px 16px 12px;gap:16px}
.body{flex:1;min-width:0}
.author{font-size:14px;font-weight:600;color:#F2F3F5;margin-top:8px}
.title{font-size:16px;font-weight:600;color:#F2F3F5;margin-top:8px}
.desc{font-size:14px;line-height:1.375;margin-top:8px}
.fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px 12px;margin-top:8px}
.field.full{grid-column:1/-1}
.fname{font-size:14px;font-weight:600;color:#F2F3F5;margin-bottom:2px}
.fvalue{font-size:14px;line-height:1.375}
.footer{font-size:12px;color:#B5BAC1;margin-top:8px}
.thumb{width:80px;height:80px;object-fit:contain;border-radius:3px;margin-top:8px;flex:none}
pre{background:#1E1F22;border:1px solid #232428;border-radius:4px;padding:7px;margin:4px 0 0;font-size:13px;
  font-family:"DejaVu Sans Mono",monospace;white-space:pre-wrap;color:#DBDEE1}
code{background:#1E1F22;border-radius:3px;padding:0 3px;font-size:85%;font-family:"DejaVu Sans Mono",monospace}
.emoji{width:22px;height:22px;vertical-align:-5px}
.ts{background:#3F4147;border-radius:3px;padding:0 2px}
.mention{background:rgba(88,101,242,.3);color:#C9CDFB;border-radius:3px;padding:0 2px}
.buttons{display:flex;gap:8px;margin-top:8px}
.button{background:#4E5058;color:#fff;font-size:14px;font-weight:500;padding:6px 16px;border-radius:3px}
.ext{font-size:12px;opacity:.8}
"""


def page(caption: str, body: str) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{STYLE}</style></head><body>'
            f'<div id="shot"><div class="caption"><b>Mock-up with sample data.</b> {html.escape(caption)}</div>'
            f"{body}</div></body></html>")


# ------------------------------------------------------------------------------ the samples


VALHEIM = row("Valheim Vikings", "Valheim", "a1")
SURVIVAL = row("Survival", "Minecraft", "b2")
ENGINEERS = row("Space Station", "Space Engineers", "c3")


def players(*names: str) -> tuple[Player, ...]:
    return tuple(Player(name=n) for n in names)


def live_card(emojis: Emojis) -> str:
    status = ServerStatus(id=VALHEIM.shard_id, state=ServerState.RUNNING,
                          players=players("Ragnhild", "Torvald", "Sigrun"), max_players=10,
                          cpu_percent=23.4, memory_mb=3712, memory_limit_mb=8192,
                          started_at=NOW - timedelta(hours=5, minutes=12))
    restart = Schedule(id="s", name="Daily restart", enabled=True, next_fire=NOW + timedelta(hours=7))
    embed = status_embed(VALHEIM, status, "play.example.net:2456", restart=restart, every=180, emojis=emojis, now=NOW)
    return message(embed, link_buttons("https://panel.example.net", "https://discord.gg/example"))


def other_states(emojis: Emojis) -> str:
    offline = ServerStatus(id=SURVIVAL.shard_id, state=ServerState.STOPPED, max_players=20)
    starting = ServerStatus(id=ENGINEERS.shard_id, state=ServerState.BUSY, max_players=8,
                            message="Updating the game files")
    licence = ServerStatus(id=SURVIVAL.shard_id, state=ServerState.AWAITING_LICENCE, max_players=20)
    return "".join(message(status_embed(r, s, every=180, emojis=emojis, now=NOW))
                   for r, s in ((SURVIVAL, offline), (ENGINEERS, starting), (SURVIVAL, licence)))


def diagnostics(emojis: Emojis) -> str:
    status = ServerStatus(id=ENGINEERS.shard_id, state=ServerState.RUNNING, players=players("Kade"), max_players=8,
                          cpu_percent=71.0, memory_mb=11264, memory_limit_mb=12288,
                          started_at=NOW - timedelta(minutes=41),
                          last_error="World save took 38 s; the server missed 3 heartbeats")
    embed = diagnostics_embed(ENGINEERS, status, DiskUsage(size_mb=18_400, limit_mb=40_960), emojis=emojis)
    return message(embed, command="/server diagnostics")


def fake_ctx(emojis: Emojis, cards: int) -> Any:
    async def list_servers() -> list[Any]:
        await asyncio.sleep(0.04)  # a round trip, so the line shows a time
        return [object(), object(), object()]

    async def rows() -> list[Any]:
        return [(VALHEIM, 55, 1), (SURVIVAL, 55, 2), (ENGINEERS, 55, 3)][:cards]

    settings = {"alert_channel": 56, "live_every": 180}

    async def all_settings() -> dict[str, Any]:
        return settings

    async def servers() -> list[ServerRow]:
        return [VALHEIM, SURVIVAL, ENGINEERS]

    return SimpleNamespace(
        port=SimpleNamespace(list_servers=list_servers), client=SimpleNamespace(latency=0.062),
        live=SimpleNamespace(summary=lambda: Summary(servers=3, online=2, players=4, reachable=True), reachable=True),
        live_cards=SimpleNamespace(rows=rows, pacer=Pacer(180)), emojis=emojis, db=SimpleNamespace(version=12),
        settings=SimpleNamespace(all=all_settings), servers=SimpleNamespace(list=servers))


async def health(emojis: Emojis) -> str:
    admin_module.STARTED = time.monotonic() - (3 * 86400 + 4 * 3600)
    embed = await AdminModule(fake_ctx(emojis, cards=3)).health_embed()
    return message(embed, command="/health")


async def setup_start(emojis: Emojis) -> str:
    setup = SetupModule(fake_ctx(emojis, cards=0))

    async def no_problems(_interaction: Any) -> list[str]:
        return []

    setup.check_setup = no_problems  # type: ignore[method-assign]
    embed = await setup.wizard_embed(SimpleNamespace())  # type: ignore[arg-type]
    return message(embed, command="/setup start")


async def main(out: Path) -> None:
    brand, plain = uploaded(), Emojis()
    EMOJI.found = brand.found  # state_text reads the shared set
    pages = {
        "live-card": ("A live status card. It edits itself every 3 minutes; Panel and Support are link buttons.",
                      live_card(brand)),
        "live-card-states": ("The same card offline, busy and waiting for the licence (EULA).", other_states(brand)),
        "diagnostics": ("/server diagnostics for one server.", diagnostics(brand)),
        "health": ("/health: one line per part of Portcullis.", await health(brand)),
        "setup-start": ("/setup start on a new install, emojis not uploaded yet. The selects and buttons under "
                        "it are not drawn.", await setup_start(plain)),
    }
    EMOJI.found = {}
    pages["live-card-fallback"] = ("The live card before the brand emojis are uploaded: plain emojis instead.",
                                   live_card(plain))
    for name, (caption, body) in pages.items():
        (out / f"{name}.html").write_text(page(caption, body), encoding="utf-8")
        print(out / f"{name}.html")


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "embeds_html")
    target.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(target))
