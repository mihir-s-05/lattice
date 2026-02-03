import json
import os


OPENAPI_YAML = (
    "openapi: 3.0.0\n"
    "info:\n"
    "  title: Contact API\n"
    "  version: 1.0.0\n"
    "paths:\n"
    "  /contact:\n"
    "    post:\n"
    "      requestBody:\n"
    "        required: true\n"
    "        content:\n"
    "          application/json:\n"
    "            schema:\n"
    "              type: object\n"
    "              properties:\n"
    "                name: {type: string}\n"
    "                email: {type: string}\n"
    "                message: {type: string}\n"
    "              required: [name, email, message]\n"
    "      responses:\n"
    "        '201': {description: Created}\n"
)


def _enqueue_ladder_mode_responses(srv, *, include_plan_init: bool = False) -> None:
    from tests.http_stub import openai_chat_completion

    if include_plan_init:
        srv.enqueue_json(openai_chat_completion('{"mode":"ladder","mode_reason":"test","stages":[],"risks":[]}'))

    backend_contract = (
        "```file:contracts/openapi.yaml\n" + OPENAPI_YAML + "```\n"
        "```file:backend/README.md\nBackend contract notes\n```\n"
    )

    backend_app = r'''import json
import os
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

EMAIL_RE = re.compile(r"^\S+@\S+\.\S+$")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def validate_contact(payload: Dict[str, Any]) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    name = str(payload.get("name") or "").strip()
    email = str(payload.get("email") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not name:
        errors["name"] = "required"
    elif len(name) > 100:
        errors["name"] = "max_length"
    if not email:
        errors["email"] = "required"
    elif len(email) > 254:
        errors["email"] = "max_length"
    elif not EMAIL_RE.match(email):
        errors["email"] = "invalid"
    if not message:
        errors["message"] = "required"
    elif len(message) > 2000:
        errors["message"] = "max_length"
    return errors


class ContactService:
    def __init__(self) -> None:
        self.submissions: List[Dict[str, Any]] = []
        self._rate: Dict[str, List[float]] = {}

    def rate_limited(self, ip: str, limit: int = 10, window_sec: int = 60) -> bool:
        now = time.time()
        bucket = [t for t in (self._rate.get(ip) or []) if now - t < window_sec]
        limited = len(bucket) >= limit
        if not limited:
            bucket.append(now)
        self._rate[ip] = bucket
        return limited

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        cid = str(uuid.uuid4())
        rec = {
            "id": cid,
            "name": str(payload.get("name") or "").strip(),
            "email": str(payload.get("email") or "").strip(),
            "message": str(payload.get("message") or "").strip(),
            "ts": int(time.time()),
        }
        self.submissions.append(rec)
        return rec


SERVICE = ContactService()


class Handler(BaseHTTPRequestHandler):
    server_version = "LatticeHTTP/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:  # noqa: N802
        _json_response(self, 200, {"ok": True})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/contact":
            _json_response(self, 404, {"success": False, "message": "not found"})
            return
        ip = (self.client_address[0] if self.client_address else "unknown") or "unknown"
        if SERVICE.rate_limited(ip):
            _json_response(self, 429, {"success": False, "message": "rate_limited"})
            return

        try:
            n = int(self.headers.get("Content-Length") or "0")
            if n <= 0 or n > 200_000:
                _json_response(self, 400, {"success": False, "message": "invalid_body"})
                return
            raw = self.rfile.read(n)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                _json_response(self, 400, {"success": False, "message": "invalid_json"})
                return
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"success": False, "message": "invalid_json"})
            return

        errors = validate_contact(payload)
        if errors:
            _json_response(self, 400, {"success": False, "errors": errors})
            return

        rec = SERVICE.create(payload)
        _json_response(self, 201, {"success": True, "id": rec["id"]})


def create_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, int(port)), Handler)


def main() -> None:
    host = os.environ.get("HOST") or "127.0.0.1"
    port = int(os.environ.get("PORT") or "8000")
    srv = create_server(host=host, port=port)
    print(f"Backend listening on http://{host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
'''

    backend_scaffold = (
        "```file:backend/app.py\n" + backend_app + "```\n"
        "```file:backend/__init__.py\n\n```\n"
        "```file:backend/README.md\nBackend scaffold\n```\n"
    )

    frontend_scaffold = (
        "```file:frontend/index.html\n<!doctype html><html><head><meta charset=\"utf-8\"><title>Contact</title><link rel=\"stylesheet\" href=\"./styles.css\"></head><body><h1>Contact</h1><form id=\"contactForm\"><input name=\"name\" placeholder=\"Name\"><input name=\"email\" placeholder=\"Email\"><textarea name=\"message\" placeholder=\"Message\"></textarea><button type=\"submit\">Send</button></form><div id=\"status\"></div><script src=\"./app.js\"></script></body></html>\n```\n"
        "```file:frontend/styles.css\nbody{font-family:system-ui;margin:24px}input,textarea{display:block;margin:8px 0;width:320px}#status{margin-top:12px}\n```\n"
        "```file:frontend/app.js\nconst form=document.getElementById('contactForm');const statusEl=document.getElementById('status');const base=window.API_BASE_URL||'';form.addEventListener('submit',async(e)=>{e.preventDefault();statusEl.textContent='Sending...';const fd=new FormData(form);const payload={name:fd.get('name')||'',email:fd.get('email')||'',message:fd.get('message')||''};try{const r=await fetch(base+'/contact',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await r.json().catch(()=>({}));if(r.ok&&data.success){statusEl.textContent='Sent';}else{statusEl.textContent='Error';}}catch(err){statusEl.textContent='Network error';}});\n```\n"
        "```file:frontend/README.md\nFrontend scaffold\n```\n"
    )

    smoke_py = r'''import json
import threading
import time
import urllib.error
import urllib.request
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return int(e.code), (json.loads(body) if body else {})


def main() -> int:
    from backend.app import create_server

    srv = create_server(host="127.0.0.1", port=0)
    port = int(srv.server_address[1])
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        time.sleep(0.05)
        status, data = _post_json(f"http://127.0.0.1:{port}/contact", {"name": "Alice", "email": "alice@example.com", "message": "Hello"})
        assert status in (200, 201), (status, data)
        assert data.get("success") is True, data
        assert isinstance(data.get("id"), str) and data.get("id"), data
        return 0
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
'''

    contract_tests = [
        {"id": "api_contract", "type": "schema", "spec_path": "contracts/openapi.yaml"},
        {
            "id": "unit-frontend-files",
            "type": "unit",
            "assertions": [
                {"kind": "file_exists", "path": "frontend/index.html"},
                {"kind": "file_exists", "path": "frontend/styles.css"},
                {"kind": "file_exists", "path": "frontend/app.js"},
            ],
        },
        {
            "id": "unit-backend-files",
            "type": "unit",
            "assertions": [
                {"kind": "file_exists", "path": "backend/app.py"},
                {"kind": "file_exists", "path": "backend/README.md"},
            ],
        },
        {
            "id": "smoke-post-contact",
            "type": "command",
            "command": "python tests/smoke_post_contact.py",
            "cwd": ".",
            "timeout_sec": 60,
            "reason": "Smoke: start backend and POST /contact",
            "expected_exit_code": 0,
        },
    ]

    tests_scaffold = (
        "```file:tests/smoke_post_contact.py\n" + smoke_py + "```\n"
        "```file:contracts/tests/contract_tests.json\n" + json.dumps(contract_tests, indent=2) + "\n```\n"
    )

    srv.enqueue_json(openai_chat_completion(backend_contract))
    srv.enqueue_json(openai_chat_completion("Adapter notes\n```json\n{\"ok\": true}\n```"))
    srv.enqueue_json(openai_chat_completion('[{"topic":"API decisions","decision":"Use stdlib http.server","rationale":"Keep it minimal"}]'))
    srv.enqueue_json(openai_chat_completion(backend_scaffold))
    srv.enqueue_json(openai_chat_completion("More adapter notes"))
    srv.enqueue_json(openai_chat_completion(frontend_scaffold))
    srv.enqueue_json(openai_chat_completion(tests_scaffold))


def _set_common_env(monkeypatch, srv) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", srv.base_url_v1)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("LATTICE_PROVIDER", "openai")
    monkeypatch.setenv("LATTICE_WEB_SEARCH", "off")
    monkeypatch.setenv("LATTICE_USE_RAG", "0")
    monkeypatch.setenv("LATTICE_RETRY_COUNT", "0")
    monkeypatch.setenv("LATTICE_HTTP_TIMEOUT", "2")
    monkeypatch.setenv("LATTICE_CONNECT_TIMEOUT", "1")
    monkeypatch.setenv("LATTICE_MAX_RETRY_DELAY", "0")


def test_ladder_mode_end_to_end_hits_http_and_tools(monkeypatch, tmp_path):
    from lattice.artifacts import ArtifactStore
    from lattice.config import ConfigurationFactory
    from lattice.execution_modes import ExecutionModeFactory
    from lattice.rag import RagIndex
    from lattice.runlog import RunLogger
    from lattice.transcript import RunningTranscript
    from tests.http_stub import StubHTTPServer

    with StubHTTPServer() as srv:
        _enqueue_ladder_mode_responses(srv)
        _set_common_env(monkeypatch, srv)

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        logger = RunLogger(str(run_dir))
        artifacts = ArtifactStore(str(run_dir))
        rag = RagIndex(str(run_dir))

        cfg = ConfigurationFactory.create_run_config("run-test", "Goal: test")
        assert cfg.router_provider_order == ["openai"]
        assert cfg.agent_provider_order == ["openai"]

        mode = ExecutionModeFactory.create("ladder", str(run_dir), logger, artifacts, rag, cfg)
        # Windows filesystem timestamp resolution can be coarse; keep freshness filtering,
        # but bias the cutoff earlier so newly-created files in the same second aren't dropped.
        started = int(__import__("time").time()) - 2
        mode.runner.run_started_at = started
        mode.evaluator.run_started_at = started
        transcript = RunningTranscript("run-test")
        result = mode.execute("Build a minimal contact app", transcript)

    ws = run_dir / "workspace"
    assert (ws / "contracts" / "openapi.yaml").exists()
    assert (ws / "backend" / "app.py").exists()
    assert (ws / "frontend" / "index.html").exists()
    assert (ws / "tests" / "smoke_post_contact.py").exists()

    snapshots = result.get("plan_snapshots") or []
    assert snapshots and snapshots[-1].get("step") == "smoke_tests"
    assert all(g.get("status") == "passed" for g in (snapshots[-1].get("gates") or []))

    results_dir = run_dir / "artifacts" / "contracts" / "results"
    api_res = json.loads((results_dir / "api_contract.json").read_text(encoding="utf-8"))
    assert api_res.get("status") == "passed"
    smoke_res = json.loads((results_dir / "smoke-post-contact.json").read_text(encoding="utf-8"))
    assert smoke_res.get("status") == "passed"

    log_path = run_dir / "run.jsonl"
    assert log_path.exists()
    events = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(e.get("event") == "workspace_write" for e in events)
    model_calls = [e for e in events if e.get("event") == "model_call"]
    assert model_calls, "expected at least one provider-backed model call"
    assert all(e.get("fallback_chain") == ["openai"] for e in model_calls if isinstance(e.get("fallback_chain"), list))


def test_ladder_mode_via_router_runner(monkeypatch, tmp_path):
    from lattice.router_main import RouterRunner
    from tests.http_stub import StubHTTPServer

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    with StubHTTPServer() as srv:
        _enqueue_ladder_mode_responses(srv, include_plan_init=True)
        _set_common_env(monkeypatch, srv)
        monkeypatch.setenv("LATTICE_ROUTER_POLICY", "policy")
        monkeypatch.setenv("LATTICE_RUNS_DIR", str(runs_dir))

        runner = RouterRunner(str(tmp_path), mode="ladder")
        result = runner.run("Build a minimal contact app")

    run_dir = runs_dir / result["run_id"]
    ws = run_dir / "workspace"
    assert (ws / "contracts" / "openapi.yaml").exists()
    assert (ws / "backend" / "app.py").exists()
    assert (ws / "frontend" / "index.html").exists()
    assert (ws / "tests" / "smoke_post_contact.py").exists()

    snapshots = json.loads((run_dir / "artifacts" / "plans" / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshots and snapshots[-1].get("step") == "smoke_tests"
    assert all(g.get("status") == "passed" for g in (snapshots[-1].get("gates") or []))

    results_dir = run_dir / "artifacts" / "contracts" / "results"
    api_res = json.loads((results_dir / "api_contract.json").read_text(encoding="utf-8"))
    assert api_res.get("status") == "passed"
    smoke_res = json.loads((results_dir / "smoke-post-contact.json").read_text(encoding="utf-8"))
    assert smoke_res.get("status") == "passed"

    log_path = run_dir / "run.jsonl"
    assert log_path.exists()
    events = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(e.get("event") == "workspace_write" for e in events)
    model_calls = [e for e in events if e.get("event") == "model_call"]
    assert model_calls, "expected at least one provider-backed model call"
    assert all(e.get("fallback_chain") == ["openai"] for e in model_calls if isinstance(e.get("fallback_chain"), list))
