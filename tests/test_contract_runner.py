import json
import os
import time


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_unit_assertions_accept_dict(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(str(workspace / "backend" / "README.md"), "ok")

    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(workspace))
    res = runner.run_test(
        spec={
            "id": "unit_backend_readme_exists",
            "type": "unit",
            "assertions": {"kind": "file_exists", "path": "backend/README.md"},
        }
    )
    assert res.status == "passed"
    assert (res.metrics or {}).get("assertions_ok") == 1
    assert (res.metrics or {}).get("assertions_total") == 1
    assert res.evidence == []


def test_unit_assertions_missing_file_has_evidence(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    workspace = tmp_path / "ws"
    workspace.mkdir()
    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(workspace))
    res = runner.run_test(
        spec={
            "id": "unit_missing",
            "type": "unit",
            "assertions": {"kind": "file_exists", "path": "backend/README.md"},
        }
    )
    assert res.status == "failed"
    assert res.evidence and res.evidence[0].get("message") == "file not found"


def test_api_consistency_can_compare_openapi_files(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(str(workspace / "api" / "openapi.yaml"), "openapi: 3.0.0\npaths: {}\n")
    _write(str(workspace / "contracts" / "openapi.yaml"), "openapi: 3.0.0\npaths: {}\n")

    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(workspace))
    res = runner.run_test(
        spec={
            "id": "api_consistency_contracts_match",
            "type": "api_consistency",
            "canonical": "api/openapi.yaml",
            "other_paths": ["contracts/openapi.yaml"],
            "match_required": True,
        }
    )
    assert res.status == "passed"
    assert (res.metrics or {}).get("test_type") == "openapi_match"


def test_api_consistency_backend_scan_regex_is_valid(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(
        str(workspace / "backend" / "app.py"),
        "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health():\n    return {'ok': True}\n",
    )
    spec = {
        "openapi": "3.0.0",
        "paths": {"/health": {"get": {"responses": {"200": {"description": "ok"}}}}},
        "components": {"schemas": {"Health": {"type": "object"}}},
    }
    _write(str(workspace / "api" / "openapi.json"), json.dumps(spec))

    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(workspace))
    res = runner.run_test(
        spec={
            "id": "api_consistency_backend",
            "type": "api_consistency",
            "spec_path": "api/openapi.json",
        }
    )
    assert res.status == "passed"


def test_schema_test_accepts_path_alias(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(str(workspace / "api" / "openapi.yaml"), "openapi: 3.0.0\npaths: {}\n")
    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(workspace))
    res = runner.run_test(spec={"id": "schema_openapi_exists", "type": "schema", "path": "api/openapi.yaml"})
    assert res.status == "passed"


def test_scan_and_run_auto_schema_check_when_no_manifests(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(str(workspace / "openapi.yaml"), "openapi: 3.0.0\npaths: {}\n")
    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(workspace), run_started_at=time.time())

    results = runner.scan_and_run()
    assert any(r.id == "api_contract" for r in results)
    api = [r for r in results if r.id == "api_contract"][0]
    assert api.status == "passed"


def test_schema_validation_supports_json_openapi(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    workspace = tmp_path / "ws"
    workspace.mkdir()
    spec = {"openapi": "3.0.0", "paths": {}, "components": {}}
    _write(str(workspace / "openapi.json"), json.dumps(spec))
    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(workspace))
    res = runner.run_test(spec={"id": "schema_json", "type": "schema", "spec_path": "openapi.json"})
    assert res.status == "passed"


def test_scan_and_run_supports_manifest_container_dict(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    workspace = tmp_path / "ws"
    workspace.mkdir()
    started = time.time()
    tests_path = str(workspace / "contracts" / "tests" / "contract_tests.json")
    _write(
        tests_path,
        json.dumps(
            {
                "tests": [
                    {
                        "id": "unit_readme",
                        "type": "unit",
                        "assertions": {"kind": "file_exists", "path": "README.md"},
                    }
                ]
            },
            indent=2,
        ),
    )
    os.utime(tests_path, (started + 1, started + 1))
    _write(str(workspace / "README.md"), "ok")
    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(workspace), run_started_at=started)
    results = runner.scan_and_run()
    unit = [r for r in results if r.id == "unit_readme"][0]
    assert unit.status == "passed"


def test_api_consistency_detects_node_serverless_routes(tmp_path, tmp_run_dir, mock_logger):
    from lattice.contracts import ContractRunner

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(str(workspace / "backend" / "package.json"), json.dumps({"name": "x", "version": "1.0.0", "dependencies": {}}))
    _write(
        str(workspace / "backend" / "api" / "contact.js"),
        "module.exports = (req, res) => { if (req.method !== 'POST') { res.statusCode = 405; return res.end('no'); } res.end('ok'); };\n",
    )
    _write(
        str(workspace / "backend" / "app.py"),
        "from pydantic import BaseModel\nfrom fastapi import FastAPI\napp = FastAPI()\nclass NotContact(BaseModel):\n    x: int\n",
    )
    spec = {
        "openapi": "3.0.0",
        "paths": {"/api/contact": {"post": {"responses": {"200": {"description": "ok"}}}}},
        "components": {"schemas": {"ContactRequest": {"type": "object", "properties": {"name": {"type": "string"}}}}},
    }
    _write(str(workspace / "api" / "openapi.json"), json.dumps(spec))

    runner = ContractRunner(run_dir=tmp_run_dir, logger=mock_logger, workspace_root=str(workspace))
    res = runner.run_test(spec={"id": "api_consistency_serverless", "type": "api_consistency", "spec_path": "api/openapi.json"})
    assert res.status == "passed"
