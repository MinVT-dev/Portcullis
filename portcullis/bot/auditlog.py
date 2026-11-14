# SPDX-License-Identifier: AGPL-3.0-or-later
"""The audit log channel: one line per admin action, as it happens.

Each line says how it ended, what it was, on which server and who asked::

    ✅ ok · `server.power.stop` · **Survival** · by @Min · confirmed
    ⛔ denied · `server.power.stop` · **Survival** · by @Guest
    ❌ failed · `server.power.update` · **Survival** · by @Min · AMP is busy with another job…

The channel is the ``audit_channel`` setting in ``/setup bot``. Only a fixed list of detail fields is
ever shown, and error text is scrubbed again, so tokens and passwords can't reach the channel even if
a caller stored one by mistake. The full record stays in the database (``/audit``).
"""

from __future__ import annotations

from portcullis.bot.alerts import AuditFeed
from portcullis.bot.base import AUDIT_CHANNEL, scrub
from portcullis.core.relay import escape
from portcullis.db.repos import AuditEntry

MARKS = {"ok": "✅", "denied": "⛔", "failed": "❌"}
# detail key -> how it reads in the line. Anything else in ``detail`` stays out of the channel.
FLAGS = {"confirmed": "confirmed", "member_start": "member start", "all": "every server"}
TEXT_FIELDS = ("outcome", "backup", "error")
LINE_MAX = 1900


def audit_line(entry: AuditEntry, server: str | None) -> str:
    who = f"<@{entry.actor_id}>" if entry.actor_id else "Portcullis"
    parts = [f"{MARKS.get(entry.result, '•')} {entry.result}", f"`{entry.action}`"]
    if server:
        parts.append(f"**{escape(server)}**")
    parts.append(f"by {who}")
    if entry.target:
        parts.append(escape(scrub(str(entry.target)))[:100])
    detail = entry.detail if isinstance(entry.detail, dict) else {}
    parts += [label for key, label in FLAGS.items() if detail.get(key) is True]
    for key in TEXT_FIELDS:
        value = detail.get(key)
        if isinstance(value, str) and value and not (key == "outcome" and value == "ok"):  # ok is already said
            parts.append(escape(scrub(value))[:200])
    return " · ".join(parts)[:LINE_MAX]


class AuditLog(AuditFeed):
    """``AuditFeed`` (rate limit, background posting) with the one-line format above."""

    async def _post(self, entry: AuditEntry) -> None:
        if not await self.ctx.settings.get(AUDIT_CHANNEL):
            return
        server = None
        if entry.shard_id:
            row = await self.ctx.servers.get(entry.shard_id)
            server = row.name if row else entry.shard_id
        skipped, self.skipped = self.skipped, 0
        note = f"\n({skipped} more entries in the last minute weren't posted; see `/audit`.)" if skipped else ""
        await self.ctx.post(AUDIT_CHANNEL, audit_line(entry, server) + note)
