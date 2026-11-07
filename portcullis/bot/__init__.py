# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Discord side of Portcullis (discord.py, slash commands).

Rule 1 of DESIGN.md section 6: nothing here imports ``portcullis.amp``. Commands reach AMP only
through the ``AmpPort`` they are given, so the tests run them against ``FakeAmpPort``.
"""

from portcullis.bot.base import Access
from portcullis.bot.client import PortcullisBot

__all__ = ["Access", "PortcullisBot"]
