# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings for logs, restart recovery, panel handling, the metrics endpoint and update checks.

They come from the same environment and ``.env`` as ``portcullis.config``. ``check`` loads both
and reports every problem from either in one list, in plain words, without echoing secrets. It
also warns about ``PORTCULLIS_*`` names that look like a typo of a real one.
"""

from __future__ import annotations

import difflib
import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from portcullis.config import ConfigError, Settings, load, parse_env_file

OFF_WORDS = {"0", "off", "false", "no", "disabled", "none"}
ON_WORDS = {"1", "on", "true", "yes", "enabled"}
LOG_FORMATS = ("text", "json")
UPDATE_MODES = ("off", "notify")
DEFAULT_UPDATE_REPO = "MinVT-dev/Portcullis"
REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")

# Every variable Portcullis reads. Used only to spot typos; an unknown name is a warning, never an error.
KNOWN = {
    "AMP_URL", "AMP_USERNAME", "AMP_PASSWORD", "DISCORD_TOKEN", "DISCORD_GUILD_ID",
    "PORTCULLIS_OWNER_IDS", "PORTCULLIS_OPERATOR_ROLE_IDS", "PORTCULLIS_DB", "PORTCULLIS_LOG_LEVEL",
    "PORTCULLIS_LOG_FILE", "PORTCULLIS_LOG_FORMAT", "PORTCULLIS_LOG_CONSOLE", "PORTCULLIS_LOG_MAX_MB",
    "PORTCULLIS_LOG_KEEP", "PORTCULLIS_BANNER", "PORTCULLIS_STATE_FILE", "PORTCULLIS_PANEL_RETRIES",
    "PORTCULLIS_PANEL_TRIP_AFTER", "PORTCULLIS_METRICS", "PORTCULLIS_METRICS_HOST", "PORTCULLIS_METRICS_PORT",
    "PORTCULLIS_METRICS_TOKEN", "PORTCULLIS_UPDATE_CHECK", "PORTCULLIS_UPDATE_REPO",
}


def version() -> str:
    try:
        return metadata.version("portcullis")
    except metadata.PackageNotFoundError:
        return "dev"


@dataclass(frozen=True)
class OpsSettings:
    log_file: Path | None = Path("data/logs/portcullis.log")  # None: no log file
    log_format: str = "text"  # the file's format
    console_format: str = "text"
    log_max_mb: int = 5
    log_keep: int = 5
    banner: bool = True
    state_file: Path | None = Path("data/state.json")  # None: no restart recovery
    panel_retries: int = 2
    panel_trip_after: int = 3
    metrics: bool = False
    metrics_host: str = "127.0.0.1"
    metrics_port: int = 9464
    metrics_token: str | None = field(default=None, repr=False)
    update_check: str = "off"
    update_repo: str = DEFAULT_UPDATE_REPO

    def secrets(self) -> list[str]:
        return [self.metrics_token] if self.metrics_token else []


@dataclass(frozen=True)
class Checked:
    settings: Settings
    ops: OpsSettings
    warnings: list[str]


def loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_ops(merged: Mapping[str, str], database: Path, problems: list[str]) -> OpsSettings:
    """Read the ops settings from ``merged``, adding each problem to ``problems``."""

    def get(name: str) -> str:
        return (merged.get(name) or "").strip()

    def flag(name: str, default: bool) -> bool:
        raw = get(name).lower()
        if not raw:
            return default
        if raw in ON_WORDS:
            return True
        if raw in OFF_WORDS:
            return False
        problems.append(f"{name} must be on or off, not {raw!r}.")
        return default

    def number(name: str, default: int, low: int, high: int, what: str) -> int:
        raw = get(name)
        if not raw:
            return default
        if not raw.isdigit() or not low <= int(raw) <= high:
            problems.append(f"{name} must be a whole number from {low} to {high} ({what}).")
            return default
        return int(raw)

    def choice(name: str, default: str, options: tuple[str, ...]) -> str:
        raw = get(name).lower() or default
        if raw not in options:
            problems.append(f"{name} must be one of {', '.join(options)}.")
            return default
        return raw

    def path(name: str, default: Path) -> Path | None:
        raw = get(name)
        if raw.lower() in OFF_WORDS:
            return None
        return Path(raw) if raw else default

    data_dir = database.parent
    log_file = path("PORTCULLIS_LOG_FILE", data_dir / "logs" / "portcullis.log")
    state_file = path("PORTCULLIS_STATE_FILE", data_dir / "state.json")

    host = get("PORTCULLIS_METRICS_HOST") or "127.0.0.1"
    token = merged.get("PORTCULLIS_METRICS_TOKEN") or None
    metrics = flag("PORTCULLIS_METRICS", False)
    if metrics and not loopback(host) and not token:
        problems.append(f"PORTCULLIS_METRICS_HOST is {host}, which other machines can reach, so set "
                        "PORTCULLIS_METRICS_TOKEN as well. Without it anyone on the network can read the bot's "
                        "health. Or keep the default 127.0.0.1.")
    if token is not None and len(token) < 16:
        problems.append("PORTCULLIS_METRICS_TOKEN is shorter than 16 characters. Use a long random value.")

    repo = get("PORTCULLIS_UPDATE_REPO") or DEFAULT_UPDATE_REPO
    if not REPO_RE.match(repo):
        problems.append("PORTCULLIS_UPDATE_REPO must look like owner/name, for example MinVT-dev/Portcullis.")
        repo = DEFAULT_UPDATE_REPO

    return OpsSettings(
        log_file=log_file,
        log_format=choice("PORTCULLIS_LOG_FORMAT", "text", LOG_FORMATS),
        console_format=choice("PORTCULLIS_LOG_CONSOLE", "text", LOG_FORMATS),
        log_max_mb=number("PORTCULLIS_LOG_MAX_MB", 5, 1, 100, "size of one log file before a new one starts"),
        log_keep=number("PORTCULLIS_LOG_KEEP", 5, 1, 50, "old log files kept"),
        banner=flag("PORTCULLIS_BANNER", True),
        state_file=state_file,
        panel_retries=number("PORTCULLIS_PANEL_RETRIES", 2, 0, 5, "extra tries when the panel doesn't answer"),
        panel_trip_after=number("PORTCULLIS_PANEL_TRIP_AFTER", 3, 1, 20,
                                "failures in a row before the bot stops asking the panel for a while"),
        metrics=metrics,
        metrics_host=host,
        metrics_port=number("PORTCULLIS_METRICS_PORT", 9464, 1024, 65535, "a free port above 1023"),
        metrics_token=token,
        update_check=choice("PORTCULLIS_UPDATE_CHECK", "off", UPDATE_MODES),
        update_repo=repo,
    )


def writable_dir(path: Path, name: str, problems: list[str]) -> None:
    """``path``'s folder exists and can be written, or can be created. Creates nothing."""
    probe = path.parent
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if probe.exists() and (not probe.is_dir() or not os.access(probe, os.W_OK)):
        problems.append(f"{name}: Portcullis can't write to {probe.resolve()}. Pick a folder your user can write to.")


def typos(names: list[str]) -> list[str]:
    out = []
    for name in sorted(names):
        if name in KNOWN or not name.startswith("PORTCULLIS_"):
            continue
        close = difflib.get_close_matches(name, KNOWN, n=1, cutoff=0.85)
        if close:
            out.append(f"{name} isn't a setting Portcullis knows. Did you mean {close[0]}?")
    return out


def check(env: Mapping[str, str] | None = None, *, env_file: Path | None = Path(".env"),
          require_discord: bool = True) -> Checked:
    """Load every setting. Raises ``ConfigError`` listing all problems at once."""
    merged: dict[str, str] = dict(parse_env_file(env_file)) if env_file else {}
    merged.update(os.environ if env is None else env)
    problems: list[str] = []
    settings: Settings | None = None
    try:
        settings = load(merged, env_file=None, require_discord=require_discord)
    except ConfigError as exc:
        problems.extend(exc.problems)
    raw_db = (merged.get("PORTCULLIS_DB") or "").strip() or "data/portcullis.db"
    database = settings.database if settings else Path(raw_db)
    ops = load_ops(merged, database, problems)
    writable_dir(database, "PORTCULLIS_DB", problems)
    if ops.log_file is not None:
        writable_dir(ops.log_file, "PORTCULLIS_LOG_FILE", problems)
    if ops.state_file is not None:
        writable_dir(ops.state_file, "PORTCULLIS_STATE_FILE", problems)
    if problems or settings is None:
        raise ConfigError(problems)
    return Checked(settings, ops, typos(list(merged)))
