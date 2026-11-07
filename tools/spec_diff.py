#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compare two AMP 3 OpenAPI specs and say what changed for a client.

    python -m tools.spec_diff OLD.json NEW.json           # report, exit 1 if anything breaking changed
    python -m tools.spec_diff NEW.json                     # compare against the pinned spec
    python -m tools.spec_diff --markdown OLD NEW > diff.md # for a PR description

Run it on every new AMP preview build: copy ``data/versions/<version>/webapi/webapi.json`` off the
AMP host and diff it against ``portcullis/amp/spec/``. Breaking means something an existing client
call could trip over: an operation or schema removed, a field removed or retyped, a parameter or
field that became required, or a success response that changed shape. Additions are listed but
are not breaking.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PIN_FILE = ROOT / "portcullis" / "amp" / "spec" / "PIN.json"
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


@dataclass
class Report:
    breaking: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.breaking or self.changed or self.added)


def type_label(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "none"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for key in ("oneOf", "anyOf", "allOf"):
        if key in schema:
            return f"{key}(" + ", ".join(type_label(s) for s in schema[key]) + ")"
    kind = schema.get("type", "any")
    if isinstance(kind, list):
        kind = "|".join(kind)
    if kind == "array":
        return f"array<{type_label(schema.get('items'))}>"
    fmt = schema.get("format")
    return f"{kind}:{fmt}" if fmt else str(kind)


def operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    # Routes ignore case in AMP 3, so key on the lower-cased path.
    ops: dict[tuple[str, str], dict[str, Any]] = {}
    for path, item in spec.get("paths", {}).items():
        for method in HTTP_METHODS:
            if method in item:
                ops[(method.upper(), path.lower())] = {**item[method], "_path": path}
    return ops


def success_schema(op: dict[str, Any]) -> tuple[str | None, str]:
    for code in sorted(op.get("responses", {})):
        if code.startswith("2"):
            content = op["responses"][code].get("content") or {}
            for ctype in ("application/json", "text/json", "text/plain"):
                if ctype in content:
                    return code, type_label(content[ctype].get("schema"))
            return code, "none"
    return None, "none"


def body_schema(op: dict[str, Any]) -> tuple[bool, str]:
    rb = op.get("requestBody")
    if not rb:
        return False, "none"
    content = rb.get("content", {})
    for ctype in ("application/json", "application/x-www-form-urlencoded", "multipart/form-data"):
        if ctype in content:
            return bool(rb.get("required")), type_label(content[ctype].get("schema"))
    return bool(rb.get("required")), "other"


def compare_ops(old: dict[str, Any], new: dict[str, Any], report: Report) -> None:
    old_ops, new_ops = operations(old), operations(new)
    for key in sorted(old_ops.keys() - new_ops.keys()):
        report.breaking.append(f"operation removed: {key[0]} {old_ops[key]['_path']}")
    for key in sorted(new_ops.keys() - old_ops.keys()):
        report.added.append(f"operation added: {key[0]} {new_ops[key]['_path']}")
    for key in sorted(old_ops.keys() & new_ops.keys()):
        a, b = old_ops[key], new_ops[key]
        name = f"{key[0]} {b['_path']}"
        pa = {(p["in"], p["name"].lower()): p for p in a.get("parameters", [])}
        pb = {(p["in"], p["name"].lower()): p for p in b.get("parameters", [])}
        for pk in sorted(pa.keys() - pb.keys()):
            report.breaking.append(f"{name}: {pk[0]} parameter '{pa[pk]['name']}' removed")
        for pk in sorted(pb.keys() - pa.keys()):
            (report.breaking if pb[pk].get("required") else report.added).append(
                f"{name}: {'required' if pb[pk].get('required') else 'optional'} {pk[0]} parameter "
                f"'{pb[pk]['name']}' added")
        for pk in sorted(pa.keys() & pb.keys()):
            ta, tb = type_label(pa[pk].get("schema")), type_label(pb[pk].get("schema"))
            if ta != tb:
                report.breaking.append(f"{name}: parameter '{pb[pk]['name']}' type {ta} -> {tb}")
            if not pa[pk].get("required") and pb[pk].get("required"):
                report.breaking.append(f"{name}: parameter '{pb[pk]['name']}' is now required")
        ra, rb_ = body_schema(a), body_schema(b)
        if ra[1] != rb_[1]:
            report.breaking.append(f"{name}: request body {ra[1]} -> {rb_[1]}")
        elif not ra[0] and rb_[0]:
            report.breaking.append(f"{name}: request body is now required")
        sa, sb = success_schema(a), success_schema(b)
        if sa[1] != sb[1]:
            report.breaking.append(f"{name}: success response {sa[1]} -> {sb[1]}")
        elif sa[0] != sb[0]:
            report.changed.append(f"{name}: success status {sa[0]} -> {sb[0]}")
        perm_a = _permission(a.get("description"))
        perm_b = _permission(b.get("description"))
        if perm_a != perm_b:
            report.changed.append(f"{name}: permission node {perm_a or 'none'} -> {perm_b or 'none'}")


def _permission(description: str | None) -> str | None:
    import re
    match = re.search(r"Requires the `([^`]+)` capability", description or "")
    return match.group(1) if match else None


def compare_schemas(old: dict[str, Any], new: dict[str, Any], report: Report) -> None:
    sa = old.get("components", {}).get("schemas", {})
    sb = new.get("components", {}).get("schemas", {})
    for name in sorted(sa.keys() - sb.keys()):
        report.breaking.append(f"schema removed: {name}")
    for name in sorted(sb.keys() - sa.keys()):
        report.added.append(f"schema added: {name}")
    for name in sorted(sa.keys() & sb.keys()):
        a, b = sa[name], sb[name]
        if a.get("enum") != b.get("enum"):
            lost = [v for v in a.get("enum") or [] if v not in (b.get("enum") or [])]
            gained = [v for v in b.get("enum") or [] if v not in (a.get("enum") or [])]
            if lost:
                report.breaking.append(f"{name}: enum values removed {lost}")
            if gained:
                report.added.append(f"{name}: enum values added {gained}")
        pa, pb = a.get("properties", {}), b.get("properties", {})
        req_a, req_b = set(a.get("required", [])), set(b.get("required", []))
        for prop in sorted(pa.keys() - pb.keys()):
            report.breaking.append(f"{name}.{prop}: field removed")
        for prop in sorted(pb.keys() - pa.keys()):
            if prop in req_b:
                report.breaking.append(f"{name}.{prop}: required field added")
            else:
                report.added.append(f"{name}.{prop}: optional field added")
        for prop in sorted(pa.keys() & pb.keys()):
            ta, tb = type_label(pa[prop]), type_label(pb[prop])
            if ta != tb:
                report.breaking.append(f"{name}.{prop}: type {ta} -> {tb}")
            if prop not in req_a and prop in req_b:
                report.breaking.append(f"{name}.{prop}: now required")
            elif prop in req_a and prop not in req_b:
                report.changed.append(f"{name}.{prop}: now optional (may be missing in responses)")


def diff(old: dict[str, Any], new: dict[str, Any]) -> Report:
    report = Report()
    compare_ops(old, new, report)
    compare_schemas(old, new, report)
    return report


def render(report: Report, old_name: str, new_name: str, markdown: bool) -> str:
    lines = [f"# AMP spec diff: {old_name} -> {new_name}" if markdown else f"{old_name} -> {new_name}"]
    if report.empty():
        lines.append("No client-visible changes.")
        return "\n".join(lines)
    for title, items in (("Breaking", report.breaking), ("Changed", report.changed), ("Added", report.added)):
        if not items:
            continue
        lines.append("")
        lines.append(f"## {title} ({len(items)})" if markdown else f"{title} ({len(items)}):")
        lines += [f"- {i}" if markdown else f"  {i}" for i in items]
    return "\n".join(lines)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("specs", nargs="+", type=Path, help="OLD NEW, or just NEW to compare with the pinned spec")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    if len(args.specs) == 1:
        pin = json.loads(PIN_FILE.read_text(encoding="utf-8"))
        old_path, new_path = PIN_FILE.parent / pin["file"], args.specs[0]
    elif len(args.specs) == 2:
        old_path, new_path = args.specs
    else:
        parser.error("give one or two spec files")
    report = diff(load(old_path), load(new_path))
    print(render(report, old_path.name, new_path.name, args.markdown))
    return 1 if report.breaking else 0


if __name__ == "__main__":
    sys.exit(main())
