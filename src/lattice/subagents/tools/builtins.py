from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
from typing import Any, Dict, Optional

from ...command_validation import command_is_dangerous, validate_command
from .registry import AgentToolContext, AgentToolRegistry


def _validate_command_for_agent(cmd: str, cfg: Any) -> Optional[str]:
    allow = list(getattr(getattr(cfg, "command_policy", None), "allowlist", []) or [])
    deny = list(getattr(getattr(cfg, "command_policy", None), "denylist", []) or [])
    messages = {
        "dangerous": "command blocked: dangerous pattern",
        "denylist": "command blocked: denylist",
        "allowlist": "command blocked: allowlist",
        "default_allow": "command blocked: not in default safe allowlist (head={head})",
    }
    return validate_command(
        cmd,
        allowlist=allow,
        denylist=deny,
        require_command=True,
        allowlist_prefix_match=False,
        blocked_tokens=None,
        messages=messages,
    )


def _write_policy_allows(ctx: AgentToolContext, rel: str) -> Optional[str]:
    reln = os.path.normpath(rel).replace("\\", "/")
    for pat in ctx.deny_write_globs or []:
        if fnmatch.fnmatch(reln, pat):
            return f"denied by policy: {pat}"
    if ctx.allow_write_globs is None:
        return None
    ok = any(fnmatch.fnmatch(reln, pat) for pat in (ctx.allow_write_globs or []))
    if ok:
        return None
    return "not permitted by policy"


def register_builtins(reg: AgentToolRegistry) -> None:
    reg.register("read_file", _read_file)
    reg.register("write_file", _write_file)
    reg.register("delete_file", _delete_file)
    reg.register("run_command", _run_command)
    reg.register("rag_search", _rag_search)
    reg.register("web_search", _web_search)
    reg.register("request_huddle", _request_huddle)


def _read_file(ctx: AgentToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    path = ctx.workspace.resolve(args.get("path") or "")
    max_bytes = int(args.get("max_bytes") or 200000)
    max_bytes = max(1, min(max_bytes, 500_000))
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read(max_bytes)
    ctx.logger.log(
        "agent_tool_file_read",
        agent=ctx.agent_name,
        path=path,
        bytes=len(content.encode("utf-8")),
        truncated=len(content) >= max_bytes,
    )
    return {"path": path, "content": content, "truncated": len(content) >= max_bytes}


def _write_file(ctx: AgentToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    path = ctx.workspace.resolve(args.get("path") or "")
    content = str(args.get("content") or "")
    mode = (args.get("mode") or "overwrite").strip().lower()
    rel = os.path.relpath(path, ctx.workspace_root).replace("\\", "/")
    deny = _write_policy_allows(ctx, rel)
    if deny:
        return {"error": "write_blocked", "reason": deny, "path": path, "rel": rel}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if mode == "append":
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    ctx.logger.log("agent_tool_file_write", agent=ctx.agent_name, path=path, bytes=len(content.encode("utf-8")), mode=mode)
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    doc_id = hashlib.sha256((sha + "|" + path).encode("utf-8")).hexdigest()[:16]
    ctx.rag.ingest_text(
        doc_id,
        content,
        path,
        tags=["workspace", "agent", ctx.agent_name],
        meta={"kind": "workspace_write", "by": f"agent:{ctx.agent_name}"},
    )
    return {"path": path, "bytes": len(content.encode("utf-8")), "mode": mode}


def _delete_file(ctx: AgentToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    path = ctx.workspace.resolve(args.get("path") or "")
    missing_ok = bool(args.get("missing_ok")) if isinstance(args, dict) else False
    rel = os.path.relpath(path, ctx.workspace_root).replace("\\", "/")
    deny = _write_policy_allows(ctx, rel)
    if deny:
        return {"error": "delete_blocked", "reason": deny, "path": path, "rel": rel}
    if not os.path.exists(path):
        return {"deleted": False, "missing": True} if missing_ok else {"error": "not_found", "path": path}
    if os.path.isdir(path):
        return {"error": "is_directory", "path": path}
    os.remove(path)
    ctx.logger.log("agent_tool_file_delete", agent=ctx.agent_name, path=path)
    return {"deleted": True, "path": path}


def _run_command(ctx: AgentToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    cmd = str(args.get("command") or "")
    reason = str(args.get("reason") or "")
    timeout_sec = int(args.get("timeout_sec") or 120)
    cwd = args.get("cwd")
    if not reason.strip():
        raise ValueError("reason is required for run_command")
    deny = _validate_command_for_agent(cmd, ctx.cfg)
    if deny:
        return {"error": deny}
    run_cwd = ctx.workspace.resolve(cwd) if cwd else ctx.workspace_root
    env = None
    cmd_l = cmd.lower()
    if "pytest" in cmd_l:
        import re
        import sys

        env = dict(os.environ)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        m = re.match(r"^\s*pytest(\.exe)?\b(.*)$", cmd, flags=re.IGNORECASE)
        if m and " -m pytest" not in cmd_l and "python -m pytest" not in cmd_l:
            cmd = f"\"{sys.executable}\" -m pytest{m.group(2) or ''}"
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=run_cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
        out = {"exit_code": proc.returncode, "stdout": proc.stdout[-5000:], "stderr": proc.stderr[-5000:]}
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:
        out = {"error": str(e)}
    ctx.logger.log("agent_tool_run_command", agent=ctx.agent_name, command=cmd, cwd=run_cwd, reason=reason, result=("ok" if "error" not in out else "error"))
    return out


def _rag_search(ctx: AgentToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    q = str(args.get("query") or "")
    k = int(args.get("top_k") or 5)
    where = args.get("where") if isinstance(args, dict) else None
    hits = ctx.rag.search_rag(q, top_k=k, where=(where if isinstance(where, dict) else None))
    return {"hits": hits}


def _web_search(ctx: AgentToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 5)
    language = args.get("language")
    engines = args.get("engines")
    time_range = args.get("time_range")
    pageno = args.get("pageno")
    if not query:
        return {"error": "query is required"}
    if not getattr(ctx.cfg, "web_search_enabled", False):
        return {"error": "tool_unavailable", "reason": "disabled_by_config"}
    adapter_cfg = getattr(ctx.cfg, "websearch_adapter", None) or {}
    search_base = (adapter_cfg.get("search_base_url") or "").rstrip("/")
    if not (adapter_cfg.get("enabled") and search_base):
        return {"error": "tool_unavailable", "reason": "adapter_not_enabled"}
    import requests

    params: Dict[str, Any] = {
        "format": "json",
        "q": query,
        "language": (language or adapter_cfg.get("language") or "en"),
    }
    if engines:
        params["engines"] = engines
    elif adapter_cfg.get("default_engines"):
        params["engines"] = adapter_cfg.get("default_engines")
    tr_map = {"d": "day", "w": "week", "m": "month", "y": "year"}
    tr_full = tr_map.get(str(time_range or "").strip(), None) or adapter_cfg.get("time_range") or None
    if tr_full:
        params["time_range"] = tr_full
    if pageno and int(pageno) >= 1:
        params["pageno"] = int(pageno)
    resp = requests.get(f"{search_base}/search", params=params, timeout=30)
    data = resp.json() if resp.ok else {"results": []}
    raw = data.get("results", []) or []
    results = []
    for r in raw[: max(1, min(top_k, 10))]:
        results.append(
            {
                "title": r.get("title") or "",
                "url": r.get("url") or r.get("link") or "",
                "snippet": (r.get("content") or r.get("summary") or "")[:500],
                "engine": r.get("engine") or r.get("source") or "",
            }
        )
    return {"query": query, "source": "adapter", "results": results}


def _request_huddle(ctx: AgentToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    if ctx.request_huddle_cb is None:
        return {"error": "tool_unavailable", "reason": "request_huddle callback not wired"}
    topic = str(args.get("topic") or "").strip() or "Agent requested huddle"
    questions = args.get("questions") or []
    if isinstance(questions, str):
        questions = [questions]
    questions = [str(q).strip() for q in (questions or []) if str(q).strip()]
    attendees = args.get("attendees")
    if isinstance(attendees, str) and attendees.strip():
        attendees = [attendees.strip()]
    if isinstance(attendees, list):
        attendees = [str(a).strip() for a in attendees if str(a).strip()]
    else:
        attendees = None
    agenda = args.get("agenda")
    if agenda and not questions:
        questions = [str(agenda).strip()]
    urgency = args.get("urgency") or "normal"
    ctx.request_huddle_cb(topic=topic, questions=questions, attendees=attendees, agenda=agenda, urgency=urgency)
    return {"queued": True, "topic": topic, "attendees": attendees}

