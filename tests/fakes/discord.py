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

    async def send(self, content: str, **kwargs: Any) -> None:
        self.sent.append({"content": content, **kwargs})


class FakeResponse:
    def __init__(self, interaction: FakeInteraction) -> None:
        self._i = interaction
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str | None = None, **kwargs: Any) -> None:
        assert not self._done, "responded twice"
        self._done = True
        self._i.replies.append({"content": content, **kwargs})

    async def defer(self, **kwargs: Any) -> None:
        assert not self._done, "deferred after responding"
        self._done = True
        self._i.deferred = kwargs


class FakeFollowup:
    def __init__(self, interaction: FakeInteraction) -> None:
        self._i = interaction

    async def send(self, content: str | None = None, **kwargs: Any) -> None:
        assert self._i.response.is_done(), "followup before the first response"
        self._i.replies.append({"content": content, **kwargs})


class FakeInteraction:
    def __init__(self, user: FakeMember, *, guild_id: int | None = 1) -> None:
        self.user = user
        self.guild_id = guild_id
        self.guild = SimpleNamespace(id=guild_id, me=FakeMember(id=999)) if guild_id else None
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


class FakeClient:
    """Stands in for ``discord.Client`` in ``DiscordSink``. Channels not in ``cached`` need a fetch."""

    def __init__(self, *channels: FakeChannel, cached: bool = True) -> None:
        self.channels = {c.id: c for c in channels}
        self.cached = set(self.channels) if cached else set()
        self.fetched: list[int] = []

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channels[channel_id] if channel_id in self.cached else None

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        self.fetched.append(channel_id)
        return self.channels[channel_id]
