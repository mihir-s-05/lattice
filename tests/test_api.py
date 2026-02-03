import json

from fastapi.testclient import TestClient

from lattice.server import app


def test_server_health_and_runs_listing(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    monkeypatch.setenv("LATTICE_RUNS_DIR", str(runs_root))

    client = TestClient(app)

    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["run_root"] == str(runs_root)

    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []

    run_id = "run-20000101-000000-abc123"
    run_dir = runs_root / run_id
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "run.jsonl").write_text(json.dumps({"ts": "2000-01-01T00:00:00", "event": "router_start"}) + "\n", encoding="utf-8")
    (run_dir / "artifacts" / "run_summary.json").write_text(
        json.dumps({"goal": "x", "mode": "weave", "provider": "openai", "model": "gpt-5-mini"}),
        encoding="utf-8",
    )

    r = client.get("/api/runs")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 1
    assert runs[0]["run_id"] == run_id
    assert runs[0]["has_summary"] is True
