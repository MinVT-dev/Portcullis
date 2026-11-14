# SPDX-License-Identifier: AGPL-3.0-or-later
"""Updating a game server from Discord: the result report after ``/server power action:update``.

AMP 3 (``POST /api/Shards/{id}/update``, checked against the 2026-11-13 API) accepts the update and
runs it in the background as a task. The task's body shape has not been seen on a real panel, so the
result is read from the server's state instead: Portcullis waits for the server to go busy, then for it
to settle, and reports where it ended up. What AMP 3 cannot do: say which game version was installed,
or update a game that needs a Steam login unless the server has a Steam credential in the panel.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord

from portcullis.bot.base import reply, scrub, state_text
from portcullis.bot.errors import plain_error
from portcullis.core.relay import escape
from portcullis.core.types import ServerState
from portcullis.db.repos import ServerRow

log = logging.getLogger(__name__)

POLL = 15.0
START_CHECKS = 4  # about a minute for the update to show up as busy
# Discord lets the bot answer an interaction for 15 minutes; the last check is a little before that.
FINISH_CHECKS = 54


def update_error(exc: BaseException) -> str:
    """AMP's two update-specific refusals in plain words; anything else as usual."""
    text = str(exc)
    if "steamLoginRequired" in text:
        return ("This game needs a Steam account to download updates. Add a SteamCMD login to the server in "
                "the AMP panel, then try again.")
    if "suspended" in text.lower() and getattr(exc, "status", None) == 409:
        return "The server is suspended in AMP, so it can't be updated. Unsuspend it in the panel first."
    return plain_error(exc)


async def follow_update(ctx: Any, interaction: discord.Interaction, row: ServerRow, *,
                        sleep: Callable[[float], Awaitable[None]]) -> None:
    """Report how an update ended, in the channel where it was asked for."""
    seen_busy = False
    status = None
    for check in range(START_CHECKS + FINISH_CHECKS):
        await sleep(POLL)
        try:
            status = await ctx.port.get_status(row.shard_id)
        except Exception as exc:  # noqa: BLE001 - one failed read is not the update failing
            log.info("following the update of %s: %s", row.name, exc)
            continue
        if status.state is ServerState.BUSY:
            seen_busy = True
            continue
        if seen_busy or check + 1 >= START_CHECKS:
            break
    name = escape(row.name)
    if status is None or status.state is ServerState.BUSY:
        outcome = "unknown"
        text = (f"**{name}** is still updating after {int(POLL * (START_CHECKS + FINISH_CHECKS) // 60)} minutes, "
                "or AMP stopped answering. Check the server in AMP.")
    elif status.state is ServerState.FAILED:
        outcome = "failed"
        why = f" AMP says: {escape(scrub(status.last_error))[:300]}" if status.last_error else ""
        text = f"The update of **{name}** failed.{why}"
    elif not seen_busy:
        outcome = "unknown"
        text = (f"AMP accepted the update of **{name}**, but the server never showed as updating. It may "
                f"have had nothing to update. It is now {state_text(status.state)}.")
    else:
        outcome = "ok"
        text = f"The update of **{name}** finished. It is now {state_text(status.state)}."
    await ctx.audit.record("server.power.update.finished", actor_id=interaction.user.id, shard_id=row.shard_id,
                           result="failed" if outcome == "failed" else "ok", detail={"outcome": outcome})
    try:
        await reply(interaction, text, ephemeral=False)
    except Exception as exc:  # noqa: BLE001 - the interaction may have expired
        log.warning("could not post how the update of %s ended: %s", row.name, exc)
