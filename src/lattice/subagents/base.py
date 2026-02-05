from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

from ..artifacts import ArtifactStore
from ..agent_tools import AgentToolExecutor, append_tool_result_message, build_agent_tools_manifest
from ..config import RunConfig
from ..huddle import decision_injection_text
from ..providers import ProviderError, call_with_fallback
from ..rag import RagIndex
from ..runlog import RunLogger
from .workspace import WorkspaceAccess


@dataclass
class ArtifactRef:
    path: str
    sha256: str
    tags: List[str] = field(default_factory=list)
    mime: str = "text/plain"
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ContractSpec:
    id: str
    subject: str
    type: str
    spec_path: str
    runner: str = "local"
    pass_criteria: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentPlan:
    step: str
    description: str
    contracts: List[ContractSpec] = field(default_factory=list)
    notes: Optional[str] = None


@dataclass
class AgentReport:
    agent: str
    status: str
    progress: str
    risks: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)


class BaseAgent:
    def __init__(
        self,
        name: str,
        cfg: RunConfig,
        logger: RunLogger,
        artifacts: ArtifactStore,
        rag: RagIndex,
        workspace_root: Optional[str] = None,
        *,
        codebase_root: Optional[str] = None,
    ) -> None:
        self.name = name
        self.cfg = cfg
        self.logger = logger
        self.artifacts = artifacts
        self.rag = rag
        self.workspace_root = workspace_root or os.getcwd()
        self.codebase_root = codebase_root or self.workspace_root
        self._last_artifacts: List[ArtifactRef] = []
        self._last_plan: Optional[AgentPlan] = None
        self._last_report: Optional[AgentReport] = None
        self._provider_usage: List[Tuple[str, str]] = []
        self._rag_queries: List[Dict[str, Any]] = []
        self._huddle_requests: List[Dict[str, Any]] = []
        self._tool_prev_response_id: Optional[str] = None
        self._tool_prev_messages_len: Optional[int] = None
        self._write_allow_globs: Optional[List[str]] = None
        self._write_deny_globs: List[str] = []
        self._workspace = WorkspaceAccess(self.workspace_root)

    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        raise NotImplementedError

    def act(self, inputs: Dict[str, Any]) -> List[ArtifactRef]:
        raise NotImplementedError

    def needs_huddle(self, context: Dict[str, Any]) -> bool:
        return False

    def propose_contracts(self, context: Dict[str, Any]) -> List[ContractSpec]:
        return []

    def report(self) -> AgentReport:
        return self._last_report or AgentReport(agent=self.name, status="ok", progress="idle")

    def request_huddle(
        self,
        topic: str,
        questions: Optional[List[str]] = None,
        *,
        attendees: Optional[List[str]] = None,
        agenda: Optional[str] = None,
        urgency: str = "normal",
    ) -> None:
        req = {
            "from": f"agent:{self.name}",
            "topic": str(topic or "").strip() or "Agent requested huddle",
            "questions": [str(q) for q in (questions or []) if str(q).strip()],
            "attendees": [str(a) for a in (attendees or []) if str(a).strip()] if attendees else None,
            "agenda": (str(agenda).strip() if agenda else None),
            "urgency": str(urgency or "normal"),
        }
        self._huddle_requests.append(req)
        self.logger.log("agent_huddle_request", agent=self.name, **req)

    def drain_huddle_requests(self) -> List[Dict[str, Any]]:
        out = list(self._huddle_requests)
        self._huddle_requests.clear()
        return out

    def set_write_policy(self, *, allow_globs: Optional[List[str]] = None, deny_globs: Optional[List[str]] = None) -> None:
        self._write_allow_globs = [str(x) for x in (allow_globs or []) if str(x).strip()] if allow_globs is not None else None
        self._write_deny_globs = [str(x) for x in (deny_globs or []) if str(x).strip()]

    def get_write_policy(self) -> Dict[str, Any]:
        return {"allow_globs": self._write_allow_globs, "deny_globs": list(self._write_deny_globs or [])}

    def _write_allowed(self, rel_path: str) -> Tuple[bool, str]:
        rel = os.path.normpath(rel_path).replace("\\", "/")
        for pat in (self._write_deny_globs or []):
            if fnmatch.fnmatch(rel, pat):
                return False, f"denied by policy: {pat}"
        if self._write_allow_globs is None:
            return True, "allowed"
        ok = any(fnmatch.fnmatch(rel, pat) for pat in (self._write_allow_globs or []))
        return (ok, "allowed" if ok else "not permitted by policy")

    def _model(self, messages: List[Dict[str, str]], temperature: Optional[float] = None) -> str:
        t0 = time.time()
        model_overrides = (
            {self.cfg.agent_provider_order[0]: self.cfg.agent_model_default}
            if (self.cfg.agent_model_default and self.cfg.agent_provider_order)
            else None
        )
        result = call_with_fallback(
            providers=self.cfg.providers,
            order=self.cfg.agent_provider_order,
            messages=messages,
            temperature=temperature if temperature is not None else self.cfg.temperature,
            max_tokens=self.cfg.max_tokens,
            logger=self.logger,
            retries=self.cfg.limits.retry_count,
            http_timeout=self.cfg.limits.http_timeout,
            connect_timeout=self.cfg.limits.connect_timeout,
            max_retry_delay=self.cfg.limits.max_retry_delay,
            model_overrides=model_overrides,
            caller=f"agent:{self.name}",
            stage="agent_model",
        )
        dt = time.time() - t0
        out = result.text or ""
        self._provider_usage.append((result.provider, result.model))
        self.logger.log(
            "agent_model_turn",
            agent=self.name,
            provider=result.provider,
            model=result.model,
            latency_ms=int(dt * 1000),
            prompt_messages=messages,
            output_preview=(out[:500] if isinstance(out, str) else str(out)[:500]),
        )
        return out

    def _run_with_tools(
        self,
        messages: List[Dict[str, Any]],
        *,
        max_iters: int = 8,
        tool_choice: str = "auto",
        allowed_tools: Optional[List[str]] = None,
        order_override: Optional[List[str]] = None,
        model_overrides_override: Optional[Dict[str, str]] = None,
        temperature_override: Optional[float] = None,
    ) -> str:
        from .tool_loop import ToolLoop

        tools = build_agent_tools_manifest(
            allowed_tools=allowed_tools,
            cfg=self.cfg,
            codebase_root=self.codebase_root,
            include_dynamic=True,
        )
        executor = AgentToolExecutor(
            agent_name=self.name,
            cfg=self.cfg,
            logger=self.logger,
            rag=self.rag,
            workspace_root=self.workspace_root,
            allow_write_globs=self._write_allow_globs,
            deny_write_globs=self._write_deny_globs,
            request_huddle_cb=lambda **kw: self.request_huddle(
                kw.get("topic") or "",
                kw.get("questions") or [],
                attendees=kw.get("attendees"),
                agenda=kw.get("agenda"),
                urgency=kw.get("urgency") or "normal",
            ),
            codebase_root=self.codebase_root,
            include_dynamic=True,
        )
        loop = ToolLoop(self.cfg, self.logger, caller=f"agent:{self.name}")
        out = loop.run(
            messages,
            tools=tools,
            executor=executor,
            tool_choice=tool_choice,
            max_iters=max_iters,
            prev_response_id=self._tool_prev_response_id,
            prev_messages_len=self._tool_prev_messages_len,
            order_override=order_override,
            model_overrides_override=model_overrides_override,
            temperature_override=temperature_override,
        )
        self._tool_prev_response_id = out.prev_response_id
        self._tool_prev_messages_len = out.prev_messages_len
        return out.text

    def _resolve_workspace_path(self, path: str) -> str:
        return self._workspace.resolve(path)

    def _read_file(self, rel_path: str, max_bytes: int = 200_000) -> str:
        abs_path = self._resolve_workspace_path(rel_path)
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(max_bytes)
        self.logger.log(
            "workspace_read",
            agent=self.name,
            path=abs_path,
            bytes=len(content.encode("utf-8")),
            truncated=len(content) >= max_bytes,
        )
        return content

    def _write_artifact(self, rel_path: str, text: str, tags: Optional[List[str]] = None, meta: Optional[Dict[str, Any]] = None) -> ArtifactRef:
        ok, reason = self._write_allowed(rel_path)
        if not ok:
            raise ValueError(f"agent write blocked: {reason}; path={rel_path}")
        abs_path = self._resolve_workspace_path(rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(text)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        ref = ArtifactRef(path=abs_path, sha256=sha, tags=tags or [], mime="text/plain", meta=meta or {})
        self._last_artifacts.append(ref)
        self.logger.log(
            "workspace_write",
            agent=self.name,
            path=abs_path,
            sha256=sha,
            tags=tags or [],
        )
        doc_id = hashlib.sha256((sha + "|" + abs_path).encode("utf-8")).hexdigest()[:16]
        rag_meta = dict(meta or {})
        rag_meta.setdefault("kind", "workspace_write")
        rag_meta.setdefault("agent", self.name)
        self.rag.ingest_text(doc_id, text, abs_path, tags=(tags or []), meta=rag_meta)
        self.logger.log("rag_ingest_agent", agent=self.name, doc_id=doc_id, path=abs_path)
        return ref

    def _parse_and_write_fenced_files(self, text: str, default_dir: str) -> List[ArtifactRef]:
        import re

        if not isinstance(text, str) or "```" not in text:
            return []

        def _extract_path(info: str) -> Optional[str]:
            s = (info or "").strip()
            if not s:
                return None
            m = re.search(r"(?:file|path)[:=](\S+)", s, flags=re.IGNORECASE)
            if m:
                return m.group(1)
            parts = s.split()
            if len(parts) >= 2 and parts[0].lower() in ("file", "path"):
                return parts[1]
            exts = (".py", ".js", ".ts", ".css", ".html", ".md", ".json", ".yaml", ".yml", ".txt")
            for p in parts:
                pl = p.lower().strip("\"'")
                if pl.endswith(exts) or ("/" in p) or ("\\" in p):
                    return p
            return None

        def _normalize_path(raw: str) -> Optional[str]:
            p = str(raw or "").strip().strip("\"'")
            if not p:
                return None
            p = p.replace("\\", "/")
            if p.startswith("/") or re.match(r"^[a-zA-Z]:", p):
                return None
            if any(seg == ".." for seg in p.split("/") if seg):
                return None
            if default_dir and not p.startswith(default_dir + "/"):
                p = default_dir.rstrip("/") + "/" + p
            return p

        refs: List[ArtifactRef] = []
        pat = re.compile(r"```([^\n]*)\n(.*?)\n```", flags=re.DOTALL)
        for m in pat.finditer(text):
            info = (m.group(1) or "").strip()
            body = m.group(2) if m.group(2) is not None else ""
            path = _normalize_path(_extract_path(info) or "")
            if not path:
                continue
            refs.append(self._write_artifact(path, body, tags=[self.name, "generated"]))
        return refs

    def _rag_search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        hits = self.rag.search_rag(query, top_k=top_k)
        self._rag_queries.append({"q": query, "top_k": top_k, "hits": [h.get("doc_id") for h in hits]})
        self.logger.log("rag_search", agent=self.name, q=query, top_k=top_k, hits=[h.get("doc_id") for h in hits])
        return hits

    def _checklist_prompt(self, inputs: Dict[str, Any]) -> str:
        text = inputs.get("checklist_prompt") if isinstance(inputs, dict) else None
        if not text:
            return ""
        return "\\n\\n" + str(text)

    def _huddle_summaries_prompt(self, inputs: Dict[str, Any], max_chars: int = 2500) -> str:
        hs = inputs.get("huddle_summaries") if isinstance(inputs, dict) else None
        if not isinstance(hs, list) or not hs:
            return ""
        chunks: List[str] = []
        used = 0
        for s in hs[-3:]:
            if not isinstance(s, str):
                continue
            ss = s.strip()
            if not ss:
                continue
            if used + len(ss) > max_chars:
                ss = ss[: max(0, max_chars - used)]
            chunks.append(ss)
            used += len(ss)
            if used >= max_chars:
                break
        if not chunks:
            return ""
        return "\\n\\nRecent huddle summaries:\\n\\n" + "\\n\\n---\\n\\n".join(chunks) + "\\n\\n"
