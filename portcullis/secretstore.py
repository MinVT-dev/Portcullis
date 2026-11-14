# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the bot's two secrets live: the AMP password and the Discord token.

Each one is looked up in this order, and the first place that has it wins:

1. An environment variable (``AMP_PASSWORD``, ``DISCORD_TOKEN``), for containers and services.
2. A file named by ``AMP_PASSWORD_FILE`` / ``DISCORD_TOKEN_FILE`` (Docker and systemd secrets).
3. The OS keyring: Windows Credential Manager, macOS Keychain, or the Secret Service on Linux,
   under the service name ``portcullis``. This is the default place for a new install.
4. The ``.env`` file, as plain text. This still works so an existing install keeps running, but the
   bot warns at every start until the secret is moved (``python -m portcullis secrets migrate``).
   ``PORTCULLIS_PLAINTEXT_SECRETS=refuse`` makes it a start-up error instead.

``python -m portcullis secrets`` manages them::

    status            where each secret comes from (never its value)
    set NAME          store a secret in the keyring (asks twice, shows nothing)
    rotate NAME       the same, keeping the old value so ``rollback`` can put it back
    rollback NAME     put the value from before the last rotate back
    migrate           move secrets from .env into the keyring and remove them from .env
    delete NAME       remove a secret from the keyring

Values are never printed, logged or echoed while typed. ``PORTCULLIS_KEYRING=off`` skips the keyring,
for a machine where it would prompt or hang.
"""

from __future__ import annotations

import getpass
import os
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SERVICE = "portcullis"
NAMES = ("AMP_PASSWORD", "DISCORD_TOKEN")
PREVIOUS = ":previous"
PLAINTEXT_MODES = ("warn", "refuse", "allow")
WHERE = {"environment": "an environment variable", "file": "a secret file", "keyring": "the OS keyring",
         ".env": "the .env file (plain text)"}


class KeyringUnavailable(Exception):
    """No OS keyring on this machine (or ``PORTCULLIS_KEYRING=off``)."""


def _keyring():  # noqa: ANN202 - the keyring module, imported only when used
    if os.environ.get("PORTCULLIS_KEYRING", "").strip().lower() in ("off", "0", "false", "no"):
        raise KeyringUnavailable("PORTCULLIS_KEYRING is off.")
    try:
        import keyring
        from keyring.backends.fail import Keyring as NoBackend
    except ImportError as exc:
        raise KeyringUnavailable("The keyring package isn't installed (pip install keyring).") from exc
    if isinstance(keyring.get_keyring(), NoBackend):
        raise KeyringUnavailable("This machine has no OS keyring (on Linux: a Secret Service such as "
                                 "GNOME Keyring or KWallet).")
    return keyring


def keyring_get(name: str) -> str | None:
    """The stored value, or None if there is none or no keyring. Never raises."""
    try:
        return _keyring().get_password(SERVICE, name) or None
    except Exception:  # noqa: BLE001 - a broken keyring must not stop the other sources
        return None


def keyring_set(name: str, value: str) -> None:
    _keyring().set_password(SERVICE, name, value)


def keyring_delete(name: str) -> bool:
    kr = _keyring()
    try:
        kr.delete_password(SERVICE, name)
    except Exception:  # noqa: BLE001 - keyring raises PasswordDeleteError when there is nothing to delete
        return False
    return True


@dataclass(frozen=True)
class Found:
    value: str
    source: str  # a key of WHERE


def find(name: str, environ: Mapping[str, str], dotenv: Mapping[str, str],
         problems: list[str] | None = None) -> Found | None:
    """Look ``name`` up in the order in the module docstring. File problems go to ``problems``."""
    value = environ.get(name) or ""
    if value:
        return Found(value, "environment")
    path_text = (environ.get(name + "_FILE") or dotenv.get(name + "_FILE") or "").strip()
    if path_text:
        path = Path(path_text)
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            if problems is not None:
                problems.append(f"{name}_FILE points to {path}, which can't be read ({exc.strerror or exc}).")
            return None
        if value:
            return Found(value, "file")
        if problems is not None:
            problems.append(f"{name}_FILE points to {path}, which is empty.")
        return None
    value = keyring_get(name) or ""
    if value:
        return Found(value, "keyring")
    value = dotenv.get(name) or ""
    if value:
        return Found(value, ".env")
    return None


def plaintext_note(names: Sequence[str]) -> str:
    listed = " and ".join(names)
    return (f"{listed} {'is' if len(names) == 1 else 'are'} stored as plain text in .env. Anyone who can read "
            "that file can take over the bot. Run `python -m portcullis secrets migrate` to move "
            f"{'it' if len(names) == 1 else 'them'} to the OS keyring.")


# ------------------------------------------------------------------------------ the command line


def _read_new(name: str, ask: Callable[[str], str]) -> str | None:
    first = ask(f"New {name} (not shown): ").strip()
    if not first:
        print("Nothing typed. Nothing changed.")
        return None
    if ask(f"Type the new {name} again: ").strip() != first:
        print("The two didn't match. Nothing changed.")
        return None
    return first


def remove_from_env_file(path: Path, names: Sequence[str]) -> list[str]:
    """Replace ``NAME=...`` lines with a comment. Returns the names it removed. Keeps the file's mode."""
    if not path.is_file():
        return []
    removed: list[str] = []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        bare = line.strip()
        bare = bare[len("export "):] if bare.startswith("export ") else bare
        key = bare.split("=", 1)[0].strip() if "=" in bare and not bare.startswith("#") else ""
        if key in names:
            removed.append(key)
            out.append(f"# {key} is in the OS keyring now (python -m portcullis secrets status)\n")
        else:
            out.append(line)
    if removed:
        mode = stat.S_IMODE(path.stat().st_mode)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(out), encoding="utf-8")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    return removed


def _status(env_file: Path) -> int:
    from portcullis.config import parse_env_file

    dotenv = parse_env_file(env_file)
    try:
        _keyring()
        keyring_note = "available"
    except KeyringUnavailable as exc:
        keyring_note = f"not available: {exc}"
    print(f"OS keyring: {keyring_note}")
    plain = []
    for name in NAMES:
        found = find(name, os.environ, dotenv)
        print(f"{name}: " + (f"from {WHERE[found.source]}" if found else "not set"))
        if found and found.source == ".env":
            plain.append(name)
        if found and found.source != ".env" and dotenv.get(name):
            print(f"  An old copy is still in {env_file} as plain text. Remove that line.")
    if plain:
        print(plaintext_note(plain))
    if env_file.is_file() and os.name == "posix" and env_file.stat().st_mode & 0o077:
        print(f"{env_file} can be read by other users on this machine. Run: chmod 600 {env_file}")
    return 0


def _set(name: str, ask: Callable[[str], str], *, keep_previous: bool) -> int:
    value = _read_new(name, ask)
    if value is None:
        return 1
    old = keyring_get(name)
    if keep_previous and old:
        keyring_set(name + PREVIOUS, old)
    keyring_set(name, value)
    if keyring_get(name) != value:
        print("The keyring didn't keep the value. Nothing else changed.")
        return 1
    print(f"{name} is stored in the OS keyring. Restart Portcullis to use it.")
    if keep_previous and old:
        print(f"The old value is kept. `python -m portcullis secrets rollback {name}` puts it back.")
    if os.environ.get(name):
        print(f"Note: the {name} environment variable is set and wins over the keyring. Unset it.")
    return 0


def _rollback(name: str) -> int:
    old = keyring_get(name + PREVIOUS)
    if not old:
        print(f"There is no earlier {name} to go back to.")
        return 1
    current = keyring_get(name)
    keyring_set(name, old)
    if current:
        keyring_set(name + PREVIOUS, current)
    print(f"{name} is back to the value from before the last rotate. Restart Portcullis to use it.")
    return 0


def _migrate(env_file: Path, confirm: Callable[[str], str], yes: bool) -> int:
    from portcullis.config import parse_env_file

    dotenv = parse_env_file(env_file)
    moving = [n for n in NAMES if dotenv.get(n)]
    if not moving:
        print(f"No secrets in {env_file}. Nothing to move.")
        return 0
    if not yes and confirm(f"Move {', '.join(moving)} from {env_file} into the OS keyring and remove "
                           "them from the file? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing changed.")
        return 1
    for name in moving:
        keyring_set(name, dotenv[name])
        if keyring_get(name) != dotenv[name]:
            print(f"The keyring didn't keep {name}. {env_file} is unchanged.")
            return 1
    removed = remove_from_env_file(env_file, moving)
    print(f"Moved {', '.join(removed)} to the OS keyring and removed {'it' if len(removed) == 1 else 'them'} "
          f"from {env_file}. Restart Portcullis.")
    print("Old copies may still be in backups of that file or in your shell history. Rotate the secret "
          "(`python -m portcullis secrets rotate NAME`) if the file was ever shared.")
    return 0


USAGE = ("Usage: python -m portcullis secrets status | set NAME | rotate NAME | rollback NAME | migrate [--yes] "
         f"| delete NAME\nNAME is one of: {', '.join(NAMES)}")


def cli(argv: Sequence[str], *, ask: Callable[[str], str] = getpass.getpass,
        confirm: Callable[[str], str] = input, env_file: Path = Path(".env")) -> int:
    args = list(argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if args else 2
    command, rest = args[0], args[1:]
    try:
        if command == "status":
            return _status(env_file)
        if command == "migrate":
            return _migrate(env_file, confirm, "--yes" in rest)
        if command in ("set", "rotate", "rollback", "delete"):
            if len(rest) != 1 or rest[0].upper() not in NAMES:
                print(USAGE)
                return 2
            name = rest[0].upper()
            if command == "rollback":
                return _rollback(name)
            if command == "delete":
                print(f"Removed {name} from the OS keyring." if keyring_delete(name) else
                      f"{name} wasn't in the OS keyring.")
                return 0
            return _set(name, ask, keep_previous=command == "rotate")
    except KeyringUnavailable as exc:
        print(f"Can't use the OS keyring: {exc}\nUse an environment variable or {NAMES[0]}_FILE instead "
              "(docs/SETUP.md, Secrets).", file=sys.stderr)
        return 1
    print(USAGE)
    return 2
