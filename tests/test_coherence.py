import os
import json
import time


def test_coherence_detects_duplicate_openapi(tmp_path):
    from lattice.coherence import run_coherence_checks

    ws = tmp_path / "ws"
    (ws / "contracts").mkdir(parents=True)
    (ws / "openapi.yaml").write_text("openapi: 3.0.0\npaths: {}\n", encoding="utf-8")
    (ws / "contracts" / "openapi.yaml").write_text("openapi: 3.0.0\npaths: {}\n", encoding="utf-8")

    findings = run_coherence_checks(str(ws))
    ids = [f.get("id") for f in findings if isinstance(f, dict)]
    assert "duplicate_openapi" in ids
    dup = [f for f in findings if f.get("id") == "duplicate_openapi"][0]
    assert dup["severity"] == "high"
    assert any("openapi.yaml" in p for p in dup.get("paths") or [])

def test_coherence_ignores_openapi_tombstone_file(tmp_path):
    from lattice.coherence import run_coherence_checks

    ws = tmp_path / "ws"
    (ws / "contracts").mkdir(parents=True)
    (ws / "openapi.yaml").write_text("openapi: 3.0.0\npaths: {}\n", encoding="utf-8")
    (ws / "contracts" / "openapi.yaml").write_text("# Canonical spec is ../openapi.yaml\n", encoding="utf-8")

    findings = run_coherence_checks(str(ws))
    ids = [f.get("id") for f in findings if isinstance(f, dict)]
    assert "duplicate_openapi" not in ids


def test_coherence_changed_since_filters_findings(tmp_path):
    from lattice.coherence import run_coherence_checks

    ws = tmp_path / "ws"
    (ws / "contracts").mkdir(parents=True)
    p1 = ws / "openapi.yaml"
    p2 = ws / "contracts" / "openapi.yaml"
    p1.write_text("openapi: 3.0.0\npaths: {}\n", encoding="utf-8")
    p2.write_text("openapi: 3.0.0\npaths: {}\n", encoding="utf-8")
    old = time.time() - 10_000
    os.utime(str(p2), (old, old))
    findings = run_coherence_checks(str(ws), changed_since=time.time() - 5)
    ids = [f.get("id") for f in findings if isinstance(f, dict)]
    assert "duplicate_openapi" not in ids


def test_coherence_detects_vite_env_mismatch(tmp_path):
    from lattice.coherence import run_coherence_checks

    ws = tmp_path / "ws"
    (ws / "frontend" / "src").mkdir(parents=True)
    (ws / "frontend" / "package.json").write_text(json.dumps({
        "name": "x",
        "scripts": {"dev": "vite", "build": "vite build"},
        "devDependencies": {"vite": "^5.0.0"},
    }), encoding="utf-8")
    (ws / "frontend" / "src" / "App.jsx").write_text("const API = process.env.REACT_APP_API_URL;\n", encoding="utf-8")

    findings = run_coherence_checks(str(ws))
    ids = [f.get("id") for f in findings if isinstance(f, dict)]
    assert any(i and i.startswith("frontend_env_mismatch:frontend") for i in ids)
