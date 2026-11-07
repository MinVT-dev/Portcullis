# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings from environment variables or a ``.env`` file, checked once at start-up.

Every problem is reported at once, in plain words, and secret values are never echoed. Real
environment variables win over ``.env``, so a container or AMP's App Runner can override the file.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from portcullis.amp.redact import RedactingFilter, Redactor

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Portcullis can't start:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class Settings:
    amp_url: str
    amp_username: str
    amp_password: str = field(repr=False)
    discord_token: str | None = field(default=None, repr=False)
    owner_ids: tuple[int, ...] = ()
    guild_id: int | None = None  # the Discord server slash commands are registered in
    operator_role_ids: tuple[int, ...] = ()  # Discord roles allowed to start, stop and restart servers
    database: Path = Path("data/portcullis.db")
    log_level: str = "INFO"

    def secrets(self) -> list[str]:
        return [s for s in (self.amp_password, self.discord_token) if s]


def parse_env_file(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines. ``#`` starts a comment line; surrounding quotes are removed."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_env_file(path: Path) -> None:
    """Copy ``.env`` values into ``os.environ`` without overriding variables that are already set."""
    for key, value in parse_env_file(path).items():
        os.environ.setdefault(key, value)


def load(
    env: Mapping[str, str] | None = None,
    *,
    env_file: Path | None = Path(".env"),
    require_discord: bool = True,
) -> Settings:
    merged: dict[str, str] = dict(parse_env_file(env_file)) if env_file else {}
    merged.update(os.environ if env is None else env)

    def get(name: str) -> str:
        return (merged.get(name) or "").strip()

    problems: list[str] = []

    amp_url = get("AMP_URL").rstrip("/")
    if not amp_url:
        problems.append("AMP_URL is missing. Set it to the panel address, e.g. http://192.168.1.10:8443")
    else:
        parts = urlsplit(amp_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            problems.append("AMP_URL must start with http:// or https:// and include the host, e.g. "
                            "http://192.168.1.10:8443")
        elif parts.path not in ("", "/") or parts.query:
            problems.append("AMP_URL should be just the panel address, without a path such as /api")

    username = get("AMP_USERNAME")
    if not username:
        problems.append("AMP_USERNAME is missing. Use a dedicated bot account, not your own.")
    password = merged.get("AMP_PASSWORD") or ""
    if not password:
        problems.append("AMP_PASSWORD is missing.")

    token = get("DISCORD_TOKEN") or None
    if require_discord and not token:
        problems.append("DISCORD_TOKEN is missing. Copy it from the Discord developer portal (Bot tab).")

    def id_list(name: str, what: str) -> list[int]:
        ids: list[int] = []
        for part in filter(None, (p.strip() for p in get(name).split(","))):
            if part.isdigit():
                ids.append(int(part))
            else:
                problems.append(f"{name} must be Discord {what} ids separated by commas; {part!r} is not a number.")
        return ids

    owner_ids = id_list("PORTCULLIS_OWNER_IDS", "user")
    operator_role_ids = id_list("PORTCULLIS_OPERATOR_ROLE_IDS", "role")

    guild_id: int | None = None
    raw_guild = get("DISCORD_GUILD_ID")
    if require_discord and not raw_guild:
        problems.append("DISCORD_GUILD_ID is missing. Portcullis serves one Discord server: right-click its "
                        "icon and Copy Server ID (turn on Developer Mode in Discord's Advanced settings first).")
    elif raw_guild:
        if raw_guild.isdigit():
            guild_id = int(raw_guild)
        else:
            problems.append("DISCORD_GUILD_ID must be the numeric id of your Discord server "
                            "(right-click the server icon, Copy Server ID).")

    level = (get("PORTCULLIS_LOG_LEVEL") or "INFO").upper()
    if level not in LOG_LEVELS:
        problems.append(f"PORTCULLIS_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}.")

    database = Path(get("PORTCULLIS_DB") or "data/portcullis.db")

    if problems:
        raise ConfigError(problems)
    return Settings(
        amp_url=amp_url,
        amp_username=username,
        amp_password=password,
        discord_token=token,
        owner_ids=tuple(owner_ids),
        guild_id=guild_id,
        operator_role_ids=tuple(operator_role_ids),
        database=database,
        log_level=level,
    )


def setup_logging(settings: Settings, redactor: Redactor | None = None) -> Redactor:
    """Configure root logging with every handler passing through the redactor."""
    redactor = redactor or Redactor()
    for secret in settings.secrets():
        redactor.add(secret)
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter(redactor))
    return redactor
