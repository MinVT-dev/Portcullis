#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compare your AMP panel's live API spec with the one ``portcullis-api`` is generated from.

This runs on your own PC, not on GitHub: GitHub's runners can't reach a panel on a home network, and
the spec is only served after a login. Run it after every AMP update, or weekly from Task Scheduler
or cron (docs/DEVELOPING.md shows how).

    python -m tools.check_spec_drift                  # fetch from the panel in .env, compare with the pin
    python -m tools.check_spec_drift FILE.json        # compare a spec you downloaded yourself
    python -m tools.check_spec_drift --save new.json  # also keep the fetched spec (panel address removed)

Settings come from ``.env`` (AMP_URL, AMP_USERNAME, AMP_PASSWORD), as for the bot. Exit codes:
0 nothing breaking, 1 something breaking (re-pin and regenerate the client), 2 the spec couldn't be read.

How the fetch works: AMP hands out a one-use ticket for its API browser (POST /api/api-browser/ticket),
the browser page trades it for a cookie, and the page names the spec's address. The page's markup
hasn't been checked against a real panel, so if no spec is found the script says which addresses it
tried; download the spec from the API browser by hand and pass the file instead.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "portcullis-api"))

import aiohttp  # noqa: E402

from portcullis.config import load_env_file  # noqa: E402
from portcullis_api import AmpClient, Credentials  # noqa: E402
from portcullis_api.errors import AmpError  # noqa: E402
from portcullis_api.redact import Redactor  # noqa: E402

# Where a Scalar page usually says its spec lives, then a few common defaults.
_SPEC_REFS = (
    re.compile(r"""data-url\s*=\s*["']([^"']+)["']"""),
    re.compile(r"""["']?url["']?\s*:\s*["']([^"']+\.json[^"']*)["']"""),
    re.compile(r"""["']([^"'\s]*openapi[^"'\s]*\.json[^"']*)["']"""),
)
FALLBACKS = ("openapi/v1.json", "scalar/openapi.json", "swagger/v1/swagger.json", "webapi.json")


class SpecUnavailable(Exception):
    pass


def spec_urls(html: str, page_url: str, panel_url: str) -> list[str]:
    """Addresses to try for the spec, the ones the page names first. Never leaves the panel's host."""
    found: list[str] = []
    for pattern in _SPEC_REFS:
        found += [urljoin(page_url, m) for m in pattern.findall(html)]
    found += [f"{panel_url}/{path}" for path in FALLBACKS]
    out: list[str] = []
    for url in found:
        if url.startswith(panel_url + "/") and url not in out:
            out.append(url)
    return out


def strip_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Drop the ``servers`` entry: it holds the panel's address, which must never be committed."""
    return {k: v for k, v in spec.items() if k != "servers"}


async def fetch_spec(panel_url: str, username: str, password: str) -> dict[str, Any]:
    redactor = Redactor([password])
    # A panel on a bare IP address: aiohttp's default cookie jar ignores cookies from those.
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        amp = AmpClient(panel_url, Credentials(username, password), session=session, redactor=redactor,
                        keep_session_renewed=False)
        try:
            ticket = await amp.platform_settings.post_api_browser_ticket()
            redactor.add(ticket.ticket, short_lived=True)
            async with session.get(f"{panel_url}/scalar/session", params={"ticket": ticket.ticket}) as page:
                html = await page.text()
                page_url = str(page.url).split("?", 1)[0]
            tried = spec_urls(html, page_url, panel_url)
            for url in tried:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    try:
                        spec = await resp.json(content_type=None)
                    except (ValueError, aiohttp.ContentTypeError):
                        continue
                if isinstance(spec, dict) and "openapi" in spec and "paths" in spec:
                    return strip_spec(spec)
        finally:
            await amp.close()
    names = ", ".join(u[len(panel_url):] for u in tried)
    raise SpecUnavailable(f"the API browser page didn't lead to a spec (tried {names})")


def main(argv: list[str] | None = None) -> int:
    from codegen.spec_diff import PIN_FILE, diff, load, render

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", nargs="?", type=Path, help="a spec file to compare instead of fetching one")
    parser.add_argument("--save", type=Path, help="write the fetched spec here (panel address removed)")
    parser.add_argument("--markdown", action="store_true", help="print the report as Markdown")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args(argv)

    pin = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    pinned = PIN_FILE.parent / pin["file"]
    if args.spec is not None:
        new, label = strip_spec(load(args.spec)), args.spec.name
    else:
        load_env_file(args.env_file)
        url = (os.environ.get("AMP_URL") or "").rstrip("/")
        username = os.environ.get("AMP_USERNAME") or ""
        if not url or not username:
            print("Set AMP_URL and AMP_USERNAME in .env (see .env.example), or pass a spec file.")
            return 2
        password = os.environ.get("AMP_PASSWORD") or getpass.getpass(f"AMP password for {username}: ")
        try:
            new = asyncio.run(fetch_spec(url, username, password))
        except (SpecUnavailable, AmpError, aiohttp.ClientError, OSError) as exc:
            print(f"Couldn't read the spec from the panel: {Redactor([password]).text(str(exc))}")
            return 2
        label = "live panel"
        if args.save:
            args.save.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
            print(f"Saved the spec to {args.save}")
    report = diff(load(pinned), new)
    print(render(report, pinned.name, label, args.markdown))
    return 1 if report.breaking else 0


if __name__ == "__main__":
    sys.exit(main())
