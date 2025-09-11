from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse

RUNS_DIR_ENV = "LATTICE_RUNS_DIR"

app = FastAPI(title="LATTICE Runs API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _runs_base() -> str:
    base = os.environ.get(RUNS_DIR_ENV) or os.path.join(os.getcwd(), "runs")
    os.makedirs(base, exist_ok=True)
    return base


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _infer_run_fields(run_dir: str) -> Dict[str, Any]:
    log_path = os.path.join(run_dir, "run.jsonl")
    events = _read_jsonl(log_path)

    started_at: Optional[str] = None
    provider: str = ""
    model: str = ""

    for e in events:
        if not started_at and e.get("event") == "run_start":
            started_at = e.get("ts")
        if e.get("event") == "router_llm_turn":
            if e.get("provider"):
                provider = e.get("provider")
            if e.get("model"):
                model = e.get("model")

    if not started_at:
        # fallback to dir mtime
        try:
            started_at = datetime.fromtimestamp(os.path.getmtime(run_dir), tz=timezone.utc).isoformat()
        except Exception:
            started_at = _now_iso()

    # status heuristic
    summary_path = os.path.join(run_dir, "artifacts", "run_summary.json")
    if os.path.exists(summary_path):
        status = "completed"
    else:
        # if recent activity in last hour -> running
        try:
            last_ts = events[-1]["ts"] if events else None
            if last_ts:
                # assume running if there are events and no summary
                status = "running"
            else:
                status = "pending"
        except Exception:
            status = "pending"

    return {
        "started_at": started_at,
        "provider": provider or "?",
        "model": model or "?",
        "status": status,
    }


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/runs")
def list_runs() -> List[Dict[str, Any]]:
    base = _runs_base()
    out: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(base)):
        run_dir = os.path.join(base, name)
        if not os.path.isdir(run_dir):
            continue
        fields = _infer_run_fields(run_dir)
        out.append({
            "run_id": name,
            **fields,
        })
    return out


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    base = _runs_base()
    run_dir = os.path.join(base, run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(status_code=404, detail="Run not found")
    fields = _infer_run_fields(run_dir)
    return {
        "run_id": run_id,
        **fields,
    }


def _infer_type_from_path(rel_path: str) -> str:
    p = rel_path.replace("\\", "/").lower()
    if "/contracts/" in p or p.endswith("openapi.yaml") or p.endswith("openapi.yml"):
        return "spec"
    if "/decisions/" in p:
        return "decision"
    if "/plans/" in p:
        return "decision"
    if "/huddles/" in p:
        return "huddle"
    if any(p.endswith(ext) for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".sh")):
        return "code"
    if "/logs/" in p or p.endswith(".log") or p.endswith(".jsonl"):
        return "log"
    if "/finalization/" in p or "/deliverables/" in p:
        return "deliverable"
    return "log"


@app.get("/runs/{run_id}/artifacts")
def list_artifacts(run_id: str) -> List[Dict[str, Any]]:
    base = _runs_base()
    run_dir = os.path.join(base, run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(status_code=404, detail="Run not found")
    idx_path = os.path.join(run_dir, "artifacts", "index.json")
    if not os.path.exists(idx_path):
        return []
    try:
        with open(idx_path, "r", encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        idx = {"artifacts": []}

    results: List[Dict[str, Any]] = []
    for a in idx.get("artifacts", []):
        try:
            rel_path = a.get("path") or ""
            abs_path = os.path.join(run_dir, rel_path)
            size = os.path.getsize(abs_path) if os.path.exists(abs_path) else 0
            mime_type = a.get("mime") or (mimetypes.guess_type(abs_path)[0] or "application/octet-stream")
            created_at = datetime.fromtimestamp(os.path.getmtime(abs_path), tz=timezone.utc).isoformat() if os.path.exists(abs_path) else _now_iso()
            results.append({
                "path": rel_path,
                "type": _infer_type_from_path(rel_path),
                "mime_type": mime_type,
                "size": size,
                "hash": a.get("sha256") or "",
                "tags": a.get("tags") or [],
                "created_at": created_at,
            })
        except Exception:
            continue
    return results


@app.get("/runs/{run_id}/artifacts/{path:path}")
def get_artifact(run_id: str, path: str):
    base = _runs_base()
    run_dir = os.path.join(base, run_id)
    abs_path = os.path.join(run_dir, path)
    if not abs_path.startswith(run_dir):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Not found")

    mime, _ = mimetypes.guess_type(abs_path)
    mime = mime or "application/octet-stream"
    return FileResponse(abs_path, media_type=mime)


@app.get("/runs/{run_id}/plan_graph")
def get_plan_graph(run_id: str) -> Dict[str, Any]:
    base = _runs_base()
    run_dir = os.path.join(base, run_id)
    pg_path = os.path.join(run_dir, "artifacts", "plans", "plan_graph.json")
    if not os.path.exists(pg_path):
        return {"segments": [], "current_segment": ""}
    try:
        with open(pg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        nodes = data.get("nodes", [])
        segments = [
            {"id": n.get("id"), "mode": n.get("name"), "status": "completed", "critical_path": False}
            for n in nodes
            if isinstance(n, dict)
        ]
        last_reason = data.get("reasons", [])[-1] if data.get("reasons") else None
        return {
            "segments": segments,
            "current_segment": segments[-1]["id"] if segments else "",
            "last_switch_reason": (last_reason or {}).get("details") if last_reason else None,
        }
    except Exception:
        return {"segments": [], "current_segment": ""}


# Optional: basic webhook to notify about new runs (no-op placeholder)
@app.post("/webhook/run_created")
def run_created(payload: Dict[str, Any]):
    # This endpoint can be used to trigger client-side refreshes via a separate pub/sub if desired.
    return {"ok": True}


def main() -> None:
    try:
        import uvicorn  # type: ignore
    except Exception:
        raise SystemExit("Please install uvicorn: pip install 'uvicorn[standard]'")
    uvicorn.run("lattice.web_api:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=True)


if __name__ == "__main__":
    main()
