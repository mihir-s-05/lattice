from __future__ import annotations

import hashlib
import os
from typing import Any, Dict

from . import ToolContext, ToolRegistry


@ToolRegistry.register("write_artifact")
def write_artifact(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    rel = args.get("path") or ""
    content = args.get("content") or ""
    tags = args.get("tags") or []
    rel_s = str(rel).replace("\\", "/")
    if rel_s.startswith("artifacts/"):
        rel_s = rel_s[len("artifacts/") :]
    art = runner.artifacts.add_text(rel_s, content, tags=tags, meta={"by": "router"})
    doc_id = hashlib.sha256((art.sha256 + "|" + art.path).encode("utf-8")).hexdigest()[:16]
    runner.rag.ingest_text(doc_id, content, art.path, tags=tags, meta={"kind": "artifact_write", "by": "router"})
    runner.logger.log("rag_ingest_router", doc_id=doc_id, path=art.path)
    return {"path": art.path, "hash": f"sha256:{art.sha256}", "size": len(str(content).encode("utf-8"))}


@ToolRegistry.register("read_artifact")
def read_artifact(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    rel = args.get("path") or ""
    rel_s = str(rel).replace("\\", "/")
    if rel_s.startswith("artifacts/"):
        rel_s = rel_s[len("artifacts/") :]
    base_art = os.path.normcase(os.path.realpath(os.path.join(runner.run_dir, "artifacts")))
    abspath = os.path.normcase(os.path.realpath(os.path.join(base_art, rel_s)))
    try:
        within = os.path.commonpath([base_art, abspath]) == base_art
    except ValueError:
        within = abspath == base_art or abspath.startswith(base_art + os.sep)
    if not within:
        raise ValueError("path escapes artifacts root")
    with open(abspath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read(200_000)
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {"path": os.path.join("artifacts", rel_s).replace("\\", "/"), "content": content, "mime": "text/plain", "hash": f"sha256:{h}"}


@ToolRegistry.register("write_file")
def write_file(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    rel = args.get("path") or ""
    content = args.get("content") or ""
    mode = str(args.get("mode") or "overwrite").lower()
    abs_path = runner._resolve_workspace_path(rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    write_mode = "a" if mode == "append" else "w"
    with open(abs_path, write_mode, encoding="utf-8") as f:
        f.write(content)
    if rel:
        runner._record_touched(abs_path)
    runner.logger.log(
        "tool_file_write",
        requester="router",
        tool_call_id=ctx.tool_call_id,
        path=abs_path,
        mode=mode,
        bytes=len(str(content).encode("utf-8")),
    )
    txt = str(content)
    sha = hashlib.sha256(txt.encode("utf-8")).hexdigest()
    doc_id = hashlib.sha256((sha + "|" + abs_path).encode("utf-8")).hexdigest()[:16]
    runner.rag.ingest_text(doc_id, txt, abs_path, tags=["workspace", "router"], meta={"kind": "workspace_write", "by": "router"})
    runner.logger.log("rag_ingest_router", doc_id=doc_id, path=abs_path)
    runner._sync_checklist_state()
    runner._save_checklist()
    return {"path": abs_path, "bytes": len(txt.encode("utf-8")), "mode": mode}


@ToolRegistry.register("read_file")
def read_file(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    rel = args.get("path") or ""
    max_bytes = int(args.get("max_bytes") or 200000)
    if max_bytes < 1:
        max_bytes = 1
    if max_bytes > 500_000:
        max_bytes = 500_000
    abs_path = runner._resolve_workspace_path(rel)
    with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read(max_bytes)
    runner.logger.log(
        "tool_file_read",
        requester="router",
        tool_call_id=ctx.tool_call_id,
        path=abs_path,
        bytes=len(content.encode("utf-8")),
        truncated=len(content) >= max_bytes,
    )
    return {"path": abs_path, "content": content, "truncated": len(content) >= max_bytes}


@ToolRegistry.register("delete_file")
def delete_file(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    rel = args.get("path") or ""
    missing_ok = bool(args.get("missing_ok")) if isinstance(args, dict) else False
    abs_path = runner._resolve_workspace_path(rel)
    if not os.path.exists(abs_path):
        return {"deleted": False, "missing": True, "path": abs_path} if missing_ok else {"error": "not_found", "path": abs_path}
    if os.path.isdir(abs_path):
        return {"error": "is_directory", "path": abs_path}
    try:
        os.remove(abs_path)
        runner._record_touched(abs_path)
        runner.logger.log("tool_file_delete", requester="router", tool_call_id=ctx.tool_call_id, path=abs_path)
        return {"deleted": True, "path": abs_path}
    except OSError as e:
        return {"error": str(e), "path": abs_path}

