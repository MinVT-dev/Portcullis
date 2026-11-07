# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import discord
import pytest
from discord import app_commands

from portcullis.bot.client import PortcullisBot, intents
from portcullis.bot.commands import Access, Commands, register
from portcullis.core.relay import GameHooks
from portcullis.core.types import PowerAction, ServerId, ServerState
from portcullis.db.database import Database
from portcullis.db.repos import AuditRepo, ServerRepo
from tests.fakes.amp import FakeAmpError, FakeAmpPort
from tests.fakes.discord import FakeChannel, FakeInteraction, FakeMember, FakeRole

GUILD = 1
OWNER, OPERATOR_ROLE = 10, 500
SURVIVAL, CREATIVE, SECRET = "a" * 32, "b" * 32, "c" * 32


class Harness:
    def __init__(self, db: Database) -> None:
        self.port = FakeAmpPort()
        self.port.add_server(SURVIVAL, "Survival", template="unturned", state=ServerState.RUNNING)
        self.port.add_server(CREATIVE, "Creative")
        self.port.add_server(SECRET, "Secret")
        self.servers = ServerRepo(db)
        self.audit = AuditRepo(db)
        self.relay_changes = 0

        async def changed() -> None:
            self.relay_changes += 1

        self.cmds = Commands(self.port, self.servers, self.audit,
                             Access(frozenset({OWNER}), frozenset({OPERATOR_ROLE})), guild_id=GUILD,
                             on_relay_changed=changed, has_chat_pattern=lambda game: game == "unturned")

    async def ready(self) -> Harness:
        await self.servers.sync(await self.port.list_servers())
        await self.servers.update(SECRET, hidden=True)
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


# ------------------------------------------------------------------------------ /server list, status


async def test_list_shows_visible_servers_with_state(h: Harness) -> None:
    h.port.join(ServerId(SURVIVAL), "Alice")
    i = FakeInteraction(member())
    await h.cmds.server_list(i)
    assert i.deferred == {"ephemeral": True, "thinking": True}
    text = i.embed.description
    assert "🟢 Online · **Survival** · 1 online" in text
    assert "🔴 Offline · **Creative**" in text
    assert "Secret" not in text


async def test_list_survives_failing_servers(h: Harness) -> None:
    async def broken(server: ServerId) -> object:
        raise FakeAmpError("boom")

    h.port.get_status = broken  # type: ignore[method-assign]
    i = FakeInteraction(member())
    await h.cmds.server_list(i)
    assert i.embed.description.count("couldn't reach AMP") == 2


async def test_status_embed(h: Harness) -> None:
    h.port.join(ServerId(SURVIVAL), "Al_ice")
    i = FakeInteraction(member())
    await h.cmds.server_status(i, "survival")
    embed = i.embed
    fields = {f.name: f.value for f in embed.fields}
    assert embed.title == "Survival"
    assert fields["State"] == "🟢 Online" and fields["Players"] == "1"
    assert fields["Online now"] == r"Al\_ice"
    assert i.replies[-1]["ephemeral"] is True


async def test_status_by_shard_id_and_unknown_and_hidden(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.cmds.server_status(i, CREATIVE)
    assert i.embed.title == "Creative"
    for name in ("Nope", "Secret"):
        i = FakeInteraction(member())
        await h.cmds.server_status(i, name)
        assert "I don't know a server called" in i.text
    i = FakeInteraction(operator())
    await h.cmds.server_status(i, "Secret")
    assert i.embed.title == "Secret"


async def test_status_reports_amp_errors(h: Harness) -> None:
    async def broken(server: ServerId) -> object:
        raise FakeAmpError("GET /api/Shards/x/state: [403] forbidden")

    h.port.get_status = broken  # type: ignore[method-assign]
    i = FakeInteraction(member())
    await h.cmds.server_status(i, "Survival")
    assert "Couldn't get the status of Survival" in i.text and "403" in i.text


async def test_autocomplete_hides_hidden_servers_from_members(h: Harness) -> None:
    names = [c.name for c in await h.cmds.autocomplete_server(FakeInteraction(member()), "")]
    assert names == ["Creative", "Survival"]
    choices = await h.cmds.autocomplete_server(FakeInteraction(operator()), "sec")
    assert [(c.name, c.value) for c in choices] == [("Secret", SECRET)]


async def test_other_guilds_are_refused(h: Harness) -> None:
    i = FakeInteraction(FakeMember(OWNER), guild_id=999)
    await h.cmds.server_power(i, "Survival", "stop")
    assert "only works in the Discord server" in i.text
    assert h.port.power_calls == []


# ------------------------------------------------------------------------------ /server power


async def test_power_denied_without_operator_role(h: Harness) -> None:
    i = FakeInteraction(member())
    await h.cmds.server_power(i, "Survival", "stop")
    assert "operator role" in i.text and i.replies[-1]["ephemeral"] is True
    assert h.port.power_calls == []
    [entry] = await h.audit.query()
    assert (entry.action, entry.result, entry.actor_id) == ("server.power.stop", "denied", 20)


@pytest.mark.parametrize("uid,roles", [(30, [FakeRole(OPERATOR_ROLE)]), (OWNER, [])])
async def test_power_by_operator_or_owner(h: Harness, uid: int, roles: list[FakeRole]) -> None:
    i = FakeInteraction(FakeMember(uid, roles=roles))
    await h.cmds.server_power(i, "Creative", "start")
    assert h.port.power_calls == [(CREATIVE, PowerAction.START)]
    assert i.deferred == {"thinking": True}  # public
    assert i.replies[-1]["ephemeral"] is False
    assert f"Starting **Creative** (asked by <@{uid}>)" in i.text
    [entry] = await h.audit.query()
    assert (entry.action, entry.result, entry.shard_id) == ("server.power.start", "ok", CREATIVE)


async def test_power_failure_is_reported_and_audited(h: Harness) -> None:
    async def broken(server: ServerId, action: PowerAction) -> None:
        raise FakeAmpError("[409] already running")

    h.port.power = broken  # type: ignore[method-assign]
    i = FakeInteraction(operator())
    await h.cmds.server_power(i, "Survival", "restart")
    assert "AMP didn't restart Survival" in i.text and "already running" in i.text
    [entry] = await h.audit.query()
    assert entry.result == "failed" and "already running" in entry.detail["error"]


async def test_power_rejects_other_actions(h: Harness) -> None:
    i = FakeInteraction(FakeMember(OWNER))
    await h.cmds.server_power(i, "Survival", "update")
    assert "Choose start, stop or restart" in i.text and h.port.power_calls == []


# ------------------------------------------------------------------------------ /setup relay


async def test_setup_needs_manage_server(h: Harness) -> None:
    i = FakeInteraction(operator())
    await h.cmds.setup_relay(i, "Survival", "console", FakeChannel(77))  # type: ignore[arg-type]
    assert "Manage Server" in i.text and h.relay_changes == 0


async def test_setup_sets_and_clears_channels(h: Harness) -> None:
    i = FakeInteraction(admin())
    await h.cmds.setup_relay(i, "Survival", "events", FakeChannel(77))  # type: ignore[arg-type]
    assert "Relaying events for **Survival** to <#77>" in i.text
    row = await h.servers.get(SURVIVAL)
    assert row is not None and row.event_channel == 77 and h.relay_changes == 1

    i = FakeInteraction(FakeMember(OWNER))
    await h.cmds.setup_relay(i, "Survival", "events", None)
    assert "Stopped relaying events" in i.text
    row = await h.servers.get(SURVIVAL)
    assert row is not None and row.event_channel is None and h.relay_changes == 2
    actions = [e.action for e in await h.audit.query()]
    assert actions == ["setup.relay.events", "setup.relay.events"]


async def test_setup_checks_the_bot_can_post(h: Harness) -> None:
    i = FakeInteraction(admin())
    await h.cmds.setup_relay(i, "Survival", "console", FakeChannel(77, can_post=False))  # type: ignore[arg-type]
    assert "I can't post in <#77>" in i.text
    row = await h.servers.get(SURVIVAL)
    assert row is not None and row.console_channel is None


async def test_setup_chat_warns_when_the_game_has_no_pattern(h: Harness) -> None:
    i = FakeInteraction(admin())
    await h.cmds.setup_relay(i, "Creative", "chat", FakeChannel(78))  # type: ignore[arg-type]
    assert "doesn't know how this game writes chat" in i.text
    i = FakeInteraction(admin())
    await h.cmds.setup_relay(i, "Survival", "chat", FakeChannel(78))  # type: ignore[arg-type]
    assert "doesn't know" not in i.text


# ------------------------------------------------------------------------------ wiring


def test_command_tree() -> None:
    client = discord.Client(intents=intents())
    tree = app_commands.CommandTree(client)
    register(tree, Commands(FakeAmpPort(), None, None, Access(), guild_id=GUILD,  # type: ignore[arg-type]
                            on_relay_changed=None))  # type: ignore[arg-type]
    groups = {g.name: g for g in tree.get_commands()}
    assert {n: sorted(c.name for c in g.commands) for n, g in groups.items()} == {  # type: ignore[union-attr]
        "server": ["list", "power", "status"], "setup": ["relay"]}
    assert groups["setup"].default_permissions == discord.Permissions(manage_guild=True)
    assert groups["server"].default_permissions is None
    assert all(g.guild_only for g in groups.values())


def test_no_privileged_intents() -> None:
    wanted = intents()
    assert wanted.guilds
    assert not (wanted.members or wanted.presences or wanted.message_content)


async def test_bot_syncs_servers_and_starts_relays(db: Database) -> None:
    port = FakeAmpPort()
    port.add_server(SURVIVAL, "Survival", template="unturned")
    hooks_seen: list[str | None] = []

    def hooks(game: str | None) -> GameHooks:
        hooks_seen.append(game)
        return GameHooks()

    bot = PortcullisBot(port=port, db=db, access=Access(), guild_id=GUILD, hooks=hooks)
    await bot.refresh()
    assert [r.name for r in await bot.servers.list()] == ["Survival"]
    assert bot.relay.targets == {}

    await bot.servers.update(SURVIVAL, console_channel=5)
    await bot.apply_relays()
    assert list(bot.relay.targets) == [SURVIVAL]
    assert bot.commands.has_chat_pattern("unturned") is False
    port.add_server("d" * 32, "New")
    await bot.refresh()
    assert len(await bot.servers.list()) == 2
    await bot.relay.close()


def test_discord_code_never_imports_amp() -> None:
    """DESIGN.md section 6, rule 1."""
    root = Path(__file__).resolve().parent.parent
    for path in [*(root / "portcullis/bot").glob("*.py"), root / "portcullis/core/relay.py"]:
        imports = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith(("from ", "import "))]
        assert not [ln for ln in imports if "portcullis.amp" in ln], path
