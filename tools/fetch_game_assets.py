"""Download the game art listed in portcullis/assets/games.json into a local cache.

The bot can link every image straight from Steam or the template's own URL, so this is
optional. Use it to look the art over, to work offline, or to serve images from your own
host. The cache is git-ignored: the images belong to their publishers and stay out of the repo.

Usage:
  python -m tools.fetch_game_assets                       # banner and logo, every game
  python -m tools.fetch_game_assets --supported --slots banner,logo,icon
  python -m tools.fetch_game_assets --games valheim,palworld --force

Files land at <cache>/<template>/<slot>.<ext>, which GameArt.cached(slot, cache) finds.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import pathlib
import sys
import urllib.request

from portcullis.assets import all_games

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ROOT / "game-art"
USER_AGENT = "portcullis-bot asset fetcher (+https://github.com/MinVT-dev/Portcullis)"
TYPES = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"RIFF": ".webp", b"GIF8": ".gif"}


def extension(body: bytes, url: str) -> str:
    for magic, ext in TYPES.items():
        if body.startswith(magic):
            return ext
    if b"<svg" in body[:2048]:
        return ".svg"
    return pathlib.PurePosixPath(url.split("?", 1)[0]).suffix or ".bin"


def fetch(url: str, dest_stem: pathlib.Path) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (URLs from our own manifest)
        body = resp.read()
    dest = dest_stem.with_suffix(extension(body, url))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return dest.name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", type=pathlib.Path, default=DEFAULT_CACHE)
    ap.add_argument("--slots", default="banner,logo", help="comma list of banner,logo,icon,hero,capsule")
    ap.add_argument("--games", default="", help="comma list of template names (default: all)")
    ap.add_argument("--supported", action="store_true", help="only games with player tracking")
    ap.add_argument("--force", action="store_true", help="download again even if cached")
    args = ap.parse_args(argv)

    slots = [s.strip() for s in args.slots.split(",") if s.strip()]
    wanted = {g.strip().casefold() for g in args.games.split(",") if g.strip()}
    jobs: list[tuple[str, pathlib.Path]] = []
    for art in all_games():
        if art.kind != "game" or (args.supported and not art.supported):
            continue
        if wanted and (art.template or "").casefold() not in wanted:
            continue
        for slot in slots:
            url = getattr(art, f"{slot}_url", None)
            if not url or (not args.force and art.cached(slot, args.cache)):
                continue
            jobs.append((url, args.cache / str(art.template) / slot))

    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, url, stem): (url, stem) for url, stem in jobs}
        for fut in concurrent.futures.as_completed(futures):
            url, stem = futures[fut]
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 (report and carry on)
                failed += 1
                print(f"failed {stem.parent.name}/{stem.name}: {exc} ({url})", file=sys.stderr)
    print(f"downloaded {len(jobs) - failed} of {len(jobs)} images into {args.cache}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
