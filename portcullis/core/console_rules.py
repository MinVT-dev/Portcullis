# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-server console rules (Gatekeeper V3's console filter, regex patterns and command denylist).

* **Hide rules** keep matching game output out of the console relay and ``/server console``. A rule
  is plain text matched anywhere in the line, ignoring case, or a regular expression when it starts
  with ``re:``.
* **Blocked commands** are console command words that ``/server send`` refuses for everyone except
  the bot's owners, such as ``op`` or ``stop``.

Patterns come from admins, but Python's ``re`` has no timeout, so expressions that can backtrack
badly (a repeated group that itself repeats, like ``(a+)+``) are refused up front.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache

HIDE_KEY = "console_hide"  # per server: list of rule strings
DENY_KEY = "console_denylist"  # per server: list of lowercase command words
HIDE_LIMIT, DENY_LIMIT = 25, 50
RULE_LENGTH = 200

_NESTED = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")
_WORD = re.compile(r"^[a-z0-9_:./-]{1,40}$")


def _check_regex(pattern: str) -> str | None:
    if _NESTED.search(pattern):
        return "it repeats a group that already repeats, which can freeze the bot. Simplify it"
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"it isn't a valid regular expression ({exc})"
    return None


def parse_hide_rules(text: str) -> tuple[list[str], str | None]:
    """Rules from one-per-line text. Returns (rules, problem); a problem means nothing was parsed."""
    rules: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        rule = raw.strip()
        if not rule:
            continue
        if len(rule) > RULE_LENGTH:
            return [], f"Line {number} is longer than {RULE_LENGTH} characters."
        if rule.lower().startswith("re:"):
            pattern = rule[3:].strip()
            if not pattern:
                return [], f"Line {number}: put a pattern after `re:`."
            problem = _check_regex(pattern)
            if problem:
                return [], f"Line {number}: {problem}."
            rule = "re:" + pattern
        if rule.lower() not in {r.lower() for r in rules}:
            rules.append(rule)
    if len(rules) > HIDE_LIMIT:
        return [], f"Up to {HIDE_LIMIT} hide rules per server."
    return rules, None


def stored_rules(value: object) -> tuple[str, ...]:
    """Rules as stored in settings, dropping anything malformed (settings can come from an import)."""
    if not isinstance(value, list):
        return ()
    good = []
    for item in value[:HIDE_LIMIT]:
        if not isinstance(item, str) or not item.strip() or len(item) > RULE_LENGTH + 3:
            continue
        if item.startswith("re:") and _check_regex(item[3:]):
            continue
        good.append(item)
    return tuple(good)


@lru_cache(maxsize=256)
def hider(rules: tuple[str, ...]) -> Callable[[str], bool] | None:
    """A function that says whether a console line should be hidden, or None when there are no rules."""
    if not rules:
        return None
    plain = tuple(r.lower() for r in rules if not r.startswith("re:"))
    regexes = tuple(re.compile(r[3:], re.IGNORECASE) for r in rules if r.startswith("re:"))

    def hide(line: str) -> bool:
        low = line.lower()
        return any(p in low for p in plain) or any(rx.search(line) for rx in regexes)

    return hide


def parse_denylist(text: str) -> tuple[list[str], str | None]:
    """Command words from text separated by spaces, commas or lines."""
    words: list[str] = []
    for raw in re.split(r"[\s,]+", text.strip()):
        word = raw.strip().lower().lstrip("/")
        if not word:
            continue
        if not _WORD.match(word):
            return [], f"`{word[:40]}` isn't a command word. Use letters, digits and `_ : . / -` only."
        if word not in words:
            words.append(word)
    if len(words) > DENY_LIMIT:
        return [], f"Up to {DENY_LIMIT} blocked commands per server."
    return words, None


def stored_denylist(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(w for w in value if isinstance(w, str) and _WORD.match(w))


def blocked_word(command: str, denylist: frozenset[str]) -> str | None:
    """The blocked command word ``command`` starts with, or None."""
    first = command.strip().split(" ", 1)[0].lower().lstrip("/")
    return first if first in denylist else None
