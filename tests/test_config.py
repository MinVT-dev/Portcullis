# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from portcullis.config import ConfigError, load, parse_env_file, setup_logging

GOOD = {"AMP_URL": "http://192.168.1.10:8443/", "AMP_USERNAME": "portcullis",
        "AMP_PASSWORD": "s3cret-pass-word", "DISCORD_TOKEN": "discord-token-value",
        "DISCORD_GUILD_ID": "123456789012345678"}


def test_valid_settings() -> None:
    s = load(GOOD, env_file=None)
    assert s.amp_url == "http://192.168.1.10:8443"
    assert "s3cret" not in repr(s) and "discord-token" not in repr(s)


def test_reports_every_problem_without_echoing_secrets() -> None:
    with pytest.raises(ConfigError) as info:
        load({"AMP_URL": "192.168.1.10:8443", "AMP_PASSWORD": "s3cret-pass-word",
              "PORTCULLIS_OWNER_IDS": "123, abc", "PORTCULLIS_LOG_LEVEL": "loud"}, env_file=None)
    text = str(info.value)
    for needle in ("AMP_URL must start", "AMP_USERNAME is missing", "DISCORD_TOKEN is missing",
                   "'abc' is not a number", "PORTCULLIS_LOG_LEVEL", "DISCORD_GUILD_ID is missing"):
        assert needle in text
    assert "s3cret" not in text


def test_discord_token_optional_for_tools() -> None:
    env = {k: v for k, v in GOOD.items() if k != "DISCORD_TOKEN"}
    assert load(env, env_file=None, require_discord=False).discord_token is None


def test_url_with_path_is_rejected() -> None:
    with pytest.raises(ConfigError, match="without a path"):
        load({**GOOD, "AMP_URL": "http://host:8443/api"}, env_file=None)


def test_env_file_is_read_and_environment_wins(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text('# comment\nexport AMP_URL="http://file:8443"\nAMP_USERNAME=fromfile\n'
                 "AMP_PASSWORD='quoted pass'\nPORTCULLIS_OWNER_IDS=1,2\n")
    assert parse_env_file(f)["AMP_PASSWORD"] == "quoted pass"
    s = load({"AMP_USERNAME": "fromenv"}, env_file=f, require_discord=False)
    assert (s.amp_url, s.amp_username, s.amp_password, s.owner_ids) == \
        ("http://file:8443", "fromenv", "quoted pass", (1, 2))


def test_logging_is_redacted(caplog: pytest.LogCaptureFixture) -> None:
    s = load(GOOD, env_file=None)
    setup_logging(s)
    for handler in logging.getLogger().handlers:
        assert handler.filters
    caplog.handler.addFilter(logging.getLogger().handlers[0].filters[0])
    logging.getLogger("x").warning("token=%s", "discord-token-value")
    assert "discord-token-value" not in caplog.text
