# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start-up dependency check: is everything the bot needs installed, and if not, install it.

Runs before the first third-party import (``python -m portcullis``), so this module may only use the
standard library. The rules, because installing code at run time is a supply-chain risk:

* Only requirement lines from this project's own ``pyproject.toml`` are ever installed, at the
  versions it names (the installed metadata is the fallback when the file is not there). Nothing from
  ``.env``, Discord or the network decides what gets installed. A requirement that points at a URL
  is refused.
* pip runs as the current interpreter (``sys.executable -m pip``), and only inside a virtualenv.
  Outside one, nothing is installed and the exact command is printed instead.
* One pass, never a loop: install, check again, report. No restart is needed because nothing the
  bot imports has been loaded yet.
* Missing *required* packages that cannot be installed stop the start with a clear message. Missing
  *optional* ones (the ``voice`` extra: PyNaCl, davey) only warn.
* ``PORTCULLIS_AUTO_INSTALL=off`` or ``--no-auto-install`` turns installing off, and ``--check``
  only reports. Both still say what is missing.
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPT_OUT_ENV = "PORTCULLIS_AUTO_INSTALL"
OPTIONAL_EXTRAS = ("voice",)  # optional-dependencies groups that only warn when missing
PIP_TIMEOUT = 600  # seconds for one pip run
EXIT_MISSING = 3  # exit code when a required package is missing and could not be installed

Runner = Callable[..., Any]  # subprocess.run, or a stand-in in tests


@dataclass
class Report:
    """What the check found and did. ``required``/``optional`` hold requirement strings."""

    missing_required: list[str] = field(default_factory=list)  # not satisfied when the check began
    missing_optional: list[str] = field(default_factory=list)
    installed: list[str] = field(default_factory=list)  # requirement strings now satisfied by this run
    failed: list[str] = field(default_factory=list)  # still not satisfied after trying
    note: str = ""  # why nothing was installed, or what pip said when it failed
    command: str = ""  # the exact pip command, when someone has to run it by hand

    @property
    def fatal(self) -> bool:
        return any(item not in self.installed for item in self.missing_required)

    @property
    def clean(self) -> bool:
        return not self.missing_required and not self.missing_optional

    def lines(self) -> list[str]:
        """Short messages for the log, the banner and the alert channel."""
        out: list[str] = []
        if self.installed:
            out.append("Installed missing packages: " + ", ".join(self.installed) + ".")
        still_required = [r for r in self.missing_required if r not in self.installed]
        still_optional = [r for r in self.missing_optional if r not in self.installed]
        if still_required:
            out.append("Required packages are missing: " + ", ".join(still_required) + ". " + self._how())
        if still_optional:
            out.append("Optional packages are missing: " + ", ".join(still_optional)
                       + " (voice support stays off). " + self._how())
        return out

    def _how(self) -> str:
        if self.command:
            return f"{self.note} Run: {self.command}".strip()
        return self.note


def pyproject_path() -> Path:
    return Path(__file__).resolve().parents[2] / "pyproject.toml"


def load_requirements(path: Path | None = None) -> tuple[list[str], list[str]]:
    """(required, optional) requirement strings, from pyproject.toml or else the installed metadata."""
    path = path or pyproject_path()
    if path.is_file():
        import tomllib

        project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
        required = [str(r) for r in project.get("dependencies", [])]
        groups = project.get("optional-dependencies", {})
        optional = [str(r) for extra in OPTIONAL_EXTRAS for r in groups.get(extra, [])]
        return required, optional
    required, optional = [], []
    for line in metadata.requires("portcullis") or []:
        spec, _, marker = line.partition(";")
        if any(f'extra == "{extra}"' in marker or f"extra == '{extra}'" in marker for extra in OPTIONAL_EXTRAS):
            optional.append(spec.strip())
        elif "extra ==" not in marker:
            required.append(line.strip())
    return required, optional


def unsatisfied(requirements: list[str]) -> list[str]:
    """The requirements that apply here and are not installed at an allowed version."""
    from packaging.requirements import InvalidRequirement, Requirement

    out: list[str] = []
    for text in requirements:
        try:
            req = Requirement(text)
        except InvalidRequirement:
            continue  # never touch a line we cannot read
        if req.url is not None:
            continue  # a direct URL is never installed by this check
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        try:
            have = metadata.version(req.name)
        except metadata.PackageNotFoundError:
            out.append(text)
            continue
        if req.specifier and not req.specifier.contains(have, prereleases=True):
            out.append(text)
    return out


def in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def pip_command(requirements: list[str]) -> list[str]:
    return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *requirements]


def command_text(requirements: list[str]) -> str:
    cmd = [sys.executable, "-m", "pip", "install", *requirements]
    return subprocess.list2cmdline(cmd) if sys.platform == "win32" else shlex.join(cmd)


def run_pip(requirements: list[str], runner: Runner = subprocess.run) -> tuple[bool, str]:
    try:
        done = runner(pip_command(requirements), capture_output=True, text=True, timeout=PIP_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"pip could not run ({type(exc).__name__})."
    if done.returncode == 0:
        return True, ""
    tail = " ".join((done.stderr or done.stdout or "").strip().splitlines()[-2:])
    return False, f"pip failed (exit {done.returncode}): {tail[:300]}".strip()


def ensure(*, install: bool = True, runner: Runner = subprocess.run, path: Path | None = None,
           venv: bool | None = None) -> Report:
    """Check, and when allowed install, what pyproject.toml lists. One pass; never raises for a pip problem."""
    try:
        required, optional = load_requirements(path)
        report = Report(missing_required=unsatisfied(required), missing_optional=unsatisfied(optional))
    except ImportError:  # packaging is missing: install just that, from pyproject's own line for it
        report = _bootstrap_packaging(install, runner, path, venv)
        if report is not None:
            return report
        required, optional = load_requirements(path)
        report = Report(missing_required=unsatisfied(required), missing_optional=unsatisfied(optional))
    if report.clean:
        return report
    wanted = [*report.missing_required, *report.missing_optional]
    report.command = command_text(wanted)
    if not install:
        report.note = "Not installing (auto-install is off or this is a dry run)."
        return report
    if not (in_virtualenv() if venv is None else venv):
        report.note = "Not installing because this is not a virtual environment (venv)."
        return report
    report.command = ""
    notes: list[str] = []
    for group in (report.missing_required, report.missing_optional):
        if not group:
            continue
        ok, note = run_pip(group, runner)
        importlib.invalidate_caches()
        left = unsatisfied(group)
        report.installed += [r for r in group if r not in left]
        report.failed += left
        if left:
            notes.append(note or "pip finished but the package still isn't importable.")
    if report.failed:
        report.note = " ".join(notes)
        report.command = command_text(report.failed)
    return report


def _bootstrap_packaging(install: bool, runner: Runner, path: Path | None, venv: bool | None) -> Report | None:
    """None once packaging is importable; otherwise a Report saying it could not be installed."""
    lines = [r for r in load_requirements_raw(path) if r.replace(" ", "").lower().startswith("packaging")]
    report = Report(missing_required=lines)
    report.command = command_text(lines) if lines else "pip install packaging"
    if not install or not (in_virtualenv() if venv is None else venv):
        report.note = "The `packaging` package is missing and is needed to check the rest."
        return report
    ok, note = run_pip(lines, runner) if lines else (False, "no requirement line for packaging")
    importlib.invalidate_caches()
    try:
        importlib.import_module("packaging.requirements")
    except ImportError:
        report.failed = list(lines)
        report.note = note or "packaging is still not importable."
        return report
    return None


def load_requirements_raw(path: Path | None = None) -> list[str]:
    required, optional = load_requirements(path)
    return [*required, *optional]


@dataclass(frozen=True)
class Options:
    check: bool
    install: bool


def options(argv: list[str], env: dict[str, str] | None = None) -> Options:
    env = os.environ if env is None else env
    off = env.get(OPT_OUT_ENV, "").strip().lower() in {"off", "0", "false", "no"}
    return Options(check="--check" in argv or "--dry-run" in argv,
                   install=not (off or "--no-auto-install" in argv or "--check" in argv or "--dry-run" in argv))


LAST: Report | None = None  # the start-up result, for the banner and the alert channel


def startup_check(argv: list[str], *, runner: Runner = subprocess.run, path: Path | None = None,
                  venv: bool | None = None, out: Any = None) -> Report:
    """Run from ``__main__`` before anything else is imported. Exits the process when a required package
    is missing and could not be installed (exit 3; ``--check`` exits 1 and reports instead of installing)."""
    global LAST
    out = out or sys.stderr
    if any(a in argv for a in ("--version", "-h", "--help")):
        return Report()
    opts = options(argv)
    report = ensure(install=opts.install, runner=runner, path=path, venv=venv)
    LAST = report
    for line in report.lines():
        print(line, file=out)
    if report.missing_required and report.fatal:
        print("Portcullis cannot start without these. Install them, then start again.", file=out)
        raise SystemExit(1 if opts.check else EXIT_MISSING)
    return report
