# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-server console rules (Gatekeeper V3's console filter, regex patterns and command denylist).

* **Hide rules** keep matching game output out of the console relay and ``/server console``. A rule
  is plain text matched anywhere in the line, ignoring case, or a regular expression when it starts
  with ``re:``.
* **Blocked commands** are console command words that ``/server send`` refuses for everyone except
  the bot's owners, such as ``op`` or ``stop``.

Patterns come from admins, but Python's ``re`` has no timeout and a matching game line can come from
any player, so patterns are held to a subset that can't backtrack badly: a repeated part may not
itself repeat or offer alternatives (``(a+)+``, ``(a|aa)+``), back-references are refused, at most
two parts may repeat without limit, and only the first ``MATCH_LENGTH`` characters of a line are
matched. The blocked-command list is a guardrail against slips, not a security boundary: anyone who
may use ``/server send`` can still reach a command through the game's own aliases.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

try:  # the parser behind ``re``; private, so fall back to the rough text check if it moves
    from re import _parser as _sre_parse  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    _sre_parse = None

HIDE_KEY = "console_hide"  # per server: list of rule strings
DENY_KEY = "console_denylist"  # per server: list of lowercase command words
HIDE_LIMIT, DENY_LIMIT = 25, 50
RULE_LENGTH = 200
MATCH_LENGTH = 300  # characters of a console line that regex rules look at
UNBOUNDED_LIMIT = 2  # repeats per pattern with no upper limit (or one above BOUND_LIMIT)
BOUND_LIMIT = 50
REGEX_LIMIT = 10  # re: rules per server

_NESTED = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")
_WORD = re.compile(r"^[a-z0-9_:./-]{1,40}$")


_SLOW = "it repeats a part that already repeats or has alternatives, which can freeze the bot. Simplify it"


def _walk(items: Any, inside_repeat: bool, unbounded: list[int]) -> str | None:
    """Check a parsed pattern. ``unbounded`` counts repeats with no upper limit."""
    assert _sre_parse is not None
    c = _sre_parse
    for op, av in items:
        if op in (c.MAX_REPEAT, c.MIN_REPEAT, getattr(c, "POSSESSIVE_REPEAT", None)):
            if inside_repeat:
                return _SLOW
            low, high, body = av
            if high == c.MAXREPEAT or high > BOUND_LIMIT:
                unbounded[0] += 1
            problem = _walk(body, True, unbounded)
            if problem:
                return problem
        elif op == c.BRANCH:
            if inside_repeat:
                return _SLOW
            for branch in av[1]:
                problem = _walk(branch, inside_repeat, unbounded)
                if problem:
                    return problem
        elif op == c.SUBPATTERN:
            problem = _walk(av[-1], inside_repeat, unbounded)
            if problem:
                return problem
        elif op in (c.ASSERT, c.ASSERT_NOT):
            problem = _walk(av[1], inside_repeat, unbounded)
            if problem:
                return problem
        elif op in (c.GROUPREF, c.GROUPREF_EXISTS):
            return "it refers back to an earlier group, which can freeze the bot. Simplify it"
        elif op == getattr(c, "ATOMIC_GROUP", None):
            problem = _walk(av, inside_repeat, unbounded)
            if problem:
                return problem
    return None


def _check_regex(pattern: str) -> str | None:
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"it isn't a valid regular expression ({exc})"
    if _sre_parse is None:
        return _SLOW if _NESTED.search(pattern) else None
    unbounded = [0]
    problem = _walk(_sre_parse.parse(pattern), False, unbounded)
    if problem:
        return problem
    if unbounded[0] > UNBOUNDED_LIMIT:
        return (f"more than {UNBOUNDED_LIMIT} parts repeat without a small limit (`*`, `+`, `{{n,}}`), which can "
                "freeze the bot. Use limits like `\\d{1,3}` instead")
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
    if sum(r.startswith("re:") for r in rules) > REGEX_LIMIT:
        return [], f"Up to {REGEX_LIMIT} `re:` rules per server; plain text rules are faster."
    return rules, None


def stored_rules(value: object) -> tuple[str, ...]:
    """Rules as stored in settings, dropping anything malformed (settings can come from an import)."""
    if not isinstance(value, list):
        return ()
    good: list[str] = []
    regexes = 0
    for item in value[:HIDE_LIMIT]:
        if not isinstance(item, str) or not item.strip() or len(item) > RULE_LENGTH + 3:
            continue
        if item.startswith("re:"):
            if regexes >= REGEX_LIMIT or _check_regex(item[3:]):
                continue
            regexes += 1
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
        return any(p in low for p in plain) or any(rx.search(line, 0, MATCH_LENGTH) for rx in regexes)

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
    """The first blocked command word in ``command``, or None.

    Checks the first word, the same word without a ``namespace:`` prefix (``minecraft:op``), and every
    word after ``run`` (``execute as @a run op Bob``). Aliases the game defines itself aren't known."""
    if not denylist:
        return None
    words = [w.lower().lstrip("/") for w in command.split()]
    candidates = [words[0]] if words else []
    candidates += [w for prev, w in zip(words, words[1:], strict=False) if prev == "run"]
    for word in candidates:
        for form in (word, word.rsplit(":", 1)[-1]):
            if form in denylist:
                return form
    return None
