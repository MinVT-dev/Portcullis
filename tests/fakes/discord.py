# SPDX-License-Identifier: AGPL-3.0-or-later
"""Just enough of discord.py's objects to run command handlers and the relay sink without Discord."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


@dataclass(frozen=True)
class FakeRole:
    id: int


@dataclass
class FakeMember:
    id: int
    roles: list[FakeRole] = field(default_factory=list)
    manage_guild: bool = False
    display_name: str = "member"
    dms: list[str] = field(default_factory=list)

    async def send(self, content: str, **kwargs: Any) -> None:
        self.dms.append(content)

    @property
    def guild_permissions(self) -> SimpleNamespace:
        return SimpleNamespace(manage_guild=self.manage_guild)

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


@dataclass
class FakeChannel:
    id: int
    can_post: bool = True
    sent: list[dict[str, Any]] = field(default_factory=list)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    def permissions_for(self, member: Any) -> SimpleNamespace:
        return SimpleNamespace(view_channel=self.can_post, send_messages=self.can_post)

    async def send(self, content: str | None = None, **kwargs: Any) -> SimpleNamespace:
        self.sent.append({"content": content, **kwargs})
        return SimpleNamespace(id=len(self.sent))

    async def fetch_message(self, message_id: int) -> FakeMessage:
        return FakeMessage(self, message_id)

    def get_partial_message(self, message_id: int) -> FakeMessage:
        return FakeMessage(self, message_id)


@dataclass
class FakeMessage:
    channel: FakeChannel
    id: int
    edits: list[dict[str, Any]] = field(default_factory=list)

    async def edit(self, **kwargs: Any) -> None:
        self.channel.sent.append({"edit": self.id, **kwargs})


class FakeResponse:
    def __init__(self, interaction: FakeInteraction) -> None:
        self._i = interaction
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str | None = None, **kwargs: Any) -> None:
        assert not self._done, "responded twice"
        assert kwargs.get("view", ...) is not None, "discord.py fails on view=None for a new message"
        self._done = True
        self._i.replies.append({"content": content, **kwargs})

    async def defer(self, **kwargs: Any) -> None:
        assert not self._done, "deferred after responding"
        self._done = True
        self._i.deferred = kwargs

    async def edit_message(self, **kwargs: Any) -> None:
        assert not self._done, "responded twice"
        self._done = True
        self._i.edits.append(kwargs)

    async def send_modal(self, modal: Any) -> None:
        assert not self._done, "responded twice"
        self._done = True
        self._i.modal = modal


class FakeFollowup:
    def __init__(self, interaction: FakeInteraction) -> None:
        self._i = interaction

    async def send(self, content: str | None = None, **kwargs: Any) -> None:
        assert self._i.response.is_done(), "followup before the first response"
        assert kwargs.get("view", ...) is not None, "discord.py fails on view=None for a new message"
        self._i.replies.append({"content": content, **kwargs})


class FakeInteraction:
    def __init__(self, user: FakeMember, *, guild_id: int | None = 1, channel_id: int = 55) -> None:
        self.user = user
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.guild = SimpleNamespace(id=guild_id, me=FakeMember(id=999), get_channel=lambda cid: None) \
            if guild_id else None
        self.edits: list[dict[str, Any]] = []
        self.modal: Any = None
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)
        self.replies: list[dict[str, Any]] = []
        self.deferred: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        """Every reply's content, for simple assertions."""
        return "\n".join(r["content"] or "" for r in self.replies)

    @property
    def embed(self) -> Any:
        return self.replies[-1].get("embed")

    async def original_response(self) -> SimpleNamespace:
        return SimpleNamespace(id=4242)

    async def edit_original_response(self, **kwargs: Any) -> None:
        self.edits.append(kwargs)


class FakeClient:
    """Stands in for ``discord.Client`` in ``DiscordSink``. Channels not in ``cached`` need a fetch."""

    def __init__(self, *channels: FakeChannel, cached: bool = True) -> None:
        self.channels = {c.id: c for c in channels}
        self.cached = set(self.channels) if cached else set()
        self.fetched: list[int] = []
        self.users: dict[int, FakeMember] = {}
        self.user = FakeMember(999)
        self.latency = 0.05

    def get_user(self, user_id: int) -> FakeMember:
        return self.users.setdefault(user_id, FakeMember(user_id))

    async def fetch_user(self, user_id: int) -> FakeMember:
        return self.get_user(user_id)

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channels[channel_id] if channel_id in self.cached else None

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        self.fetched.append(channel_id)
        return self.channels[channel_id]
