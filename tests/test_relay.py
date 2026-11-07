# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import discord

from portcullis.bot.client import DiscordSink
from portcullis.core.relay import MESSAGE_LIMIT, GameHooks, Relay, RelayTarget, escape, pack
from portcullis.core.types import ConsoleLine, ServerEvent, ServerId, ServerState, StateChanged
from tests.fakes.amp import FakeAmpPort
from tests.fakes.discord import FakeChannel, FakeClient

CONSOLE, EVENTS, CHAT = 101, 102, 103


class RecordingSink:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail: set[int] = set()

    async def send(self, channel_id: int, text: str) -> None:
        if channel_id in self.fail:
            raise RuntimeError("Missing Access")
        self.sent.append((channel_id, text))

    def to(self, channel_id: int) -> list[str]:
        return [t for c, t in self.sent if c == channel_id]


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def now() -> datetime:
    return datetime.now(UTC)


def line(sid: ServerId, text: str, stream: str = "main", kind: str = "line") -> ConsoleLine:
    return ConsoleLine(server=sid, at=now(), text=text, stream=stream, kind=kind)


def target(sid: ServerId, **channels: int) -> RelayTarget:
    return RelayTarget(server=sid, name="Survival", game="unturned", **channels)


async def started(port: FakeAmpPort, sink: RecordingSink, t: RelayTarget, **kwargs: object) -> Relay:
    relay = Relay(port, sink, flush_interval=3600, **kwargs)  # type: ignore[arg-type]
    await relay.apply([t])
    await settle()
    return relay


async def test_console_relays_main_stream_only() -> None:
    port, sink = FakeAmpPort(), RecordingSink()
    sid = port.add_server("a" * 32, "Survival")
    relay = await started(port, sink, target(sid, console_channel=CONSOLE))
    port.emit(line(sid, "Server started on port 27015"))
    port.emit(line(sid, "agent noise", stream="agent"))
    port.emit(line(sid, "system noise", stream="system"))
    port.emit(line(sid, "-- restarted --", kind="marker"))
    port.emit(line(sid, "say ``` @everyone"))
    await settle()
    await relay.flush()
    [message] = sink.to(CONSOLE)
    assert message.startswith("```\n") and message.endswith("\n```")
    assert "Server started" in message and "noise" not in message and "restarted" not in message
    assert "@everyone" not in message and message.count("```") == 2
    await relay.close()


async def test_joins_leaves_and_real_state_changes_only() -> None:
    port, sink = FakeAmpPort(), RecordingSink()
    sid = port.add_server("b" * 32, "Survival", state=ServerState.STOPPED)
    relay = await started(port, sink, target(sid, event_channel=EVENTS))
    port.emit(StateChanged(server=sid, at=now(), state=ServerState.STOPPED))  # AMP repeats the state
    port.join(sid, "@everyone_*bold*")
    port.leave(sid, "@everyone_*bold*")
    port.emit(StateChanged(server=sid, at=now(), state=ServerState.RUNNING))
    port.emit(StateChanged(server=sid, at=now(), state=ServerState.RUNNING))
    await settle()
    await relay.flush()
    [message] = sink.to(EVENTS)
    lines = message.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("➡️") and "joined Survival" in lines[0]
    assert lines[1].startswith("⬅️") and "left Survival" in lines[1]
    assert lines[2] == "🟢 **Survival** is now Online"
    assert "@everyone" not in message and r"\*bold\*" in message
    await relay.close()


async def test_chat_needs_a_game_parser() -> None:
    port, sink = FakeAmpPort(), RecordingSink()
    sid = port.add_server("c" * 32, "Survival")

    relay = await started(port, sink, target(sid, chat_channel=CHAT))
    port.emit(line(sid, '[World] Alice [Alice]: "hello"'))
    await settle()
    await relay.flush()
    assert sink.to(CHAT) == []  # off by default
    await relay.close()

    def parse(event: ConsoleLine) -> tuple[str, str] | None:
        if event.text.startswith("[World] "):
            name, _, rest = event.text[8:].partition(" [")
            return name, rest.split(": ", 1)[1].strip('"')
        return None

    relay = await started(port, sink, target(sid, chat_channel=CHAT), hooks=lambda game: GameHooks(chat=parse))
    port.emit(line(sid, '[World] Alice [Alice]: "hello @here"'))
    port.emit(line(sid, "not chat"))
    await settle()
    await relay.flush()
    assert sink.to(CHAT) == ["💬 **Alice**: hello @​here"]
    await relay.close()


async def test_drop_filter_and_hooks_get_the_template() -> None:
    port, sink = FakeAmpPort(), RecordingSink()
    sid = port.add_server("d" * 32, "Survival")
    seen: list[str | None] = []

    def hooks(game: str | None) -> GameHooks:
        seen.append(game)
        return GameHooks(drop=lambda event: event.text == "dup")

    relay = await started(port, sink, target(sid, console_channel=CONSOLE), hooks=hooks)
    port.emit(line(sid, "dup"))
    port.emit(line(sid, "kept"))
    await settle()
    await relay.flush()
    assert seen == ["unturned"]
    assert "dup" not in sink.to(CONSOLE)[0] and "kept" in sink.to(CONSOLE)[0]
    await relay.close()


def test_pack_respects_discord_limit() -> None:
    items = [("console", "x" * 300)] * 20 + [("text", "y" * 2500)]
    messages = pack(items)  # type: ignore[arg-type]
    assert len(messages) > 3
    assert all(len(m) <= MESSAGE_LIMIT for m in messages)
    assert sum(m.count("x" * 300) for m in messages) == 20


async def test_slow_channel_drops_oldest_and_says_so() -> None:
    port, sink = FakeAmpPort(), RecordingSink()
    sid = port.add_server("e" * 32, "Survival")
    relay = await started(port, sink, target(sid, console_channel=CONSOLE), max_pending=3)
    for n in range(5):
        port.emit(line(sid, f"line {n}"))
    await settle()
    await relay.flush()
    text = "\n".join(sink.to(CONSOLE))
    assert "2 line(s) skipped" in text and "line 0" not in text and "line 4" in text
    await relay.close()


async def test_apply_starts_stops_and_restarts_followers() -> None:
    port, sink = FakeAmpPort(), RecordingSink()
    sid = port.add_server("f" * 32, "Survival")
    relay = await started(port, sink, target(sid, console_channel=CONSOLE))
    assert len(port._subs[sid]) == 1
    await relay.apply([target(sid, console_channel=CONSOLE)])  # unchanged: same follower
    await settle()
    assert len(port._subs[sid]) == 1
    await relay.apply([target(sid, console_channel=CONSOLE, event_channel=EVENTS)])
    await settle()
    assert len(port._subs[sid]) == 1 and relay.targets[sid].event_channel == EVENTS
    await relay.apply([target(sid)])  # no channels left
    await settle()
    assert not port._subs[sid] and relay.targets == {}
    await relay.close()


async def test_a_failing_channel_does_not_stop_others() -> None:
    port, sink = FakeAmpPort(), RecordingSink()
    sid = port.add_server("0" * 32, "Survival")
    sink.fail.add(CONSOLE)
    relay = await started(port, sink, target(sid, console_channel=CONSOLE, event_channel=EVENTS))
    port.say(sid, "hello")
    port.join(sid, "Bob")
    await settle()
    await relay.flush()
    assert sink.to(CONSOLE) == [] and len(sink.to(EVENTS)) == 1
    await relay.close()


class FlakyPort(FakeAmpPort):
    def __init__(self) -> None:
        super().__init__()
        self.failures = 1

    async def events(self, server: ServerId) -> AsyncIterator[ServerEvent]:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("hub went away")
        async for event in super().events(server):
            yield event


async def test_reconnects_after_the_stream_fails() -> None:
    port, sink = FlakyPort(), RecordingSink()
    sid = port.add_server("1" * 32, "Survival")
    relay = await started(port, sink, target(sid, console_channel=CONSOLE), retry_min=0.01)
    await asyncio.sleep(0.05)
    await settle()
    port.say(sid, "back again")
    await settle()
    await relay.flush()
    assert "back again" in sink.to(CONSOLE)[0]
    await relay.close()


async def test_sender_flushes_on_its_own() -> None:
    port, sink = FakeAmpPort(), RecordingSink()
    sid = port.add_server("2" * 32, "Survival")
    relay = Relay(port, sink, flush_interval=0.01)
    await relay.apply([target(sid, console_channel=CONSOLE)])
    await settle()
    port.say(sid, "one")
    port.say(sid, "two")
    await asyncio.sleep(0.05)
    assert sink.to(CONSOLE) == ["```\none\ntwo\n```"]
    await relay.close()


async def test_discord_sink_disables_mentions_and_fetches_unknown_channels() -> None:
    cached, uncached = FakeChannel(1), FakeChannel(2)
    client = FakeClient(cached, uncached)
    client.cached.discard(2)
    sink = DiscordSink(client)  # type: ignore[arg-type]
    await sink.send(1, "hi")
    await sink.send(2, "there")
    assert client.fetched == [2]
    for channel in (cached, uncached):
        [sent] = channel.sent
        mentions = sent["allowed_mentions"]
        assert isinstance(mentions, discord.AllowedMentions)
        assert not mentions.everyone and not mentions.users and not mentions.roles
        assert sent["suppress_embeds"] is True


def test_escape() -> None:
    assert escape("a_b*c @everyone [x](http://y)") == r"a\_b\*c @" + "​" + r"everyone \[x\]\(http://y\)"
