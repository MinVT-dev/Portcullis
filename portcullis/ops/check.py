# SPDX-License-Identifier: AGPL-3.0-or-later
"""``python -m portcullis --check``: test the settings without starting the bot.

It reads every setting (``ops.settings.check``), prints the banner, then tries the two logins:

* the panel: logs in with AMP_USERNAME and AMP_PASSWORD, then logs out again;
* Discord: checks DISCORD_TOKEN over HTTP and whether the bot is in DISCORD_GUILD_ID. It does not
  connect to the gateway, so the bot doesn't come online and no commands are registered.

Nothing is written: no database, no log file, no state file. Add ``--offline`` to skip both logins.
Exit code 0 when everything passed, 1 when a login failed (settings problems exit with 2 before).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import TextIO

import discord

from portcullis.amp import AmpClient, Credentials
from portcullis.ops.banner import BootInfo, can_draw, show
from portcullis.ops.settings import Checked, version
from portcullis_api.redact import Redactor

TIMEOUT = 20.0


async def check_panel(checked: Checked, redactor: Redactor) -> tuple[bool, str]:
    s = checked.settings
    amp = AmpClient(s.amp_url, Credentials(s.amp_username, s.amp_password), redactor=redactor,
                    keep_session_renewed=False)
    try:
        await asyncio.wait_for(amp.start(), TIMEOUT)
        return True, f"logged in as {s.amp_username}"
    except TimeoutError:
        return False, f"no answer within {TIMEOUT:.0f} s. Is AMP_URL right and the panel running?"
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        name = type(exc).__name__
        if name == "AuthFailed":
            return False, "the panel refused the login. Check AMP_USERNAME and AMP_PASSWORD."
        if name == "LoginNotSupported":
            return False, "the account needs two-factor or a passkey, which a bot can't use."
        if name == "Unreachable":
            return False, "the panel didn't answer. Is AMP_URL right and the panel running?"
        return False, redactor.text(f"{name}: {exc}")
    finally:
        await amp.close()


async def check_discord(checked: Checked) -> tuple[bool, str]:
    s = checked.settings
    client = discord.Client(intents=discord.Intents.none())
    try:
        await asyncio.wait_for(client.login(s.discord_token or ""), TIMEOUT)
        name = str(client.user)
        try:
            guild = await asyncio.wait_for(client.fetch_guild(s.guild_id or 0), TIMEOUT)
        except (discord.Forbidden, discord.NotFound):
            return False, (f"token works ({name}), but the bot isn't in the server DISCORD_GUILD_ID names. "
                           "Invite it with the URL in docs/SETUP.md.")
        return True, f"token works ({name}), in {guild.name}"
    except discord.LoginFailure:
        return False, "Discord refused DISCORD_TOKEN. Reset it on the Bot tab of the developer portal."
    except TimeoutError:
        return False, "Discord didn't answer in time."
    except discord.HTTPException as exc:
        return False, f"Discord answered {exc.status}."
    finally:
        await client.close()


async def run_checks(checked: Checked, *, offline: bool = False, stream: TextIO | None = None,
                     panel: Callable[[Checked, Redactor], Awaitable[tuple[bool, str]]] = check_panel,
                     discord_check: Callable[[Checked], Awaitable[tuple[bool, str]]] = check_discord) -> int:
    stream = stream or sys.stdout
    redactor = Redactor()
    for secret in checked.settings.secrets() + checked.ops.secrets():
        redactor.add(secret)
    ok_mark, bad_mark = ("✓", "✗") if can_draw(stream) else ("ok", "FAIL")
    results: list[tuple[str, bool, str]] = []
    if not offline:
        for name, (ok, detail) in (("Panel", await panel(checked, redactor)),
                                   ("Discord", await discord_check(checked))):
            results.append((name, ok, redactor.text(detail)))
    panel_ok = not results or results[0][1]
    show(checked.settings, checked.ops,
         BootInfo(version=version(), panel=("skipped (--offline)" if offline else results[0][2]), panel_ok=panel_ok,
                  last_run="(dry run: nothing is started or written)", warnings=checked.warnings),
         stream)
    print("Settings: all valid.", file=stream)
    for name, ok, detail in results:
        print(f"{ok_mark if ok else bad_mark} {name}: {detail}", file=stream)
    if any(not ok for _name, ok, _detail in results):
        print("Fix the items marked above, then run the check again.", file=stream)
        return 1
    print("Everything checks out. Start the bot with: python -m portcullis", file=stream)
    return 0
