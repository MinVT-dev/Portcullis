# SPDX-License-Identifier: AGPL-3.0-or-later
"""Logging: readable lines in the console, and a rotating file that can be JSON.

* Console: ``19:40:02 INFO    panel: The panel is answering again``, coloured by level when the
  console supports it. ``PORTCULLIS_LOG_CONSOLE=json`` prints JSON instead (for Docker or systemd).
* File (default ``data/logs/portcullis.log``): a new file every ``PORTCULLIS_LOG_MAX_MB``, keeping
  ``PORTCULLIS_LOG_KEEP`` old ones. Text by default; ``PORTCULLIS_LOG_FORMAT=json`` writes one JSON
  object per line with ``time``, ``level``, ``logger``, ``message`` and, after an error, ``error``.

Every handler masks secrets (passwords, tokens) through the same ``RedactingFilter`` as before,
so neither the console, the file nor JSON can carry them.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import UTC, datetime
from typing import TextIO

from portcullis.config import Settings, plain_http_risk
from portcullis.ops.settings import OpsSettings
from portcullis_api.redact import RedactingFilter, Redactor

TAG = "_portcullis_ops"  # marks handlers this module added, so a second setup replaces them
LEVEL_COLOURS = {"DEBUG": "\x1b[2m", "INFO": "\x1b[36m", "WARNING": "\x1b[33m", "ERROR": "\x1b[31m",
                 "CRITICAL": "\x1b[1;31m"}
RESET = "\x1b[0m"
NOISY = ("discord.gateway", "discord.http", "discord.client")  # INFO chatter on every reconnect


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        error = traceback_of(self, record)
        if error:
            data["error"] = error
        return json.dumps(data, ensure_ascii=False)


def traceback_of(formatter: logging.Formatter, record: logging.LogRecord) -> str | None:
    """The record's traceback. ``RedactingFilter`` has already masked it into ``exc_text``."""
    if record.exc_text:
        return record.exc_text
    if record.exc_info:
        return formatter.formatException(record.exc_info)
    return None


class ConsoleFormatter(logging.Formatter):
    """Short lines for people. The logger name drops the ``portcullis.`` prefix."""

    def __init__(self, colour: bool) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self.colour = colour

    def format(self, record: logging.LogRecord) -> str:
        name = record.name.removeprefix("portcullis.").removeprefix("bot.")
        level = f"{record.levelname:<7}"
        if self.colour:
            level = f"{LEVEL_COLOURS.get(record.levelname, '')}{level}{RESET}"
        line = f"{self.formatTime(record, self.datefmt)} {level} {name}: {record.getMessage()}"
        error = traceback_of(self, record)
        if error:
            line += "\n" + error
        return line


def supports_colour(stream: TextIO) -> bool:
    """Colour only on a real terminal, never when NO_COLOR is set, and on Windows only once VT mode is on."""
    if os.environ.get("NO_COLOR") or not getattr(stream, "isatty", lambda: False)():
        return False
    if sys.platform != "win32":
        return os.environ.get("TERM") != "dumb"
    return enable_windows_vt()


def enable_windows_vt() -> bool:  # pragma: no cover - Windows only
    """Turn on ANSI colour in the Windows console (on by default in Windows Terminal, off in old conhost)."""
    try:
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        ok = False
        for std in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel.GetStdHandle(std)
            mode = ctypes.c_uint32()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                ok = bool(kernel.SetConsoleMode(handle, mode.value | 0x0004)) or ok  # VT processing
        return ok
    except Exception:  # noqa: BLE001 - no colour is fine
        return False


def setup(settings: Settings, ops: OpsSettings, redactor: Redactor | None = None, *,
          stream: TextIO | None = None) -> Redactor:
    """Replace root logging with the console and file handlers, all masking secrets. Returns the redactor."""
    redactor = redactor or Redactor()
    for secret in settings.secrets() + ops.secrets():
        redactor.add(secret)
    root = logging.getLogger()
    for handler in list(root.handlers):
        # Ours from an earlier setup, or a plain one from logging.basicConfig. Test capture handlers stay.
        if getattr(handler, TAG, False) or type(handler) is logging.StreamHandler:
            root.removeHandler(handler)
            if getattr(handler, TAG, False):
                handler.close()
    root.setLevel(settings.log_level)
    stream = stream or sys.stderr

    console = logging.StreamHandler(stream)
    console.setFormatter(JsonFormatter() if ops.console_format == "json" else ConsoleFormatter(supports_colour(stream)))
    handlers: list[logging.Handler] = [console]

    if ops.log_file is not None:
        try:
            ops.log_file.parent.mkdir(parents=True, exist_ok=True)
            file = logging.handlers.RotatingFileHandler(ops.log_file, maxBytes=ops.log_max_mb * 1024 * 1024,
                                                        backupCount=ops.log_keep, encoding="utf-8", delay=True)
            file.setFormatter(JsonFormatter() if ops.log_format == "json" else
                              logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            handlers.append(file)
        except OSError as exc:
            print(f"Portcullis can't write its log file {ops.log_file}: {exc}. Logging to the console only.",
                  file=stream)

    for handler in handlers:
        setattr(handler, TAG, True)
        handler.addFilter(RedactingFilter(redactor))
        root.addHandler(handler)
    if settings.log_level != "DEBUG":
        for name in NOISY:
            logging.getLogger(name).setLevel(logging.WARNING)

    risk = plain_http_risk(settings.amp_url)
    if risk:
        logging.getLogger("portcullis").warning(risk)
    return redactor


def close() -> None:
    """Flush the console and close the log file this module opened, so the file is complete on exit."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, TAG, False):
            handler.flush()
            if isinstance(handler, logging.FileHandler):
                root.removeHandler(handler)
                handler.close()
