# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811  (pytest fixtures db and h are imported from test_bot)
"""Panel handling (retries, circuit breaker, stale cards), restart recovery, settings checks and the dry
run, the boot banner, logs, the metrics endpoint and the update check."""

from __future__ import annotations

import io
import json
import logging
import logging.handlers
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

import portcullis.__main__ as entry
from portcullis.bot.base import MUTED
from portcullis.bot.errors import plain_error
from portcullis.bot.livecard import CARD_KEY, LiveCards
from portcullis.bot.refresh import Pacer
from portcullis.config import ConfigError, Settings
from portcullis.core.guard import (
    COOLDOWN_MIN,
    GuardedPort,
    PanelUnavailable,
    panel_down,
    status_from_json,
    status_to_json,
)
from portcullis.core.types import Player, PowerAction, ServerId, ServerState, ServerStatus
from portcullis.ops import banner, logs
from portcullis.ops.check import run_checks
from portcullis.ops.metrics import MetricsServer, Snapshot, health_json, prometheus, snapshot_of
from portcullis.ops.settings import Checked, OpsSettings, check, loopback, typos
from portcullis.ops.state import LastRun, StateFile, describe
from portcullis.ops.updates import Release, UpdateChecker, newer, notice, parse_version, read_release
from tests.test_bot import SURVIVAL, Harness, db, h  # noqa: F401
from tests.test_ux import Clock, EditChannel, add_channel

SECRET = "hunter2-very-secret"
TOKEN = "discord.token.value"
GOOD_ENV = {"AMP_URL": "http://192.168.1.10:8443", "AMP_USERNAME": "bot", "AMP_PASSWORD": SECRET,
            "DISCORD_TOKEN": TOKEN, "DISCORD_GUILD_ID": "123", "PORTCULLIS_OWNER_IDS": "1"}


class Unreachable(Exception):  # the client's class, read by name
    pass


class Status(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"status {status}")
        self.status = status


class Inner:
    """A port whose calls fail with the next queued error, then succeed."""

    def __init__(self) -> None:
        self.errors: list[BaseException] = []
        self.calls: list[str] = []

    def _next(self, name: str) -> None:
        self.calls.append(name)
        if self.errors:
            raise self.errors.pop(0)

    async def get_status(self, server: ServerId) -> ServerStatus:
        self._next("status")
        return ServerStatus(id=server, state=ServerState.RUNNING, players=(Player("Ann"),), cpu_percent=12.0)

    async def list_servers(self) -> list[Any]:
        self._next("list")
        return []

    async def power(self, server: ServerId, action: PowerAction) -> None:
        self._next("power")

    async def close(self) -> None:
        self.calls.append("close")


async def no_sleep(_seconds: float) -> None:
    return None


def guarded(inner: Inner, clock: Clock, **kw: Any) -> GuardedPort:
    return GuardedPort(inner, clock=clock, sleep=no_sleep, **kw)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------ panel guard


def test_panel_down_only_for_no_answer() -> None:
    assert panel_down(TimeoutError()) and panel_down(ConnectionResetError()) and panel_down(Unreachable())
    assert panel_down(Status(502)) and panel_down(Status(503))
    assert not panel_down(Status(403)) and not panel_down(Status(404)) and not panel_down(ValueError())
    assert not panel_down(PanelUnavailable(5))


async def test_reads_are_retried_actions_are_not() -> None:
    inner, clock = Inner(), Clock()
    port = guarded(inner, clock, retries=2)
    inner.errors = [TimeoutError(), Unreachable()]
    status = await port.get_status(ServerId("a"))
    assert status.state is ServerState.RUNNING and inner.calls == ["status"] * 3
    assert port.stats.retries == 2 and port.reachable

    inner.calls.clear()
    inner.errors = [TimeoutError()]
    with pytest.raises(TimeoutError):
        await port.power(ServerId("a"), PowerAction.STOP)
    assert inner.calls == ["power"]  # never sent twice


async def test_a_real_answer_is_not_retried_or_counted() -> None:
    inner, clock = Inner(), Clock()
    port = guarded(inner, clock, retries=2, trip_after=1)
    inner.errors = [Status(403)]
    with pytest.raises(Status):
        await port.list_servers()
    assert inner.calls == ["list"] and port.reachable and port.stats.failures == 0


async def test_circuit_opens_refuses_fast_then_recovers(caplog: pytest.LogCaptureFixture) -> None:
    inner, clock = Inner(), Clock()
    port = guarded(inner, clock, retries=0, trip_after=3)
    caplog.set_level(logging.INFO, "portcullis.panel")
    for _ in range(3):
        inner.errors = [Unreachable()]
        with pytest.raises(Unreachable):
            await port.list_servers()
    assert not port.reachable and port.breaker.state() == "open"
    assert sum("stopped answering" in r.message for r in caplog.records) == 1

    calls = len(inner.calls)
    for _ in range(5):  # refused at once, no call reaches the panel, nothing more logged
        with pytest.raises(PanelUnavailable) as caught:
            await port.get_status(ServerId("a"))
    assert len(inner.calls) == calls and port.stats.rejected == 5
    assert "can't reach AMP" in plain_error(caught.value) and "Try again in" in plain_error(caught.value)
    assert sum("stopped answering" in r.message for r in caplog.records) == 1

    clock.now += COOLDOWN_MIN  # cool-down over: one test call; it fails, so the wait doubles
    inner.errors = [Unreachable()]
    with pytest.raises(Unreachable):
        await port.list_servers()
    assert inner.calls[-1] == "list" and port.breaker.wait() == pytest.approx(COOLDOWN_MIN * 2)

    clock.now += COOLDOWN_MIN * 2
    await port.list_servers()  # answers: closed again
    assert port.reachable and port.breaker.state() == "closed"
    assert any("answering again" in r.message for r in caplog.records)


async def test_only_one_test_call_while_half_open() -> None:
    inner, clock = Inner(), Clock()
    port = guarded(inner, clock, retries=0, trip_after=1)
    inner.errors = [Unreachable()]
    with pytest.raises(Unreachable):
        await port.list_servers()
    clock.now += COOLDOWN_MIN
    assert port.breaker.before_call()  # the test call goes out...
    assert not port.breaker.before_call()  # ...and nothing else until it ends


async def test_last_known_status_survives_a_restart() -> None:
    inner, clock = Inner(), Clock()
    port = guarded(inner, clock)
    await port.get_status(ServerId("a"))
    saved = json.loads(json.dumps(port.export_statuses()))  # through the state file's JSON

    fresh = guarded(Inner(), clock)
    assert fresh.import_statuses(saved) == 1
    found = fresh.cached_status("a")
    assert found is not None
    status, at = found
    assert status.state is ServerState.RUNNING and status.players[0].name == "Ann" and at.tzinfo is not None
    assert fresh.import_statuses({"b": {"at": "nope"}, "c": "x"}) == 0  # broken entries are skipped
    weird = status_from_json({"id": "z", "state": "weird"})
    assert weird is not None and weird.state is ServerState.UNKNOWN
    assert status_from_json(status_to_json(status)) == status


async def test_passes_other_calls_through() -> None:
    inner = Inner()
    await guarded(inner, Clock()).close()
    assert inner.calls == ["close"]


# ------------------------------------------------------------------------------ stale live cards


async def test_live_card_shows_last_known_status_marked_stale(h: Harness) -> None:
    channel = EditChannel(77)
    add_channel(h, channel)
    port = GuardedPort(h.port, retries=0, trip_after=1, sleep=no_sleep)
    h.ctx.port = port  # type: ignore[assignment]
    await h.ctx.settings.set(CARD_KEY, {"channel": 77}, scope=SURVIVAL)
    cards = LiveCards(h.ctx, pacer=Pacer(clock=Clock()))
    row = await h.ctx.servers.get(SURVIVAL)
    assert row is not None
    await cards.refresh_one(row)
    fresh = channel.sent[-1]["embed"]
    assert fresh.colour.value != MUTED and not fresh.description

    async def down(_server: ServerId) -> ServerStatus:
        raise Unreachable("no answer")

    h.port.get_status = down  # type: ignore[method-assign]
    await cards.refresh_one(row)
    stale = channel.edits[-1]["embed"]
    fields = {f.name: f.value for f in stale.fields}
    assert stale.colour.value == MUTED and "isn't answering" in stale.description
    assert fields["State"] == {f.name: f.value for f in fresh.fields}["State"]  # last known data, not "Unknown"
    assert not port.reachable

    await cards.refresh_one(row)  # circuit open: still the stale card, refused without asking the panel
    assert "isn't answering" in channel.edits[-1]["embed"].description


# ------------------------------------------------------------------------------ settings and the dry run


def test_defaults_keep_optional_features_off(tmp_path: Path) -> None:
    checked = check(GOOD_ENV | {"PORTCULLIS_DB": str(tmp_path / "bot.db")}, env_file=None)
    ops = checked.ops
    assert not ops.metrics and ops.update_check == "off"
    assert ops.metrics_host == "127.0.0.1" and ops.log_format == "text" and ops.banner
    assert ops.log_file == tmp_path / "logs" / "portcullis.log" and ops.state_file == tmp_path / "state.json"


def test_every_problem_is_listed_at_once_without_secrets() -> None:
    env = {"AMP_URL": "ftp://x", "AMP_PASSWORD": SECRET, "PORTCULLIS_METRICS": "on",
           "PORTCULLIS_METRICS_HOST": "0.0.0.0", "PORTCULLIS_LOG_KEEP": "999", "PORTCULLIS_BANNER": "maybe",
           "PORTCULLIS_UPDATE_CHECK": "apply", "PORTCULLIS_UPDATE_REPO": "not a repo"}
    with pytest.raises(ConfigError) as caught:
        check(env, env_file=None)
    text = str(caught.value)
    for expected in ("AMP_URL", "AMP_USERNAME", "DISCORD_TOKEN", "PORTCULLIS_METRICS_TOKEN", "PORTCULLIS_LOG_KEEP",
                     "PORTCULLIS_BANNER must be on or off", "PORTCULLIS_UPDATE_CHECK must be one of off, notify",
                     "owner/name"):
        assert expected in text
    assert SECRET not in text


def test_metrics_on_another_host_needs_a_long_token(tmp_path: Path) -> None:
    base = GOOD_ENV | {"PORTCULLIS_DB": str(tmp_path / "bot.db"), "PORTCULLIS_METRICS": "on",
                       "PORTCULLIS_METRICS_HOST": "0.0.0.0"}
    with pytest.raises(ConfigError, match="shorter than 16"):
        check(base | {"PORTCULLIS_METRICS_TOKEN": "short"}, env_file=None)
    ok = check(base | {"PORTCULLIS_METRICS_TOKEN": "x" * 32}, env_file=None)
    assert ok.ops.metrics and ok.ops.secrets() == ["x" * 32]
    assert loopback("127.0.0.1") and loopback("localhost") and loopback("::1") and not loopback("0.0.0.0")


def test_typos_are_warned_about() -> None:
    assert typos(["PORTCULLIS_LOG_LEVLE", "PORTCULLIS_DB", "PATH", "PORTCULLIS_SOMETHING_NEW"]) == [
        "PORTCULLIS_LOG_LEVLE isn't a setting Portcullis knows. Did you mean PORTCULLIS_LOG_LEVEL?"]


def test_unwritable_folder_is_a_problem(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(ConfigError, match="can't write"):
        check(GOOD_ENV | {"PORTCULLIS_DB": str(blocker / "bot.db")}, env_file=None)


def checked_for(tmp_path: Path, **ops: Any) -> Checked:
    settings = Settings(amp_url="http://192.168.1.10:8443", amp_username="bot", amp_password=SECRET,
                        discord_token=TOKEN, guild_id=123, owner_ids=(1,), database=tmp_path / "bot.db")
    return Checked(settings, OpsSettings(**ops), [])


async def test_dry_run_reports_logins_and_never_prints_secrets(tmp_path: Path) -> None:
    async def panel_bad(_c: Checked, _r: Any) -> tuple[bool, str]:
        return False, f"the panel refused the login ({SECRET})"

    async def discord_ok(_c: Checked) -> tuple[bool, str]:
        return True, "token works (Portcullis#0001), in Test"

    out = io.StringIO()
    code = await run_checks(checked_for(tmp_path), stream=out, panel=panel_bad, discord_check=discord_ok)
    text = out.getvalue()
    assert code == 1 and "FAIL Panel" in text and "ok Discord" in text  # StringIO has no encoding: ASCII marks
    assert SECRET not in text and TOKEN not in text
    assert not (tmp_path / "bot.db").exists()  # a dry run writes nothing

    out = io.StringIO()
    assert await run_checks(checked_for(tmp_path), offline=True, stream=out) == 0
    assert "skipped (--offline)" in out.getvalue()


def test_main_check_offline_and_bad_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    for key in list(GOOD_ENV) + ["PORTCULLIS_DB"]:
        monkeypatch.delenv(key, raising=False)
    assert entry.main(["--check", "--offline"]) == 2  # nothing set
    err = capsys.readouterr().err
    assert "AMP_URL is missing" in err and "--check" in err
    for key, value in GOOD_ENV.items():
        monkeypatch.setenv(key, value)
    assert entry.main(["--check", "--offline"]) == 0
    assert "Everything checks out" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()


async def test_first_login_survives_a_panel_that_is_down() -> None:
    class Amp:
        base_url = "http://u:p@10.0.0.1:8443"

        def __init__(self, error: BaseException) -> None:
            self.error = error

        async def start(self) -> None:
            raise self.error

    ok, text = await entry.first_login(Amp(Unreachable()))  # type: ignore[arg-type]
    assert not ok and "retrying" in text
    with pytest.raises(Status):
        await entry.first_login(Amp(Status(401)))  # type: ignore[arg-type]


# ------------------------------------------------------------------------------ restart recovery


def test_state_file_records_clean_and_unclean_ends(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    first = StateFile(path)
    assert first.load() == {} and first.last_run is None
    first.started("0.1.0", {"a": {"at": "x"}})

    crashed = StateFile(path)
    assert crashed.load() == {"a": {"at": "x"}}
    assert crashed.last_run is not None and not crashed.last_run.clean
    assert "without a clean stop" in describe(crashed.last_run)

    crashed.started("0.1.0")
    crashed.stopped("Ctrl+C", {})
    clean = StateFile(path)
    clean.load()
    assert clean.last_run is not None and clean.last_run.clean and "(Ctrl+C)" in describe(clean.last_run)
    assert not path.with_name("state.json.tmp").exists()


def test_broken_state_file_is_a_fresh_start(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json")
    state = StateFile(path)
    assert state.load() == {} and state.last_run is None
    assert StateFile(None).load() == {} and not StateFile(None).save()


def test_describe_wording() -> None:
    at = datetime(2026, 11, 14, 12, tzinfo=UTC)
    assert describe(None) == "first start (no earlier run recorded)"
    run = LastRun(started_at=at - timedelta(hours=3), seen_at=at - timedelta(hours=2), stopped_at=None,
                  stop_reason=None, version="0.1.0")
    assert describe(run, at) == "ended without a clean stop (crash or power loss); last seen 2 h ago"


# ------------------------------------------------------------------------------ banner


def test_banner_hides_secrets_and_falls_back_to_ascii(tmp_path: Path) -> None:
    checked = checked_for(tmp_path)
    settings = Settings(**{**checked.settings.__dict__, "amp_url": f"http://bot:{SECRET}@10.0.0.1:8443"})
    info = banner.BootInfo("0.1.0", "reachable, logged in", True, "first start", ["a warning"])
    fancy = banner.render(settings, checked.ops, info, unicode=True, colour=True)
    plain = banner.render(settings, checked.ops, info, unicode=False, colour=False)
    for text in (fancy, plain):
        assert SECRET not in text and TOKEN not in text
        assert "http://10.0.0.1:8443" in text and "metrics off" in text and "a warning" in text
    assert "\x1b[38;2;232;113;10m" in fancy and "█" in fancy and "╔" not in fancy
    plain.encode("ascii")  # no block characters, no middle dots
    assert "\x1b[" not in plain


def test_banner_mark_is_five_lines_in_every_mode() -> None:
    for unicode, colour in ((True, True), (True, False), (False, False)):
        rows = banner.mark_lines(unicode=unicode, colour=colour)
        assert len(rows) == 5
        assert all(len(re.sub(r"\x1b\[[0-9;]*m", "", row)) == banner.MARK_WIDTH for row in rows)
    assert all("\x1b" not in row for row in banner.mark_lines(unicode=True, colour=False))
    assert "▒" in "".join(banner.mark_lines(unicode=True, colour=False))  # folds stay visible without colour
    "".join(banner.mark_lines(unicode=False)).encode("ascii")


def test_banner_survives_a_legacy_console_encoding(tmp_path: Path) -> None:
    checked = checked_for(tmp_path)
    info = banner.BootInfo("0.1.0", "reachable", True, "first start")
    for encoding in ("cp1252", "cp437", "ascii"):
        stream = io.TextIOWrapper(io.BytesIO(), encoding=encoding)  # strict: raises on what it can't encode
        banner.show(checked.settings, checked.ops, info, stream)  # must not raise UnicodeEncodeError
        stream.flush()
        assert b"portcullis" in stream.buffer.getvalue()
        assert b"\x1b[" not in stream.buffer.getvalue()  # not a terminal: no colour codes either


def test_no_colour_env_and_redirected_output_drop_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    from portcullis.ops.logs import supports_colour

    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setenv("NO_COLOR", "1")
    assert not supports_colour(Tty())
    monkeypatch.delenv("NO_COLOR")
    assert not supports_colour(io.StringIO())


def test_banner_detects_what_the_console_can_draw() -> None:
    assert banner.can_draw(io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    assert not banner.can_draw(io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    assert not banner.can_draw(io.TextIOWrapper(io.BytesIO(), encoding="cp437"))


def test_banner_can_be_turned_off(tmp_path: Path) -> None:
    out = io.StringIO()
    checked = checked_for(tmp_path, banner=False)
    banner.show(checked.settings, checked.ops, banner.BootInfo("1", "x", True, "y"), out)
    assert out.getvalue() == ""


# ------------------------------------------------------------------------------ logs


def test_json_log_file_masks_secrets_and_rotates(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "bot.log"
    checked = checked_for(tmp_path, log_file=log_file, log_format="json", log_max_mb=1, log_keep=2)
    console = io.StringIO()
    root = logging.getLogger()
    level = root.level
    try:
        logs.setup(checked.settings, checked.ops, stream=console)
        logging.getLogger("portcullis.test").warning("password is %s", SECRET)
        try:
            raise ValueError(TOKEN)
        except ValueError:
            logging.getLogger("portcullis.test").exception("failed")
        handler = next(h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler))
        assert handler.maxBytes == 1024 * 1024 and handler.backupCount == 2
    finally:
        logs.close()
        for handler in list(root.handlers):
            if getattr(handler, logs.TAG, False):
                root.removeHandler(handler)
        root.setLevel(level)
    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["level"] == "WARNING" and lines[0]["logger"] == "portcullis.test"
    assert "error" in lines[1] and lines[0]["time"].endswith("+00:00")
    everything = log_file.read_text(encoding="utf-8") + console.getvalue()
    assert SECRET not in everything and TOKEN not in everything
    assert "WARNING" in console.getvalue() and "\x1b[" not in console.getvalue()  # not a terminal: no colour


# ------------------------------------------------------------------------------ metrics


def snap(**kw: Any) -> Snapshot:
    base: dict[str, Any] = dict(version="0.1.0", uptime=12.5, discord_ready=True, discord_latency=0.05,
                                panel_reachable=True, panel_circuit="closed", panel_calls=7, servers={"running": 2},
                                players=3)
    return Snapshot(**(base | kw))


def test_prometheus_text() -> None:
    text = prometheus(snap())
    assert "# TYPE portcullis_panel_requests_total counter" in text
    assert 'portcullis_servers{state="running"} 2' in text and "portcullis_players_online 3" in text
    assert 'portcullis_info{version="0.1.0"} 1' in text and text.endswith("\n")
    assert health_json(snap(panel_reachable=False))["status"] == "degraded"


async def test_endpoint_health_status_and_token() -> None:
    current = {"snap": snap()}
    server = MetricsServer(lambda: current["snap"], token="t" * 20)
    async with TestClient(TestServer(server.app())) as client:
        assert (await client.get("/healthz")).status == 401
        assert (await client.get("/metrics", headers={"Authorization": "Bearer wrong"})).status == 401
        auth = {"Authorization": "Bearer " + "t" * 20}
        resp = await client.get("/healthz", headers=auth)
        assert resp.status == 200 and (await resp.json())["discord"]["latency_ms"] == 50
        current["snap"] = snap(discord_ready=False)
        assert (await client.get("/healthz", headers=auth)).status == 503
        resp = await client.get("/metrics", headers=auth)
        assert resp.status == 200 and resp.headers["Content-Type"].startswith("text/plain; version=0.0.4")
        assert (await client.post("/metrics", headers=auth)).status == 405


async def test_endpoint_reports_a_taken_port_without_raising() -> None:
    first = MetricsServer(snap, port=0)
    assert await first.start()
    try:
        second = MetricsServer(snap, port=1)  # privileged or unusable: start() reports it, never raises
        await second.start()
        await second.close()
    finally:
        await first.close()


def test_snapshot_of_reads_the_bot() -> None:
    from portcullis.core.live import LiveState

    live = LiveState()
    live.states = {ServerId("a"): ServerState.RUNNING, ServerId("b"): ServerState.STOPPED}

    class Bot:
        latency = float("inf")

        def __init__(self) -> None:
            self.live = live

        def is_ready(self) -> bool:
            return True

    result = snapshot_of(Bot(), guarded(Inner(), Clock()), "0.1.0", 0.0)
    assert result.servers == {"running": 1, "stopped": 1} and result.discord_latency is None
    assert result.healthy and result.panel_circuit == "closed"


# ------------------------------------------------------------------------------ updates


REPO = "MinVT-dev/Portcullis"


def test_release_parsing_and_comparison() -> None:
    good = {"tag_name": "v0.2.0", "html_url": f"https://github.com/{REPO}/releases/tag/v0.2.0"}
    assert read_release(good, REPO) == Release("v0.2.0", good["html_url"])
    assert read_release(good | {"prerelease": True}, REPO) is None
    assert read_release(good | {"draft": True}, REPO) is None
    assert read_release(good | {"html_url": "https://evil.example/releases/x"}, REPO) is None
    assert read_release(good | {"tag_name": "nightly"}, REPO) is None
    assert parse_version("1.2.3") == (1, 2, 3) and parse_version("1.2") is None
    assert newer("0.1.9", Release("v0.2.0", "")) and not newer("0.2.0", Release("v0.2.0", ""))
    assert newer("dev", Release("v0.0.1", ""))
    assert "never updates itself" in notice("0.1.0", Release("v0.2.0", "u"))


async def test_update_check_notifies_once_per_release() -> None:
    sent: list[str] = []
    store: dict[str, str] = {}

    async def notify(text: str) -> None:
        sent.append(text)

    async def seen() -> str | None:
        return store.get("tag")

    async def remember(tag: str) -> None:
        store["tag"] = tag

    checker = UpdateChecker(REPO, "0.1.0", notify=notify, seen=seen, remember=remember)

    async def latest() -> Release | None:
        return Release("v0.2.0", f"https://github.com/{REPO}/releases/tag/v0.2.0")

    checker.latest = latest  # type: ignore[method-assign]
    assert await checker.check() is not None
    assert await checker.check() is None  # already told
    assert len(sent) == 1 and "v0.2.0" in sent[0]

    async def broken() -> Release | None:
        raise TimeoutError

    checker.latest = broken  # type: ignore[method-assign]
    assert await checker.check() is None  # logged, never raised


# ------------------------------------------------------------------------------ the whole start and stop


def test_run_with_ops_features_records_a_clean_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``run()`` as ``python -m portcullis`` runs it, with the state file, log file and metrics endpoint on."""
    import asyncio
    import signal
    import socket

    from aiohttp import ClientSession
    from discord import app_commands

    from portcullis.bot.client import PortcullisBot
    from portcullis.bot.emojis import EMOJI
    from portcullis_api.testing import PASSWORD, USERNAME, FakeAmp
    from tests.test_bot import GUILD

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    seen: dict[str, Any] = {}

    async def login(self: PortcullisBot, token: str) -> None:
        await self.setup_hook()

    async def connect(self: PortcullisBot, *, reconnect: bool = True) -> None:
        async def poke() -> None:
            async with ClientSession() as http:
                async with http.get(f"http://127.0.0.1:{free_port}/healthz") as resp:
                    seen["health"] = resp.status, await resp.json()
            signal.raise_signal(signal.SIGTERM)

        asyncio.get_running_loop().call_later(0.2, lambda: asyncio.ensure_future(poke()))
        await asyncio.Event().wait()

    async def no_sync(self: app_commands.CommandTree, *, guild: Any = None) -> list[Any]:
        return []

    async def no_emojis(client: Any) -> list[str]:
        return []

    monkeypatch.setattr(PortcullisBot, "login", login)
    monkeypatch.setattr(PortcullisBot, "connect", connect)
    monkeypatch.setattr(app_commands.CommandTree, "sync", no_sync)
    monkeypatch.setattr(EMOJI, "fetch", no_emojis)
    ops = OpsSettings(log_file=tmp_path / "logs" / "bot.log", log_format="json", state_file=tmp_path / "state.json",
                      banner=False, metrics=True, metrics_port=free_port)

    async def scenario() -> str:
        server = TestServer(FakeAmp().app())
        await server.start_server()
        settings = Settings(amp_url=str(server.make_url("")).rstrip("/"), amp_username=USERNAME,
                            amp_password=PASSWORD, discord_token="x.y.z", guild_id=GUILD,
                            database=tmp_path / "bot.db")
        try:
            return await entry.run(settings, ops)
        finally:
            await server.close()

    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert asyncio.run(scenario()) == "SIGTERM"
    finally:
        logs.close()
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
    status, body = seen["health"]
    assert body["panel"]["reachable"] is True and body["discord"]["connected"] is False and status == 503
    state = StateFile(tmp_path / "state.json")
    state.load()
    assert state.last_run is not None and state.last_run.clean and state.last_run.stop_reason == "SIGTERM"
    assert PASSWORD not in (tmp_path / "logs" / "bot.log").read_text(encoding="utf-8")
