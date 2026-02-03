from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
from typing import Any, Dict, List, Optional

from .rag import RagIndex
from .runlog import RunLogger
from .config import RunConfig
from .command_validation import command_is_dangerous, validate_command


def _tool_schema(name: str, desc: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


def build_agent_tools_manifest() -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    tools.append(_tool_schema(
        "read_file",
        "Read a file from the workspace (relative to project root unless absolute).",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 1, "maximum": 500000},
            },
            "required": ["path"],
        },
    ))

    tools.append(_tool_schema(
        "delete_file",
        "Delete a file from the workspace (relative to project root unless absolute). Prefer this over run_command for file cleanup.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "missing_ok": {"type": ["boolean", "null"]},
            },
            "required": ["path"],
        },
    ))
    tools.append(_tool_schema(
        "write_file",
        "Write a file to the workspace (relative to project root unless absolute).",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["overwrite", "append"]},
            },
            "required": ["path", "content"],
        },
    ))
    tools.append(_tool_schema(
        "run_command",
        "Run a shell command in the workspace. Must be safe and justified.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": ["string", "null"]},
                "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 600},
                "reason": {"type": "string"},
            },
            "required": ["command", "reason"],
        },
    ))
    tools.append(_tool_schema(
        "rag_search",
        "Keyword-based search (BM25) over run-scoped artifacts and transcripts.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "where": {
                    "type": ["object", "null"],
                    "properties": {
                        "doc_id_prefix": {"type": ["string", "null"]},
                        "path_prefix": {"type": ["string", "null"]},
                        "path_contains": {"type": ["string", "null"]},
                        "tags_any": {"type": "array", "items": {"type": "string"}},
                        "tags_all": {"type": "array", "items": {"type": "string"}},
                        "kind": {"type": ["string", "null"]},
                    },
                },
            },
            "required": ["query", "top_k"],
        },
    ))
    tools.append({
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Perform a web search via the configured local adapter (if enabled).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    "language": {"type": ["string", "null"]},
                    "time_range": {"type": ["string", "null"], "enum": ["d", "w", "m", "y", None]},
                    "engines": {"type": ["string", "null"]},
                    "pageno": {"type": ["integer", "null"], "minimum": 1},
                },
                "required": ["query", "top_k"],
            },
        },
    })
    tools.append(_tool_schema(
        "request_huddle",
        "Request a huddle/check-in (router will coordinate opening it).",
        {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "questions": {"type": "array", "items": {"type": "string"}},
                "attendees": {"type": ["array", "null"], "items": {"type": "string"}},
                "agenda": {"type": ["string", "null"]},
                "urgency": {"type": ["string", "null"], "enum": ["low", "normal", "high", None]},
            },
            "required": ["topic"],
        },
    ))
    return tools


class AgentToolExecutor:
    def __init__(
        self,
        *,
        agent_name: str,
        cfg: RunConfig,
        logger: RunLogger,
        rag: RagIndex,
        workspace_root: str,
        allow_write_globs: Optional[List[str]] = None,
        deny_write_globs: Optional[List[str]] = None,
        request_huddle_cb: Optional[Any] = None,
    ) -> None:
        self.agent_name = agent_name
        self.cfg = cfg
        self.logger = logger
        self.rag = rag
        self.workspace_root = workspace_root
        self.allow_write_globs = [str(x) for x in (allow_write_globs or []) if str(x).strip()] if allow_write_globs is not None else None
        self.deny_write_globs = [str(x) for x in (deny_write_globs or []) if str(x).strip()]
        self._request_huddle_cb = request_huddle_cb

    def _resolve_workspace_path(self, path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            raise ValueError("path is required")
        if not os.path.isabs(raw):
            raw = os.path.join(self.workspace_root, raw)
        abs_path = os.path.realpath(os.path.abspath(raw))
        root = os.path.normcase(os.path.realpath(os.path.abspath(self.workspace_root)))
        target = os.path.normcase(abs_path)
        try:
            within = os.path.commonpath([root, target]) == root
        except ValueError:
            within = target == root or target.startswith(root + os.sep)
        if not within:
            raise ValueError("path escapes workspace root")
        return abs_path

    def _command_is_dangerous(self, cmd: str) -> bool:
        return command_is_dangerous(cmd)

    def _validate_command(self, cmd: str) -> Optional[str]:
        allow = list(getattr(self.cfg.command_policy, "allowlist", []) or [])
        deny = list(getattr(self.cfg.command_policy, "denylist", []) or [])
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

    def execute(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name == "read_file":
            path = self._resolve_workspace_path(args.get("path") or "")
            max_bytes = int(args.get("max_bytes") or 200000)
            max_bytes = max(1, min(max_bytes, 500_000))
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(max_bytes)
            self.logger.log("agent_tool_file_read", agent=self.agent_name, path=path, bytes=len(content.encode("utf-8")), truncated=len(content) >= max_bytes)
            return {"path": path, "content": content, "truncated": len(content) >= max_bytes}

        if name == "write_file":
            path = self._resolve_workspace_path(args.get("path") or "")
            content = str(args.get("content") or "")
            mode = (args.get("mode") or "overwrite").strip().lower()
            rel = os.path.relpath(path, self.workspace_root).replace("\\", "/")
            for pat in (self.deny_write_globs or []):
                if fnmatch.fnmatch(rel, pat):
                    return {"error": "write_blocked", "reason": f"denied by policy: {pat}", "path": path}
            if self.allow_write_globs is not None:
                ok = any(fnmatch.fnmatch(rel, pat) for pat in (self.allow_write_globs or []))
                if not ok:
                    return {"error": "write_blocked", "reason": "not permitted by policy", "path": path, "rel": rel, "allow_globs": self.allow_write_globs}
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if mode == "append":
                with open(path, "a", encoding="utf-8") as f:
                    f.write(content)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
            self.logger.log("agent_tool_file_write", agent=self.agent_name, path=path, bytes=len(content.encode("utf-8")), mode=mode)
            sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            doc_id = hashlib.sha256((sha + "|" + path).encode("utf-8")).hexdigest()[:16]
            self.rag.ingest_text(
                doc_id,
                content,
                path,
                tags=["workspace", "agent", self.agent_name],
                meta={"kind": "workspace_write", "by": f"agent:{self.agent_name}"},
            )
            return {"path": path, "bytes": len(content.encode("utf-8")), "mode": mode}

        if name == "delete_file":
            path = self._resolve_workspace_path(args.get("path") or "")
            missing_ok = bool(args.get("missing_ok")) if isinstance(args, dict) else False
            rel = os.path.relpath(path, self.workspace_root).replace("\\", "/")
            for pat in (self.deny_write_globs or []):
                if fnmatch.fnmatch(rel, pat):
                    return {"error": "delete_blocked", "reason": f"denied by policy: {pat}", "path": path}
            if self.allow_write_globs is not None:
                ok = any(fnmatch.fnmatch(rel, pat) for pat in (self.allow_write_globs or []))
                if not ok:
                    return {"error": "delete_blocked", "reason": "not permitted by policy", "path": path, "rel": rel, "allow_globs": self.allow_write_globs}
            if not os.path.exists(path):
                return {"deleted": False, "missing": True} if missing_ok else {"error": "not_found", "path": path}
            if os.path.isdir(path):
                return {"error": "is_directory", "path": path}
            try:
                os.remove(path)
            except OSError as e:
                return {"error": str(e)}
            self.logger.log("agent_tool_file_delete", agent=self.agent_name, path=path)
            return {"deleted": True, "path": path}

        if name == "run_command":
            cmd = str(args.get("command") or "")
            reason = str(args.get("reason") or "")
            timeout_sec = int(args.get("timeout_sec") or 120)
            cwd = args.get("cwd")
            if not reason.strip():
                raise ValueError("reason is required for run_command")
            deny = self._validate_command(cmd)
            if deny:
                return {"error": deny}
            run_cwd = self._resolve_workspace_path(cwd) if cwd else self.workspace_root
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
            self.logger.log("agent_tool_run_command", agent=self.agent_name, command=cmd, cwd=run_cwd, reason=reason, result=("ok" if "error" not in out else "error"))
            return out

        if name == "rag_search":
            q = str(args.get("query") or "")
            k = int(args.get("top_k") or 5)
            where = args.get("where") if isinstance(args, dict) else None
            hits = self.rag.search_rag(q, top_k=k, where=(where if isinstance(where, dict) else None))
            return {"hits": hits}

        if name == "web_search":
            query = str(args.get("query") or "").strip()
            top_k = int(args.get("top_k") or 5)
            language = args.get("language")
            engines = args.get("engines")
            time_range = args.get("time_range")
            pageno = args.get("pageno")
            if not query:
                return {"error": "query is required"}
            if not getattr(self.cfg, "web_search_enabled", False):
                return {"error": "tool_unavailable", "reason": "disabled_by_config"}
            adapter_cfg = getattr(self.cfg, "websearch_adapter", None) or {}
            search_base = (adapter_cfg.get("search_base_url") or "").rstrip("/")
            if not (adapter_cfg.get("enabled") and search_base):
                return {"error": "tool_unavailable", "reason": "adapter_not_enabled"}
            try:
                import requests
                from requests import RequestException
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
                results: List[Dict[str, Any]] = []
                for r in raw[: max(1, min(top_k, 10))]:
                    results.append({
                        "title": r.get("title") or "",
                        "url": r.get("url") or r.get("link") or "",
                        "snippet": (r.get("content") or r.get("summary") or "")[:500],
                        "engine": r.get("engine") or r.get("source") or "",
                    })
                return {"query": query, "source": "adapter", "results": results}
            except (RequestException, ValueError) as e:
                return {"error": "web_search_failed", "reason": str(e)}

        if name == "request_huddle":
            if self._request_huddle_cb is None:
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
            self._request_huddle_cb(topic=topic, questions=questions, attendees=attendees, agenda=agenda, urgency=urgency)
            return {"queued": True, "topic": topic, "attendees": attendees}

        return {"error": "tool_unavailable", "reason": f"unknown tool: {name}"}


def append_tool_result_message(messages: List[Dict[str, Any]], tool_call: Dict[str, Any], obs: Dict[str, Any]) -> None:
    call_id = tool_call.get("id") if isinstance(tool_call, dict) else None
    name = (tool_call.get("function") or {}).get("name") if isinstance(tool_call, dict) else None
    messages.append({
        "role": "tool",
        "tool_call_id": call_id,
        "name": name or "",
        "content": json.dumps(obs, ensure_ascii=False),
    })
