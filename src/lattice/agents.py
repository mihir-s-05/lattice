from __future__ import annotations

import json
import os
import time
import fnmatch
import hashlib
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

from .artifacts import ArtifactStore
from .config import RunConfig
from .providers import call_with_fallback, ProviderError
from .rag import RagIndex
from .runlog import RunLogger
from .huddle import decision_injection_text
from .agent_tools import build_agent_tools_manifest, AgentToolExecutor, append_tool_result_message


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
    ) -> None:
        self.name = name
        self.cfg = cfg
        self.logger = logger
        self.artifacts = artifacts
        self.rag = rag
        self.workspace_root = workspace_root or os.getcwd()
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
        try:
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
        except ProviderError as e:
            self.logger.log("agent_error", agent=self.name, error=str(e))
            raise
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
    ) -> str:
        """Run an agent LLM call with tool access.

        This enables:
        - on-demand huddle requests (request_huddle tool)
        - workspace IO (read_file/write_file)
        - safe command execution (run_command)
        - retrieval (rag_search)

        If tool calling is not supported by the provider, falls back to a plain model call.
        """
        tools = build_agent_tools_manifest()
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
        )

        def _delta_messages(full: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if self._tool_prev_messages_len is None:
                return full
            start = min(self._tool_prev_messages_len, len(full))
            delta = full[start:]
            if not delta:
                return full[-1:]
            return delta

        def _can_use_prev_response_id() -> bool:
            if len(self.cfg.agent_provider_order or []) != 1:
                return False
            if (self.cfg.agent_provider_order[0] if self.cfg.agent_provider_order else None) != "openai":
                return False
            openai_cfg = self.cfg.providers.get("openai")
            base = (openai_cfg.base_url or "").lower()
            if "openai.com" not in base:
                return False
            model = (self.cfg.agent_model_default or openai_cfg.model or "").lower()
            return ("gpt-5" in model) or model.startswith("o3") or model.startswith("o4")

        for _i in range(max_iters):
            try:
                model_overrides = (
                    {self.cfg.agent_provider_order[0]: self.cfg.agent_model_default}
                    if (self.cfg.agent_model_default and self.cfg.agent_provider_order)
                    else None
                )

                msg_payload = messages
                prev_id = None
                if self._tool_prev_response_id and _can_use_prev_response_id():
                    prev_id = self._tool_prev_response_id
                    msg_payload = _delta_messages(messages)
                    msg_payload = [m for m in msg_payload if not (isinstance(m, dict) and m.get("_skip_for_responses"))]
                result = call_with_fallback(
                    providers=self.cfg.providers,
                    order=self.cfg.agent_provider_order,
                    messages=msg_payload,
                    temperature=self.cfg.temperature,
                    max_tokens=self.cfg.max_tokens,
                    logger=self.logger,
                    retries=self.cfg.limits.retry_count,
                    http_timeout=self.cfg.limits.http_timeout,
                    connect_timeout=self.cfg.limits.connect_timeout,
                    max_retry_delay=self.cfg.limits.max_retry_delay,
                    tools=tools,
                    tool_choice=tool_choice,
                    model_overrides=model_overrides,
                    previous_response_id=prev_id,
                    caller=f"agent:{self.name}",
                    stage="agent_tools",
                )
            except ProviderError:
                return self._model([{"role": m.get("role"), "content": m.get("content") or ""} for m in messages if isinstance(m, dict)], temperature=None)

            tool_calls = result.tool_calls or []
            out_text = result.text or ""
            if result.api == "responses" and result.response_id:
                self._tool_prev_response_id = result.response_id
                self._tool_prev_messages_len = len(messages)
            else:
                self._tool_prev_response_id = None
                self._tool_prev_messages_len = None
            if not tool_calls:
                return out_text

            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": (out_text if out_text else None), "tool_calls": tool_calls}
            if result.api == "responses":
                assistant_msg["_skip_for_responses"] = True
            messages.append(assistant_msg)
            for tc in tool_calls:
                tool_name = (tc or {}).get("function", {}).get("name") or ""
                tool_args_s = (tc or {}).get("function", {}).get("arguments") or "{}"
                try:
                    tool_args = json.loads(tool_args_s)
                except json.JSONDecodeError:
                    tool_args = {}
                obs = executor.execute(tool_name, tool_args if isinstance(tool_args, dict) else {})
                self.logger.log("agent_tool_call", agent=self.name, tool_name=tool_name, params=tool_args, observation=(obs if len(str(obs)) < 5000 else {"note": "obs too large"}))
                append_tool_result_message(messages, tc, obs)

        return ""

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
            if default_dir and ("/" not in p):
                p = f"{default_dir.strip().rstrip('/')}/{p}"
            return p

        fence_re = re.compile(r"```([^\n`]*)\n(.*?)```", flags=re.DOTALL)
        refs: List[ArtifactRef] = []
        for m in fence_re.finditer(text):
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
        return "\n\n" + str(text)

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
        return "\n\nRecent huddle summaries (for context, do not re-open full transcripts unless needed):\n\n" + "\n\n---\n\n".join(chunks) + "\n\n"


class FrontendAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        step = str(step_or_goal or "").strip().lower()
        is_planning = any(k in step for k in ("spec", "design", "plan"))
        is_build = any(k in step for k in ("scaffold", "implement", "integration", "test", "smoke", "handoff", "review", "frontend", "ui"))
        if (not is_planning) and is_build:
            plan = AgentPlan(
                step="fe_scaffold",
                description="Implement a minimal frontend scaffold aligned to the contract",
                contracts=[],
            )
        else:
            plan = AgentPlan(
                step="fe_wireframes",
                description="Produce wireframes and a UI schema proposal",
                contracts=[],
            )
        self._last_plan = plan
        return plan

    def act(self, inputs: Dict[str, Any]) -> List[ArtifactRef]:
        goal = inputs.get("goal", "")
        decisions = inputs.get("decisions", [])
        inject = decision_injection_text(decisions) if decisions else ""
        huddle = self._huddle_summaries_prompt(inputs)
        checklist = self._checklist_prompt(inputs)
        mode = getattr(self._last_plan, "step", "fe_wireframes") if self._last_plan else "fe_wireframes"
        if mode == "fe_scaffold":
            sys = (
                "You are the FrontendAgent. Generate the frontend as a set of files.\n"
                "Output only fenced file blocks. Each fence must include the file path in the fence info.\n"
                "Example: ```file:frontend/index.html\n...\n```\n"
            )
            user = (
                f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\n"
                "Generate these files:\n"
                "- frontend/index.html\n"
                "- frontend/styles.css\n"
                "- frontend/app.js\n"
                "- frontend/README.md\n\n"
                "Behavior:\n"
                "- `frontend/app.js` submits the form to `${base}/contact` with JSON.\n"
                "- `base` is `window.API_BASE_URL` if set, else empty string.\n"
                "- Show success/failure messages in the UI.\n"
            )
            out = self._run_with_tools([{"role": "system", "content": sys}, {"role": "user", "content": user}])
            refs = self._parse_and_write_fenced_files(out, "frontend")

            self._last_report = AgentReport(
                agent=self.name,
                status="ok",
                progress="frontend scaffold" if refs else "no files written",
                artifacts=[r.path for r in refs],
            )
            return refs

        _ = self._rag_search("API contract")
        messages = [
            {"role": "system", "content": "You are the FrontendAgent. Create concise, actionable artifacts. Use a single frontend stack and do not mix frameworks or generate parallel scaffolds."},
            {"role": "user", "content": f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\nProduce: (1) wireframes/UX notes (markdown), (2) a minimal UI schema JSON describing key views and components."},
        ]
        out = self._run_with_tools(messages)
        wire = out
        schema: Optional[str] = None
        if "```json" in out:
            try:
                schema = out.split("```json", 1)[1].split("```", 1)[0].strip()
                wire = out.replace(f"```json{schema}```", "").strip()
            except (IndexError, ValueError):
                schema = None
        if not schema and "{" in out and "}" in out:
            try:
                start = out.index("{")
                end = out.rindex("}") + 1
                schema = out[start:end]
                wire = (out[:start] + "\n\n" + out[end:]).strip()
            except ValueError:
                schema = None
        refs2: List[ArtifactRef] = []
        refs2.append(self._write_artifact(os.path.join("fe", "wireframes.md"), wire, tags=["fe", "wireframes"]))
        if schema:
            refs2.append(self._write_artifact(os.path.join("fe", "ui_schema.json"), schema, tags=["fe", "schema"]))

        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="wireframes + ui schema" if schema else "wireframes (no UI schema parsed)",
            artifacts=[r.path for r in refs2],
        )
        return refs2

    def needs_huddle(self, context: Dict[str, Any]) -> bool:
        return False


class BackendAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        step = str(step_or_goal or "").strip().lower()
        is_planning = any(k in step for k in ("spec", "design", "plan"))
        is_build = any(k in step for k in ("scaffold", "implement", "integration", "test", "smoke", "handoff", "review", "backend", "api"))
        if (not is_planning) and is_build:
            plan = AgentPlan(
                step="be_scaffold",
                description="Implement a minimal backend API aligned to the contract",
                contracts=[],
            )
        else:
            contracts = [
                ContractSpec(
                    id="api_contract",
                    subject="API",
                    type="schema",
                    spec_path="contracts/openapi.yaml",
                    runner="local",
                    pass_criteria={"schema_valid": True},
                )
            ]
            plan = AgentPlan(
                step="be_contract",
                description="Draft OpenAPI contract and backend integration notes",
                contracts=contracts,
            )
        self._last_plan = plan
        return plan

    def act(self, inputs: Dict[str, Any]) -> List[ArtifactRef]:
        goal = inputs.get("goal", "")
        decisions = inputs.get("decisions", [])
        inject = decision_injection_text(decisions) if decisions else ""
        huddle = self._huddle_summaries_prompt(inputs)
        checklist = self._checklist_prompt(inputs)
        mode = getattr(self._last_plan, "step", "be_contract") if self._last_plan else "be_contract"
        if mode == "be_scaffold":
            sys = (
                "You are the BackendAgent. Generate a minimal backend as a set of files.\n"
                "Output only fenced file blocks, and include the file path in the fence info.\n"
                "Required files: backend/app.py, backend/__init__.py, backend/README.md\n"
            )
            user = (
                f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\n"
                "Implement a minimal API that matches the contract.\n"
                "Requirements for backend/app.py:\n"
                "- Expose create_server(host, port) returning an http.server.ThreadingHTTPServer.\n"
                "- Implement POST /contact returning JSON with success=true and an id on success.\n"
                "- Implement basic input validation.\n"
            )
            out = self._run_with_tools([{"role": "system", "content": sys}, {"role": "user", "content": user}])
            refs = self._parse_and_write_fenced_files(out, "backend")

            self._last_report = AgentReport(
                agent=self.name,
                status="ok",
                progress="backend scaffold" if refs else "no files written",
                artifacts=[r.path for r in refs],
            )
            return refs


        _ = self._rag_search("OpenAPI contract")
        sys = (
            "You are the BackendAgent. Draft an OpenAPI contract aligned to the goal.\n"
            "Output only fenced file blocks.\n"
            "Required: contracts/openapi.yaml (OpenAPI YAML). Optional: backend/README.md.\n"
        )
        user = (
            f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\n"
            "Produce contracts/openapi.yaml as OpenAPI 3.0 YAML with /contact POST.\n"
        )
        out = self._run_with_tools([{"role": "system", "content": sys}, {"role": "user", "content": user}])
        refs2 = self._parse_and_write_fenced_files(out, "")

        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="API contract + docs" if refs2 else "no files written",
            artifacts=[r.path for r in refs2],
        )
        return refs2

    def needs_huddle(self, context: Dict[str, Any]) -> bool:
        return not bool((context or {}).get("decisions"))

    def propose_contracts(self, context: Dict[str, Any]) -> List[ContractSpec]:
        if self._last_plan:
            return self._last_plan.contracts
        return []


class LLMApiAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        plan = AgentPlan(
            step="llm_adapters",
            description="Design prompt IO and integration shims",
            contracts=[],
        )
        self._last_plan = plan
        return plan

    def act(self, inputs: Dict[str, Any]) -> List[ArtifactRef]:
        goal = inputs.get("goal", "")
        decisions = inputs.get("decisions", [])
        inject = decision_injection_text(decisions) if decisions else ""
        huddle = self._huddle_summaries_prompt(inputs)
        checklist = self._checklist_prompt(inputs)
        _ = self._rag_search("LLM adapters")
        messages = [
            {"role": "system", "content": "You are the LLMApiAgent. Output concise adapters. Do not invent tools unrelated to the goal."},
            {"role": "user", "content": f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\nProduce: (1) adapter notes (markdown) for LLM requests, (2) prompt IO schema JSON aligned with the goal domain. Return JSON between ```json fences."},
        ]
        out = self._run_with_tools(messages)
        refs: List[ArtifactRef] = []
        refs.append(self._write_artifact(os.path.join("llm", "adapters.md"), out, tags=["llm", "adapters"]))
        def _extract_json_block(text: str) -> Optional[Dict[str, Any]]:
            try:
                if "```json" in text:
                    frag = text.split("```json", 1)[1].split("```", 1)[0]
                    return json.loads(frag)
                s = text[text.find("{") : text.rfind("}") + 1]
                return json.loads(s)
            except (IndexError, ValueError, json.JSONDecodeError):
                return None
        schema = _extract_json_block(out)
        if schema and isinstance(schema, dict):
            refs.append(self._write_artifact(os.path.join("llm", "prompt_io.json"), json.dumps(schema, indent=2), tags=["llm", "schema"]))
        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="LLM adapters + IO schema" if schema else "LLM adapters (no JSON schema parsed)",
            artifacts=[r.path for r in refs],
        )
        return refs

    def needs_huddle(self, context: Dict[str, Any]) -> bool:
        return not bool((context or {}).get("decisions"))


class TestAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        plan = AgentPlan(
            step="contract_tests",
            description="Propose meaningful tests (unit, command, smoke, or contract if applicable)",
            contracts=[],
        )
        self._last_plan = plan
        return plan

    def act(self, inputs: Dict[str, Any]) -> List[ArtifactRef]:
        _ = inputs.get("goal", "")

        file_index: List[str] = []
        ctx_files = inputs.get("workspace_files")
        if isinstance(ctx_files, list) and ctx_files:
            file_index = sorted({str(p) for p in ctx_files if isinstance(p, str)})[:200]

        need_paths = [
            os.path.join(self.workspace_root, "contracts", "openapi.yaml"),
            os.path.join(self.workspace_root, "backend", "app.py"),
            os.path.join(self.workspace_root, "frontend", "index.html"),
            os.path.join(self.workspace_root, "frontend", "app.js"),
        ]
        if not all(os.path.exists(p) for p in need_paths):
            self._last_report = AgentReport(
                agent=self.name,
                status="ok",
                progress="Deferring tests until scaffold files exist",
                artifacts=[],
            )
            return []

        goal = inputs.get("goal", "")
        decisions = inputs.get("decisions", [])
        inject = decision_injection_text(decisions) if decisions else ""
        huddle = self._huddle_summaries_prompt(inputs)
        checklist = self._checklist_prompt(inputs)
        sys = (
            "You are the TestAgent. Generate test files and a contract test manifest.\n"
            "Output only fenced file blocks. Required: tests/smoke_post_contact.py and contracts/tests/contract_tests.json\n"
        )
        user = (
            f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\n"
            "Write:\n"
            "- tests/smoke_post_contact.py: starts backend via backend.app.create_server and POSTs /contact\n"
            "- contracts/tests/contract_tests.json: includes api_contract schema + unit checks + a smoke command test\n"
        )
        out = self._run_with_tools([{"role": "system", "content": sys}, {"role": "user", "content": user}])
        refs = self._parse_and_write_fenced_files(out, "")

        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="Smoke + contract tests written" if refs else "no files written",
            artifacts=[r.path for r in refs],
        )
        return refs

    def needs_huddle(self, context: Dict[str, Any]) -> bool:
        return False
