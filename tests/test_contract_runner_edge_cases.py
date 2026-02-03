import json
import os
import time

import pytest


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_scan_specs_skips_invalid_json(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    ws = tmp_path / "ws"
    (ws / "contracts" / "tests").mkdir(parents=True)
    _write(str(ws / "contracts" / "tests" / "bad.json"), "{not: json")

    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(ws), run_started_at=time.time())
    specs = runner.scan_specs()
    assert specs == []


def test_run_from_file_returns_empty_on_invalid_json(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    ws = tmp_path / "ws"
    ws.mkdir()
    bad = ws / "bad.json"
    bad.write_text("{not: json", encoding="utf-8")

    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(ws))
    assert runner.run_from_file(str(bad)) == []


@pytest.mark.parametrize(
    "spec, expect_status, expect_message_substr",
    [
        ({"id": "t1", "type": "schema", "spec_path": "missing.yaml"}, "failed", "spec file not found"),
        ({"id": "t2", "type": "unknown_type"}, "failed", "unknown test type"),
        ({"id": "t3"}, "failed", "unknown test type"),
    ],
)
def test_run_test_handles_missing_fields(tmp_path, tmp_run_dir, mock_logger, spec, expect_status, expect_message_substr):
    from lattice.contracts import ContractRunner

    ws = tmp_path / "ws"
    ws.mkdir()
    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(ws))
    res = runner.run_test(spec, allow_commands=True, command_validator=lambda _c: None)
    assert res.status == expect_status
    assert any(expect_message_substr in (e.get("message") or "") for e in (res.evidence or []))


def test_schema_validation_fails_when_paths_missing(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    ws = tmp_path / "ws"
    (ws / "contracts").mkdir(parents=True)
    _write(str(ws / "contracts" / "openapi.yaml"), "openapi: 3.0.0\ninfo:\n  title: x\n  version: 1\n")

    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(ws))
    res = runner.run_test({"id": "api_contract", "type": "schema", "spec_path": "contracts/openapi.yaml"})
    assert res.status == "failed"
    assert (res.metrics or {}).get("schema_valid") is False


def test_command_test_timeout_is_reported(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    ws = tmp_path / "ws"
    ws.mkdir()

    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(ws))
    res = runner.run_test(
        {
            "id": "cmd_timeout",
            "type": "command",
            "command": 'python -c "import time; time.sleep(2)"',
            "cwd": ".",
            "timeout_sec": 0.1,
            "reason": "ensure timeout path covered",
            "expected_exit_code": 0,
        },
        allow_commands=True,
        command_validator=lambda _c: None,
    )
    assert res.status == "failed"
    assert any("command execution error" in (e.get("message") or "") for e in (res.evidence or []))


def test_results_are_json_serializable(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    ws = tmp_path / "ws"
    ws.mkdir()
    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(ws))
    res = runner.run_test({"id": "t", "type": "unit", "assertions": [{"kind": "file_exists", "path": "README.md"}]})
    obj = json.loads(res.to_json())
    assert obj["id"] == "t"

