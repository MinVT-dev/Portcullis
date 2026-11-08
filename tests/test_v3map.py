# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""The Gatekeeper V3 command map, its generated doc and /help v3:."""

from __future__ import annotations

from pathlib import Path

from portcullis.bot.v3map import STATUSES, V3_COMMANDS, counts, lookup, render_markdown, search
from tests.fakes.discord import FakeInteraction
from tests.test_bot import Harness, db, h, member  # noqa: F401

DOC = Path(__file__).resolve().parent.parent / "docs" / "V3_PARITY.md"


def test_all_117_v3_commands_once() -> None:
    names = [c.v3 for c in V3_COMMANDS]
    assert len(names) == 117
    assert len(set(names)) == 117
    assert all(n.startswith("/") and n == n.strip() for n in names)


def test_every_entry_has_a_valid_status_and_a_home() -> None:
    for c in V3_COMMANDS:
        assert c.status in STATUSES, c
        assert c.home.strip(), c
        assert "—" not in c.home, c  # house style: no em dashes
    assert sum(counts().values()) == 117


def test_same_name_entries_really_have_the_same_name() -> None:
    for c in V3_COMMANDS:
        if c.status == "same":
            assert c.home.split()[0] == c.v3.split()[0], c


def test_doc_is_in_sync() -> None:
    assert DOC.read_text(encoding="utf-8") == render_markdown(), \
        "docs/V3_PARITY.md is stale: regenerate it from portcullis.bot.v3map.render_markdown()"


def test_lookup_forgives_slash_case_and_spaces() -> None:
    assert lookup("/server start") is lookup("SERVER   start") is lookup(" server start ")
    assert lookup("/nope") is None


def test_search_caps_at_25_and_matches_substrings() -> None:
    assert len(search("")) == 25
    assert search("whitelist_req") == ["/whitelist_request"]
    assert all("banner" in n for n in search("banner"))


async def test_help_v3_says_where_a_command_went(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.admin.help(i, v3="/server start")
    embed = i.embed
    assert embed.title == "/server start in Portcullis"
    assert "/server power" in embed.fields[0].value
    assert i.replies[-1]["ephemeral"]


async def test_help_v3_unknown_command(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.admin.help(i, v3="/made up")
    assert "no command called" in i.text


async def test_help_v3_autocomplete(h: Harness) -> None:
    choices = await h.m.admin.autocomplete_v3(FakeInteraction(member()), "")
    assert len(choices) == 25
    assert [c.value for c in await h.m.admin.autocomplete_v3(FakeInteraction(member()), "poll del")] == \
        ["/poll delete"]


async def test_help_without_v3_still_lists_commands(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.admin.help(i)
    assert "/server list" in i.embed.description
