# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start the bot: ``python -m portcullis``.

This is the one place that joins the AMP side to the Discord side; neither imports the other.
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
from portcullis_api.errors import AuthFailed

log = logging.getLogger("portcullis")


async def run(settings: Settings) -> None:
    redactor = setup_logging(settings)
    use_scrubber(redactor.text)  # error text shown in Discord and the audit log is masked too
    assert settings.discord_token is not None and settings.guild_id is not None  # checked by config.load
    db = await Database.open(settings.database)
    try:
        async with AmpClient(settings.amp_url, Credentials(settings.amp_username, settings.amp_password),
                             redactor=redactor) as amp:
            port = AmpServerPort(amp)
            bot = PortcullisBot(
                port=port,
                db=db,
                access=Access(frozenset(settings.owner_ids), frozenset(settings.operator_role_ids)),
                guild_id=settings.guild_id,
                hooks=game_hooks,
            )
            try:
                await bot.start(settings.discord_token)
            finally:
                await bot.close()
                await port.close()
    finally:
        await db.close()


def main() -> int:
    try:
        settings = load()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        asyncio.run(run(settings))
    except AuthFailed:
        log.error("AMP refused the bot's login. Check AMP_USERNAME and AMP_PASSWORD in .env.")
        return 1
    except discord.LoginFailure:
        log.error("Discord refused the bot token. Reset it on the Bot tab of the developer portal and "
                  "put the new one in DISCORD_TOKEN.")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
