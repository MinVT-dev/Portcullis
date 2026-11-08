# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

from portcullis.bot.base import Access, Ctx, fill, valid_name
from portcullis.bot.client import PortcullisBot, intents
from portcullis.bot.commands import Modules
from portcullis.bot.server import SAY_KEY
from portcullis.bot.setup import WHITELIST_CHANNEL, WL_ADD, WL_REMOVE, ServerPanel
from portcullis.core.live import LinkVerifier, LiveState, PlayerTracker, Summary
from portcullis.core.relay import ChatLine, GameHooks
from portcullis.core.types import Player, PlayerJoined, PlayerLeft, PowerAction, ServerId, ServerState, StateChanged
from portcullis.db.database import Database
from tests.fakes.amp import FakeAmpError, FakeAmpPort
from tests.fakes.discord import FakeChannel, FakeClient, FakeInteraction, FakeMember, FakeRole

GUILD = 1
OWNER, OPERATOR_ROLE = 10, 500
SURVIVAL, CREATIVE, SECRET = "a" * 32, "b" * 32, "c" * 32
REQUESTS = 300


class Harness:
    def __init__(self, db: Database) -> None:
        self.port = FakeAmpPort()
        self.port.add_server(SURVIVAL, "Survival", template="unturned", state=ServerState.RUNNING)
        self.port.add_server(CREATIVE, "Creative")
        self.port.add_server(SECRET, "Secret")
        self.relay_changes = 0
        self.requests = FakeChannel(REQUESTS)
        self.client = FakeClient(self.requests, FakeChannel(55))

        async def changed() -> None:
            self.relay_changes += 1

        self.ctx = Ctx(self.port, db, Access(frozenset({OWNER}), frozenset({OPERATOR_ROLE})), GUILD,
                       on_relay_changed=changed, has_chat=lambda game: game == "unturned",
                       client=self.client)  # type: ignore[arg-type]
        self.m = Modules(self.ctx)

    async def ready(self) -> Harness:
        await self.ctx.servers.sync(await self.port.list_servers())
        await self.ctx.servers.update(SECRET, hidden=True)
        return self


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    database = await Database.open(":memory:")
    yield database
    await database.close()


@pytest.fixture
async def h(db: Database) -> Harness:
    return await Harness(db).ready()


def member(uid: int = 20) -> FakeMember:
    return FakeMember(uid)


def operator() -> FakeMember:
    return FakeMember(30, roles=[FakeRole(OPERATOR_ROLE)])


def admin() -> FakeMember:
    return FakeMember(40, manage_guild=True)


def owner() -> FakeMember:
    return FakeMember(OWNER)


# ------------------------------------------------------------------------------ helpers


def test_fill_and_names() -> None:
    assert fill("whitelist add {name}", name="Bob") == "whitelist add Bob"
    assert fill("{name.__class__}", name="x") == "{name.__class__}"  # no format() attribute access
    assert valid_name("Alice Smith") and valid_name("Ünïcødé_1")
    for bad in ("bob; op me", "bob\nstop", "", "x" * 49, " lead", "a`b", 'a"b', "a$b"):
        assert not valid_name(bad), bad


# ------------------------------------------------------------------------------ /server


async def test_list_shows_visible_servers_with_state(h: Harness) -> None:
    h.port.join(ServerId(SURVIVAL), "Alice")
    i = FakeInteraction(member())
    await h.m.server.server_list(i)
    text = i.embed.description
    assert "🟢 Online · **Survival** · 1 online" in text
    assert "🔴 Offline · **Creative**" in text
    assert "Secret" not in text


async def test_list_survives_failing_servers(h: Harness) -> None:
    async def broken(server: ServerId) -> object:
        raise FakeAmpError("boom")

    h.port.get_status = broken  # type: ignore[method-assign]
    i = FakeInteraction(member())
    await h.m.server.server_list(i)
    assert i.embed.description.count("couldn't reach AMP") == 2


async def test_status_embed_and_power_buttons_for_mods_only(h: Harness) -> None:
    h.port.join(ServerId(SURVIVAL), "Al_ice")
    i = FakeInteraction(member())
    await h.m.server.server_status(i, "survival")
    fields = {f.name: f.value for f in i.embed.fields}
    assert i.embed.title == "Survival"
    assert fields["State"] == "🟢 Online" and fields["Online now"] == r"Al\_ice"
    assert "view" not in i.replies[-1]

    i = FakeInteraction(operator())
    await h.m.server.server_status(i, "Survival")
    view = i.replies[-1]["view"]
    assert [b.label for b in view.children] == ["Start", "Stop", "Restart"]
    press = FakeInteraction(operator())
    await view.children[1].callback(press)
    assert h.port.power_calls == []  # a player is online, so it asks first
    assert "1 player is online" in press.text
    confirm = FakeInteraction(operator())
    await press.replies[-1]["view"].children[0].callback(confirm)
    assert h.port.power_calls == [(SURVIVAL, PowerAction.STOP)]


async def test_status_hidden_and_unknown(h: Harness) -> None:
    for name in ("Nope", "Secret"):
        i = FakeInteraction(member())
        await h.m.server.server_status(i, name)
        assert "I don't know a server called" in i.text
    i = FakeInteraction(operator())
    await h.m.server.server_status(i, "Secret")
    assert i.embed.title == "Secret"


async def test_autocomplete_hides_hidden_servers_from_members(h: Harness) -> None:
    names = [c.name for c in await h.m.server.autocomplete_server(FakeInteraction(member()), "")]
    assert names == ["Creative", "Survival"]
    choices = await h.m.server.autocomplete_server(FakeInteraction(operator()), "sec")
    assert [(c.name, c.value) for c in choices] == [("Secret", SECRET)]


async def test_other_guilds_are_refused(h: Harness) -> None:
    i = FakeInteraction(owner(), guild_id=999)
    await h.m.server.server_power(i, "Survival", "stop")
    assert "only works in the Discord server" in i.text and h.port.power_calls == []


async def test_power_permissions_and_audit(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.server.server_power(i, "Survival", "stop")
    assert "operator role" in i.text and h.port.power_calls == []
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Creative", "start")
    assert h.port.power_calls == [(CREATIVE, PowerAction.START)]
    assert i.replies[-1]["ephemeral"] is False and "Starting **Creative** (asked by <@30>)" in i.text
    results = [(e.action, e.result) for e in await h.ctx.audit.query()]
    assert results == [("server.power.start", "ok"), ("server.power.stop", "denied")]


async def test_power_failure_and_bad_action(h: Harness) -> None:
    async def broken(server: ServerId, action: PowerAction) -> None:
        raise FakeAmpError("[409] already running")

    h.port.power = broken  # type: ignore[method-assign]
    i = FakeInteraction(operator())
    await h.m.server.server_power(i, "Survival", "restart")
    assert "AMP didn't restart Survival" in i.text
    i = FakeInteraction(owner())
    await h.m.server.server_power(i, "Survival", "explode")
    assert "Choose one of the actions in the list" in i.text


async def test_send_console_and_chat(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.server.server_send(i, "Survival", "save")
    assert "operator role" in i.text and h.port.sent == []

    i = FakeInteraction(operator())
    await h.m.server.server_send(i, "Survival", "op secretpassword", "console")
    assert h.port.sent == [(SURVIVAL, "op secretpassword")]
    [entry] = [e for e in await h.ctx.audit.query() if e.result == "ok"]
    assert entry.target == "op"  # never the arguments

    i = FakeInteraction(operator())
    await h.m.server.server_send(i, "Survival", "hello", "chat")
    assert "no chat command set up" in i.text
    await h.ctx.settings.set(SAY_KEY, "say {message}", scope=SURVIVAL)
    i = FakeInteraction(operator())
    await h.m.server.server_send(i, "Survival", "hello all", "chat")
    assert h.port.sent[-1] == (SURVIVAL, "say hello all")

    i = FakeInteraction(operator())
    await h.m.server.server_send(i, "Survival", "a\nstop", "console")
    assert "without line breaks" in i.text and len(h.port.sent) == 2


async def test_backups_list(h: Harness) -> None:
    from portcullis.core.types import Backup

    h.port.servers[ServerId(SURVIVAL)].backups = [
        Backup("b1", "Nightly", datetime(2026, 11, 7, tzinfo=UTC), size_mb=120, sticky=True)]
    i = FakeInteraction(operator())
    await h.m.server.backups(i, "Survival")
    assert "**Nightly**" in i.embed.description and "120 MB" in i.embed.description and "kept" in i.embed.description
    i = FakeInteraction(operator())
    await h.m.server.backups(i, "Creative")
    assert "has no backups" in i.text


# ------------------------------------------------------------------------------ /setup


async def test_setup_relay(h: Harness) -> None:
    i = FakeInteraction(operator())
    await h.m.setup.setup_relay(i, "Survival", "console", FakeChannel(77))
    assert "Manage Server" in i.text and h.relay_changes == 0
    i = FakeInteraction(admin())
    await h.m.setup.setup_relay(i, "Survival", "events", FakeChannel(77))
    assert "Relaying events for **Survival** to <#77>" in i.text
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None and row.event_channel == 77 and h.relay_changes == 1
    i = FakeInteraction(admin())
    await h.m.setup.setup_relay(i, "Survival", "console", FakeChannel(78, can_post=False))
    assert "I can't post in <#78>" in i.text
    i = FakeInteraction(admin())
    await h.m.setup.setup_relay(i, "Creative", "chat", FakeChannel(79))
    assert "doesn't know how this game writes chat" in i.text
    i = FakeInteraction(owner())
    await h.m.setup.setup_relay(i, "Survival", "events", None)
    assert "Stopped relaying events" in i.text


async def test_setup_server_panel(h: Harness) -> None:
    i = FakeInteraction(admin())
    await h.m.setup.setup_server(i, "Survival")
    panel = i.replies[-1]["view"]
    assert isinstance(panel, ServerPanel)
    assert {f.name for f in i.embed.fields} >= {"Display name", "Console relay", "Whitelist add"}

    pick = FakeInteraction(admin())
    await panel.pick(pick, "console", 81)
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None and row.console_channel == 81 and "Relaying console" in pick.edits[-1]["content"]

    problem = await h.m.setup.save_commands(FakeInteraction(admin()), panel.row, {WL_ADD: "whitelist add"})
    assert problem is not None and "{name}" in problem
    assert await h.m.setup.save_commands(FakeInteraction(admin()), panel.row,
                                         {WL_ADD: "whitelist add {name}", WL_REMOVE: "", SAY_KEY: "say {message}"}) \
        is None
    assert await h.ctx.settings.get(WL_ADD, scope=SURVIVAL) == "whitelist add {name}"

    row = await h.m.setup.save_names(FakeInteraction(admin()), panel.row, "Big Survival", "SV", "yes")
    assert (row.name, row.prefix, row.hidden) == ("Big Survival", "SV", True)

    # the panel refuses members who aren't admins
    assert await panel.interaction_check(FakeInteraction(operator())) is False


async def test_setup_bot_whitelist_channel(h: Harness) -> None:
    await h.m.setup.set_whitelist_channel(FakeInteraction(admin()), REQUESTS)
    assert await h.ctx.settings.get(WHITELIST_CHANNEL) == REQUESTS
    embed = await h.m.setup.bot_embed()
    fields = {f.name: f.value for f in embed.fields}
    assert f"<#{REQUESTS}>" in fields["Whitelist requests go to"] and f"<@&{OPERATOR_ROLE}>" in fields["Operator roles"]


# ------------------------------------------------------------------------------ /whitelist


async def whitelist_ready(h: Harness) -> None:
    await h.ctx.settings.set(WL_ADD, "whitelist add {name}", scope=SURVIVAL)
    await h.ctx.settings.set(WL_REMOVE, "whitelist remove {name}", scope=SURVIVAL)
    await h.ctx.settings.set(WHITELIST_CHANNEL, REQUESTS)


async def test_whitelist_request_needs_setup(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.community.whitelist_request(i, "Survival", "Bob")
    assert "doesn't take whitelist requests" in i.text
    await h.ctx.settings.set(WL_ADD, "whitelist add {name}", scope=SURVIVAL)
    i = FakeInteraction(member())
    await h.m.community.whitelist_request(i, "Survival", "Bob")
    assert "Whitelist requests are off" in i.text


async def test_whitelist_request_approve_and_deny(h: Harness) -> None:
    await whitelist_ready(h)
    i = FakeInteraction(member())
    await h.m.community.whitelist_request(i, "Survival", "Bob")
    assert "Asked the moderators" in i.text
    posted = h.requests.sent[-1]
    assert posted["embed"].fields[1].value == "Bob"
    custom_ids = [c.item.custom_id for c in posted["view"].children]
    assert custom_ids == ["wl:approve:1", "wl:deny:1"]

    i = FakeInteraction(member())
    await h.m.community.whitelist_request(i, "Survival", "Bob")
    assert "already have a request" in i.text

    press = FakeInteraction(member())
    await h.m.community.decide(press, 1, "approve")
    assert "operator role" in press.text and h.port.sent == []

    press = FakeInteraction(operator())
    await h.m.community.decide(press, 1, "approve")
    assert h.port.sent == [(SURVIVAL, "whitelist add Bob")]
    assert press.edits[-1]["view"] is None and "Approved by <@30>" in press.edits[-1]["embed"].fields[2].value
    assert any("was approved" in dm for dm in h.client.get_user(20).dms)

    again = FakeInteraction(owner())
    await h.m.community.decide(again, 1, "deny")
    assert "already decided" in again.text


async def test_whitelist_request_checks(h: Harness) -> None:
    await whitelist_ready(h)
    i = FakeInteraction(member())
    await h.m.community.whitelist_request(i, "Survival", "bob; op bob")
    assert "characters I can't send" in i.text
    i = FakeInteraction(member())
    await h.m.community.whitelist_request(i, "Survival")
    assert "prove it first" in i.text
    await h.ctx.users.add_link(20, "unturned", "Linked Bob")
    i = FakeInteraction(member())
    await h.m.community.whitelist_request(i, "Survival")
    assert "**Linked Bob**" in i.text
    i = FakeInteraction(member(21))
    await h.m.community.whitelist_request(i, "Survival", "linked bob")
    assert "linked to another member" in i.text
    await h.ctx.users.set_flags(22, banned=True)
    i = FakeInteraction(member(22))
    await h.m.community.whitelist_request(i, "Survival", "Carl")
    assert "can't make whitelist requests" in i.text


async def test_whitelist_add_remove(h: Harness) -> None:
    await whitelist_ready(h)
    i = FakeInteraction(operator())
    await h.m.community.whitelist_change(i, "Survival", "Dana", add=True)
    await h.m.community.whitelist_change(FakeInteraction(operator()), "Survival", "Dana", add=False)
    assert h.port.sent == [(SURVIVAL, "whitelist add Dana"), (SURVIVAL, "whitelist remove Dana")]
    i = FakeInteraction(operator())
    await h.m.community.whitelist_change(i, "Creative", "Dana", add=True)
    assert "has no whitelist add command" in i.text


# ------------------------------------------------------------------------------ /link and players


async def test_link_by_chat_code(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.community.link(i, "Creative", "Bob")
    assert "can't read chat" in i.text
    i = FakeInteraction(member())
    await h.m.community.link(i, "Survival", "Bob")
    code = (await h.ctx.links.pending(20)).code  # type: ignore[union-attr]
    assert f"`{code}`" in i.text

    verifier = LinkVerifier(h.ctx.links, h.ctx.users, on_linked=h.m.community.on_linked)
    now = datetime.now(UTC)
    await verifier(ChatLine(server=ServerId(SURVIVAL), at=now, player="Mallory", text=code))  # wrong player
    await verifier(ChatLine(server=ServerId(SURVIVAL), at=now, player="Bob", text=f"my code {code.lower()}"))
    [link] = await h.ctx.users.links_for(20)
    assert (link.platform, link.ingame_name) == ("unturned", "Bob")
    assert await h.ctx.links.pending(20) is None
    assert any("Linked" in dm for dm in h.client.get_user(20).dms)

    i = FakeInteraction(member())
    await h.m.community.link(i)
    assert "**Bob** on unturned" in i.text and "view" in i.replies[-1]


async def test_player_tracking_lookup_and_top(h: Harness) -> None:
    tracker = PlayerTracker(h.ctx.players)
    sid = ServerId(SURVIVAL)
    t0 = datetime.now(UTC) - timedelta(hours=3)
    await tracker(PlayerJoined(server=sid, at=t0, player=Player("Alice", id="7656")))
    await tracker(PlayerJoined(server=sid, at=t0, player=Player("Alice")))  # duplicate join
    await tracker(PlayerLeft(server=sid, at=t0 + timedelta(hours=2), player=Player("Alice")))
    await tracker(PlayerJoined(server=sid, at=t0 + timedelta(hours=1), player=Player("Bob")))
    await tracker(StateChanged(server=sid, at=t0, state=ServerState.STOPPED))  # closes Bob's session

    i = FakeInteraction(member())
    await h.m.community.player_lookup(i, "alice")
    fields = {f.name: f.value for f in i.embed.fields}
    assert fields["Play time"].startswith("2h 0m over 1 sessions") and "Survival" in fields["Servers"]

    i = FakeInteraction(member())
    await h.m.community.player_top(i)
    assert i.embed.description.splitlines()[0].startswith("1. **Alice** · 2h")
    i = FakeInteraction(member())
    await h.m.community.player_lookup(i, "nobody")
    assert "haven't seen" in i.text
    assert await h.m.community.autocomplete_player(FakeInteraction(member()), "al") is not None


async def test_user_panel_toggles_flags(h: Harness) -> None:
    target = FakeMember(77, display_name="Target")
    i = FakeInteraction(member())
    await h.m.community.user(i, target)
    assert "operator role" in i.text
    i = FakeInteraction(operator())
    await h.m.community.user(i, target)
    view = i.replies[-1]["view"]
    await h.m.community.toggle_flag(FakeInteraction(operator()), target, "donator")
    assert (await h.ctx.users.flags(77))["donator"] is True
    assert await view.interaction_check(FakeInteraction(member())) is False


# ------------------------------------------------------------------------------ polls


async def test_poll_create_vote_close(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.polls.create(i, "Map?", "A; B")
    assert "operator role" in i.text
    i = FakeInteraction(operator())
    await h.m.polls.create(i, "Map?", "only one")
    assert "2 to 10 options" in i.text
    i = FakeInteraction(operator())
    await h.m.polls.create(i, "Next map?", "Desert; Forest | Snow")
    poll = (await h.ctx.polls.open_polls())[0]
    assert poll.options == ("Desert", "Forest", "Snow") and poll.message_id == 4242
    assert [c.item.custom_id for c in i.replies[-1]["view"].children][-1] == f"pollclose:{poll.id}"

    for voter, option in ((20, 0), (21, 1), (20, 1)):  # 20 changes their vote
        await h.m.polls.vote(FakeInteraction(member(voter)), poll.id, option)
    assert await h.ctx.polls.tally(poll.id) == {1: 2}

    press = FakeInteraction(member(21))
    await h.m.polls.close_by_button(press, poll.id)
    assert "operator role" in press.text
    press = FakeInteraction(operator())
    await h.m.polls.close_by_button(press, poll.id)
    assert press.edits[-1]["view"] is None and "Closed" in press.edits[-1]["embed"].footer.text
    late = FakeInteraction(member(22))
    await h.m.polls.vote(late, poll.id, 0)
    assert "closed" in late.text


async def test_due_polls_close(h: Harness) -> None:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    poll = await h.ctx.polls.create(55, "Q", ["a", "b"], 30, past)
    await h.ctx.polls.set_message(poll.id, 9)
    assert await h.m.polls.close_due() == 1
    assert h.client.channels[55].sent[-1]["edit"] == 9


# ------------------------------------------------------------------------------ help, audit, admin


async def test_help_lists_only_what_you_can_use(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.m.admin.help(i)
    assert "/server list" in i.embed.description and "/server power" not in i.embed.description
    i = FakeInteraction(owner())
    await h.m.admin.help(i)
    assert "/server power" in i.embed.description and "/admin" in i.embed.description


async def test_audit_list_and_export(h: Harness) -> None:
    await h.ctx.audit.record("server.power.start", actor_id=30, shard_id=SURVIVAL)
    await h.ctx.audit.record("server.power.stop", actor_id=20, shard_id=SURVIVAL, result="denied")
    i = FakeInteraction(operator())
    await h.m.admin.audit(i)
    assert "Manage Server" in i.text
    i = FakeInteraction(admin())
    await h.m.admin.audit(i, result="denied")
    assert "server.power.stop" in i.embed.description and "power.start" not in i.embed.description
    i = FakeInteraction(admin())
    await h.m.admin.audit(i, action="server.power", export=True)
    exported = json.loads(i.replies[-1]["file"].fp.read())
    assert {e["action"] for e in exported} == {"server.power.start", "server.power.stop"}


async def test_admin_export_import_roundtrip(h: Harness) -> None:
    await h.ctx.servers.update(SURVIVAL, console_channel=5, display_name="SV")
    await h.ctx.settings.set(WL_ADD, "whitelist add {name}", scope=SURVIVAL)
    data = await h.m.admin.export_data()
    await h.ctx.servers.update(SURVIVAL, console_channel=None, display_name=None)
    await h.ctx.settings.delete(WL_ADD, scope=SURVIVAL)
    data["servers"]["f" * 32] = {"hidden": True}
    updated, written, problems = await h.m.admin.import_data(json.loads(json.dumps(data)), by=40)
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None and (row.console_channel, row.display_name) == (5, "SV")
    assert await h.ctx.settings.get(WL_ADD, scope=SURVIVAL) == "whitelist add {name}"
    assert updated == 3 and written == 1 and problems == [f"skipped unknown server {'f' * 32}"]
    assert (await h.m.admin.import_data({"something": 1}, by=40))[2][0].startswith("This isn't")


async def test_diagnostics(h: Harness) -> None:
    i = FakeInteraction(admin())
    await h.m.admin.diagnostics(i)
    fields = {f.name: f.value for f in i.embed.fields}
    assert fields["AMP"].startswith("✅ reachable, 3 server(s)") and "Survival" in fields["Servers"]


# ------------------------------------------------------------------------------ live state and presence


async def test_live_state_summary() -> None:
    changes = 0

    async def changed() -> None:
        nonlocal changes
        changes += 1

    live = LiveState(on_change=changed)
    a, b = ServerId("a"), ServerId("b")
    from portcullis.core.types import ServerStatus

    await live.seed([ServerStatus(a, ServerState.RUNNING, players=(Player("x"),)),
                     ServerStatus(b, ServerState.STOPPED)], [a, b, ServerId("c")])
    assert live.summary() == Summary(servers=3, online=1, players=1)
    assert live.summary().text() == "1/3 servers online · 1 player"
    now = datetime.now(UTC)
    await live(PlayerJoined(server=a, at=now, player=Player("y")))
    await live(StateChanged(server=b, at=now, state=ServerState.RUNNING))
    await live(StateChanged(server=b, at=now, state=ServerState.RUNNING))  # repeat: no change
    await live(StateChanged(server=ServerId("hidden"), at=now, state=ServerState.RUNNING))  # not tracked
    assert live.summary().text() == "2/3 servers online · 2 players"
    assert changes == 3
    await live.set_reachable(False)
    assert live.summary().text() == "Can't reach AMP"


async def test_bot_wires_everything(db: Database) -> None:
    port = FakeAmpPort()
    port.add_server(SURVIVAL, "Survival", template="unturned", state=ServerState.RUNNING)
    bot = PortcullisBot(port=port, db=db, access=Access(), guild_id=GUILD,
                        hooks=lambda game: GameHooks(chat=None))
    await bot.refresh()
    assert [r.name for r in await bot.servers.list()] == ["Survival"]
    assert list(bot.relay.targets) == [SURVIVAL]  # followed even without relay channels
    assert bot.live.summary().text() == "1/1 servers online · 0 players"
    status, activity = bot.presence()
    assert status is discord.Status.online and activity.name == "1/1 servers online · 0 players"

    sent: list[str] = []

    async def change_presence(**kwargs: object) -> None:
        sent.append(kwargs["activity"].name)  # type: ignore[union-attr]

    bot.change_presence = change_presence  # type: ignore[method-assign]
    bot.is_ready = lambda: True  # type: ignore[method-assign]
    await bot.update_presence()
    await bot.update_presence()  # same text: not sent again
    assert sent == ["1/1 servers online · 0 players"]
    await bot.relay.close()


def test_command_tree() -> None:
    client = discord.Client(intents=intents())
    tree = app_commands.CommandTree(client)
    ctx = SimpleNamespace(access=Access())
    from portcullis.bot import admin, community, events, notify, polls, server, setup

    for cls in (server.ServerModule, setup.SetupModule, community.CommunityModule, polls.PollModule,
                notify.NotifyModule, events.EventModule, admin.AdminModule):
        module = cls.__new__(cls)
        module.ctx = ctx  # type: ignore[assignment]
        module.register(tree)
    names = {}
    for c in tree.get_commands():
        names[c.name] = sorted(x.name for x in getattr(c, "commands", []))
    assert names == {
        "server": ["banner", "console", "list", "power", "power-all", "send", "status"], "backups": [],
        "setup": ["bot", "relay", "server"], "whitelist": ["add", "list", "remove", "request"], "link": [],
        "player": ["id", "lookup", "moderate", "top", "watch", "watchlist"], "user": [], "Portcullis user": [],
        "poll": ["create", "list"], "notify": [], "event": ["create", "list"], "help": [], "audit": [],
        "admin": ["bot", "diagnostics", "export", "import"],
    }
    groups = {c.name: c for c in tree.get_commands()}
    for admin_only in ("setup", "admin", "audit"):
        assert groups[admin_only].default_permissions == discord.Permissions(manage_guild=True)
    assert groups["server"].default_permissions is None


def test_no_privileged_intents() -> None:
    wanted = intents()
    assert wanted.guilds and not (wanted.members or wanted.presences or wanted.message_content)


def test_discord_code_never_imports_amp() -> None:
    """DESIGN.md section 6, rule 1."""
    root = Path(__file__).resolve().parent.parent
    paths = [*(root / "portcullis/bot").glob("*.py"), root / "portcullis/core/relay.py",
             root / "portcullis/core/live.py"]
    for path in paths:
        imports = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith(("from ", "import "))]
        assert not [ln for ln in imports if "portcullis.amp" in ln or "portcullis_api" in ln], path
