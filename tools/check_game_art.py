# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check that every art link in ``portcullis/assets/games.json`` still answers with an image.

The registry holds links, not files, and most are Steam CDN paths with a content hash in them. When
a publisher uploads new art the old path stops working and the embed silently shows no image. This
finds those links without needing a checkout of the AMP templates::

    python tools/check_game_art.py            # exit code 1 if any link is dead (404 or not an image)
    python tools/check_game_art.py --json     # machine-readable list of dead links

To repair dead links, rebuild the registry: ``tools/build_game_assets.py --templates <dir> --verify``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

import aiohttp

MANIFEST = pathlib.Path(__file__).resolve().parent.parent / "portcullis" / "assets" / "games.json"
# Wikimedia refuses clients that don't say who they are, so the User-Agent names the project.
HEADERS = {"Range": "bytes=0-0",
           "User-Agent": "portcullis-art-check/1.0 (+https://github.com/MinVT-dev/Portcullis)"}
# The host refused this checker (often by its address, as on a CI runner). That says nothing about
# whether Discord can load the image, so these are reported but don't fail the check.
BLOCKED = (401, 403, 429)


def art_links(manifest: dict) -> list[tuple[str, str, str]]:
    """Every (template, slot, url) in the manifest."""
    return [(game.get("template") or game.get("name") or "?", slot, url)
            for game in manifest.get("games", [])
            for slot, url in (game.get("art") or {}).items() if url]


async def check(links: list[tuple[str, str, str]], *, concurrency: int = 16, timeout: float = 30.0,
                attempts: int = 3) -> list[dict[str, object]]:
    """Fetch the first byte of each link. Returns the ones that didn't answer with an image, each with
    the reason and ``blocked`` set when the host refused the checker rather than lacking the image."""
    gate = asyncio.Semaphore(concurrency)
    dead: list[dict[str, object]] = []

    async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async def one(template: str, slot: str, url: str) -> None:
            reason = ""
            for attempt in range(attempts):
                try:
                    async with gate, session.get(url, headers=HEADERS) as resp:
                        kind = resp.headers.get("Content-Type", "")
                        if resp.status < 400 and kind.startswith("image/"):
                            return
                        reason = f"HTTP {resp.status}" if resp.status >= 400 else f"not an image ({kind or 'no type'})"
                        if resp.status in (404, 410, *BLOCKED):
                            break  # a retry won't change the answer
                except (aiohttp.ClientError, TimeoutError) as exc:
                    reason = type(exc).__name__
                await asyncio.sleep(2 * (attempt + 1))
            dead.append({"template": template, "slot": slot, "url": url, "reason": reason,
                         "blocked": reason in {f"HTTP {code}" for code in BLOCKED}})

        await asyncio.gather(*(one(*link) for link in links))
    return sorted(dead, key=lambda d: (str(d["template"]), str(d["slot"])))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=pathlib.Path, default=MANIFEST)
    ap.add_argument("--json", action="store_true", help="print the dead links as JSON")
    args = ap.parse_args(argv)
    links = art_links(json.loads(args.manifest.read_text(encoding="utf-8")))
    failed = asyncio.run(check(links))
    dead = [d for d in failed if not d["blocked"]]
    if args.json:
        print(json.dumps(failed, indent=1))
    else:
        for d in failed:
            print(f"{'BLOCKED' if d['blocked'] else 'DEAD'} {d['template']} {d['slot']}: {d['reason']}  {d['url']}")
        print(f"{len(links) - len(failed)} of {len(links)} art links are alive; {len(dead)} dead; "
              f"{len(failed) - len(dead)} could not be checked (the host refused this checker).")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
