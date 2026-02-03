import os
import io
import json
import time
import asyncio
import fnmatch
import pathlib
import traceback
from collections import deque
from typing import Any, AsyncGenerator, Deque, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware


from .constants import get_runs_base_dir, DEFAULT_RUN_LOG_FILE
from .secrets import redact_secrets


def _run_root() -> str:
    run_root_env = os.environ.get("LATTICE_RUNS_DIR") or os.environ.get("LATTICE_RUN_ROOT")
    if run_root_env and run_root_env.strip():
        base = os.path.expanduser(run_root_env)
        os.makedirs(base, exist_ok=True)
        return base
    return get_runs_base_dir()


def _safe_run_path(run_id: str) -> str:
    base = _run_root()
    if not run_id or any(ch in run_id for ch in ("/", "\\", os.path.sep)):
        raise HTTPException(status_code=400, detail="Invalid run id")
    base_real = os.path.normcase(os.path.realpath(base))
    run_path = os.path.normcase(os.path.realpath(os.path.join(base, run_id)))
    try:
        within = os.path.commonpath([base_real, run_path]) == base_real
    except ValueError:
        within = run_path == base_real or run_path.startswith(base_real + os.sep)
    if not within:
        raise HTTPException(status_code=400, detail="Invalid run id")
    if not os.path.isdir(run_path):
        raise HTTPException(status_code=404, detail="Run not found")
    return run_path


def _safe_artifact_path(run_id: str, rel_path: str) -> str:
    base = _safe_run_path(run_id)
    base_art = os.path.normcase(os.path.realpath(os.path.join(base, "artifacts")))
    target = os.path.normcase(os.path.realpath(os.path.join(base_art, rel_path)))
    try:
        within = os.path.commonpath([base_art, target]) == base_art
    except ValueError:
        within = target == base_art or target.startswith(base_art + os.sep)
    if not within:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Not found")
    return target


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _iter_jsonl(path: str):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield {"_raw": line.rstrip("\n")}


def _detect_status(log_path: str) -> str:
    status = "unknown"
    try:
        for obj in _iter_jsonl(log_path):
            ev = (obj or {}).get("event", "")
            if ev == "run_failed":
                status = "failed"
            elif ev == "run_complete":
                status = "complete"
        if status == "unknown":
            status = "running" if os.path.exists(log_path) else "unknown"
    except OSError:
        return "unknown"
    return status


def _first_ts_from_log(log_path: str) -> Optional[str]:
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = obj.get("ts")
                if ts:
                    return ts
    except OSError:
        return None
    return None


def _last_provider_model_from_log(log_path: str) -> Tuple[Optional[str], Optional[str]]:
    prov = None
    model = None
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                e = obj.get("event")
                if e in ("router_llm_turn", "model_call", "agent_model_turn"):
                    prov = obj.get("provider", prov)
                    model = obj.get("model", model)
    except OSError:
        return None, None
    return prov, model


def _dir_size_bytes(root: str) -> int:
    total = 0
    for d, _dirs, files in os.walk(root):
        for f in files:
            fp = os.path.join(d, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                continue
    return total


def _count_lines(path: str, limit: Optional[int] = None) -> int:
    count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for _ in f:
                count += 1
                if limit and count >= limit:
                    break
    except OSError:
        return 0
    return count


def _list_runs(search: Optional[str] = None,
               provider: Optional[str] = None,
               mode: Optional[str] = None,
               status: Optional[str] = None) -> List[Dict[str, Any]]:
    base = _run_root()
    results: List[Dict[str, Any]] = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        names = []
    for name in names:
        run_dir = os.path.join(base, name)
        if not os.path.isdir(run_dir):
            continue
        log_path = os.path.join(run_dir, DEFAULT_RUN_LOG_FILE)
        started_at = _first_ts_from_log(log_path) or time.strftime("%Y-%m-%dT%H:%M:%S")
        st = _detect_status(log_path) if os.path.exists(log_path) else "unknown"
        prov, model = _last_provider_model_from_log(log_path) if os.path.exists(log_path) else (None, None)
        summary_path = os.path.join(run_dir, "artifacts", "run_summary.json")
        goal = None
        mode_val = None
        if os.path.exists(summary_path):
            try:
                s = _read_json(summary_path)
                goal = s.get("goal") or s.get("task")
                mode_val = s.get("mode")
                prov = s.get("provider", prov)
                model = s.get("model", model)
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                pass
        size = _dir_size_bytes(run_dir)
        event_count = _count_lines(log_path)
        item = {
            "run_id": name,
            "started_at": started_at,
            "status": st,
            "provider": prov,
            "model": model,
            "mode": mode_val,
            "event_count": event_count,
            "size": size,
            "has_summary": os.path.exists(summary_path),
        }
        if provider and (item.get("provider") or "").lower() != provider.lower():
            continue
        if mode and (item.get("mode") or "").lower() != mode.lower():
            continue
        if status and (item.get("status") or "").lower() != status.lower():
            continue
        if search:
            sterm = search.lower()
            if sterm not in name.lower() and not (goal and sterm in str(goal).lower()):
                artifacts_dir = os.path.join(run_dir, "artifacts")
                found = False
                if os.path.isdir(artifacts_dir):
                    for d, _dirs, files in os.walk(artifacts_dir):
                        rel = os.path.relpath(d, artifacts_dir).replace("\\", "/")
                        if sterm in rel.lower():
                            found = True
                            break
                        for f in files:
                            if sterm in f.lower():
                                found = True
                                break
                        if found:
                            break
                if not found:
                    continue
        results.append(item)
    def _sort_key(it: Dict[str, Any]):
        run_dir = os.path.join(_run_root(), it["run_id"])
        log_path = os.path.join(run_dir, DEFAULT_RUN_LOG_FILE)
        try:
            return os.path.getmtime(log_path)
        except OSError:
            return 0

    results.sort(key=_sort_key, reverse=True)
    return results


async def _tail_jsonl_sse(request: Request, log_path: str, start_from_end: bool = True) -> AsyncGenerator[bytes, None]:
    loop = asyncio.get_event_loop()
    try:
        f = open(log_path, "r", encoding="utf-8")
    except FileNotFoundError:
        deadline = time.time() + 30
        while time.time() < deadline:
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.25)
            if os.path.exists(log_path):
                break
        if not os.path.exists(log_path):
            yield b"event: error\n"
            yield b"data: {\"error\": \"log file not found\"}\n\n"
            return
        f = open(log_path, "r", encoding="utf-8")

    with f:
        if start_from_end:
            f.seek(0, os.SEEK_END)
        else:
            f.seek(0)
        while True:
            if await request.is_disconnected():
                return
            line = await loop.run_in_executor(None, f.readline)
            if not line:
                await asyncio.sleep(0.3)
                continue
            try:
                obj = json.loads(line)
                obj = redact_secrets(obj)
                data = json.dumps(obj, ensure_ascii=False)
            except json.JSONDecodeError:
                data = json.dumps({"_raw": line.rstrip("\n")}, ensure_ascii=False)
            yield f"data: {data}\n\n".encode("utf-8")


def _read_last_events(path: str, n: int) -> List[Dict[str, Any]]:
    dq: Deque[str] = deque(maxlen=n)
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                dq.append(line)
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in dq:
        try:
            obj = json.loads(line)
            obj = redact_secrets(obj)
        except json.JSONDecodeError:
            obj = {"_raw": line.rstrip("\n")}
        out.append(obj)
    return out


def _artifacts_tree(root: str) -> Dict[str, Any]:
    def node(path: str) -> Dict[str, Any]:
        st = os.stat(path)
        nm = os.path.basename(path)
        if os.path.isdir(path):
            children = [node(os.path.join(path, c)) for c in sorted(os.listdir(path))]
            return {
                "name": nm,
                "type": "dir",
                "size": 0,
                "mtime": int(st.st_mtime),
                "children": children,
            }
        else:
            return {
                "name": nm,
                "type": "file",
                "size": int(st.st_size),
                "mtime": int(st.st_mtime),
            }

    if not os.path.exists(root):
        return {"name": "artifacts", "type": "dir", "size": 0, "mtime": 0, "children": []}
    return node(root)


app = FastAPI(title="LATTICE API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def get_health():
    return {"ok": True, "run_root": _run_root()}


@app.get("/api/runs")
def get_runs(
    q: Optional[str] = Query(default=None, description="search term"),
    provider: Optional[str] = None,
    mode: Optional[str] = None,
    status: Optional[str] = None,
):
    return _list_runs(search=q, provider=provider, mode=mode, status=status)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run_dir = _safe_run_path(run_id)
    log_path = os.path.join(run_dir, DEFAULT_RUN_LOG_FILE)
    summary_path = os.path.join(run_dir, "artifacts", "run_summary.json")
    md = {
        "run_id": run_id,
        "status": _detect_status(log_path) if os.path.exists(log_path) else "unknown",
        "started_at": _first_ts_from_log(log_path),
        "event_count": _count_lines(log_path),
        "size": _dir_size_bytes(run_dir),
    }
    if os.path.exists(summary_path):
        try:
            md["summary"] = _read_json(summary_path)
        except (OSError, json.JSONDecodeError, ValueError):
            md["summary"] = None
    return md


@app.get("/api/runs/{run_id}/summary")
def get_run_summary(run_id: str):
    p = _safe_artifact_path(run_id, os.path.join("", "run_summary.json"))
    return _read_json(p)


@app.get("/api/runs/{run_id}/events")
def get_events(run_id: str, tail: int = 200):
    run_dir = _safe_run_path(run_id)
    log_path = os.path.join(run_dir, DEFAULT_RUN_LOG_FILE)
    if not os.path.exists(log_path):
        return []
    tail = max(1, min(int(tail), 2000))
    return _read_last_events(log_path, tail)


@app.get("/api/runs/{run_id}/events/stream")
async def stream_events(request: Request, run_id: str, start: str = "end"):
    run_dir = _safe_run_path(run_id)
    log_path = os.path.join(run_dir, DEFAULT_RUN_LOG_FILE)
    start_from_end = start != "head"
    generator = _tail_jsonl_sse(request, log_path, start_from_end=start_from_end)
    return StreamingResponse(generator, media_type="text/event-stream")


@app.get("/api/runs/{run_id}/artifacts/tree")
def get_artifacts_tree(run_id: str):
    artifacts_root = _safe_artifact_path(run_id, "")
    return _artifacts_tree(artifacts_root)


@app.get("/api/runs/{run_id}/artifacts/{path:path}")
def get_artifact_file(run_id: str, path: str):
    full = _safe_artifact_path(run_id, path)
    return FileResponse(full, filename=os.path.basename(full))


@app.get("/api/runs/{run_id}/citations")
def get_citations(run_id: str):
    p = _safe_artifact_path(run_id, os.path.join("citations", "index.json"))
    return _read_json(p)


from subprocess import Popen
try:
    from subprocess import CREATE_NO_WINDOW
except ImportError:
    CREATE_NO_WINDOW = 0
import sys


def _spawn_cli_run(goal: str, options: Dict[str, Any]) -> Tuple[Popen, float]:
    args: List[str] = [sys.executable, "-m", "lattice.cli", "run", goal]
    provider = options.get("provider")
    model = options.get("model")
    if provider:
        args.extend(["--provider", str(provider)])
    if model:
        args.extend(["--model", str(model)])
    router_provider = options.get("router_provider")
    router_model = options.get("router_model")
    if router_provider:
        args.extend(["--router-provider", str(router_provider)])
    if router_model:
        args.extend(["--router-model", str(router_model)])
    web_search = options.get("web_search")
    if web_search and str(web_search).lower() == "off":
        args.append("--no-websearch")
    rag = options.get("rag")
    if rag is False:
        args.append("--no-rag")
    huddles = options.get("huddles")
    if huddles in ("dialog", "synthesis"):
        args.extend(["--huddles", huddles])

    env = os.environ.copy()
    if env.get("LATTICE_RUN_ROOT") and not env.get("LATTICE_RUNS_DIR"):
        env["LATTICE_RUNS_DIR"] = env["LATTICE_RUN_ROOT"]

    creationflags = CREATE_NO_WINDOW
    pop = Popen(args, env=env, creationflags=creationflags)
    return pop, time.time()


def _detect_new_run(start_time: float, timeout: float = 8.0) -> Optional[str]:
    base = _run_root()
    deadline = time.time() + timeout
    seen: set = set()
    while time.time() < deadline:
        try:
            names = os.listdir(base)
        except OSError:
            names = []

        for name in names:
            if name in seen:
                continue
            run_dir = os.path.join(base, name)
            try:
                st = os.stat(run_dir)
            except OSError:
                continue
            if st.st_mtime < start_time - 0.5:
                seen.add(name)
                continue
            log_path = os.path.join(run_dir, DEFAULT_RUN_LOG_FILE)
            if os.path.exists(log_path):
                return name
        time.sleep(0.25)
    return None


@app.post("/api/runs")
async def start_run(req: Request):
    body = await req.json()
    goal = (body.get("goal") or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Missing goal")
    options = body.get("options") or {}
    proc, t0 = _spawn_cli_run(goal, options)
    run_id = _detect_new_run(t0, timeout=10.0)
    if run_id:
        return {"run_id": run_id, "pid": proc.pid}
    return JSONResponse({"pending": True, "pid": proc.pid}, status_code=202)
