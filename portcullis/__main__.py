# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start the bot: ``python -m portcullis``.

This is the one place that joins the AMP side to the Discord side; neither imports the other.
Stopping (Ctrl+C, Ctrl+Break, SIGTERM or ``/admin bot stop``) goes through ``portcullis.shutdown``:
the first signal closes everything in order, a second one stops at once. Both exit with code 0.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import discord

from portcullis.amp import AmpClient, Credentials
from portcullis.amp.port import AmpServerPort
from portcullis.bot import Access, PortcullisBot
from portcullis.bot.base import use_scrubber
from portcullis.config import ConfigError, Settings, load, setup_logging
from portcullis.core.games import game_hooks
from portcullis.db.database import Database
from portcullis.shutdown import StopSignals, cancel_leftovers, step
from portcullis_api.errors import AuthFailed

log = logging.getLogger("portcullis")

# Seconds each shutdown step may take before it is skipped. Together they stay under 30 s, the
# default time systemd and Docker wait after SIGTERM before they kill the process.
CARDS_TIMEOUT = 3.0  # grey out the live status cards
DISCORD_TIMEOUT = 10.0
HUB_TIMEOUT = 5.0  # leave the AMP event streams
AMP_TIMEOUT = 6.0  # log out of AMP (5 s inside) and close the HTTP session
DB_TIMEOUT = 5.0
FORCED_TIMEOUT = 2.0  # the essential steps after a second Ctrl+C


async def serve(bot: PortcullisBot, token: str, stopper: StopSignals) -> str:
    """Run Discord until it ends by itself (``/admin bot stop``) or a stop is requested. Returns why it stopped."""
    discord_task = asyncio.create_task(bot.start(token), name="discord")
    stopper.serving()
    stop_task = asyncio.create_task(stopper.wait(), name="stop-signal")
    try:
        done, _ = await asyncio.wait({discord_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop_task.cancel()
    if discord_task in done:
        discord_task.result()  # a refused token or another startup error goes up to main()
        return "/admin bot stop"
    reason = stop_task.result()
    log.info("Stopping Portcullis (%s). Press Ctrl+C again to stop at once.", reason)
    return reason


async def run(settings: Settings) -> str:
    redactor = setup_logging(settings)
    use_scrubber(redactor.text)  # error text shown in Discord and the audit log is masked too
    assert settings.discord_token is not None and settings.guild_id is not None  # checked by config.load
    with StopSignals() as stopper:

        def limit(seconds: float) -> float:
            return min(seconds, FORCED_TIMEOUT) if stopper.forced else seconds

        db = await Database.open(settings.database)
        try:
            amp = AmpClient(settings.amp_url, Credentials(settings.amp_username, settings.amp_password),
                            redactor=redactor)
            try:
                await amp.start()
                port = AmpServerPort(amp)
                bot = PortcullisBot(
                    port=port,
                    db=db,
                    access=Access(frozenset(settings.owner_ids), frozenset(settings.operator_role_ids)),
                    guild_id=settings.guild_id,
                    hooks=game_hooks,
                )
                try:
                    return await serve(bot, settings.discord_token, stopper)
                finally:
                    await step("the live cards", bot.mark_stopped, CARDS_TIMEOUT, stopper=stopper)
                    await step("Discord", bot.close, DISCORD_TIMEOUT, stopper=stopper)
                    await step("Discord's HTTP session", bot.http.close, limit(DISCORD_TIMEOUT), stopper=stopper,
                               essential=True)  # already closed after a clean stop; this covers a forced one
                    await step("the AMP event streams", port.close, HUB_TIMEOUT, stopper=stopper)
            finally:
                await step("the AMP session", amp.close, limit(AMP_TIMEOUT), stopper=stopper, essential=True)
        finally:
            await step("the database", db.close, limit(DB_TIMEOUT), stopper=stopper, essential=True)
            await cancel_leftovers(limit(3.0))
            await asyncio.sleep(0.1)  # lets closed sockets finish closing before the loop does (Windows)


def stopped(reason: str) -> None:
    text = f"Portcullis stopped ({reason})."
    if log.isEnabledFor(logging.INFO):
        log.info(text)
    else:
        print(text, file=sys.stderr)
    for handler in logging.getLogger().handlers:
        handler.flush()


def main() -> int:
    try:
        settings = load()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        reason = asyncio.run(run(settings))
    except AuthFailed:
        log.error("AMP refused the bot's login. Check AMP_USERNAME and AMP_PASSWORD in .env.")
        return 1
    except discord.LoginFailure:
        log.error("Discord refused the bot token. Reset it on the Bot tab of the developer portal and "
                  "put the new one in DISCORD_TOKEN.")
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        reason = "forced"  # a second Ctrl+C, or Ctrl+C while still starting up
    stopped(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
