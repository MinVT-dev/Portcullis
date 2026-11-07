"""Build portcullis/assets/games.json: one entry per game AMP can run, with art URLs.

Where the list comes from:
  * docs/AMP2_SUPPORTED.md, the game and service tables (the list the project agreed on),
  * plus any template in CubeCoders/AMPTemplates that the doc does not list yet,
  * plus the two dedicated modules (Minecraft Java Edition and Rust).

Where the art comes from, in order:
  1. The template's own ``Meta.DisplayImageSource``. This is the image AMP shows for the
     game in its panel, and AMP 3 copies it onto every shard as ``displayImageSource``.
     ``steam:<appid>`` names a Steam app; ``url:<url>`` is a direct image.
  2. Steam's public store API (IStoreBrowseService/GetItems, no key needed) for every Steam
     app id, which gives the exact current URLs of the header (banner), library logo,
     library hero and community icon. New Steam apps only have hashed asset paths, so the
     URLs are resolved here rather than built from a pattern.

Nothing is downloaded into the repo. With --verify every candidate URL is fetched once, so
the manifest only lists URLs that answered 200, together with the image size.

Usage:
  git clone --depth 1 https://github.com/CubeCoders/AMPTemplates.git /tmp/AMPTemplates
  python tools/build_game_assets.py --templates /tmp/AMPTemplates --verify
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import io
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "AMP2_SUPPORTED.md"
OUT = ROOT / "portcullis" / "assets" / "games.json"

STEAM_API = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
STEAM_ASSETS = "https://shared.fastly.steamstatic.com/store_item_assets/"
STEAM_COMMUNITY_ICON = "https://shared.fastly.steamstatic.com/community_assets/images/apps/{appid}/{hash}.jpg"
STEAM_URL_APPID = re.compile(r"steamstatic\.com/(?:store_item_assets/)?steam/apps/(\d+)/")
# github.com/<owner>/<repo>/blob/<ref>/<path>?raw=true is a redirect to the raw file.
GITHUB_BLOB = re.compile(r"^https://github\.com/([^/]+/[^/]+)/blob/([^/]+)/(.+)$")
USER_AGENT = "portcullis-bot asset builder (+https://github.com/MinVT-dev/Portcullis)"
ART_SLOTS = ("banner", "logo", "icon", "hero", "capsule")

# Games on dedicated AMP modules have no template in AMPTemplates.
DEDICATED = [
    {"template": "minecraft", "name": "Minecraft Java Edition", "module": "minecraft", "image_source": None},
    {"template": "rust", "name": "Rust", "module": "rust", "image_source": "steam:252490"},
]


# ---------------------------------------------------------------- the game list
def parse_doc(text: str) -> list[dict]:
    """Read the game and service tables from AMP2_SUPPORTED.md."""
    rows: list[dict] = []
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].lower()
            if title.startswith("games on the generic"):
                section = "game"
            elif title.startswith("services and tools"):
                section = "service"
            else:
                section = None
            continue
        if section is None or not line.startswith("| ") or "`" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        row = {"template": cells[1].strip("`"), "name": cells[0], "kind": section}
        if section == "game":
            row.update(join_leave=cells[3] == "yes", chat=cells[4] == "yes")
        else:
            row.update(join_leave=cells[4] == "yes", chat=False)
        rows.append(row)
    return rows


def read_kvp(path: pathlib.Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.startswith("Meta."):
            out[key] = value.strip()
    return out


def git_commit(path: pathlib.Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "log", "-1", "--format=%H %cs"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# ---------------------------------------------------------------- image sources
def parse_image_source(source: str | None) -> tuple[int | None, str | None]:
    """Return (steam app id, direct url) for a Meta.DisplayImageSource value."""
    if not source:
        return None, None
    if source.startswith("steam:"):
        digits = source[6:].strip()
        return (int(digits) if digits.isdigit() else None), None
    url = source[4:] if source.startswith("url:") else source
    if url.startswith(("http://", "https://")):
        url = GITHUB_BLOB.sub(r"https://raw.githubusercontent.com/\1/\2/\3", url.replace("?raw=true", ""))
        match = STEAM_URL_APPID.search(url)
        return (int(match.group(1)) if match else None), url
    return None, None  # internal:<name> is an image bundled inside the AMP panel


def http_get(url: str, timeout: float = 30.0, tries: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def steam_assets(appids: list[int]) -> dict[int, dict]:
    """Ask Steam's store API for the asset file names of each app."""
    found: dict[int, dict] = {}
    for i in range(0, len(appids), 40):
        batch = appids[i : i + 40]
        payload = {
            "ids": [{"appid": a} for a in batch],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_assets": True, "include_assets_without_overrides": True},
        }
        url = STEAM_API + "?" + urllib.parse.urlencode({"input_json": json.dumps(payload)})
        data = json.loads(http_get(url))
        for item in data.get("response", {}).get("store_items", []):
            if item.get("success") == 1 and item.get("assets"):
                found[item["appid"]] = item
    return found


def steam_urls(appid: int, info: dict | None) -> dict[str, list[str]]:
    """Candidate URLs per art slot, best first.

    Steam's answer comes first. ``assets_without_overrides`` is the game's normal art; ``assets``
    can hold seasonal sale art, so it is only a fallback. The old un-hashed paths come last:
    many older apps (and some delisted ones) still serve them, and they are the only place a
    plain ``logo.png`` lives for games that have no ``library_logo`` in the API.
    """
    out: dict[str, list[str]] = {slot: [] for slot in ART_SLOTS}
    keys = {"banner": "header", "logo": "library_logo", "hero": "library_hero", "capsule": "library_capsule"}
    for group in ("assets_without_overrides", "assets"):
        assets = (info or {}).get(group) or {}
        fmt = assets.get("asset_url_format", f"steam/apps/{appid}/${{FILENAME}}")
        for slot, key in keys.items():
            if assets.get(key):
                out[slot].append(STEAM_ASSETS + fmt.replace("${FILENAME}", assets[key]))
        if assets.get("community_icon"):
            out["icon"].append(STEAM_COMMUNITY_ICON.format(appid=appid, hash=assets["community_icon"]))
    legacy = f"{STEAM_ASSETS}steam/apps/{appid}/"
    out["banner"].append(legacy + "header.jpg")
    out["logo"].append(legacy + "logo.png")
    out["hero"].append(legacy + "library_hero.jpg")
    out["capsule"].append(legacy + "library_600x900.jpg")
    return {slot: list(dict.fromkeys(urls)) for slot, urls in out.items() if urls}


def probe(url: str) -> dict | None:
    """Fetch an image; return its size, or None when it does not load."""
    try:
        body = http_get(url, timeout=30)
    except Exception:  # noqa: BLE001 (any failure means "not usable")
        return None
    size, alpha = None, False
    try:
        from PIL import Image

        with Image.open(io.BytesIO(body)) as img:
            size = list(img.size)
            alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    except Exception:  # noqa: BLE001 (SVG and WebP without a codec still count as images)
        pass
    if size is None and b"<svg" not in body[:2048] and not body[:12].startswith(b"RIFF"):
        return None  # an HTML error page, not an image
    return {"url": url, "size": size, "alpha": alpha or size is None}


# ---------------------------------------------------------------- build
def collect(templates_dir: pathlib.Path) -> list[dict]:
    doc_rows = parse_doc(DOC.read_text(encoding="utf-8"))
    in_doc = {r["template"] for r in doc_rows}
    entries: list[dict] = []
    for row in doc_rows:
        kvp_path = templates_dir / f"{row['template']}.kvp"
        meta = read_kvp(kvp_path) if kvp_path.exists() else {}
        entries.append(
            {
                **row,
                "module": "generic",
                "in_docs": True,
                "in_templates": kvp_path.exists(),
                "image_source": meta.get("Meta.DisplayImageSource") or None,
            }
        )
    for kvp_path in sorted(templates_dir.glob("*.kvp")):
        if kvp_path.stem in in_doc:
            continue
        meta = read_kvp(kvp_path)
        if "Meta.DisplayName" not in meta:
            continue  # not an application template
        entries.append(
            {
                "template": kvp_path.stem,
                "name": meta["Meta.DisplayName"],
                "kind": "game",
                "join_leave": None,
                "chat": None,
                "module": "generic",
                "in_docs": False,
                "in_templates": True,
                "image_source": meta.get("Meta.DisplayImageSource") or None,
            }
        )
    for d in DEDICATED:
        entries.append({**d, "kind": "game", "join_leave": None, "chat": None, "in_docs": True, "in_templates": False})
    return entries


def build(templates_dir: pathlib.Path, verify: bool) -> dict:
    entries = collect(templates_dir)
    template_images: dict[str, str | None] = {}
    for e in entries:
        e["steam_appid"], template_images[e["template"]] = parse_image_source(e["image_source"])

    steam = steam_assets(sorted({e["steam_appid"] for e in entries if e["steam_appid"]}))
    candidates = {
        e["template"]: steam_urls(e["steam_appid"], steam.get(e["steam_appid"])) if e["steam_appid"] else {}
        for e in entries
    }

    checked: dict[str, dict | None] = {}
    if verify:
        urls = {u for c in candidates.values() for us in c.values() for u in us}
        urls |= {u for u in template_images.values() if u}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            checked = dict(zip(sorted(urls), pool.map(probe, sorted(urls)), strict=True))

    def usable(url: str) -> bool:
        return not verify or bool(checked.get(url))

    for e in entries:
        art: dict[str, str] = {}
        for slot, urls in candidates[e["template"]].items():
            first = next((u for u in urls if usable(u)), None)
            if first:
                art[slot] = first
        steam_art = bool(art)
        own = template_images[e["template"]]
        if own and usable(own) and own not in art.values():
            # The template's own image fills a gap. Transparent images are logos (a wordmark
            # can be wide too); an opaque wide image is a banner; anything else is a logo.
            info = checked.get(own) or {}
            w_h = info.get("size")
            wide = bool(w_h and w_h[1] and w_h[0] / w_h[1] >= 1.6)
            slot = "banner" if wide and not info.get("alpha") else "logo"
            art.setdefault(slot, own)
        e["art"] = {slot: art[slot] for slot in ART_SLOTS if slot in art}
        e["art_size"] = {
            slot: checked[u]["size"] for slot, u in e["art"].items() if checked.get(u) and checked[u]["size"]
        }
        e["art_source"] = "steam" if steam_art else ("template" if art else "none")

    entries.sort(key=lambda x: (x["kind"] != "game", x["name"].casefold()))
    games = [e for e in entries if e["kind"] == "game"]
    supported = [g for g in games if g["join_leave"] or g["module"] != "generic"]
    return {
        "generated": dt.date.today().isoformat(),
        "templates_commit": git_commit(templates_dir),
        "verified": verify,
        "note": (
            "Art URLs point at the publishers' own images (Steam CDN or the template's image). "
            "They are not part of Portcullis and are not licensed to it; see docs/GAME_ASSETS.md."
        ),
        "coverage": {
            "games": len(games),
            "supported_games": len(supported),
            **{slot: sum(1 for g in games if slot in g["art"]) for slot in ART_SLOTS},
            "supported_banner": sum(1 for g in supported if "banner" in g["art"]),
            "supported_logo": sum(1 for g in supported if "logo" in g["art"]),
            "no_art": sum(1 for g in games if not g["art"]),
        },
        "games": entries,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--templates", type=pathlib.Path, required=True, help="a checkout of CubeCoders/AMPTemplates")
    ap.add_argument("--verify", action="store_true", help="fetch every URL and keep only those that load")
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    args = ap.parse_args(argv)
    manifest = build(args.templates, args.verify)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest["coverage"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
