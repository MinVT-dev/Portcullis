# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/player id``: Gatekeeper V3's ``/bot utils steamid`` and ``uuid`` lookups in one command.

Steam IDs are converted between their three formats offline. Minecraft names and UUIDs are looked
up at Mojang's public API, the only network call here; answers are cached for an hour.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

STEAM64_BASE = 76561197960265728
_STEAM64 = re.compile(r"^7656119\d{10}$")
_STEAM2 = re.compile(r"^STEAM_[0-5]:([01]):(\d{1,10})$", re.IGNORECASE)
_STEAM3 = re.compile(r"^\[?U:1:(\d{1,10})\]?$", re.IGNORECASE)
_PROFILE = re.compile(r"^(?:https?://)?steamcommunity\.com/profiles/(7656119\d{10})/?$", re.IGNORECASE)
_VANITY = re.compile(r"^(?:https?://)?steamcommunity\.com/id/[^/\s]+/?$", re.IGNORECASE)
_MC_NAME = re.compile(r"^[A-Za-z0-9_]{3,16}$")
_MC_UUID = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")

MOJANG_NAME = "https://api.mojang.com/users/profiles/minecraft/{}"
MOJANG_UUID = "https://sessionserver.mojang.com/session/minecraft/profile/{}"
CACHE_FOR = 3600.0
TIMEOUT = 5.0


@dataclass(frozen=True)
class SteamIds:
    steam64: str
    steam2: str
    steam3: str

    @property
    def profile(self) -> str:
        return f"https://steamcommunity.com/profiles/{self.steam64}"


def steam_ids(value: str) -> SteamIds | None:
    """Any of SteamID64, STEAM_X:Y:Z, [U:1:N] or a /profiles/ link, in all three formats."""
    value = value.strip()
    account: int | None = None
    if m := _PROFILE.match(value):
        value = m.group(1)
    if _STEAM64.match(value):
        account = int(value) - STEAM64_BASE
    elif m := _STEAM2.match(value):
        account = int(m.group(2)) * 2 + int(m.group(1))
    elif m := _STEAM3.match(value):
        account = int(m.group(1))
    if account is None or not 0 < account < 2**32:
        return None
    return SteamIds(steam64=str(account + STEAM64_BASE), steam2=f"STEAM_0:{account & 1}:{account >> 1}",
                    steam3=f"[U:1:{account}]")


def is_vanity_link(value: str) -> bool:
    return bool(_VANITY.match(value.strip()))


def dashed(uuid_hex: str) -> str:
    h = uuid_hex.replace("-", "").lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


@dataclass(frozen=True)
class MinecraftProfile:
    name: str
    uuid: str  # dashed


class MojangLookup:
    """Name to UUID and back, through Mojang's public API, with a small time-limited cache."""

    def __init__(self, session_factory: type[aiohttp.ClientSession] = aiohttp.ClientSession) -> None:
        self._session_factory = session_factory
        self._cache: dict[str, tuple[float, MinecraftProfile | None]] = {}

    @staticmethod
    def wants(value: str) -> bool:
        value = value.strip()
        return bool(_MC_NAME.match(value) or _MC_UUID.match(value))

    async def find(self, value: str) -> MinecraftProfile | None:
        """The profile for a name or UUID, or None when Mojang has none. Raises on network trouble."""
        value = value.strip()
        if _MC_UUID.match(value):
            key, url = "u:" + value.replace("-", "").lower(), MOJANG_UUID.format(value.replace("-", "").lower())
        elif _MC_NAME.match(value):
            key, url = "n:" + value.lower(), MOJANG_NAME.format(value)
        else:
            return None
        hit = self._cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < CACHE_FOR:
            return hit[1]
        async with self._session_factory(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session, \
                session.get(url, allow_redirects=False) as resp:
            if resp.status in (204, 404):
                profile = None
            elif resp.status == 200:
                data = await resp.json(content_type=None)
                raw_id, name = str(data.get("id", "")), str(data.get("name", ""))
                profile = MinecraftProfile(name=name, uuid=dashed(raw_id)) \
                    if _MC_UUID.match(raw_id) and _MC_NAME.match(name) else None
            else:
                raise RuntimeError(f"Mojang answered {resp.status}")
        if len(self._cache) > 1000:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), profile)
        return profile
