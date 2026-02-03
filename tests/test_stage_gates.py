import os
import json
import time


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def test_artifact_exists_workspace_requires_recent_mtime(tmp_path, mock_logger):
    from lattice.artifacts import ArtifactStore
    from lattice.stage_gates import GateEvaluator

    run_dir = str(tmp_path / "run")
    os.makedirs(os.path.join(run_dir, "artifacts"), exist_ok=True)
    _write_json(os.path.join(run_dir, "artifacts", "index.json"), {"artifacts": []})
    store = ArtifactStore(run_dir)

    ws = tmp_path / "ws"
    (ws / "frontend").mkdir(parents=True)
    fpath = ws / "frontend" / "old.txt"
    fpath.write_text("x", encoding="utf-8")

    started = time.time()
    os.utime(str(fpath), (started - 10, started - 10))

    ev = GateEvaluator(run_dir, store, mock_logger, workspace_root=str(ws), run_started_at=started)
    assert ev._artifact_exists("frontend/**") is False

    os.utime(str(fpath), (started + 1, started + 1))
    assert ev._artifact_exists("frontend/**") is True


def test_tests_pass_aliases_and_active_filtering(tmp_path, mock_logger):
    from lattice.artifacts import ArtifactStore
    from lattice.stage_gates import GateEvaluator

    run_dir = str(tmp_path / "run")
    os.makedirs(os.path.join(run_dir, "artifacts"), exist_ok=True)
    _write_json(os.path.join(run_dir, "artifacts", "index.json"), {"artifacts": []})
    store = ArtifactStore(run_dir)

    ws = tmp_path / "ws"
    (ws / "contracts" / "tests").mkdir(parents=True)
    (ws / "contracts" / "results").mkdir(parents=True)

    started = time.time()
    specs = [
        {"id": "schema-001", "type": "schema", "spec_path": "openapi.yaml"},
        {"id": "command-001", "type": "command", "command": "pytest -q", "cwd": ".", "reason": "smoke", "expected_exit_code": 0},
    ]
    tests_path = ws / "contracts" / "tests" / "contract_tests.json"
    tests_path.write_text(json.dumps(specs, indent=2), encoding="utf-8")
    os.utime(str(tests_path), (started + 1, started + 1))

    res_dir = os.path.join(run_dir, "artifacts", "contracts", "results")
    _write_json(os.path.join(res_dir, "schema-001.json"), {"id": "schema-001", "status": "passed", "metrics": {"test_type": "schema"}, "evidence": []})
    _write_json(os.path.join(res_dir, "command-001.json"), {"id": "command-001", "status": "passed", "metrics": {"test_type": "command"}, "evidence": []})
    _write_json(os.path.join(res_dir, "api_consistency-001.json"), {"id": "api_consistency-001", "status": "failed", "metrics": {"test_type": "api_consistency"}, "evidence": []})

    ev = GateEvaluator(run_dir, store, mock_logger, workspace_root=str(ws), run_started_at=started)
    ev.load_test_results()

    assert "api_consistency-001" not in ev.latest_tests
    assert ev._tests_pass("api_contract") is True
    assert ev._tests_pass("smoke_suite") is True


def test_tests_pass_api_contract_requires_all_contract_tests_pass(tmp_path, mock_logger):
    from lattice.artifacts import ArtifactStore
    from lattice.stage_gates import GateEvaluator

    run_dir = str(tmp_path / "run")
    os.makedirs(os.path.join(run_dir, "artifacts"), exist_ok=True)
    _write_json(os.path.join(run_dir, "artifacts", "index.json"), {"artifacts": []})
    store = ArtifactStore(run_dir)

    ws = tmp_path / "ws"
    (ws / "contracts" / "tests").mkdir(parents=True)
    started = time.time()

    specs = [
        {"id": "schema-001", "type": "schema", "spec_path": "openapi.yaml"},
        {"id": "api-001", "type": "api_consistency", "spec_path": "openapi.yaml"},
    ]
    tests_path = ws / "contracts" / "tests" / "contract_tests.json"
    tests_path.write_text(json.dumps(specs, indent=2), encoding="utf-8")
    os.utime(str(tests_path), (started + 1, started + 1))

    res_dir = os.path.join(run_dir, "artifacts", "contracts", "results")
    _write_json(os.path.join(res_dir, "schema-001.json"), {"id": "schema-001", "status": "passed", "metrics": {"test_type": "schema"}, "evidence": []})
    _write_json(os.path.join(res_dir, "api-001.json"), {"id": "api-001", "status": "failed", "metrics": {"test_type": "api_consistency"}, "evidence": []})

    ev = GateEvaluator(run_dir, store, mock_logger, workspace_root=str(ws), run_started_at=started)
    ev.load_test_results()
    assert ev._tests_pass("api_contract") is False

    _write_json(os.path.join(res_dir, "api-001.json"), {"id": "api-001", "status": "passed", "metrics": {"test_type": "api_consistency"}, "evidence": []})
    ev.load_test_results()
    assert ev._tests_pass("api_contract") is True


def test_tests_pass_allows_smoke_suite_result_even_if_not_in_specs(tmp_path, mock_logger):
    from lattice.artifacts import ArtifactStore
    from lattice.stage_gates import GateEvaluator

    run_dir = str(tmp_path / "run")
    os.makedirs(os.path.join(run_dir, "artifacts"), exist_ok=True)
    _write_json(os.path.join(run_dir, "artifacts", "index.json"), {"artifacts": []})
    store = ArtifactStore(run_dir)

    ws = tmp_path / "ws"
    (ws / "contracts" / "tests").mkdir(parents=True)
    started = time.time()

    specs = [{"id": "schema-001", "type": "schema", "spec_path": "openapi.yaml"}]
    tests_path = ws / "contracts" / "tests" / "contract_tests.json"
    tests_path.write_text(json.dumps(specs, indent=2), encoding="utf-8")
    os.utime(str(tests_path), (started + 1, started + 1))

    res_dir = os.path.join(run_dir, "artifacts", "contracts", "results")
    _write_json(os.path.join(res_dir, "schema-001.json"), {"id": "schema-001", "status": "passed", "metrics": {"test_type": "schema"}, "evidence": []})
    _write_json(os.path.join(res_dir, "smoke_suite.json"), {"id": "smoke_suite", "status": "passed", "metrics": {"test_type": "command"}, "evidence": []})

    ev = GateEvaluator(run_dir, store, mock_logger, workspace_root=str(ws), run_started_at=started)
    ev.load_test_results()
    assert ev.latest_tests.get("smoke_suite") == "passed"
    assert ev._tests_pass("smoke_suite") is True
