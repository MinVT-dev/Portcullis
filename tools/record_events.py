#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record every SignalR event AMP 3 sends for one or more shards, with secrets removed.

Runs on your own PC against your own AMP 3 panel. Nothing is sent anywhere else.

    python -m tools.record_events                       # all shards the bot can see
    python -m tools.record_events --shard <shard-id>    # one shard (repeat for more)
    python -m tools.record_events --duration 600        # stop after 10 minutes (default: Ctrl+C)

Settings come from environment variables or a ``.env`` file in the repo root (see .env.example):
AMP_URL, AMP_USERNAME and AMP_PASSWORD. If AMP_PASSWORD is empty you are asked for it, and it is
never echoed or written to disk.

While it runs, join the game, chat, run a console command and leave, so every event type shows up.
Output goes to ``captures/<time>/`` (git ignores this folder):

* ``events.jsonl``: one line per event: time, hub, event name, arguments
* ``lifecycle.jsonl``: connects, drops, reconnects, which connection mode worked, tickets used
* ``snapshot.json``: the REST view at start-up (shards, roster, state, recent console, permissions)
* ``summary.txt``: counts per event type

Tokens, hub tickets, the password and connection ids are always removed. By default the panel
address and any IPv4 address are also replaced, because player IPs may appear in join events; use
``--keep-host`` / ``--keep-ips`` only for captures you will not share.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portcullis.amp import AmpClient, Credentials  # noqa: E402
from portcullis.amp.errors import AmpError, AuthFailed, LoginNotSupported  # noqa: E402
from portcullis.amp.events import state_from  # noqa: E402
from portcullis.amp.redact import RedactingFilter, Redactor  # noqa: E402
from portcullis.amp.signalr import MODES, HubConnection, HubError, HubEvent, HubGaveUp  # noqa: E402
from portcullis.config import load_env_file  # noqa: E402

log = logging.getLogger("recorder")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class CaptureRedactor(Redactor):
    def __init__(self, *, scrub_ips: bool) -> None:
        super().__init__()
        self.scrub_ips = scrub_ips

    def text(self, value: str) -> str:
        value = super().text(value)
        if self.scrub_ips:
            value = IPV4.sub("<ip>", value)
        return value


class Recorder:
    def __init__(self, out_dir: Path, redactor: Redactor, echo: bool) -> None:
        self.out_dir = out_dir
        self.redactor = redactor
        self.echo = echo
        self.counts: Counter[str] = Counter()
        out_dir.mkdir(parents=True, exist_ok=True)
        self._events = (out_dir / "events.jsonl").open("a", encoding="utf-8")
        self._lifecycle = (out_dir / "lifecycle.jsonl").open("a", encoding="utf-8")

    @staticmethod
    def _line(fh: Any, record: dict[str, Any]) -> None:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        fh.flush()

    async def on_event(self, event: HubEvent) -> None:
        self.counts[f"{event.hub}:{event.target}"] += 1
        record = {
            "t": event.received_at.isoformat(),
            "hub": event.hub,
            "target": event.target,
            "arguments": self.redactor.data(event.arguments),
        }
        self._line(self._events, record)
        if self.echo:
            preview = json.dumps(record["arguments"], ensure_ascii=False, default=str)
            print(f"{record['t'][11:19]} {event.hub}:{event.target} {preview[:160]}")

    def on_lifecycle(self, hub: str, kind: str, detail: dict[str, Any]) -> None:
        record = {"t": datetime.now(UTC).isoformat(), "hub": hub, "event": kind, **self.redactor.data(detail)}
        self._line(self._lifecycle, record)
        print(f"-- {hub}: {kind} {json.dumps(self.redactor.data(detail), default=str)}")

    def write_json(self, name: str, data: Any) -> None:
        (self.out_dir / name).write_text(
            json.dumps(self.redactor.data(data), indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )

    def write_text(self, name: str, text: str) -> None:
        (self.out_dir / name).write_text(self.redactor.text(text), encoding="utf-8")

    def close(self, extra: dict[str, Any]) -> None:
        lines = [f"{name}: {count}" for name, count in sorted(self.counts.items())] or ["no events received"]
        lines += [""] + [f"{k}: {v}" for k, v in extra.items()]
        (self.out_dir / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._events.close()
        self._lifecycle.close()


async def snapshot(amp: AmpClient, shard_ids: list[str]) -> dict[str, Any]:
    """REST twins of the events, so payloads can be compared with what the REST API says."""

    async def safe(coro: Any) -> Any:
        try:
            result = await coro
        except AmpError as exc:
            return {"error": str(exc)}
        if isinstance(result, list):
            return [r.model_dump(mode="json", by_alias=True) if hasattr(r, "model_dump") else r for r in result]
        return result.model_dump(mode="json", by_alias=True) if hasattr(result, "model_dump") else result

    t = amp.transport
    data: dict[str, Any] = {
        "health": await safe(t.request("GET", "/api/Health/status")),
        "permissions": await safe(t.request("GET", "/api/me/permissions")),
        "shards": {},
    }
    for sid in shard_ids:
        p = {"id": sid}
        data["shards"][sid] = {
            "state": await safe(t.request("GET", "/api/Shards/{id}/state", path_params=p)),
            "users": await safe(t.request("GET", "/api/Shards/{id}/users", path_params=p)),
            "console_history": await safe(
                t.request("GET", "/api/Shards/{id}/console/history", path_params=p, params={"limit": 50})
            ),
        }
    return data


def shard_state_label(snap: dict[str, Any], sid: str) -> str:
    state = (snap.get("shards", {}).get(sid) or {}).get("state")
    if not isinstance(state, dict) or "error" in state:
        return "state unknown"
    return state_from(state)[0].label.lower()


PANEL_SCRIPT = re.compile(r"""(?:src|href)=["']([^"']+\.js)["']""")
CHUNK_REF = re.compile(r"""["'`]((?:\.{0,2}/)?(?:[\w.-]+/)*[\w.-]+\.js)["'`]""")


async def inspect_panel(amp: AmpClient, recorder: Recorder, limit: int = 300) -> int:
    """Find how the panel's own JavaScript calls the shard hub, and save those snippets.

    Only reads the panel's public script files. Returns how many snippets were found.
    """
    from urllib.parse import urljoin

    base = amp.base_url + "/"
    seen: set[str] = set()
    queue: list[str] = []
    snippets: list[str] = []
    try:
        async with amp.session.get(base) as resp:
            html = await resp.text()
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        recorder.on_lifecycle("panel", "inspect-failed", {"error": str(exc)})
        return 0
    queue += [urljoin(base, m) for m in PANEL_SCRIPT.findall(html)]
    while queue and len(seen) < limit:
        url = queue.pop(0)
        if url in seen or not url.startswith(amp.base_url):
            continue
        seen.add(url)
        try:
            async with amp.session.get(url) as resp:
                if resp.status != 200:
                    continue
                js = await resp.text()
        except Exception:  # noqa: BLE001 - diagnostics only
            continue
        queue += [urljoin(url, m) for m in CHUNK_REF.findall(js)]
        for target in ("JoinShard", "LeaveShard"):
            start = 0
            while (i := js.find(target, start)) != -1:
                snippets.append(f"--- {target} in {url.rsplit('/', 1)[-1]} at {i}\n{js[max(0, i - 400):i + 300]}\n")
                start = i + len(target)
    recorder.write_text("panel_hub_calls.txt", "\n".join(snippets) or f"No JoinShard found in {len(seen)} scripts.\n")
    recorder.on_lifecycle("panel", "inspected", {"scripts": len(seen), "snippets": len(snippets)})
    return len(snippets)


async def pick_mode(amp: AmpClient, wanted: str, recorder: Recorder) -> str:
    candidates = MODES if wanted == "auto" else (wanted,)
    for mode in candidates:
        conn = amp.hub("shard", mode=mode)  # type: ignore[arg-type]
        try:
            await conn.probe()
        except AmpError as exc:
            recorder.on_lifecycle("shard", "probe-failed", {"mode": mode, "error": str(exc)})
            continue
        recorder.on_lifecycle("shard", "probe-ok", {"mode": mode, "tickets_used": conn.tickets_used})
        return mode
    raise SystemExit("No connection mode worked. See lifecycle.jsonl in the capture folder.")


async def run(args: argparse.Namespace) -> int:
    load_env_file(args.env_file)
    url = (args.url or os.environ.get("AMP_URL") or "").rstrip("/")
    username = args.username or os.environ.get("AMP_USERNAME") or ""
    if not url or not username:
        print("Set AMP_URL and AMP_USERNAME in .env (see .env.example) or pass --url/--username.")
        return 2
    password = os.environ.get("AMP_PASSWORD") or getpass.getpass(f"AMP password for {username}: ")

    redactor = CaptureRedactor(scrub_ips=not args.keep_ips)
    redactor.add(password)
    if not args.keep_host:
        host = urlsplit(url).netloc
        redactor.add(host)
        redactor.add(urlsplit(url).hostname)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactingFilter(redactor))

    out_dir = args.out / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    recorder = Recorder(out_dir, redactor, echo=not args.quiet)
    print(f"Writing to {out_dir}")

    connections: list[HubConnection] = []
    exit_code = 0
    amp = AmpClient(url, Credentials(username, password), redactor=redactor)
    extra: dict[str, Any] = {}
    try:
        try:
            await amp.start()
        except (AuthFailed, LoginNotSupported) as exc:
            print(f"Login failed: {exc}")
            return 1
        shards = await amp.shards.get_shards()
        names = {str(s.id): s.name for s in shards if s.id}
        # The panel calls JoinShard(scope, shardId); scope is the tenant id or the spire id.
        scopes = {str(s.id): [(k, v) for k, v in (("tenant", s.tenant_id),
                                                  ("spire", str(s.spire_id) if s.spire_id else None)) if v]
                  for s in shards if s.id}
        shard_ids = [s.lower() for s in args.shard] or list(names)
        unknown = [s for s in shard_ids if s not in names]
        if unknown:
            print(f"The bot account can't see shard {', '.join(unknown)}. Check the id and the bot's role.")
            return 1
        if not shard_ids:
            print("The bot account can't see any shards. Check its role in the panel.")
            return 1
        snap = await snapshot(amp, shard_ids)
        recorder.write_json("snapshot.json", snap)
        print("Shards:")
        for sid in shard_ids:
            print(f"  {names[sid]} ({sid}): {shard_state_label(snap, sid)}")

        if not args.no_inspect:
            found = await inspect_panel(amp, recorder)
            print(f"Panel scripts: {found} JoinShard/LeaveShard call(s) saved to panel_hub_calls.txt")

        mode = await pick_mode(amp, args.mode, recorder)
        extra["mode"] = mode
        working: list[str] = []  # the scope kind that worked, once one has

        async def join(conn: HubConnection) -> None:
            failed: dict[str, str] = {}
            for sid in shard_ids:
                candidates = sorted(scopes[sid], key=lambda c: c[0] not in working)
                errors: list[str] = []
                for kind, scope in candidates:
                    call = [scope, sid]
                    recorder.on_lifecycle("shard", "join", {"shard": sid, "name": names[sid], "scope": kind,
                                                            "arguments": call})
                    try:
                        result = await conn.invoke("JoinShard", *call)
                    except HubError as exc:
                        errors.append(f"{kind}: {exc}")
                        recorder.on_lifecycle("shard", "join-failed", {
                            "shard": sid, "name": names[sid], "scope": kind, "state": shard_state_label(snap, sid),
                            "arguments": call, "error": str(exc)})
                        continue
                    working[:] = [kind]
                    extra["join_scope"] = kind
                    recorder.on_lifecycle("shard", "joined", {"shard": sid, "scope": kind, "result": result})
                    break
                else:
                    failed[sid] = "; ".join(errors) or "no tenant or spire id"
            if len(failed) == len(shard_ids):
                raise HubError("no shard could be joined: " + "; ".join(
                    f"{names[s]}: {e}" for s, e in failed.items()))

        hubs = ["shard", *args.extra_hub]
        tasks = []
        for name in hubs:
            conn = amp.hub(name, mode=mode, on_connected=join if name == "shard" else None,  # type: ignore[arg-type]
                           join_attempts=args.join_attempts,
                           on_lifecycle=lambda kind, d, n=name: recorder.on_lifecycle(n, kind, d))
            connections.append(conn)
            tasks.append(asyncio.create_task(conn.run(recorder.on_event), name=f"hub-{name}"))
        print("Recording. Join the game, chat, run a console command and leave. Ctrl+C to stop.")
        done, _ = await asyncio.wait(tasks, timeout=args.duration, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            exc = task.exception()
            if isinstance(exc, HubGaveUp):
                print(f"Stopped: {exc}")
                print("The hub connection works, but AMP refused JoinShard every time. "
                      "lifecycle.jsonl has the exact calls and errors, and panel_hub_calls.txt shows how "
                      "the panel itself calls it. Send those two files.")
                exit_code = 3
            elif exc:
                print(f"Hub task stopped: {exc}")
                exit_code = 1
    finally:
        for conn in connections:
            await conn.close()
        extra.update({
            "connects": {c.hub: c.connects for c in connections},
            "tickets_used": {c.hub: c.tickets_used for c in connections},
            "logins": amp.auth.logins,
            "renewals": amp.auth.renewals,
        })
        recorder.close(extra)
        with contextlib.suppress(Exception):
            await amp.close()
        print(f"Saved. Summary in {out_dir / 'summary.txt'}")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="panel URL, e.g. http://192.168.1.10:8443 (default: AMP_URL)")
    parser.add_argument("--username", help="bot account (default: AMP_USERNAME)")
    parser.add_argument("--shard", action="append", default=[], help="shard id to join (repeatable; default all)")
    parser.add_argument("--extra-hub", action="append", default=[], choices=["tasks", "spire"],
                        help="also record this hub (no join call is made)")
    parser.add_argument("--mode", default="auto", choices=["auto", *MODES],
                        help="how to spend hub tickets; auto tries each and keeps the first that works")
    parser.add_argument("--duration", type=float, default=None, help="seconds to record (default: until Ctrl+C)")
    parser.add_argument("--out", type=Path, default=ROOT / "captures")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--keep-host", action="store_true", help="don't replace the panel address")
    parser.add_argument("--keep-ips", action="store_true", help="don't replace IPv4 addresses")
    parser.add_argument("--join-attempts", type=int, default=3,
                        help="give up after this many identical JoinShard failures (default 3)")
    parser.add_argument("--no-inspect", action="store_true",
                        help="don't look for JoinShard calls in the panel's own scripts")
    parser.add_argument("--quiet", action="store_true", help="don't print events as they arrive")
    parser.add_argument("--verbose", action="store_true", help="debug logging (still redacted)")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
