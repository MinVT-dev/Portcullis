# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checking for a newer Portcullis release. Off unless ``PORTCULLIS_UPDATE_CHECK=notify``.

Notify only, on purpose. Once a day the bot asks GitHub for the latest published release of
``PORTCULLIS_UPDATE_REPO``; if its version is newer than the one running, it logs that and posts
once in the alerts channel, with the release link and the commands to update. It never downloads
or installs anything:

* the bot runs from a git checkout with a virtual environment, and an update can bring a database
  migration or new settings that someone should read first;
* replacing running code safely needs a signed release to verify against, and Portcullis doesn't
  publish signed releases yet.

So the update itself stays a deliberate step by the owner (docs/SETUP.md, "Updating"). Drafts and
pre-releases are skipped, and the release link must point at the configured repository.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

CHECK_EVERY = 24 * 3600.0
FIRST_CHECK_AFTER = 300.0  # let start-up settle first
TIMEOUT = 15.0
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
UPDATE_STEPS = "`git pull`, then `pip install -e .` in the bot's virtual environment, then restart the bot"


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.match(text.strip())
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


@dataclass(frozen=True)
class Release:
    tag: str
    url: str


def newer(current: str, release: Release) -> bool:
    have, offered = parse_version(current), parse_version(release.tag)
    if offered is None:
        return False
    return have is None or offered > have  # a dev build compares as older than any release


def read_release(data: object, repo: str) -> Release | None:
    """The release in GitHub's ``releases/latest`` answer, or None if it isn't a usable stable release."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag, url = data.get("tag_name"), data.get("html_url")
    if not isinstance(tag, str) or not isinstance(url, str) or parse_version(tag) is None:
        return None
    if not url.lower().startswith(f"https://github.com/{repo.lower()}/releases/"):
        return None  # a link somewhere else is not trusted
    return Release(tag=tag, url=url)


def notice(current: str, release: Release) -> str:
    return (f"Portcullis {release.tag} is out (this bot runs {current}). Read the release notes first: "
            f"{release.url}\nTo update: {UPDATE_STEPS}. The bot never updates itself.")


class UpdateChecker:
    def __init__(self, repo: str, current: str, *, notify: Callable[[str], Awaitable[object]],
                 seen: Callable[[], Awaitable[str | None]], remember: Callable[[str], Awaitable[object]],
                 session: aiohttp.ClientSession | None = None) -> None:
        self.repo = repo
        self.current = current
        self.notify = notify
        self.seen = seen
        self.remember = remember
        self._session = session
        self._task: asyncio.Task[None] | None = None

    async def latest(self) -> Release | None:
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "portcullis-update-check"}
        own = self._session is None
        session = self._session or aiohttp.ClientSession()
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
                if resp.status == 404:
                    log.debug("no published release of %s yet", self.repo)
                    return None
                if resp.status != 200:
                    log.info("update check: GitHub answered %s; trying again tomorrow", resp.status)
                    return None
                return read_release(await resp.json(content_type=None), self.repo)
        finally:
            if own:
                await session.close()

    async def check(self) -> Release | None:
        """One check. Returns the release when a notice went out."""
        try:
            release = await self.latest()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            log.info("update check failed (%s); trying again tomorrow", type(exc).__name__)
            return None
        if release is None or not newer(self.current, release):
            return None
        if await self.seen() == release.tag:
            return None
        log.warning("A newer Portcullis is available: %s (running %s). %s", release.tag, self.current, release.url)
        await self.notify(notice(self.current, release))
        await self.remember(release.tag)
        return release

    async def run(self, first_after: float = FIRST_CHECK_AFTER, every: float = CHECK_EVERY) -> None:
        await asyncio.sleep(first_after)
        while True:
            try:
                await self.check()
            except Exception:  # noqa: BLE001 - never let the check stop the bot
                log.exception("update check failed")
            await asyncio.sleep(every)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name="portcullis-update-check")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
