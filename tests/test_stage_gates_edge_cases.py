import json
import os
import time


def _write_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def test_load_test_results_ignores_corrupt_json(tmp_path, mock_logger):
    from lattice.artifacts import ArtifactStore
    from lattice.stage_gates import GateEvaluator

    run_dir = str(tmp_path / "run")
    os.makedirs(os.path.join(run_dir, "artifacts"), exist_ok=True)
    _write_json(os.path.join(run_dir, "artifacts", "index.json"), {"artifacts": []})

    store = ArtifactStore(run_dir)
    res_dir = os.path.join(run_dir, "artifacts", "contracts", "results")
    os.makedirs(res_dir, exist_ok=True)
    with open(os.path.join(res_dir, "corrupt.json"), "w", encoding="utf-8") as f:
        f.write("{not json")

    ws = tmp_path / "ws"
    (ws / "contracts" / "tests").mkdir(parents=True)
    started = time.time()
    tests_path = ws / "contracts" / "tests" / "contract_tests.json"
    tests_path.write_text(json.dumps([{"id": "t1", "type": "schema", "spec_path": "openapi.yaml"}]), encoding="utf-8")
    os.utime(str(tests_path), (started + 1, started + 1))

    ev = GateEvaluator(run_dir, store, mock_logger, workspace_root=str(ws), run_started_at=started)
    ev.load_test_results()
    assert "corrupt" not in ev.latest_tests


def test_gate_parser_handles_unknown_atoms_as_false(tmp_path, mock_logger):
    from lattice.artifacts import ArtifactStore
    from lattice.stage_gates import GateEvaluator, StageGate

    run_dir = str(tmp_path / "run")
    os.makedirs(os.path.join(run_dir, "artifacts"), exist_ok=True)
    _write_json(os.path.join(run_dir, "artifacts", "index.json"), {"artifacts": []})
    store = ArtifactStore(run_dir)

    ev = GateEvaluator(run_dir, store, mock_logger, workspace_root=str(tmp_path))
    gates = [StageGate(id="g", name="g", conditions=["unknown.func() or tests.pass('x')"])]
    out = ev.evaluate(gates)
    assert out[0].status == "failed"
