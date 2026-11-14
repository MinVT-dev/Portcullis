# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start the bot: ``python -m portcullis``.

This is the one place that joins the AMP side to the Discord side; neither imports the other.
Stopping (Ctrl+C, Ctrl+Break, SIGTERM or ``/admin bot stop``) goes through ``portcullis.shutdown``:
the first signal closes everything in order, a second one stops at once. Both exit with code 0.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from typing import Any

import discord

from portcullis.amp import AmpClient, Credentials
from portcullis.amp.port import AmpServerPort
from portcullis.bot import Access, PortcullisBot
from portcullis.bot.base import ALERT_CHANNEL, use_scrubber
from portcullis.config import ConfigError, Settings
from portcullis.core.games import game_hooks
from portcullis.core.guard import GuardedPort, panel_down
from portcullis.db.database import Database
from portcullis.ops import logs
from portcullis.ops.banner import BootInfo, panel_address
from portcullis.ops.banner import show as show_banner
from portcullis.ops.check import run_checks
from portcullis.ops.metrics import MetricsServer, snapshot_of
from portcullis.ops.settings import OpsSettings, version
from portcullis.ops.settings import check as check_settings
from portcullis.ops.state import StateFile, describe
from portcullis.ops.updates import UpdateChecker
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
LOGIN_TIMEOUT = 20.0  # the first panel login; a slower panel counts as not answering
HEARTBEAT_EVERY = 60.0  # how often the state file records that the bot is alive
UPDATE_SEEN_KEY = "update_notified"  # bot setting: the last release tag the update check posted about


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


async def first_login(amp: AmpClient) -> tuple[bool, str]:
    """Log in to the panel. A wrong password stops the bot; a panel that doesn't answer does not."""
    try:
        await asyncio.wait_for(amp.start(), LOGIN_TIMEOUT)
    except Exception as exc:
        if not panel_down(exc):
            raise
        log.warning("The panel at %s isn't answering (%s). Starting anyway: live cards show the last known "
                    "status, and Portcullis keeps trying to log in.", panel_address(amp.base_url), type(exc).__name__)
        return False, "not answering yet; using the last known data and retrying"
    return True, "reachable, logged in"


async def login_later(amp: AmpClient, first_wait: float = 10.0) -> None:
    """Keep trying to log in after a start with the panel down, waiting longer each time (up to 2 minutes)."""
    wait = first_wait
    while True:
        await asyncio.sleep(wait)
        try:
            await asyncio.wait_for(amp.start(), LOGIN_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - logged; the loop goes on
            if panel_down(exc):
                log.debug("panel still not answering: %s", type(exc).__name__)
            else:
                log.error("The panel answered but refused the login (%s). Check AMP_USERNAME and AMP_PASSWORD.",
                          type(exc).__name__)
            wait = min(wait * 2, 120.0)
            continue
        log.info("Logged in to the panel.")
        return


async def heartbeat(state: StateFile, port: GuardedPort, every: float = HEARTBEAT_EVERY) -> None:
    """Record that the bot is alive, with the latest statuses, for the next start's recovery."""
    while True:
        await asyncio.sleep(every)
        state.heartbeat(port.export_statuses())


def quiet_ops() -> OpsSettings:
    """For callers (tests, embedders) that pass no ops settings: console logs only, no files, no banner."""
    return OpsSettings(log_file=None, state_file=None, banner=False)


async def run(settings: Settings, ops: OpsSettings | None = None, warnings: list[str] | None = None) -> str:
    ops = ops or quiet_ops()
    redactor = logs.setup(settings, ops)
    use_scrubber(redactor.text)  # error text shown in Discord and the audit log is masked too
    for warning in warnings or []:
        log.warning(warning)
    assert settings.discord_token is not None and settings.guild_id is not None  # checked by config.load
    state = StateFile(ops.state_file)
    saved = state.load()
    started = time.monotonic()
    with StopSignals() as stopper:

        def limit(seconds: float) -> float:
            return min(seconds, FORCED_TIMEOUT) if stopper.forced else seconds

        db = await Database.open(settings.database)
        try:
            amp = AmpClient(settings.amp_url, Credentials(settings.amp_username, settings.amp_password),
                            redactor=redactor)
            try:
                panel_ok, panel_text = await first_login(amp)
                port = GuardedPort(AmpServerPort(amp), retries=ops.panel_retries, trip_after=ops.panel_trip_after)
                recovered = port.import_statuses(saved)
                show_banner(settings, ops, BootInfo(version=version(), panel=panel_text, panel_ok=panel_ok,
                                                    last_run=describe(state.last_run), warnings=warnings or []))
                state.started(version(), port.export_statuses())
                if recovered:
                    log.info("restored the last known status of %d server(s) from the last run", recovered)
                bot = PortcullisBot(
                    port=port,
                    db=db,
                    access=Access(frozenset(settings.owner_ids), frozenset(settings.operator_role_ids)),
                    guild_id=settings.guild_id,
                    hooks=game_hooks,
                )
                extras = start_extras(bot, ops)
                if not panel_ok:
                    extras.append(asyncio.create_task(login_later(amp), name="portcullis-panel-login"))
                if ops.state_file is not None:
                    extras.append(asyncio.create_task(heartbeat(state, port), name="portcullis-heartbeat"))
                metrics = await start_metrics(bot, port, ops, started)
                reason: str | None = None
                try:
                    reason = await serve(bot, settings.discord_token, stopper)
                    return reason
                finally:
                    for task in extras:
                        task.cancel()
                    if metrics is not None:
                        await step("the metrics endpoint", metrics.close, 2.0, stopper=stopper)
                    await step("the live cards", bot.mark_stopped, CARDS_TIMEOUT, stopper=stopper)
                    await step("Discord", bot.close, DISCORD_TIMEOUT, stopper=stopper)
                    await step("Discord's HTTP session", bot.http.close, limit(DISCORD_TIMEOUT), stopper=stopper,
                               essential=True)  # already closed after a clean stop; this covers a forced one
                    await step("the AMP event streams", port.close, HUB_TIMEOUT, stopper=stopper)
                    if reason is not None or stopper.forced:  # a crash leaves the run marked as unfinished
                        state.stopped(reason or "forced", port.export_statuses())
            finally:
                await step("the AMP session", amp.close, limit(AMP_TIMEOUT), stopper=stopper, essential=True)
        finally:
            await step("the database", db.close, limit(DB_TIMEOUT), stopper=stopper, essential=True)
            await cancel_leftovers(limit(3.0))
            await asyncio.sleep(0.1)  # lets closed sockets finish closing before the loop does (Windows)


def start_extras(bot: PortcullisBot, ops: OpsSettings) -> list[asyncio.Task[Any]]:
    """Optional background jobs that need the bot: the update check (notify only)."""
    tasks: list[asyncio.Task[Any]] = []
    if ops.update_check == "notify":
        settings = bot.ctx.settings

        async def notify(text: str) -> bool:
            return await bot.ctx.post(ALERT_CHANNEL, text)

        async def seen() -> str | None:
            value = await settings.get(UPDATE_SEEN_KEY)
            return value if isinstance(value, str) else None

        async def remember(tag: str) -> None:
            await settings.set(UPDATE_SEEN_KEY, tag)

        checker = UpdateChecker(ops.update_repo, version(), notify=notify, seen=seen, remember=remember)
        tasks.append(asyncio.create_task(checker.run(), name="portcullis-update-check"))
    return tasks


async def start_metrics(bot: PortcullisBot, port: GuardedPort, ops: OpsSettings,
                        started: float) -> MetricsServer | None:
    if not ops.metrics:
        return None
    server = MetricsServer(lambda: snapshot_of(bot, port, version(), started), host=ops.metrics_host,
                           port=ops.metrics_port, token=ops.metrics_token)
    return server if await server.start() else None


def stopped(reason: str) -> None:
    text = f"Portcullis stopped ({reason})."
    if log.isEnabledFor(logging.INFO):
        log.info(text)
    else:
        print(text, file=sys.stderr)
    logs.close()
    for handler in logging.getLogger().handlers:
        handler.flush()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m portcullis", description="Run the Portcullis Discord bot.")
    parser.add_argument("--check", "--dry-run", action="store_true", dest="check",
                        help="check the settings and both logins, then exit without starting the bot")
    parser.add_argument("--offline", action="store_true", help="with --check: skip the panel and Discord logins")
    parser.add_argument("--version", action="version", version=f"Portcullis {version()}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        checked = check_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        print("Fix these in .env (see .env.example), then start again. "
              "`python -m portcullis --check` tests the settings without starting the bot.", file=sys.stderr)
        return 2
    if args.check:
        return asyncio.run(run_checks(checked, offline=args.offline))
    settings = checked.settings
    try:
        reason = asyncio.run(run(settings, checked.ops, checked.warnings))
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
