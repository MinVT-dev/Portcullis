# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stopping the bot cleanly on Ctrl+C, Ctrl+Break and SIGTERM, on Windows and Linux.

The first signal asks for a clean stop: ``StopSignals.wait()`` returns and ``__main__`` closes
Discord, the AMP event streams, the AMP session and the database, each step with its own time limit.
A second signal stops at once by cancelling the main task; the steps that are left are skipped,
except closing the AMP HTTP session and the database, which take no network time.

``signal.signal`` is used rather than ``loop.add_signal_handler`` because the latter does not exist
on Windows. The handler only calls ``loop.call_soon_threadsafe``, which also wakes a loop that is
waiting on I/O.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import types
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("portcullis")

Handler = Callable[[int, types.FrameType | None], Any] | int | None


def stop_signals() -> list[signal.Signals]:
    """SIGINT (Ctrl+C) everywhere, SIGTERM (service managers, ``docker stop``), SIGBREAK (Ctrl+Break, Windows)."""
    names = ("SIGINT", "SIGTERM", "SIGBREAK")
    return [getattr(signal, name) for name in names if hasattr(signal, name)]


class StopSignals:
    """Turns stop signals into an event the main task can wait on.

    Use as a context manager inside the running loop. Before ``serving()`` is called (still
    logging in, say) the first signal cancels the main task, since there is nothing to stop
    gracefully yet. Afterwards the first signal sets the event and the second one cancels.
    """

    def __init__(self) -> None:
        self.reason: str | None = None
        self.forced = False
        self._event = asyncio.Event()
        self._serving = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._previous: dict[signal.Signals, Handler] = {}

    def __enter__(self) -> StopSignals:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        for sig in stop_signals():
            try:
                self._previous[sig] = signal.signal(sig, self._on_signal)
            except (ValueError, OSError):  # not the main thread, or the platform refuses this one
                log.debug("cannot handle %s here", sig.name)
        return self

    def __exit__(self, *exc: object) -> None:
        for sig, previous in self._previous.items():
            try:
                signal.signal(sig, previous if previous is not None else signal.SIG_DFL)
            except (ValueError, OSError):
                pass
        self._previous.clear()

    def serving(self) -> None:
        """Startup is done: from now on the first signal asks for a clean stop."""
        self._serving = True

    def request(self, reason: str) -> None:
        """Ask for a clean stop (a signal, or a test). A second request forces it."""
        if self._event.is_set() or not self._serving:
            self.forced = True
            if self.reason is None:
                self.reason = reason
            if self._task is not None and not self._task.done():
                self._task.cancel()
            return
        self.reason = reason
        self._event.set()

    async def wait(self) -> str:
        await self._event.wait()
        return self.reason or "stop requested"

    def _on_signal(self, signum: int, _frame: types.FrameType | None) -> None:
        name = signal.Signals(signum).name
        reason = {"SIGINT": "Ctrl+C", "SIGBREAK": "Ctrl+Break", "SIGTERM": "SIGTERM"}.get(name, name)
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self.request, reason)


async def step(name: str, action: Callable[[], Awaitable[Any]], timeout: float, *,
               stopper: StopSignals | None = None, essential: bool = False) -> bool:
    """Run one shutdown step within ``timeout`` seconds. Never raises; returns whether it finished.

    After a forced stop only ``essential`` steps run, so a second Ctrl+C really is quick.
    """
    if stopper is not None and stopper.forced and not essential:
        log.debug("skipped %s: forced stop", name)
        return False
    try:
        await asyncio.wait_for(action(), timeout)
        return True
    except TimeoutError:
        log.warning("%s did not stop within %.0f s; going on without it", name, timeout)
    except asyncio.CancelledError:
        log.warning("%s was cut short by a forced stop", name)
    except Exception:  # noqa: BLE001 - shutting down: log it and keep going
        log.exception("could not stop %s cleanly", name)
    return False


async def cancel_leftovers(timeout: float = 3.0) -> int:
    """Cancel every task still running except the caller, and wait for them. Returns how many there were.

    asyncio.run would cancel them too, but it reports any that fail while cancelling as errors.
    """
    current = asyncio.current_task()
    tasks = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    if not tasks:
        return 0
    log.debug("cancelling %d leftover task(s): %s", len(tasks), ", ".join(sorted(t.get_name() for t in tasks)))
    for task in tasks:
        task.cancel()
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in done:
        if not task.cancelled() and task.exception() is not None:
            log.debug("task %s ended with %r while stopping", task.get_name(), task.exception())
    if pending:
        log.warning("%d task(s) ignored the stop: %s", len(pending), ", ".join(t.get_name() for t in pending))
    return len(tasks)
