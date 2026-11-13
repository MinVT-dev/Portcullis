# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live server events to chat channels (DESIGN.md section 6, "Console, chat and events").

Knows nothing about Discord or AMP: it reads ``AmpPort.events()`` and hands finished message text to a
``Sink``. Per server there are up to three destinations:

- ``console_channel``: lines from the game's own ``main`` console stream (AMP's ``agent`` and
  ``system`` streams are never relayed);
- ``event_channel``: players joining and leaving, and state changes (only real changes: AMP repeats
  the current state every few seconds);
- ``chat_channel``: in-game chat, parsed from console lines by a per-game pattern. Off unless the
  game hooks supply a parser, because AMP 3 has no chat event.

Lines are batched per channel and sent at most once per ``flush_interval``, so a busy console becomes
a few messages instead of hitting Discord's rate limit. When a channel can't keep up, the oldest
lines are dropped and the next message says how many.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from portcullis.core.console_rules import hider
from portcullis.core.port import AmpPort
from portcullis.core.types import (
    ConsoleLine,
    PlayerJoined,
    PlayerLeft,
    ServerEvent,
    ServerId,
    ServerState,
    StateChanged,
)

log = logging.getLogger(__name__)

MESSAGE_LIMIT = 2000  # Discord's message length limit
LINE_LIMIT = 1500  # longer console lines are cut, so one line never fills a whole message

Kind = Literal["console", "text"]

STATE_ICONS = {
    ServerState.RUNNING: "🟢",
    ServerState.STOPPED: "🔴",
    ServerState.BUSY: "🟡",
    ServerState.SLEEPING: "💤",
    ServerState.FAILED: "❌",
    ServerState.SUSPENDED: "⏸️",
    ServerState.AWAITING_LICENCE: "📜",
    ServerState.UNKNOWN: "❔",
}


class Sink(Protocol):
    async def send(self, channel_id: int, text: str) -> None: ...


@dataclass(frozen=True)
class ChatLine(ServerEvent):
    """In-game chat, parsed from a console line by the game's hooks. Passed to listeners only."""

    player: str
    text: str


# Called with every event of a followed server (after the game's drop filter), plus ChatLine.
Listener = Callable[[ServerEvent], Awaitable[None]]

ChatParser = Callable[[ConsoleLine], "tuple[str, str] | None"]  # line -> (player, text)
LineFilter = Callable[[ConsoleLine], bool]  # True drops the line


@dataclass(frozen=True)
class GameHooks:
    """Per-game behaviour, built fresh for each followed server (filters may keep state)."""

    chat: ChatParser | None = None
    drop: LineFilter | None = None


def no_hooks(game: str | None) -> GameHooks:
    return GameHooks()


@dataclass(frozen=True)
class RelayTarget:
    server: ServerId
    name: str
    game: str | None = None  # AMP template, used to pick the game hooks
    console_channel: int | None = None
    event_channel: int | None = None
    chat_channel: int | None = None
    hide: tuple[str, ...] = ()  # console hide rules (core.console_rules); only the console relay skips them

    @property
    def active(self) -> bool:
        return any((self.console_channel, self.event_channel, self.chat_channel))


# ------------------------------------------------------------------------------ formatting

_MARKDOWN = re.compile(r"([\\*_~`|>#\[\]()-])")


def escape(text: str) -> str:
    """Make game text safe to show as Discord markdown: no formatting, links or mentions.

    The sink also sends with mentions disabled; this keeps ``@everyone`` from even looking like one.
    """
    text = _MARKDOWN.sub(r"\\\1", text)
    return text.replace("@", "@​")


def console_safe(text: str) -> str:
    """Console lines go inside a code block: only a closing fence or a mention can escape it."""
    text = text.replace("```", "`​`​`").replace("@", "@​")
    return text if len(text) <= LINE_LIMIT else text[: LINE_LIMIT - 1] + "…"


def state_line(name: str, state: ServerState) -> str:
    return f"{STATE_ICONS.get(state, '❔')} **{escape(name)}** is now {state.label}"


# ------------------------------------------------------------------------------ batching


class ChannelBuffer:
    def __init__(self, channel_id: int, max_pending: int) -> None:
        self.channel_id = channel_id
        self.items: deque[tuple[Kind, str]] = deque()
        self.max_pending = max_pending
        self.skipped = 0
        self.ready = asyncio.Event()
        self.failures = 0  # sends that failed in a row
        self.muted_until = 0.0  # no sends before this (monotonic) time, after a failure
        self.lost = False  # lines were dropped while the channel couldn't be posted to

    def add(self, kind: Kind, text: str) -> None:
        if len(self.items) >= self.max_pending:
            self.items.popleft()
            self.skipped += 1
        self.items.append((kind, text))
        self.ready.set()

    def take(self) -> list[str]:
        """Everything pending, as Discord-sized messages, oldest first."""
        items = list(self.items)
        self.items.clear()
        self.ready.clear()
        if self.skipped:
            items.insert(0, ("text", f"*… {self.skipped} line(s) skipped because they came too fast*"))
            self.skipped = 0
        if self.lost and items:
            items.insert(0, ("text", "*… some lines are missing: I couldn't post in this channel for a while*"))
            self.lost = False
        return pack(items)

    def drop(self) -> None:
        """Throw away what is pending (the channel can't be posted to right now)."""
        if self.items:
            self.lost = True
        self.items.clear()
        self.skipped = 0
        self.ready.clear()


def pack(items: list[tuple[Kind, str]]) -> list[str]:
    """Join lines into as few messages as fit; console lines go in code blocks."""
    messages: list[str] = []
    current: list[str] = []
    current_kind: Kind | None = None
    size = 0

    def close() -> None:
        nonlocal current, size
        if current:
            body = "\n".join(current)
            messages.append(f"```\n{body}\n```" if current_kind == "console" else body)
        current, size = [], 0

    for kind, text in items:
        overhead = 8 if kind == "console" else 0  # the code fences and their newlines
        text = text[: MESSAGE_LIMIT - overhead]
        if kind != current_kind or size + len(text) + overhead > MESSAGE_LIMIT:
            close()
            current_kind = kind
        current.append(text)
        size += len(text) + 1
    close()
    return messages


# ------------------------------------------------------------------------------ relay


class Relay:
    def __init__(
        self,
        port: AmpPort,
        sink: Sink,
        *,
        hooks: Callable[[str | None], GameHooks] = no_hooks,
        flush_interval: float = 1.5,
        max_pending: int = 200,
        retry_min: float = 1.0,
        retry_max: float = 60.0,
        send_retry_min: float = 30.0,
        send_retry_max: float = 900.0,
        follow_all: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.port = port
        self.sink = sink
        self.hooks = hooks
        self.flush_interval = flush_interval
        self.max_pending = max_pending
        self.retry_min = retry_min
        self.retry_max = retry_max
        # After a failed send (a deleted channel, a missing permission) the channel is left alone for
        # send_retry_min seconds, doubling up to send_retry_max. Hammering it earns nothing but
        # log noise and "invalid request" strikes from Discord.
        self.send_retry_min = send_retry_min
        self.send_retry_max = send_retry_max
        self._clock = clock
        self._targets: dict[ServerId, RelayTarget] = {}
        self._followers: dict[ServerId, asyncio.Task[None]] = {}
        self._buffers: dict[int, ChannelBuffer] = {}
        self._senders: dict[int, asyncio.Task[None]] = {}
        self._last_state: dict[ServerId, ServerState] = {}
        self.follow_all = follow_all  # follow servers without relay channels too, for the listeners
        self.listeners: list[Listener] = []

    @property
    def targets(self) -> dict[ServerId, RelayTarget]:
        return dict(self._targets)

    async def apply(self, targets: list[RelayTarget]) -> None:
        """Follow exactly these servers. Unchanged targets keep their connection."""
        wanted = {t.server: t for t in targets if t.active or self.follow_all}
        for sid in list(self._targets):
            if wanted.get(sid) != self._targets[sid]:
                await self._stop(sid)
        for sid, target in wanted.items():
            if sid not in self._targets:
                self._targets[sid] = target
                self._followers[sid] = asyncio.create_task(self._follow(target), name=f"relay-{sid[:8]}")

    async def _stop(self, sid: ServerId) -> None:
        self._targets.pop(sid, None)
        task = self._followers.pop(sid, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def close(self) -> None:
        for sid in list(self._targets):
            await self._stop(sid)
        await self.flush()
        for task in self._senders.values():
            task.cancel()
        for task in list(self._senders.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._senders.clear()

    # -------------------------------------------------------------------------- events -> lines

    def interpret(self, event: ServerEvent, hooks: GameHooks) -> list[ServerEvent]:
        """Filter one raw event and add the chat it carries. An empty list means drop it."""
        if not isinstance(event, ConsoleLine):
            return [event]
        if event.stream != "main" or event.kind != "line" or not event.text.strip():
            return []
        if hooks.drop is not None and hooks.drop(event):
            return []
        chat = hooks.chat(event) if hooks.chat is not None else None
        if chat is None:
            return [event]
        return [event, ChatLine(server=event.server, at=event.at, player=chat[0], text=chat[1])]

    def render(self, target: RelayTarget, event: ServerEvent, hooks: GameHooks) -> list[tuple[int, Kind, str]]:
        out: list[tuple[int, Kind, str]] = []
        for item in self.interpret(event, hooks):
            out += self._render_one(target, item)
        return out

    def _render_one(self, target: RelayTarget, event: ServerEvent) -> list[tuple[int, Kind, str]]:
        out: list[tuple[int, Kind, str]] = []
        if isinstance(event, ChatLine):
            if target.chat_channel:
                out.append((target.chat_channel, "text", f"💬 **{escape(event.player)}**: {escape(event.text)}"))
        elif isinstance(event, ConsoleLine):
            hide = hider(target.hide)
            if target.console_channel and not (hide and hide(event.text)):
                out.append((target.console_channel, "console", console_safe(event.text)))
        elif isinstance(event, PlayerJoined | PlayerLeft) and target.event_channel:
            verb, icon = ("joined", "➡️") if isinstance(event, PlayerJoined) else ("left", "⬅️")
            out.append((target.event_channel, "text",
                        f"{icon} **{escape(event.player.name)}** {verb} {escape(target.name)}"))
        elif isinstance(event, StateChanged):
            previous = self._last_state.get(target.server)
            self._last_state[target.server] = event.state
            if target.event_channel and previous is not None and previous != event.state:
                out.append((target.event_channel, "text", state_line(target.name, event.state)))
        return out

    async def _follow(self, target: RelayTarget) -> None:
        hooks = self.hooks(target.game)
        with contextlib.suppress(Exception):  # best effort: the first state event then isn't announced
            self._last_state[target.server] = (await self.port.get_status(target.server)).state
        delay = self.retry_min
        while True:
            events = self.port.events(target.server)
            try:
                async for event in events:
                    delay = self.retry_min
                    for item in self.interpret(event, hooks):
                        for channel, kind, text in self._render_one(target, item):
                            self._buffer(channel).add(kind, text)
                        await self._tell(item)
                log.info("event stream for %s ended; reconnecting", target.name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep relaying other servers; retry this one
                log.warning("event stream for %s failed (%s); retrying in %.1fs", target.name, exc, delay)
            finally:
                aclose = getattr(events, "aclose", None)
                if aclose is not None:
                    with contextlib.suppress(Exception):
                        await aclose()
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.retry_max)

    async def _tell(self, event: ServerEvent) -> None:
        for listener in self.listeners:
            try:
                await listener(event)
            except Exception:  # noqa: BLE001 - a listener bug must not stop the relay
                log.exception("relay listener failed on %s", type(event).__name__)

    # -------------------------------------------------------------------------- sending

    def _buffer(self, channel_id: int) -> ChannelBuffer:
        buf = self._buffers.get(channel_id)
        if buf is None:
            buf = self._buffers[channel_id] = ChannelBuffer(channel_id, self.max_pending)
        task = self._senders.get(channel_id)
        if task is None or task.done():
            self._senders[channel_id] = asyncio.create_task(self._sender(buf), name=f"relay-send-{channel_id}")
        return buf

    async def _sender(self, buf: ChannelBuffer) -> None:
        while True:
            await buf.ready.wait()
            await asyncio.sleep(self.flush_interval)  # gather a burst into one message
            await self._send(buf)

    async def _send(self, buf: ChannelBuffer) -> None:
        if self._clock() < buf.muted_until:
            buf.drop()
            return
        for text in buf.take():
            try:
                await self.sink.send(buf.channel_id, text)
            except Exception as exc:  # noqa: BLE001 - a missing channel or permission must not stop the relay
                wait = min(self.send_retry_max, self.send_retry_min * 2 ** buf.failures)
                buf.failures += 1
                buf.muted_until = self._clock() + wait
                buf.lost = True
                log.warning("could not post to channel %s: %s. Leaving it alone for %.0f s; pick another "
                            "channel in /setup if it was deleted.", buf.channel_id, exc, wait)
                return
            buf.failures = 0

    async def flush(self) -> None:
        """Send everything pending now (used at shutdown and in tests)."""
        for buf in list(self._buffers.values()):
            await self._send(buf)
