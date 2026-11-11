"""Game art registry: the manifest matches the docs, and lookups always give something usable."""

from __future__ import annotations

import pathlib

import pytest

from portcullis.assets import FALLBACK_BANNER, FALLBACK_LOGO, all_games, find_game, game_art, manifest
from tools.build_game_assets import DOC, parse_doc, parse_image_source, steam_urls

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_every_documented_game_is_in_the_manifest():
    documented = {row["template"] for row in parse_doc(DOC.read_text(encoding="utf-8"))}
    listed = {e["template"] for e in manifest()["games"]}
    assert documented - listed == set(), "rebuild games.json: python tools/build_game_assets.py ..."
    assert {"minecraft", "rust"} <= listed  # the two dedicated modules


def test_doc_parse_counts_match_the_doc():
    rows = parse_doc(DOC.read_text(encoding="utf-8"))
    games = [r for r in rows if r["kind"] == "game"]
    assert len(games) == 225
    assert sum(r["join_leave"] for r in games) == 159
    assert len([r for r in rows if r["kind"] == "service"]) == 19


def test_coverage_numbers_are_honest():
    cov = manifest()["coverage"]
    games = [e for e in manifest()["games"] if e["kind"] == "game"]
    assert cov["games"] == len(games)
    assert cov["banner"] == sum(1 for g in games if "banner" in g["art"])
    assert cov["logo"] == sum(1 for g in games if "logo" in g["art"])


def test_manifest_urls_are_https():
    for entry in manifest()["games"]:
        for slot, url in entry["art"].items():
            assert url.startswith("https://"), (entry["template"], slot, url)


@pytest.mark.parametrize("query", ["valheim", "Valheim", "VALHEIM", " valheim "])
def test_find_by_template_or_name(query):
    art = find_game(query)
    assert art is not None and art.template == "valheim"
    assert art.steam_appid == 892970
    assert art.supported


def test_find_by_name_ignores_punctuation():
    art = find_game("dont starve together")
    assert art is not None and art.name == "Don't Starve Together"


def test_find_by_image_source():
    assert find_game(image_source="steam:892970").template == "valheim"
    assert find_game(image_source="STEAM:892970").template == "valheim"


def test_unknown_game_returns_none_from_find():
    assert find_game("definitely not a game") is None
    assert find_game() is None


def test_game_art_never_none_and_falls_back():
    art = game_art("definitely not a game")
    assert not art.known and art.banner_url is None and art.thumbnail_url is None
    assert art.banner_file == FALLBACK_BANNER and art.banner_file.is_file()
    assert art.logo_file == FALLBACK_LOGO and art.logo_file.is_file()


def test_game_art_builds_steam_urls_for_unlisted_apps():
    art = game_art("Brand New Game", image_source="steam:12345")
    assert not art.known and art.steam_appid == 12345
    assert art.banner_url.endswith("/steam/apps/12345/header.jpg")


def test_game_art_uses_template_url_for_unlisted_games():
    art = game_art(image_source="url:https://example.com/logo.png", name="Thing")
    assert art.logo_url == "https://example.com/logo.png" and art.name == "Thing"
    assert game_art(image_source="internal:Something").thumbnail_url is None


def test_dedicated_modules_count_as_supported():
    assert game_art("minecraft").supported
    assert game_art("Rust").steam_appid == 252490


def test_services_are_not_games():
    art = find_game("mariadb")
    assert art is not None and art.kind == "service" and not art.supported


def test_cached_finds_downloaded_file(tmp_path):
    art = find_game("valheim")
    assert art.cached("banner", tmp_path) is None
    (tmp_path / "valheim").mkdir()
    (tmp_path / "valheim" / "banner.jpg").write_bytes(b"\xff\xd8\xff")
    assert art.cached("banner", tmp_path) == tmp_path / "valheim" / "banner.jpg"


def test_all_games_has_no_duplicate_templates():
    templates = [a.template for a in all_games()]
    assert len(templates) == len(set(templates))


def test_parse_image_source():
    assert parse_image_source("steam:892970") == (892970, None)
    assert parse_image_source("internal:MinecraftBedrock") == (None, None)
    appid, url = parse_image_source(
        "url:https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/1623730/abc/header.jpg"
    )
    assert appid == 1623730 and url.endswith("header.jpg")
    _, url = parse_image_source("url:https://github.com/OpenRA/OpenRA/blob/bleed/mods/ra/icon-3x.png?raw=true")
    assert url == "https://raw.githubusercontent.com/OpenRA/OpenRA/bleed/mods/ra/icon-3x.png"


def test_steam_urls_prefer_normal_art_over_seasonal():
    info = {
        "assets": {"asset_url_format": "steam/apps/1/${FILENAME}", "header": "sale/header_alt.jpg"},
        "assets_without_overrides": {"asset_url_format": "steam/apps/1/${FILENAME}", "header": "h/header.jpg"},
    }
    urls = steam_urls(1, info)
    assert urls["banner"][0].endswith("/steam/apps/1/h/header.jpg")
    assert urls["banner"][1].endswith("sale/header_alt.jpg")
    assert urls["banner"][-1].endswith("/steam/apps/1/header.jpg")  # legacy path last


def test_game_art_cache_is_git_ignored():
    assert "game-art/" in (ROOT / ".gitignore").read_text().splitlines()


def test_art_link_checker_lists_every_link() -> None:
    from tools.check_game_art import art_links

    links = art_links({"games": [{"template": "a", "art": {"banner": "https://x/b.jpg", "logo": None}},
                                 {"name": "B", "art": {"icon": "https://x/i.jpg"}}, {"template": "c"}]})
    assert links == [("a", "banner", "https://x/b.jpg"), ("B", "icon", "https://x/i.jpg")]
