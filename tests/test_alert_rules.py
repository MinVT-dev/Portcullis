# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""/alert create and /alert list: admin-defined rules, checked once a minute."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from portcullis.bot.base import ALERT_CHANNEL
from portcullis.bot.rules import AlertRuleModule, check_values
from portcullis.core.types import Player, ServerId, ServerState
from portcullis.db.ops import AlertRuleRepo
from tests.fakes.discord import FakeChannel, FakeInteraction
from tests.test_bot import CREATIVE, SURVIVAL, Harness, admin, db, h, member  # noqa: F401

ALERTS, OPS = 400, 410
ROLE = 700


def role(rid: int = ROLE, *, default: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=rid, is_default=lambda: default)


def channels(h: Harness) -> dict[int, FakeChannel]:
    out = {}
    for cid in (ALERTS, OPS):
        out[cid] = h.client.channels[cid] = FakeChannel(cid)
        h.client.cached.add(cid)
    return out


async def seed(h: Harness) -> None:
    await h.ctx.live.seed([await h.port.get_status(ServerId(s)) for s in (SURVIVAL, CREATIVE)], [SURVIVAL, CREATIVE])


def busy(h: Harness, cpu: float) -> None:
    real = type(h.port).get_status

    async def status(server: ServerId):  # noqa: ANN202
        return replace(await real(h.port, server), cpu_percent=cpu)

    h.port.get_status = status  # type: ignore[method-assign]


def test_value_checks() -> None:
    assert check_values("cpu", 0, 5) and check_values("cpu", 101, 5)
    assert check_values("players_above", 1001, 5) and check_values("players_below", 0, 5)
    assert check_values("memory", 90, 1441) and check_values("disk", 1, 1)
    assert check_values("cpu", 90, 0) is None and check_values("offline", 0, 10) is None


async def test_create_validates_and_lists(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.rules.create(i, "cpu", 90)
    assert "Manage Server" in i.text
    assert (await h.ctx.audit.query(action_prefix="alert.create"))[0].result == "denied"

    i = FakeInteraction(admin())
    await h.m.rules.create(i, "cpu", 150)
    assert "1 to 100" in i.text and await h.ctx.alert_rules.all() == []

    i = FakeInteraction(admin())
    await h.m.rules.create(i, "offline", 0, role=role(1, default=True))
    assert "@everyone" in i.text

    i = FakeInteraction(admin())
    await h.m.rules.create(i, "cpu", 90, 10, server="Survival", channel=FakeChannel(OPS), role=role())
    assert "Alert 1 saved" in i.text and "Survival CPU at or above 90% for 10 min" in i.text
    rule = (await h.ctx.alert_rules.all())[0]
    assert (rule.shard_id, rule.channel_id, rule.role_id, rule.minutes) == (SURVIVAL, OPS, ROLE, 10)

    i = FakeInteraction(admin())
    await h.m.rules.create(i, "offline", 42)
    assert (await h.ctx.alert_rules.get(2)).threshold == 0  # type: ignore[union-attr]

    i = FakeInteraction(admin())
    await h.m.rules.list_rules(i)
    desc = i.embed.description
    assert "Survival CPU at or above 90%" in desc and "any server offline" in desc and f"<@&{ROLE}>" in desc
    assert i.replies[-1]["view"] is not None


async def test_rule_limit(h: Harness) -> None:
    for _ in range(AlertRuleRepo.LIMIT):
        await h.ctx.alert_rules.add(metric="offline", threshold=0, minutes=0, by=1)
    i = FakeInteraction(admin())
    await h.m.rules.create(i, "offline", 0)
    assert f"already {AlertRuleRepo.LIMIT}" in i.text


async def test_fires_after_sustain_then_clears(h: Harness) -> None:
    ch = channels(h)
    await seed(h)
    await h.ctx.alert_rules.add(metric="cpu", threshold=90, minutes=5, by=1, shard_id=SURVIVAL,
                                channel_id=OPS, role_id=ROLE)
    rules: AlertRuleModule = h.m.rules
    busy(h, 97.0)
    await rules.tick(now=0)
    assert ch[OPS].sent == []
    await rules.tick(now=240)
    assert ch[OPS].sent == []
    await rules.tick(now=301)
    msg = ch[OPS].sent[-1]
    assert msg["content"].startswith(f"<@&{ROLE}>") and "Survival" in msg["content"] and "for 5 min" in msg["content"]
    assert [r.id for r in msg["allowed_mentions"].roles] == [ROLE]
    assert not msg["allowed_mentions"].everyone
    assert (await h.ctx.alert_rules.get(1)).last_fired_at is not None  # type: ignore[union-attr]
    await rules.tick(now=400)
    assert len(ch[OPS].sent) == 1  # one alert per episode

    busy(h, 10.0)
    await rules.tick(now=460)
    assert "cleared" in ch[OPS].sent[-1]["content"] and "<@&" not in ch[OPS].sent[-1]["content"]


async def test_offline_and_players_rules_use_alert_channel(h: Harness) -> None:
    ch = channels(h)
    await h.ctx.settings.set(ALERT_CHANNEL, ALERTS)
    await seed(h)  # Survival is running, Creative stopped
    await h.ctx.alert_rules.add(metric="offline", threshold=0, minutes=0, by=1)
    await h.ctx.alert_rules.add(metric="players_above", threshold=1, minutes=0, by=1, shard_id=SURVIVAL)
    await h.m.rules.tick(now=0)
    texts = [m["content"] for m in ch[ALERTS].sent]
    assert any("Creative" in t and "offline" in t for t in texts)
    assert not any("Survival" in t for t in texts)

    h.port.servers[ServerId(SURVIVAL)].players = [Player("a"), Player("b")]
    await h.m.rules.tick(now=60)
    assert "more than 1 players" in ch[ALERTS].sent[-1]["content"]


async def test_mute_suppresses_and_actions(h: Harness) -> None:
    ch = channels(h)
    await seed(h)
    rule_id = await h.ctx.alert_rules.add(metric="cpu", threshold=50, minutes=0, by=1, channel_id=OPS)
    assert rule_id is not None

    i = FakeInteraction(member())
    await h.m.rules.act(i, rule_id, "mute")
    assert "Manage Server" in i.text

    i = FakeInteraction(admin())
    await h.m.rules.act(i, rule_id, "mute")
    assert "Muted alert 1" in i.edits[-1]["content"]
    assert (await h.ctx.alert_rules.get(rule_id)).muted(datetime.now(UTC))  # type: ignore[union-attr]
    busy(h, 99.0)
    await h.m.rules.tick(now=0)
    busy(h, 1.0)
    await h.m.rules.tick(now=60)
    assert ch[OPS].sent == []  # muted fire, and no "cleared" for an alert never posted

    busy(h, 99.0)
    await h.m.rules.tick(now=120)  # still muted: held, not posted
    assert ch[OPS].sent == []
    await h.m.rules.act(FakeInteraction(admin()), rule_id, "unmute")
    assert (await h.ctx.alert_rules.get(rule_id)).muted_until is None  # type: ignore[union-attr]
    await h.m.rules.tick(now=180)  # unmuted while still high: alerts now, not after the next episode
    assert "Alert 1" in ch[OPS].sent[-1]["content"]
    future = datetime.now(UTC) + timedelta(hours=2)
    assert not (await h.ctx.alert_rules.get(rule_id)).muted(future)  # type: ignore[union-attr]

    i = FakeInteraction(admin())
    await h.m.rules.act(i, rule_id, "delete")
    assert "Deleted alert 1" in i.edits[-1]["content"] and await h.ctx.alert_rules.all() == []
    actions = [e.action for e in await h.ctx.audit.query(action_prefix="alert")]
    assert "alert.delete" in actions and "alert.mute" in actions


async def test_status_fetched_once_per_server(h: Harness) -> None:
    await seed(h)
    calls: list[str] = []
    real = type(h.port).get_status

    async def counted(server: ServerId):  # noqa: ANN202
        calls.append(server)
        return await real(h.port, server)

    h.port.get_status = counted  # type: ignore[method-assign]
    for metric in ("cpu", "memory", "players_above", "players_below"):
        await h.ctx.alert_rules.add(metric=metric, threshold=5, minutes=0, by=1)
    await h.m.rules.tick(now=0)
    assert SURVIVAL in calls and len(calls) == len(set(calls))  # once per server, however many rules
    assert CREATIVE not in calls  # stopped, so it isn't asked
    assert h.ctx.live.states[ServerId(CREATIVE)] is ServerState.STOPPED
