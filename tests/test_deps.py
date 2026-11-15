# SPDX-License-Identifier: AGPL-3.0-or-later
"""The start-up dependency check and the time zone fallback. pip is always a stand-in here."""

from __future__ import annotations

import importlib.metadata as metadata
import io
import subprocess
import zoneinfo
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from portcullis.bot import events
from portcullis.bot.commands import Modules
from portcullis.ops import deps

PYPROJECT = """\
[project]
name = "demo"
dependencies = [
    "tzdata>=2024.1",
    "keyring>=25",
    "pywin32>=300; sys_platform == 'nowhere'",
]
[project.optional-dependencies]
voice = ["PyNaCl>=1.5", "davey>=0.1"]
dev = ["pytest>=8"]
"""


@pytest.fixture
def pyproject(tmp_path: Path) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT, encoding="utf-8")
    return path


def fake_versions(monkeypatch: pytest.MonkeyPatch, installed: dict[str, str]) -> None:
    def version(name: str) -> str:
        if name.lower() in installed:
            return installed[name.lower()]
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(deps.metadata, "version", version)


class Pip:
    """A stand-in for subprocess.run: records the command and, on success, 'installs' the packages."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, installed: dict[str, str], *, code: int = 0,
                 error: Exception | None = None, only: set[str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.installed, self.code, self.error, self.only = installed, code, error, only
        self.monkeypatch = monkeypatch

    def __call__(self, cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if self.error:
            raise self.error
        if self.code == 0:
            for item in cmd[cmd.index("--no-input") + 1:]:
                name = item.split(">")[0].lower()
                if self.only is None or name in self.only:
                    self.installed[name] = "9999"
        return subprocess.CompletedProcess(cmd, self.code, "", "ERROR: no network" if self.code else "")


def test_reads_required_and_optional_from_pyproject_only(pyproject: Path) -> None:
    required, optional = deps.load_requirements(pyproject)
    assert required[:2] == ["tzdata>=2024.1", "keyring>=25"] and optional == ["PyNaCl>=1.5", "davey>=0.1"]  # no dev


def test_nothing_missing_means_no_pip(monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    fake_versions(monkeypatch, {"tzdata": "2026.1", "keyring": "25.1", "pynacl": "1.6", "davey": "0.2"})
    pip = Pip(monkeypatch, {})
    report = deps.ensure(runner=pip, path=pyproject, venv=True)
    assert report.clean and not pip.calls and report.lines() == []


def test_installs_missing_packages_in_a_venv_and_verifies(monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    have = {"keyring": "25.1"}
    fake_versions(monkeypatch, have)
    pip = Pip(monkeypatch, have)
    report = deps.ensure(runner=pip, path=pyproject, venv=True)
    assert len(pip.calls) == 2  # required first, then the optional ones: one pass, no loop
    assert pip.calls[0][1:5] == ["-m", "pip", "install", "--disable-pip-version-check"]
    assert pip.calls[0][0] == deps.sys.executable and pip.calls[0][-1] == "tzdata>=2024.1"
    assert pip.calls[1][-2:] == ["PyNaCl>=1.5", "davey>=0.1"]
    assert report.installed == ["tzdata>=2024.1", "PyNaCl>=1.5", "davey>=0.1"] and not report.failed
    assert not report.fatal
    assert report.lines() == ["Installed missing packages: tzdata>=2024.1, PyNaCl>=1.5, davey>=0.1."]


def test_a_marker_that_does_not_apply_is_skipped(monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    fake_versions(monkeypatch, {"tzdata": "2026.1", "keyring": "25.1", "pynacl": "1.6", "davey": "0.2"})
    assert deps.unsatisfied(deps.load_requirements(pyproject)[0]) == []  # pywin32 is marked for another platform


def test_an_old_version_counts_as_missing(monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    fake_versions(monkeypatch, {"tzdata": "2023.1", "keyring": "25.1"})
    assert deps.unsatisfied(["tzdata>=2024.1", "keyring>=25"]) == ["tzdata>=2024.1"]


def test_outside_a_venv_nothing_is_installed_and_the_command_is_printed(
        monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    fake_versions(monkeypatch, {"keyring": "25.1"})
    pip = Pip(monkeypatch, {})
    report = deps.ensure(runner=pip, path=pyproject, venv=False)
    assert not pip.calls and report.fatal
    text = " ".join(report.lines())
    assert "not a virtual environment" in text and "-m pip install" in text and "tzdata>=2024.1" in text


def test_opt_out_and_check_mode_report_without_installing(monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    fake_versions(monkeypatch, {"keyring": "25.1"})
    pip = Pip(monkeypatch, {})
    assert deps.ensure(install=False, runner=pip, path=pyproject, venv=True).missing_required == ["tzdata>=2024.1"]
    assert not pip.calls
    assert not deps.options(["--check"], {}).install and not deps.options(["--no-auto-install"], {}).install
    assert not deps.options([], {deps.OPT_OUT_ENV: "off"}).install and deps.options([], {}).install
    assert deps.options(["--check"], {}).check


def test_pip_failure_on_a_required_package_is_fatal(monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    fake_versions(monkeypatch, {"keyring": "25.1"})
    report = deps.ensure(runner=Pip(monkeypatch, {}, code=1), path=pyproject, venv=True)  # offline
    assert report.fatal and "tzdata>=2024.1" in report.failed
    assert "pip failed (exit 1)" in " ".join(report.lines()) and "no network" in report.note


def test_a_missing_optional_package_only_warns(monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    have = {"tzdata": "2026.1", "keyring": "25.1"}
    fake_versions(monkeypatch, have)
    report = deps.ensure(runner=Pip(monkeypatch, have, code=1), path=pyproject, venv=True)
    assert not report.fatal and report.failed == ["PyNaCl>=1.5", "davey>=0.1"]
    assert "voice support stays off" in report.lines()[0]


def test_pip_that_cannot_start_does_not_raise(monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    fake_versions(monkeypatch, {"keyring": "25.1"})
    report = deps.ensure(runner=Pip(monkeypatch, {}, error=FileNotFoundError("python")), path=pyproject, venv=True)
    assert report.fatal and "pip could not run" in report.note


def test_a_url_requirement_is_never_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_versions(monkeypatch, {})
    assert deps.unsatisfied(["evil @ https://example.com/evil.zip", "not a requirement !!"]) == []


def test_startup_check_exits_on_a_missing_required_package(
        monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    fake_versions(monkeypatch, {"keyring": "25.1"})
    err = io.StringIO()
    with pytest.raises(SystemExit) as stop:
        deps.startup_check([], runner=Pip(monkeypatch, {}, code=1), path=pyproject, venv=True, out=err)
    assert stop.value.code == deps.EXIT_MISSING and "cannot start without" in err.getvalue()
    with pytest.raises(SystemExit) as stop:  # --check reports and exits 1, and installs nothing
        pip = Pip(monkeypatch, {})
        deps.startup_check(["--check"], runner=pip, path=pyproject, venv=True, out=err)
    assert stop.value.code == 1 and not pip.calls


def test_startup_check_lets_the_start_continue_with_only_optional_packages_missing(
        monkeypatch: pytest.MonkeyPatch, pyproject: Path) -> None:
    fake_versions(monkeypatch, {"tzdata": "2026.1", "keyring": "25.1"})
    err = io.StringIO()
    report = deps.startup_check(["--no-auto-install"], runner=Pip(monkeypatch, {}), path=pyproject, venv=True, out=err)
    assert report.missing_optional and "Optional packages are missing" in err.getvalue()


def test_the_projects_own_pyproject_lists_tzdata_and_the_voice_extra() -> None:
    required, optional = deps.load_requirements()
    assert any(r.startswith("tzdata") for r in required) and any(r.startswith("packaging") for r in required)
    assert any(r.startswith("PyNaCl") for r in optional) and any(r.startswith("davey") for r in optional)


# ------------------------------------------------------------------------------ time zones


def no_tz_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(key: str) -> Any:
        raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found with key {key}")

    monkeypatch.setattr(events, "ZoneInfo", missing)


def test_zone_never_raises_without_a_time_zone_database(monkeypatch: pytest.MonkeyPatch) -> None:
    no_tz_database(monkeypatch)  # a Windows Python without tzdata: even "UTC" is not found
    for name in (None, "", "UTC", "Europe/Amsterdam", "nonsense"):
        assert events.zone(name) is UTC
    assert events.zone_name(events.zone("Europe/Amsterdam")) == "UTC"
    assert events.valid_zone("UTC") and not events.valid_zone("Europe/Amsterdam")
    start = events.parse_when("2099-01-01 20:00", events.zone("UTC"), datetime(2098, 12, 1, tzinfo=UTC))
    assert start == datetime(2099, 1, 1, 20, tzinfo=UTC)


def test_zone_still_uses_real_zones_when_the_database_exists() -> None:
    pytest.importorskip("tzdata")
    assert events.zone_name(events.zone("Europe/Amsterdam")) == "Europe/Amsterdam"
    assert events.zone("UTC") is not None and isinstance(events.zone("UTC"), (zoneinfo.ZoneInfo, timezone))


# ------------------------------------------------------------------------------ repeating job failures


def test_a_job_that_fails_every_minute_logs_one_traceback_then_stays_quiet(
        caplog: pytest.LogCaptureFixture) -> None:
    modules = Modules.__new__(Modules)
    modules._failing = {}
    caplog.set_level("DEBUG", logger="portcullis.bot.commands")
    for _ in range(5):
        modules.job_failed("digest", RuntimeError("tz"))
    assert [r.levelname for r in caplog.records] == ["ERROR"]
    assert "RuntimeError: tz" in caplog.text  # the one traceback
    modules.job_failed("digest", ValueError("another problem"))  # a different error is reported again
    assert [r.levelname for r in caplog.records] == ["ERROR", "ERROR"]
    for _ in range(60):
        modules.job_failed("digest", ValueError("another problem"))
    assert caplog.records[-1].levelname == "WARNING" and "still failing" in caplog.records[-1].getMessage()
