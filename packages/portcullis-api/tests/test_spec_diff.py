# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import copy
import json
from pathlib import Path

from codegen import spec_diff

PIN = json.loads((Path(spec_diff.PIN_FILE)).read_text())
SPEC = json.loads((spec_diff.PIN_FILE.parent / PIN["file"]).read_text())


def test_same_spec_has_no_changes() -> None:
    assert spec_diff.diff(SPEC, SPEC).empty()


def test_removed_operation_and_field_are_breaking() -> None:
    new = copy.deepcopy(SPEC)
    del new["paths"]["/api/Shards/{id}/console"]["post"]
    del new["components"]["schemas"]["ConsoleLineDto"]["properties"]["seq"]
    report = spec_diff.diff(SPEC, new)
    assert "operation removed: POST /api/Shards/{id}/console" in report.breaking
    assert "ConsoleLineDto.seq: field removed" in report.breaking


def test_optional_additions_are_not_breaking() -> None:
    new = copy.deepcopy(SPEC)
    new["components"]["schemas"]["ConsoleLineDto"]["properties"]["colour"] = {"type": "string"}
    new["paths"]["/api/Shards/{id}/kill"] = {"post": {"responses": {"202": {"description": "ok"}}}}
    report = spec_diff.diff(SPEC, new)
    assert not report.breaking
    assert "ConsoleLineDto.colour: optional field added" in report.added
    assert "operation added: POST /api/Shards/{id}/kill" in report.added


def test_retyped_and_newly_required_are_breaking() -> None:
    new = copy.deepcopy(SPEC)
    schema = new["components"]["schemas"]["ConsoleInputBody"]
    schema["properties"]["promptReply"] = {"type": "string"}
    schema["required"] = ["command", "promptReply"]
    report = spec_diff.diff(SPEC, new)
    assert "ConsoleInputBody.promptReply: type boolean -> string" in report.breaking
    assert "ConsoleInputBody.promptReply: now required" in report.breaking


def test_path_case_changes_are_ignored() -> None:
    new = copy.deepcopy(SPEC)
    new["paths"]["/api/shards/{id}/console"] = new["paths"].pop("/api/Shards/{id}/console")
    assert spec_diff.diff(SPEC, new).empty()


def test_cli_exit_codes(tmp_path: Path) -> None:
    new = copy.deepcopy(SPEC)
    del new["components"]["schemas"]["HubTokenResponse"]
    path = tmp_path / "new.json"
    path.write_text(json.dumps(new))
    assert spec_diff.main([str(path)]) == 1
    same = tmp_path / "same.json"
    same.write_text(json.dumps(SPEC))
    assert spec_diff.main([str(same)]) == 0
