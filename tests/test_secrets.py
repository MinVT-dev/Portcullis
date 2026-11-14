# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where secrets come from, the plain-text warning, and the ``secrets`` command (with a fake keyring)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from portcullis import secretstore
from portcullis.config import ConfigError, load
from portcullis.secretstore import cli, find, remove_from_env_file

BASE = {"AMP_URL": "http://10.0.0.2:8443", "AMP_USERNAME": "bot", "DISCORD_GUILD_ID": "1"}
PASSWORD, TOKEN = "s3cret-pass-word", "discord-token-value"


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store: dict[str, str] = {}

    def delete(service: str, name: str) -> None:
        if name not in store:
            raise KeyError(name)
        del store[name]

    fake = SimpleNamespace(get_password=lambda service, name: store.get(name),
                           set_password=lambda service, name, value: store.__setitem__(name, value),
                           delete_password=delete)
    monkeypatch.setattr(secretstore, "_keyring", lambda: fake)
    return store


def write_env(tmp_path: Path, **values: str) -> Path:
    path = tmp_path / ".env"
    path.write_text("# settings\n" + "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def test_lookup_order(tmp_path: Path, vault: dict[str, str]) -> None:
    secret_file = tmp_path / "pw"
    secret_file.write_text(" from-file \n", encoding="utf-8")
    vault["AMP_PASSWORD"] = "from-keyring"
    dotenv = {"AMP_PASSWORD": "from-dotenv"}
    assert find("AMP_PASSWORD", {"AMP_PASSWORD": "from-env"}, dotenv) == secretstore.Found("from-env", "environment")
    from_file = find("AMP_PASSWORD", {"AMP_PASSWORD_FILE": str(secret_file)}, dotenv)
    assert from_file == secretstore.Found("from-file", "file")
    assert find("AMP_PASSWORD", {}, dotenv) == secretstore.Found("from-keyring", "keyring")
    vault.clear()
    assert find("AMP_PASSWORD", {}, dotenv) == secretstore.Found("from-dotenv", ".env")
    problems: list[str] = []
    assert find("AMP_PASSWORD", {"AMP_PASSWORD_FILE": str(tmp_path / "missing")}, dotenv, problems) is None
    assert "can't be read" in problems[0]


def test_plaintext_env_warns_by_default_and_can_refuse(tmp_path: Path) -> None:
    env_file = write_env(tmp_path, AMP_PASSWORD=PASSWORD, DISCORD_TOKEN=TOKEN)
    settings = load(BASE, env_file=env_file)
    assert settings.amp_password == PASSWORD and settings.discord_token == TOKEN
    assert dict(settings.secret_sources) == {"AMP_PASSWORD": ".env", "DISCORD_TOKEN": ".env"}
    [note] = settings.notes
    assert "plain text" in note and PASSWORD not in note
    with pytest.raises(ConfigError) as caught:
        load({**BASE, "PORTCULLIS_PLAINTEXT_SECRETS": "refuse"}, env_file=env_file)
    assert PASSWORD not in str(caught.value) and "secrets migrate" in str(caught.value)
    assert load({**BASE, "PORTCULLIS_PLAINTEXT_SECRETS": "allow"}, env_file=env_file).notes == ()


def test_keyring_secrets_load_without_warning(tmp_path: Path, vault: dict[str, str]) -> None:
    vault.update(AMP_PASSWORD=PASSWORD, DISCORD_TOKEN=TOKEN)
    settings = load(BASE, env_file=write_env(tmp_path))
    assert settings.amp_password == PASSWORD and settings.notes == ()
    assert dict(settings.secret_sources) == {"AMP_PASSWORD": "keyring", "DISCORD_TOKEN": "keyring"}
    assert PASSWORD in settings.secrets() and TOKEN in settings.secrets()  # so the log redactor masks them


def test_migrate_moves_secrets_and_keeps_other_lines(tmp_path: Path, vault: dict[str, str],
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    env_file = write_env(tmp_path, AMP_USERNAME="bot", AMP_PASSWORD=PASSWORD, DISCORD_TOKEN=TOKEN)
    assert cli(["migrate"], confirm=lambda prompt: "n", env_file=env_file) == 1
    assert PASSWORD in env_file.read_text(encoding="utf-8") and vault == {}
    assert cli(["migrate"], confirm=lambda prompt: "y", env_file=env_file) == 0
    text = env_file.read_text(encoding="utf-8")
    assert PASSWORD not in text and TOKEN not in text and "AMP_USERNAME=bot" in text
    assert vault == {"AMP_PASSWORD": PASSWORD, "DISCORD_TOKEN": TOKEN}
    if os.name == "posix":
        assert env_file.stat().st_mode & 0o777 == 0o600
    out = capsys.readouterr().out
    assert PASSWORD not in out and TOKEN not in out


def test_rotate_and_rollback(tmp_path: Path, vault: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    vault["DISCORD_TOKEN"] = "old-token"
    typed = iter(["new-token", "new-token"])
    assert cli(["rotate", "discord_token"], ask=lambda prompt: next(typed), env_file=tmp_path / ".env") == 0
    assert vault == {"DISCORD_TOKEN": "new-token", "DISCORD_TOKEN:previous": "old-token"}
    assert cli(["rollback", "DISCORD_TOKEN"], env_file=tmp_path / ".env") == 0
    assert vault["DISCORD_TOKEN"] == "old-token"
    typed = iter(["one", "two"])
    assert cli(["set", "AMP_PASSWORD"], ask=lambda prompt: next(typed), env_file=tmp_path / ".env") == 1
    assert "AMP_PASSWORD" not in vault
    out = capsys.readouterr().out
    assert "new-token" not in out and "old-token" not in out


def test_status_names_sources_not_values(tmp_path: Path, vault: dict[str, str], monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("AMP_PASSWORD", raising=False)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    vault["AMP_PASSWORD"] = PASSWORD
    env_file = write_env(tmp_path, AMP_PASSWORD="stale", DISCORD_TOKEN=TOKEN)
    assert cli(["status"], env_file=env_file) == 0
    out = capsys.readouterr().out
    assert "AMP_PASSWORD: from the OS keyring" in out and "old copy is still in" in out
    assert "DISCORD_TOKEN: from the .env file (plain text)" in out
    assert PASSWORD not in out and TOKEN not in out and "stale" not in out


def test_no_keyring_is_reported_plainly(capsys: pytest.CaptureFixture[str]) -> None:
    typed = iter(["x", "x"])
    assert cli(["set", "AMP_PASSWORD"], ask=lambda prompt: next(typed)) == 1  # conftest turns the keyring off
    assert "Can't use the OS keyring" in capsys.readouterr().err
    assert cli(["set", "SOMETHING_ELSE"]) == 2 and cli([]) == 2


def test_remove_from_env_file_handles_export_and_comments(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# AMP_PASSWORD=old note\nexport AMP_PASSWORD='x'\nAMP_URL=http://a\n", encoding="utf-8")
    assert remove_from_env_file(path, ["AMP_PASSWORD"]) == ["AMP_PASSWORD"]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# AMP_PASSWORD=old note" and "'x'" not in lines[1] and lines[2] == "AMP_URL=http://a"


def test_startup_check_still_sees_plaintext_env(tmp_path: Path) -> None:
    from portcullis.ops.settings import check

    env_file = write_env(tmp_path, AMP_PASSWORD=PASSWORD, DISCORD_TOKEN=TOKEN)
    checked = check({**BASE, "PORTCULLIS_DB": str(tmp_path / "p.db"), "PORTCULLIS_LOG_FILE": "off",
                     "PORTCULLIS_STATE_FILE": "off"}, env_file=env_file)
    assert dict(checked.settings.secret_sources)["AMP_PASSWORD"] == ".env"
    assert any("plain text" in w for w in checked.warnings) and not any(PASSWORD in w for w in checked.warnings)
