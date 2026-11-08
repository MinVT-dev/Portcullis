#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate the typed AMP 3 REST client from the pinned OpenAPI spec.

    python -m codegen.generate_client          # regenerate src/portcullis_api/models.py and src/portcullis_api/api/
    python -m codegen.generate_client --check  # exit 1 if the committed code is out of date (used in CI)

Inputs: src/portcullis_api/spec/PIN.json names the spec file and its sha256. Outputs:

* ``src/portcullis_api/models.py``: pydantic v2 models for every schema (datamodel-code-generator).
* ``src/portcullis_api/api/<tag>.py``: one class per OpenAPI tag with one async method per operation.
  The spec has no operationIds, so names come from the method and path:
  ``GET /api/Shards/{id}/console/history`` -> ``get_shards_console_history(id, ...)``, and a path that
  ends in a parameter gets ``_by_<param>``.

To move to a new AMP build: drop its webapi.json into src/portcullis_api/spec/, run
``python -m codegen.spec_diff OLD NEW`` to review, update PIN.json, then rerun this script.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import keyword
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = ROOT / "src" / "portcullis_api" / "spec"
PIN_FILE = SPEC_DIR / "PIN.json"
AMP_PKG = ROOT / "src" / "portcullis_api"

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

DATAMODEL_ARGS = [
    "--input-file-type", "openapi",
    "--output-model-type", "pydantic_v2.BaseModel",
    "--target-python-version", "3.11",
    "--use-annotated",
    "--field-constraints",
    "--use-standard-collections",
    "--use-union-operator",
    "--snake-case-field",
    "--allow-population-by-field-name",
    "--disable-timestamp",
    "--use-schema-description",
    "--use-field-description",
    "--enum-field-as-literal", "one",
    "--formatters", "black", "isort",
]


def load_pin() -> tuple[Path, dict[str, Any]]:
    pin = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    spec_path = SPEC_DIR / pin["file"]
    digest = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    if digest != pin["sha256"]:
        sys.exit(f"{spec_path.name}: sha256 {digest} does not match PIN.json ({pin['sha256']}). "
                 "If you replaced the spec on purpose, review it with codegen.spec_diff and update PIN.json.")
    return spec_path, pin


# --------------------------------------------------------------------------- naming

def snake(text: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "_", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    return re.sub(r"_+", "_", text).strip("_").lower()


def pascal(text: str) -> str:
    return "".join(part.capitalize() for part in snake(text).split("_"))


def safe_ident(name: str) -> str:
    name = snake(name) or "value"
    if name[0].isdigit():
        name = "_" + name
    if keyword.iskeyword(name) or name in {"self", "body", "form", "timeout", "import"}:
        name += "_"
    return name


def operation_name(method: str, path: str) -> str:
    segments = [s for s in path.split("/") if s][1:]  # drop "api"
    words = [snake(s) for s in segments if not s.startswith("{")]
    name = "_".join([method] + [w for w in words if w])
    if segments and segments[-1].startswith("{"):
        name += "_by_" + snake(segments[-1].strip("{}"))
    return name


# --------------------------------------------------------------------------- types

def schema_type(schema: dict[str, Any] | None, model_names: set[str]) -> str:
    if not schema:
        return "Any"
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return f"m.{name}" if name in model_names else "Any"
    for key in ("oneOf", "anyOf"):
        if key in schema:
            parts = sorted({schema_type(s, model_names) for s in schema[key]})
            return "Any" if "Any" in parts else " | ".join(parts)
    kinds = schema.get("type")
    if isinstance(kinds, list):
        parts = [schema_type({**schema, "type": k}, model_names) for k in kinds if k != "null"]
        joined = " | ".join(dict.fromkeys(parts))
        return f"{joined} | None" if "null" in kinds else joined
    fmt = schema.get("format")
    if kinds == "string":
        if fmt == "uuid":
            return "UUID | str"
        if fmt == "binary":
            return "bytes"
        return "str"
    if kinds == "integer":
        return "int"
    if kinds == "number":
        return "float"
    if kinds == "boolean":
        return "bool"
    if kinds == "array":
        return f"list[{schema_type(schema.get('items'), model_names)}]"
    if kinds == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"dict[str, {schema_type(extra, model_names)}]"
        return "dict[str, Any]"
    return "Any"


def response_schema(op: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    for code in sorted(op.get("responses", {})):
        if code.startswith("2"):
            content = op["responses"][code].get("content") or {}
            for ctype in ("application/json", "text/json", "text/plain"):
                if ctype in content:
                    return code, content[ctype].get("schema")
            return code, None
    return None, None


# --------------------------------------------------------------------------- emit

def doc_lines(op: dict[str, Any], method: str, path: str) -> list[str]:
    summary = (op.get("summary") or "").strip().replace('"""', "'''")
    lines = [summary or f"{method.upper()} {path}", "", f"``{method.upper()} {path}``"]
    perm = re.search(r"Requires the `([^`]+)` capability", op.get("description") or "")
    if perm:
        lines.append(f"Needs the ``{perm.group(1)}`` permission node.")
    return lines


def emit_operation(method: str, path: str, op: dict[str, Any], name: str, model_names: set[str]) -> list[str]:
    params = op.get("parameters", [])
    path_params = [p for p in params if p.get("in") == "path"]
    query_params = [p for p in params if p.get("in") == "query"]
    # Keep path order for positional args.
    order = re.findall(r"{([^}]+)}", path)
    path_params.sort(key=lambda p: order.index(p["name"]) if p["name"] in order else 99)

    sig: list[str] = ["self"]
    call_path: list[str] = []
    for p in path_params:
        ident = safe_ident(p["name"])
        sig.append(f"{ident}: {schema_type(p.get('schema'), model_names)}")
        call_path.append(f"{p['name']!r}: {ident}")

    body_arg = None
    form_arg = False
    rb = op.get("requestBody")
    if rb:
        content = rb.get("content", {})
        if "application/json" in content:
            btype = schema_type(content["application/json"].get("schema"), model_names)
            btype = f"{btype} | dict[str, Any]" if btype.startswith("m.") else btype
            if rb.get("required"):
                sig.append(f"body: {btype}")
            else:
                body_arg = f"body: {btype} | None = None"
        elif content:
            form_arg = True

    kw: list[str] = []
    if body_arg:
        kw.append(body_arg)
    if form_arg:
        kw.append("form: dict[str, str]" if rb and rb.get("required") else "form: dict[str, str] | None = None")
    call_query: list[str] = []
    for p in query_params:
        ident = safe_ident(p["name"])
        ptype = schema_type(p.get("schema"), model_names)
        if p.get("required"):
            kw.append(f"{ident}: {ptype}")
        else:
            kw.append(f"{ident}: {ptype} | None = None")
        call_query.append(f"{p['name']!r}: {ident}")
    kw.append("timeout: float | None = None")
    if kw:
        sig.append("*")
        sig.extend(kw)

    _, rschema = response_schema(op)
    rtype = schema_type(rschema, model_names) if rschema else "None"

    lines = [f"    async def {name}("]
    lines += [f"        {s}," for s in sig]
    lines.append(f"    ) -> {rtype}:")
    doc = doc_lines(op, method, path)
    lines.append(f'        """{doc[0]}')
    lines += [f"        {d}".rstrip() for d in doc[1:]]
    lines.append('        """')
    args = [f"{method.upper()!r}", f"{path!r}"]
    if call_path:
        args.append("path_params={" + ", ".join(call_path) + "}")
    if call_query:
        args.append("params={" + ", ".join(call_query) + "}")
    if rb and not form_arg:
        args.append("body=body")
    if form_arg:
        args.append("form=form")
    if rschema:
        args.append(f"response_type={rtype}")
    args.append("timeout=timeout")
    lines.append("        return await self._t.request(")
    lines += [f"            {a}," for a in args]
    lines.append("        )")
    return lines


def generate_api(spec: dict[str, Any], pin: dict[str, Any], out_dir: Path, model_names: set[str]) -> list[str]:
    groups: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, item in spec["paths"].items():
        for method in HTTP_METHODS:
            if method in item:
                op = item[method]
                tag = (op.get("tags") or ["Misc"])[0]
                groups.setdefault(tag, []).append((method, path, op))

    header = [
        "# SPDX-License-Identifier: AGPL-3.0-or-later",
        f"# Generated by codegen/generate_client.py from {pin['file']} (sha256 {pin['sha256'][:16]}).",
        "# Do not edit by hand: change the generator or the spec, then regenerate.",
        "# ruff: noqa",
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "from uuid import UUID",
        "",
        "from portcullis_api import models as m",
        "from portcullis_api.api._base import ApiGroup",
        "",
    ]
    warnings: list[str] = []
    index: list[tuple[str, str, str]] = []
    for tag in sorted(groups):
        module = safe_ident(tag)
        cls = pascal(tag) + "Api"
        index.append((module, cls, tag))
        lines = list(header)
        lines += ["", f"class {cls}(ApiGroup):", f'    """Operations tagged {tag!r} in the AMP 3 spec."""']
        used: set[str] = set()
        for method, path, op in sorted(groups[tag], key=lambda x: (x[1].lower(), HTTP_METHODS.index(x[0]))):
            name = operation_name(method, path)
            if name in used:
                n = 2
                while f"{name}_{n}" in used:
                    n += 1
                warnings.append(f"{tag}: {method.upper()} {path} renamed {name} -> {name}_{n}")
                name = f"{name}_{n}"
            used.add(name)
            lines.append("")
            lines += emit_operation(method, path, op, name, model_names)
        (out_dir / f"{module}.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

    init = [header[0], header[1], header[2], "", "from __future__ import annotations", ""]
    init += [f"from portcullis_api.api.{module} import {cls}" for module, cls, _ in index]
    init += ["", "# attribute name on AmpClient -> class", "GROUPS = {"]
    init += [f"    {module!r}: {cls}," for module, cls, _ in index]
    init += ["}", "", "__all__ = [\"GROUPS\", " + ", ".join(repr(cls) for _, cls, _ in index) + "]"]
    (out_dir / "__init__.py").write_text("\n".join(init) + "\n", encoding="utf-8")
    return warnings


def generate(target: Path) -> list[str]:
    """Write models.py and api/ under ``target`` (a copy of src/portcullis_api)."""
    spec_path, pin = load_pin()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    models = target / "models.py"
    cmd = [sys.executable, "-m", "datamodel_code_generator", "--input", str(spec_path),
           "--output", str(models), *DATAMODEL_ARGS]
    subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)
    text = models.read_text(encoding="utf-8")
    text = text.replace(f"#   filename:  {spec_path.name}",
                        f"#   filename:  {spec_path.name} (sha256 {pin['sha256'][:16]})\n# ruff: noqa", 1)
    models.write_text("# SPDX-License-Identifier: AGPL-3.0-or-later\n" + text, encoding="utf-8")
    model_names = set(re.findall(r"^class (\w+)\(", text, flags=re.M))
    api_dir = target / "api"
    api_dir.mkdir(exist_ok=True)
    for old in api_dir.glob("*.py"):
        if old.name != "_base.py":
            old.unlink()
    return generate_api(spec, pin, api_dir, model_names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if committed code differs from a fresh run")
    args = parser.parse_args()
    if not args.check:
        for w in generate(AMP_PKG):
            print("note:", w)
        print("Regenerated src/portcullis_api/models.py and src/portcullis_api/api/")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)
        (target / "api").mkdir()
        shutil.copy(AMP_PKG / "api" / "_base.py", target / "api" / "_base.py")
        generate(target)
        stale = [str(p.relative_to(target)) for p in sorted(target.rglob("*.py"))
                 if not (AMP_PKG / p.relative_to(target)).exists()
                 or not filecmp.cmp(p, AMP_PKG / p.relative_to(target), shallow=False)]
        extra = [str(p.relative_to(AMP_PKG)) for p in (AMP_PKG / "api").glob("*.py")
                 if not (target / p.relative_to(AMP_PKG)).exists()]
    if stale or extra:
        print("Generated client is out of date. Run: python -m codegen.generate_client")
        for name in stale + extra:
            print("  ", name)
        return 1
    print("Generated client matches the pinned spec.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
