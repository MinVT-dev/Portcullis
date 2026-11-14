# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mock-ups of the alert messages, graphs and weekly summary, drawn by the real code with made-up data.

Every server, player and number here is invented, and each picture says so in its caption.

Usage, from the repo root:
    python3 tools/render_monitoring.py /tmp/monitoring_html
    NODE_PATH=$(npm root -g) node tools/screenshot_embeds.js /tmp/monitoring_html docs/images/monitoring
"""

from __future__ import annotations

import asyncio
import base64
import html
import math
import random
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import discord

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portcullis.bot.alerting import Alert, render  # noqa: E402
from portcullis.bot.base import Access, Ctx  # noqa: E402
from portcullis.bot.commands import Modules  # noqa: E402
from portcullis.core.types import ServerId, ServerInfo  # noqa: E402
from portcullis.db.database import Database  # noqa: E402
from tests.fakes.amp import FakeAmpPort  # noqa: E402
from tools import render_embeds  # noqa: E402
from tools.render_embeds import AVATAR, STYLE, data_uri, embed_html  # noqa: E402

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)  # a Monday, when the weekly summary posts
STAMP = int(NOW.timestamp())
VALHEIM, SURVIVAL, STATION = "a1" * 16, "b2" * 16, "c3" * 16
EXTRA = ".image{display:block;max-width:100%;border-radius:4px;margin-top:12px}" \
        ".text{font-size:15px;line-height:1.375;margin-top:2px}.dm{font-size:12px;color:#949BA4;margin:0 0 4px}"


def markdown(text: str) -> str:
    """The shared Markdown mock, plus ``<t:N:t>`` and ``<t:N:f>`` shown as clock times."""
    def clock(m: re.Match[str]) -> str:
        when = datetime.fromtimestamp(int(m.group(1)), UTC)
        return when.strftime("%H:%M") if m.group(2) == "t" else when.strftime("Today at %H:%M")
    return BASE_MARKDOWN(re.sub(r"<t:(\d+):([tf])>", clock, text))


BASE_MARKDOWN = render_embeds.markdown
render_embeds.markdown = markdown  # embed_html uses it too


def png_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def message(content: str | None, embed: discord.Embed | None = None, *, image: bytes | None = None,
            note: str | None = None) -> str:
    body = embed_html(embed) if embed is not None else ""
    if image is not None and body:
        img = f'<img class="image" src="{png_uri(image)}" alt="">'
        body = body.replace('<div class="footer">', img + '<div class="footer">', 1) if 'class="footer"' in body \
            else body.replace("</div></div>", img + "</div></div>", 1)
    text = f'<div class="text">{markdown(content)}</div>' if content else ""
    above = f'<div class="dm">{html.escape(note)}</div>' if note else ""
    return (f'<div class="message">{above}<img class="avatar" src="{data_uri(AVATAR)}" alt="">'
            f'<div class="content"><div class="head"><span class="name">Portcullis</span><span class="app">APP'
            f'</span><span class="when">Today at 09:00</span></div>{text}{body}</div></div>')


def page(caption: str, body: str) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{STYLE}{EXTRA}</style></head><body>'
            f'<div id="shot"><div class="caption"><b>Mock-up with sample data.</b> {html.escape(caption)}</div>'
            f"{body}</div></body></html>")


def alert(*alerts: Alert, ping: bool = True, note: str | None = None) -> str:
    content, embed = render(list(alerts))
    if ping and any(a.ping for a in alerts):
        content = f"<@&1> {content}"
    return message(content.replace("<@&1>", "@Admins"), embed, note=note)


async def seed(ctx: Ctx, modules: Modules) -> None:
    rng = random.Random(7)
    monitor = modules.monitor
    start = NOW - timedelta(days=7)
    for step in range(7 * 24 * 12):  # 5-minute windows
        at = start + timedelta(minutes=5 * step)
        hour = at.hour + at.minute / 60
        evening = max(0.0, math.sin((hour - 13) / 12 * math.pi))
        for sid, scale, down in ((VALHEIM, 9, (2, 40, 46)), (SURVIVAL, 14, (4, 100, 112)), (STATION, 4, (None,))):
            online = not (down[0] is not None and at.weekday() == down[0] and down[1] <= (step % 288) < down[2])
            players = int(round(scale * evening * (1.3 if at.weekday() >= 5 else 1) + rng.random())) if online else 0
            cpu = 18 + 6 * players + rng.random() * 10 if online else None
            await monitor.history.add(sid, int(at.timestamp()) - int(at.timestamp()) % 300, samples=5,
                                      up=5 if online else 0, players_max=players, players_sum=players * 5,
                                      cpu_max=cpu, memory_max=(55 + players * 1.5) if online else None)
    for sid, kind, days_ago in ((SURVIVAL, "crash", 3), (SURVIVAL, "alert", 3), (SURVIVAL, "alert", 3),
                                (VALHEIM, "alert", 5), (SURVIVAL, "start", 3), (VALHEIM, "start", 5),
                                (STATION, "start", 6), (SURVIVAL, "start", 1)):
        await monitor.incidents.add(sid, kind, int((NOW - timedelta(days=days_ago)).timestamp()))


async def main(out: Path) -> None:
    db = await Database.open(":memory:")
    port = FakeAmpPort()
    for sid, name in ((VALHEIM, "Valheim Vikings"), (SURVIVAL, "Survival"), (STATION, "Space Station")):
        port.add_server(sid, name)
    ctx = Ctx(port, db, Access(), 1)
    modules = Modules(ctx)
    await ctx.servers.sync([ServerInfo(ServerId(s), n) for s, n in ((VALHEIM, "Valheim Vikings"),
                                                                    (SURVIVAL, "Survival"),
                                                                    (STATION, "Space Station"))])
    await seed(ctx, modules)
    monitor = modules.monitor
    crash = Alert(kind="crash", server=SURVIVAL, title="Crashed",
                  text="🔥 **Survival** crashed: AMP reports it as Failed.",
                  fields=(("When", f"<t:{STAMP - 240}:f>"),
                          ("AMP says", "The process exited with code 137 (out of memory).")))
    back = Alert(kind="recovered", server=SURVIVAL, severity="good", ping=False,
                 text="🟢 **Survival** is back online after 4m (it crashed).")
    offline = [Alert(kind="offline", server=s, title="Offline", text=f"🔴 **{n}** went offline, and nobody stopped "
                     "it through Portcullis. (A stop from AMP's own panel also counts.)")
               for s, n in ((VALHEIM, "Valheim Vikings"), (SURVIVAL, "Survival"), (STATION, "Space Station"))]
    loop = Alert(kind="crash_loop", server=SURVIVAL, title="Crash loop",
                 text="🔥 **Survival** crashed 3 times in 15 minutes. Its alerts are muted until "
                      f"<t:{STAMP + 3600}:t>; "
                      "`/monitor mute minutes:0` turns them back on.")
    load = Alert(kind="load_cpu", server=VALHEIM, severity="warn", title="High CPU",
                 text="🟠 **Valheim Vikings**: CPU at 96% for 5 minutes (limit 90%).",
                 fields=(("Clears at", "80% or lower"),))
    clear = Alert(kind="load_cpu_clear", server=VALHEIM, severity="good", ping=False,
                  text="✅ **Valheim Vikings**: CPU is back to 41% (limit 90%).")
    updating = Alert(kind="update", server=STATION, severity="info", ping=False,
                     text="⬇️ **Space Station** is updating (started through Portcullis).")
    done = Alert(kind="update_done", server=STATION, severity="good", ping=False,
                 text="✅ **Space Station** finished updating: `1.205.026` → `1.206.011`.")
    failed = Alert(kind="update_failed", server=STATION, title="Update failed",
                   text="❌ **Space Station**: the game update failed.",
                   fields=(("AMP says", "SteamCMD returned error 8 (disk write failure)."),))

    players_png, facts = await monitor.graph_png(SURVIVAL, "Survival", "players", "day", NOW)
    uptime_png, uptime_facts = await monitor.graph_png(SURVIVAL, "Survival", "uptime", "week", NOW)
    cpu_png, _ = await monitor.graph_png(VALHEIM, "Valheim Vikings", "cpu", "day", NOW)
    weekly_embed, weekly_png = await monitor.weekly.build(NOW)

    def graph(png: bytes, title: str, text: str) -> str:
        embed = discord.Embed(title=title, description=text, colour=0xE8710A)
        embed.set_footer(text="Recorded by Portcullis every minute. Gaps are times it wasn't running.")
        return message(None, embed, image=png)

    pages = {
        "alert-crash": ("A crash alert in the alert channel, then the all-clear once it is back. Only the alert "
                        "role is pinged.", alert(crash) + alert(back)),
        "alert-grouped": ("Three servers went down within 10 seconds (an AMP host restart): one message, not "
                          "three. Then a crash loop, which mutes that server for an hour.",
                          alert(*offline) + alert(loop)),
        "alert-load": ("A CPU limit with hysteresis: it fires after 5 minutes over 90% and clears at 80%.",
                       alert(load) + alert(clear)),
        "alert-update": ("Game update status: updating, finished (installed version changed), and a failed update.",
                         alert(updating) + alert(done) + alert(failed)),
        "alert-dm": ("The same crash alert as a DM, for members on the alert DM list. DMs never ping.",
                     alert(crash, ping=False, note="Direct message")),
        "graph-players": ("/graph server:Survival metric:players period:day", graph(players_png, "Survival",
                                                                                   " · ".join(facts))),
        "graph-uptime": ("/graph server:Survival metric:uptime period:week. Red is time offline.",
                         graph(uptime_png, "Survival", " · ".join(uptime_facts))),
        "graph-cpu": ("/graph metric:cpu (operators only).", graph(cpu_png, "Valheim Vikings", "")),
        "weekly-summary": ("The weekly summary (off by default), posted Monday 09:00 in the summary channel.",
                           message(None, weekly_embed, image=weekly_png)),
    }
    for name, (caption, body) in pages.items():
        (out / f"{name}.html").write_text(page(caption, body), encoding="utf-8")
        print(out / f"{name}.html")
    await db.close()


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "monitoring_html")
    target.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(target))
