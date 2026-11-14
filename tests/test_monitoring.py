# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""Alerts routing, thresholds, update status, history, graphs and the weekly summary."""

from __future__ import annotations

import asyncio
import io
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from PIL import Image

from portcullis.bot.alerting import ALERT_DM, MUTED_KEY, ROUTE_KEY, Alert, AlertRouter, render
from portcullis.bot.alerts import CPU_KEY, MEMORY_KEY, Alerts
from portcullis.bot.base import ALERT_CHANNEL, ALERT_ROLE, SUMMARY_CHANNEL
from portcullis.bot.graphs import Series, nice_max
from portcullis.bot.graphs import render as render_graph
from portcullis.bot.monitor import HISTORY_DAYS, Monitor
from portcullis.bot.thresholds import DISK_KEY, GAP_KEY, SUSTAIN_KEY, Thresholds
from portcullis.bot.updates import NOTICES_KEY, SEEN_KEY, Updates
from portcullis.bot.weekly import POSTED_KEY, WEEKLY_KEY, stored_config
from portcullis.core.monitoring import HistoryBuffer, Hysteresis, clear_level, sample_from
from portcullis.core.types import DiskUsage, OtherEvent, ServerId, ServerState, StateChanged
from portcullis.db.history import IncidentRepo
from tests.fakes.discord import FakeChannel, FakeInteraction, FakeMember
from tests.test_bot import CREATIVE, SECRET, SURVIVAL, Harness, admin, db, h, member, operator  # noqa: F401

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)  # a Monday
ALERTS, OPS, WEEKLY = 400, 401, 402


def channels(h: Harness, *ids: int) -> dict[int, FakeChannel]:
    out = {}
    for cid in ids:
        out[cid] = h.client.channels[cid] = FakeChannel(cid)
        h.client.cached.add(cid)
    return out


def metrics(sid: str, at: datetime, cpu: float = 10.0, memory: float = 1000, limit: float = 4000,
            users: int = 2) -> OtherEvent:
    payload = {"shardId": sid, "cpuPercent": cpu, "memoryMb": memory, "memoryLimitMb": limit, "activeUsers": users}
    return OtherEvent(server=ServerId(sid), at=at, name="Metrics", payload=payload)


def state(sid: str, value: ServerState, at: datetime = T0, message: str | None = None) -> StateChanged:
    return StateChanged(server=ServerId(sid), at=at, state=value, message=message)


async def seeded(h: Harness) -> None:
    await h.ctx.live.seed([await h.port.get_status(ServerId(s)) for s in (SURVIVAL, CREATIVE)], [SURVIVAL, CREATIVE])


# ------------------------------------------------------------------------------ pure parts


def test_hysteresis_waits_and_needs_the_gap() -> None:
    limit = Hysteresis(90, clear_level(90, 10), sustain=300, clear_after=120)
    assert limit.update(95, T0) is None
    assert limit.update(80, T0 + timedelta(seconds=100)) is None  # dipped: the five minutes start again
    assert limit.update(95, T0 + timedelta(seconds=200)) is None
    assert limit.update(96, T0 + timedelta(seconds=500)) == "fire"
    assert limit.update(85, T0 + timedelta(seconds=600)) is None  # under the limit, not under the gap
    assert limit.update(85, T0 + timedelta(seconds=900)) is None
    assert limit.update(79, T0 + timedelta(seconds=1000)) is None
    assert limit.update(78, T0 + timedelta(seconds=1120)) == "clear"
    assert clear_level(5, 10) == 0


def test_metrics_payload_shapes() -> None:
    sample = sample_from(metrics(SURVIVAL, T0, cpu=50, memory=2000, limit=4000, users=3))
    assert sample is not None and sample.cpu == 50 and sample.memory_percent == 50 and sample.players == 3
    nested = OtherEvent(server=ServerId(SURVIVAL), at=T0, name="Metrics",
                        payload={"metrics": {"cpuUsagePercent": "12.5", "memoryUsageMB": 100, "memoryLimitMB": 0,
                                             "activeConnections": -1}})
    sample = sample_from(nested)
    assert sample is not None and sample.cpu == 12.5 and sample.memory_percent is None and sample.players is None
    assert sample_from(OtherEvent(server=ServerId(SURVIVAL), at=T0, name="Metrics", payload="junk")) is None
    assert sample_from(OtherEvent(server=ServerId(SURVIVAL), at=T0, name="Lifecycle", payload={})) is None
    assert sample_from(state(SURVIVAL, ServerState.RUNNING)) is None


def test_history_buffer_windows() -> None:
    buffer = HistoryBuffer()
    assert buffer.observe("s", T0, online=True, players=2, cpu=10) == []
    assert buffer.observe("s", T0 + timedelta(minutes=1), online=False, players=0, cpu=None) == []
    [(server, window)] = buffer.observe("s", T0 + timedelta(minutes=5), online=True, players=4)
    assert server == "s" and window.samples == 2 and window.up == 1 and window.players_max == 2
    assert window.cpu_max == 10 and window.start == int(T0.timestamp())
    assert buffer.drain(T0 + timedelta(minutes=6)) == []
    assert len(buffer.drain(T0 + timedelta(minutes=10))) == 1


def test_graph_renders_png() -> None:
    since = int(T0.timestamp())
    points = [(since + i * 1800, float(i % 7)) for i in range(48)] + [(since + 49 * 1800, None)]
    for style in ("line", "area", "bars"):
        png = render_graph("Survival: players", "test", [Series("Players", "#E8710A", points)], since=since,
                           until=since + 86400, step=1800, tz=UTC, style=style)
        image = Image.open(io.BytesIO(png))
        assert image.format == "PNG" and image.size == (1000, 380)
    empty = render_graph("Nothing", "test", [], since=since, until=since + 86400, step=1800, tz=UTC)
    assert empty.startswith(b"\x89PNG")
    assert nice_max(0) == 1 and nice_max(7) == 8 and nice_max(97) == 100


# ------------------------------------------------------------------------------ routing


async def test_router_channel_dm_and_server_channel(h: Harness) -> None:
    sent = channels(h, ALERTS, OPS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    await h.ctx.settings.set(ALERT_ROLE, 700)
    await h.ctx.settings.set(ALERT_DM, [77])
    await h.ctx.settings.set(ROUTE_KEY, {"channel": OPS, "dm": [78, 77]}, scope=SURVIVAL)
    router = AlertRouter(h.ctx)
    assert await router.send(Alert(kind="crash", text="🔥 **Survival** crashed", server=SURVIVAL,
                                   fields=(("AMP says", "boom"),)))
    assert sent[ALERTS].sent == [] and sent[OPS].sent[0]["content"].startswith("<@&700> 🔥 **Survival** crashed")
    assert sent[OPS].sent[0]["embed"].fields[0].value == "boom"
    assert h.client.users[77].dms == ["🔥 **Survival** crashed"] and h.client.users[78].dms  # once each
    assert await router.send(Alert(kind="crash", text="again", server=SURVIVAL)) is False  # cooling down
    assert await router.send(Alert(kind="recovered", text="🟢 back", server=CREATIVE, ping=False))
    assert sent[ALERTS].sent[-1]["content"] == "🟢 back"  # no ping, the bot's channel
    await h.ctx.settings.set(ALERT_ROLE, 1)  # @everyone (the guild id) is never pinged
    await router.send(Alert(kind="offline", text="down", server=CREATIVE))
    assert sent[ALERTS].sent[-1]["content"] == "down"


async def test_router_mute_and_crash_loop(h: Harness) -> None:
    sent = channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    clock = [0.0]
    router = AlertRouter(h.ctx, quiet_for=0, clock=lambda: clock[0])
    for i in range(3):
        clock[0] = i * 60.0
        await router.send(Alert(kind="crash", text=f"crash {i}", server=SURVIVAL))
    texts = [m["content"] for m in sent[ALERTS].sent]
    assert texts[:2] == ["crash 0", "crash 1"] and "crashed 3 times in 15 minutes" in texts[2]
    assert await router.muted_until(SURVIVAL) is not None
    assert await router.send(Alert(kind="offline", text="muted", server=SURVIVAL)) is False
    assert await router.send(Alert(kind="test", text="test still goes", server=SURVIVAL))
    await router.mute(SURVIVAL, 0)
    assert await h.ctx.settings.get(MUTED_KEY, scope=SURVIVAL) is None
    assert await router.send(Alert(kind="offline", text="on again", server=SURVIVAL))


async def test_router_groups_a_burst(h: Harness) -> None:
    sent = channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    router = AlertRouter(h.ctx, window=0.05)
    await router.send(Alert(kind="offline", text="🔴 Survival down", server=SURVIVAL))
    await router.send(Alert(kind="offline", text="🔴 Creative down", server=CREATIVE))
    assert sent[ALERTS].sent == []
    await asyncio.sleep(0.1)
    [message] = sent[ALERTS].sent
    assert message["content"] == "⚠️ 2 alerts on 2 servers." and "Creative down" in message["embed"].description
    text, embed = render([Alert(kind="x", text=str(i)) for i in range(25)])
    assert "5 more" in embed.description
    await router.close()


# ------------------------------------------------------------------------------ alerts


async def test_crash_has_reason_and_recovery_is_posted(h: Harness) -> None:
    sent = channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    real = h.port.get_status

    async def failed(server: ServerId):  # noqa: ANN202
        return replace(await real(server), last_error="Java ran out of memory")

    h.port.get_status = failed  # type: ignore[method-assign]
    alerts = Alerts(h.ctx)
    await alerts(state(SURVIVAL, ServerState.RUNNING))
    await alerts(state(SURVIVAL, ServerState.FAILED))
    crash = sent[ALERTS].sent[-1]
    assert "crashed" in crash["content"] and crash["embed"].fields[1].value == "Java ran out of memory"
    await alerts(state(SURVIVAL, ServerState.RUNNING))
    back = sent[ALERTS].sent[-1]["content"]
    assert "back online after" in back and "(it crashed)" in back
    before = len(sent[ALERTS].sent)
    await alerts(state(SURVIVAL, ServerState.STOPPED))
    await alerts(state(SURVIVAL, ServerState.RUNNING))
    assert len(sent[ALERTS].sent) == before + 2  # offline, then back online


# ------------------------------------------------------------------------------ thresholds


async def test_thresholds_from_hub_metrics(h: Harness) -> None:
    sent = channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    await seeded(h)
    await h.ctx.settings.set(CPU_KEY, 90, scope=SURVIVAL)
    await h.ctx.settings.set(MEMORY_KEY, 80, scope=SURVIVAL)
    await h.ctx.settings.set(SUSTAIN_KEY, 1, scope=SURVIVAL)
    await h.ctx.settings.set(GAP_KEY, 20, scope=SURVIVAL)
    limits = Thresholds(h.ctx, AlertRouter(h.ctx))
    await limits(metrics(SURVIVAL, T0, cpu=97))
    assert sent[ALERTS].sent == []  # not for a minute yet
    await limits(metrics(SURVIVAL, T0 + timedelta(seconds=61), cpu=97, memory=3900))
    assert "CPU at 97% for 1 minute (limit 90%)" in sent[ALERTS].sent[-1]["content"]
    await limits(metrics(SURVIVAL, T0 + timedelta(seconds=125), cpu=80, memory=3900))
    await limits(metrics(SURVIVAL, T0 + timedelta(seconds=400), cpu=80, memory=3900))
    assert sum("memory at 98%" in m["content"] for m in sent[ALERTS].sent) == 1
    assert not any("back to" in m["content"] for m in sent[ALERTS].sent)  # 80 is above 90 - 20
    await limits(metrics(SURVIVAL, T0 + timedelta(seconds=500), cpu=60))
    await limits(metrics(SURVIVAL, T0 + timedelta(seconds=650), cpu=60))
    assert any("CPU is back to 60%" in m["content"] for m in sent[ALERTS].sent[-2:])
    await limits(metrics(CREATIVE, T0, cpu=100))  # no limits: nothing
    await limits(state(SURVIVAL, ServerState.STOPPED))
    assert not any(h.alerted for (s, _), h in limits.limits.items() if s == SURVIVAL and _ != "disk")


async def test_disk_threshold_reads_disk_rarely(h: Harness) -> None:
    sent = channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    await h.ctx.settings.set(DISK_KEY, 90, scope=SURVIVAL)
    h.port.disk[ServerId(SURVIVAL)] = DiskUsage(size_mb=950, limit_mb=1000)
    clock = [0.0]
    limits = Thresholds(h.ctx, AlertRouter(h.ctx, quiet_for=0), clock=lambda: clock[0])
    calls = []
    real = h.port.disk_usage

    async def counted(server: ServerId) -> DiskUsage:
        calls.append(server)
        return await real(server)

    h.port.disk_usage = counted  # type: ignore[method-assign]
    await limits.tick(T0)
    assert "disk at 95%" in sent[ALERTS].sent[-1]["content"]
    await limits.tick(T0)
    assert len(calls) == 1  # not again for 30 minutes
    clock[0] = 1800.0
    h.port.disk[ServerId(SURVIVAL)] = DiskUsage(size_mb=500, limit_mb=1000)
    await limits.tick(T0)
    assert "disk is back to 50%" in sent[ALERTS].sent[-1]["content"]
    h.port.disk[ServerId(SURVIVAL)] = DiskUsage(size_mb=950, limit_mb=0)  # no budget: no percentage
    clock[0] = 3600.0
    await limits.tick(T0)
    assert "back to 50%" in sent[ALERTS].sent[-1]["content"]


# ------------------------------------------------------------------------------ updates


async def test_update_status(h: Harness) -> None:
    sent = channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    updates = Updates(h.ctx, AlertRouter(h.ctx, quiet_for=0))
    info = h.port.servers[ServerId(SURVIVAL)].info
    await updates.seen([replace(info, installed_version="1.0", installed_at="2026-11-01T00:00:00Z")])
    assert sent[ALERTS].sent == []  # first sighting
    assert (await h.ctx.settings.get(SEEN_KEY, scope=SURVIVAL))["version"] == "1.0"

    await updates(state(SURVIVAL, ServerState.BUSY, message="Updating application (SteamCMD)"))
    assert "is updating" in sent[ALERTS].sent[-1]["content"] and h.ctx.offline_expected(SURVIVAL)
    await updates.seen([replace(info, installed_version="1.1", installed_at="2026-11-09T12:00:00Z")])
    assert "finished updating: `1.0` → `1.1`" in sent[ALERTS].sent[-1]["content"]
    assert SURVIVAL not in updates.updating

    await updates.started(SURVIVAL, "test")
    await updates(state(SURVIVAL, ServerState.FAILED, message="SteamCMD error 8"))
    last = sent[ALERTS].sent[-1]
    assert "update failed" in last["content"] and last["embed"].fields[0].value == "SteamCMD error 8"

    await h.ctx.settings.set(NOTICES_KEY, False)
    before = len(sent[ALERTS].sent)
    await updates.seen([replace(info, installed_version="1.2", installed_at="2026-11-10T12:00:00Z")])
    await updates(state(CREATIVE, ServerState.BUSY, message="Starting"))  # not an update
    assert len(sent[ALERTS].sent) == before


async def test_update_through_portcullis_quiets_offline_alert(h: Harness) -> None:
    sent = channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    await h.ctx.audit.record("server.power.update", actor_id=40, shard_id=CREATIVE)
    await asyncio.sleep(0)
    await asyncio.gather(*h.m.monitor.updates.tasks)
    await h.m.router.flush()
    assert h.ctx.offline_expected(CREATIVE)
    assert any("Creative** is updating (started through Portcullis)" in m["content"] for m in sent[ALERTS].sent)


# ------------------------------------------------------------------------------ history, graphs, weekly


async def record_day(monitor: Monitor, h: Harness) -> None:
    await seeded(h)
    for minute in range(0, 24 * 60, 10):
        at = T0 - timedelta(days=1) + timedelta(minutes=minute)
        online = not (300 <= minute < 360)
        h.ctx.live.states[ServerId(SURVIVAL)] = ServerState.RUNNING if online else ServerState.STOPPED
        h.ctx.live.players[ServerId(SURVIVAL)] = {f"p{i}" for i in range(minute // 120)}
        monitor.thresholds.latest[SURVIVAL] = sample_from(metrics(SURVIVAL, at, cpu=minute / 15))  # type: ignore[assignment]
        await monitor.write(monitor.sample(at))
    await monitor.write(monitor.buffer.drain())


async def test_history_and_graph_command(h: Harness) -> None:
    monitor = h.m.monitor
    await record_day(monitor, h)
    since, until = int((T0 - timedelta(days=1)).timestamp()), int(T0.timestamp())
    stats = await monitor.history.stats(SURVIVAL, since, until)
    assert stats.players_max == 11 and 0.95 < (stats.uptime or 0) < 0.97
    points = await monitor.history.series(SURVIVAL, since, until, 3600)
    assert len(points) == 24 and points[5].uptime == 0 and points[0].cpu_max is not None

    i = FakeInteraction(member())
    await monitor.graph(i, "Survival", "players", "day")
    reply = i.replies[-1]
    assert reply["file"].filename == "graph.png" and reply["embed"].image.url == "attachment://graph.png"
    i = FakeInteraction(member())
    await monitor.graph(i, "Survival", "cpu", "day")
    assert "operator role" in i.text
    i = FakeInteraction(member(21))
    await monitor.graph(i, "Secret", "players", "day")
    assert "don't know a server" in i.text  # hidden servers stay hidden
    i = FakeInteraction(operator())
    await monitor.graph(i, "Survival", "memory", "week", share=True)
    assert i.replies[-1]["ephemeral"] is False
    i = FakeInteraction(operator())
    await monitor.graph(i, "Survival", "uptime", "day")
    assert "every 10 seconds" in i.text


async def test_history_can_be_turned_off_and_is_pruned(h: Harness) -> None:
    monitor = h.m.monitor
    await seeded(h)
    await h.ctx.settings.set(HISTORY_DAYS, 0)
    await monitor.tick(T0)
    await monitor.tick(T0 + timedelta(minutes=6))
    assert (await monitor.history.stats(SURVIVAL, 0, 2**31)).samples == 0
    await h.ctx.settings.set(HISTORY_DAYS, 1)
    await monitor.history.add(SURVIVAL, int((T0 - timedelta(days=3)).timestamp()), samples=5, up=5, players_max=1,
                              players_sum=5, cpu_max=None, memory_max=None)
    await monitor.tick(T0)
    await monitor.tick(T0 + timedelta(minutes=6))
    stats = await monitor.history.stats(SURVIVAL, 0, 2**31)
    assert stats.samples == 1  # the old row is gone, the new window is in


async def test_starts_and_crashes_are_logged(h: Harness) -> None:
    monitor = h.m.monitor
    await monitor(state(SURVIVAL, ServerState.STOPPED))
    await monitor(state(SURVIVAL, ServerState.RUNNING))
    await monitor(state(SURVIVAL, ServerState.FAILED, message="boom"))
    counts = await IncidentRepo(h.ctx.db).counts(SURVIVAL, 0, 2**31)
    assert counts == {"start": 1, "crash": 1}


async def test_weekly_summary_is_off_by_default_and_posts_once(h: Harness) -> None:
    sent = channels(h, WEEKLY)
    monitor = h.m.monitor
    assert stored_config(await h.ctx.settings.get(WEEKLY_KEY)).enabled is False
    await record_day(monitor, h)
    await IncidentRepo(h.ctx.db).add(SURVIVAL, "crash", int(T0.timestamp()) - 3600)
    await IncidentRepo(h.ctx.db).add(SURVIVAL, "alert", int(T0.timestamp()) - 3600, "crash")
    assert await monitor.weekly.tick(T0) is False  # off

    i = FakeInteraction(admin())
    await monitor.weekly_settings(i, enabled=True, channel=FakeChannel(WEEKLY), day=0, hour=12)
    assert "every Monday at 12:00" in i.text
    assert await monitor.weekly.tick(T0 - timedelta(hours=1)) is False  # before the hour
    assert await monitor.weekly.tick(T0) is True
    [post] = sent[WEEKLY].sent
    assert "**Survival**: " in post["embed"].description and "% up" in post["embed"].description
    assert "1 crash" in post["embed"].description and "1 alert" in post["embed"].description
    assert post["file"].filename == "week.png"
    assert await h.ctx.settings.get(POSTED_KEY) == T0.date().isoformat()
    assert await monitor.weekly.tick(T0 + timedelta(hours=2)) is False  # once a week

    i = FakeInteraction(member())
    await monitor.weekly_settings(i, enabled=False)
    assert "Manage Server" in i.text
    i = FakeInteraction(admin())
    await monitor.weekly_settings(i, preview=True)
    assert i.replies[-1]["embed"].title.startswith("Week of")


async def test_weekly_falls_back_to_summary_channel(h: Harness) -> None:
    sent = channels(h, WEEKLY)
    await h.ctx.settings.set(SUMMARY_CHANNEL, WEEKLY)
    await h.ctx.settings.set(WEEKLY_KEY, {"enabled": True, "day": 0, "hour": 0})
    assert await h.m.monitor.weekly.tick(T0)
    assert "Nobody" not in sent[WEEKLY].sent[0]["embed"].description
    assert "file" not in sent[WEEKLY].sent[0]  # no players recorded: no graph


# ------------------------------------------------------------------------------ /monitor settings


async def test_monitor_alert_settings(h: Harness) -> None:
    channels(h, ALERTS, OPS)
    monitor = h.m.monitor
    i = FakeInteraction(admin())
    await monitor.alerts(i, channel=FakeChannel(ALERTS), dm_add=FakeMember(77), update_notices=False)
    assert await h.ctx.settings.get(ALERT_CHANNEL) == ALERTS and await h.ctx.settings.get(ALERT_DM) == [77]
    assert await h.ctx.settings.get(NOTICES_KEY) is False
    assert "Update notices:** off" in i.embed.description
    i = FakeInteraction(admin())
    await monitor.alerts(i, server="Survival", channel=FakeChannel(OPS), dm_add=FakeMember(78))
    assert await h.ctx.settings.get(ROUTE_KEY, scope=SURVIVAL) == {"channel": OPS, "dm": [78]}
    assert "<@77>, <@78>" in i.embed.description
    i = FakeInteraction(admin())
    await monitor.alerts(i, server="Survival", reset_channel=True, dm_remove=FakeMember(78))
    assert await h.ctx.settings.get(ROUTE_KEY, scope=SURVIVAL) == {"channel": None, "dm": []}
    i = FakeInteraction(admin())
    bot = FakeMember(79)
    bot.bot = True  # type: ignore[attr-defined]
    await monitor.alerts(i, dm_add=bot)
    assert "bots can't get DMs" in i.embed.description
    i = FakeInteraction(member())
    await monitor.alerts(i, channel=FakeChannel(OPS))
    assert "Manage Server" in i.text and await h.ctx.settings.get(ALERT_CHANNEL) == ALERTS


async def test_monitor_mute_thresholds_history_and_test(h: Harness) -> None:
    sent = channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    monitor = h.m.monitor
    i = FakeInteraction(member())
    await monitor.mute(i, "Survival", 60)
    assert "operator role" in i.text
    i = FakeInteraction(operator())
    await monitor.mute(i, "Survival", 60)
    assert "muted until" in i.text and await monitor.router.muted_until(SURVIVAL) is not None
    i = FakeInteraction(admin())
    await monitor.mute(i, "Survival", 0)
    assert "on again" in i.text

    i = FakeInteraction(admin())
    await monitor.limits(i, "Survival", cpu=90, memory=0, disk=85, gap=15, minutes=3)
    assert await h.ctx.settings.get(CPU_KEY, scope=SURVIVAL) == 90
    assert await h.ctx.settings.get(DISK_KEY, scope=SURVIVAL) == 85
    assert "**Memory:** off" in i.embed.description and "15 points lower" in i.embed.description
    i = FakeInteraction(admin())
    await monitor.limits(i, "Survival", cpu=120)
    assert "1 to 100" in i.text

    i = FakeInteraction(admin())
    await monitor.history_settings(i, 7)
    assert "keeps 7 days" in i.text
    i = FakeInteraction(admin())
    await monitor.history_settings(i, 0)
    assert "off" in i.text

    i = FakeInteraction(admin())
    await monitor.test(i, "Survival")
    assert "Sent." in i.text and "Test alert for **Survival**" in sent[ALERTS].sent[-1]["content"]
    await h.ctx.settings.delete(ALERT_CHANNEL)
    i = FakeInteraction(admin())
    await monitor.test(i)
    assert "Nothing went out" in i.text


async def test_router_survives_a_closed_dm(h: Harness) -> None:
    sent = channels(h, ALERTS)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    await h.ctx.settings.set(ALERT_DM, [90])

    async def closed(content: str, **kwargs: object) -> None:
        raise RuntimeError("Cannot send messages to this user")

    h.client.get_user(90).send = closed  # type: ignore[method-assign]
    assert await AlertRouter(h.ctx).send(Alert(kind="offline", text="down", server=SURVIVAL))
    assert sent[ALERTS].sent[-1]["content"] == "down"  # the channel still got it
