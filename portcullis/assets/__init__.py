# SPDX-License-Identifier: AGPL-3.0-or-later
"""Game art: a banner, logo and icon for every game AMP can run.

``games.json`` is built by ``tools/build_game_assets.py`` from docs/AMP2_SUPPORTED.md and the
AMP templates. It holds URLs, not images: the art belongs to each game's publisher and is
served from Steam's CDN or the template's own link (see docs/GAME_ASSETS.md). Games with no
art get Portcullis' own placeholders in ``fallback/``, which ship with the package.

Find a game by whatever you have at hand::

    art = game_art("valheim")                    # template name
    art = game_art("Valheim")                    # display name, any case or punctuation
    art = game_art(image_source="steam:892970")  # a shard's displayImageSource from AMP 3

``game_art`` always returns something. For a Discord embed::

    if art.banner_url:
        embed.set_image(url=art.banner_url)
    else:  # attach the placeholder and point the embed at it
        file = discord.File(art.banner_file, filename="banner.png")
        embed.set_image(url="attachment://banner.png")
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

__all__ = ["FALLBACK_BANNER", "FALLBACK_LOGO", "GameArt", "all_games", "find_game", "game_art", "manifest"]

_HERE = Path(__file__).resolve().parent
FALLBACK_BANNER = _HERE / "fallback" / "game-banner.png"
FALLBACK_LOGO = _HERE / "fallback" / "game-logo.png"
_STEAM_LEGACY = "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/{file}"


@dataclass(frozen=True)
class GameArt:
    """Art for one game. A ``*_url`` is None when there is no real image for that slot."""

    template: str | None
    name: str
    kind: str = "game"
    supported: bool = False  # join/leave patterns in the template, or a dedicated module
    steam_appid: int | None = None
    banner_url: str | None = None  # wide, about 2.14:1 (Steam header 460x215)
    logo_url: str | None = None  # transparent logo, or the template's own squarish image
    icon_url: str | None = None  # small square (Steam community icon)
    hero_url: str | None = None  # very wide background with no text (Steam library hero)
    capsule_url: str | None = None  # portrait cover, 600x900
    known: bool = True  # False when built on the fly for a game not in games.json
    sizes: dict[str, list[int]] = field(default_factory=dict, compare=False, repr=False)

    @property
    def thumbnail_url(self) -> str | None:
        """Best small image for an embed thumbnail: the logo, else the icon."""
        return self.logo_url or self.icon_url

    @property
    def banner_file(self) -> Path:
        """Portcullis' placeholder banner, for when ``banner_url`` is None."""
        return FALLBACK_BANNER

    @property
    def logo_file(self) -> Path:
        """Portcullis' placeholder logo, for when ``thumbnail_url`` is None."""
        return FALLBACK_LOGO

    def cached(self, slot: str, cache_dir: Path) -> Path | None:
        """The copy ``tools/fetch_game_assets.py`` saved for this slot, if there is one."""
        if not self.template:
            return None
        matches = sorted((cache_dir / self.template).glob(f"{slot}.*"))
        return matches[0] if matches else None


def _key(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.casefold())


def _steam_appid(source: str) -> int | None:
    kind, _, rest = source.partition(":")
    rest = rest.strip()
    return int(rest) if kind.casefold() == "steam" and rest.isdigit() else None


@cache
def manifest() -> dict[str, Any]:
    """The whole games.json, parsed once."""
    with resources.files(__package__).joinpath("games.json").open(encoding="utf-8") as f:
        return json.load(f)


@cache
def _index() -> tuple[dict[str, dict], dict[str, dict], dict[str, dict], dict[int, dict]]:
    by_template: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    by_appid: dict[int, dict] = {}
    for entry in manifest()["games"]:
        by_template[entry["template"].casefold()] = entry
        by_name.setdefault(_key(entry["name"]), entry)
        if entry.get("image_source"):
            by_source.setdefault(entry["image_source"].strip().casefold(), entry)
        if entry.get("steam_appid"):
            by_appid.setdefault(entry["steam_appid"], entry)
    return by_template, by_name, by_source, by_appid


def _from_entry(entry: dict) -> GameArt:
    art = entry.get("art", {})
    return GameArt(
        template=entry["template"],
        name=entry["name"],
        kind=entry.get("kind", "game"),
        supported=bool(entry.get("join_leave")) or entry.get("module", "generic") != "generic",
        steam_appid=entry.get("steam_appid"),
        banner_url=art.get("banner"),
        logo_url=art.get("logo"),
        icon_url=art.get("icon"),
        hero_url=art.get("hero"),
        capsule_url=art.get("capsule"),
        sizes=entry.get("art_size", {}),
    )


def all_games() -> list[GameArt]:
    """Every entry in games.json, games first, then services."""
    return [_from_entry(e) for e in manifest()["games"]]


def find_game(query: str | None = None, *, image_source: str | None = None) -> GameArt | None:
    """Look a game up by template name, display name or AMP image source. None if unknown."""
    by_template, by_name, by_source, by_appid = _index()
    if query and query.strip():
        q = query.strip()
        entry = by_template.get(q.casefold()) or by_name.get(_key(q))
        if entry:
            return _from_entry(entry)
    if image_source and image_source.strip():
        src = image_source.strip()
        appid = _steam_appid(src)
        entry = by_source.get(src.casefold()) or (by_appid.get(appid) if appid else None)
        if entry:
            return _from_entry(entry)
    return None


def game_art(query: str | None = None, *, image_source: str | None = None, name: str | None = None) -> GameArt:
    """Art for a game, never None.

    A game missing from games.json (a template added after the manifest was built) still gets
    art when AMP gives an image source: ``steam:<appid>`` becomes Steam's standard header and
    logo paths, and ``url:<link>`` is used as the logo. These are not checked, so ``known`` is
    False. With nothing to go on, the result has no URLs and the caller uses the placeholders.
    """
    found = find_game(query, image_source=image_source)
    if found:
        return found
    label = name or query or "Unknown game"
    src = (image_source or "").strip()
    appid = _steam_appid(src)
    if appid:
        return GameArt(
            template=None,
            name=label,
            steam_appid=appid,
            banner_url=_STEAM_LEGACY.format(appid=appid, file="header.jpg"),
            logo_url=_STEAM_LEGACY.format(appid=appid, file="logo.png"),
            known=False,
        )
    link = src[4:] if src.casefold().startswith("url:") else src
    if link.startswith("https://"):
        return GameArt(template=None, name=label, logo_url=link, known=False)
    return GameArt(template=None, name=label, known=False)
