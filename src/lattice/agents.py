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
        - retrieval (rag_search / semantic_search)

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

    def _rag_search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        hits = self.rag.search(query, top_k=top_k)
        self._rag_queries.append({"q": query, "top_k": top_k, "hits": [h.get("doc_id") for h in hits]})
        self.logger.log("rag_search", agent=self.name, q=query, top_k=top_k, hits=[h.get("doc_id") for h in hits])
        return hits

    def _rag_search_semantic(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        hits = self.rag.search_semantic(query, top_k=top_k)
        self._rag_queries.append({"q": query, "top_k": top_k, "hits": [h.get("doc_id") for h in hits], "mode": "semantic"})
        self.logger.log("semantic_search", agent=self.name, q=query, top_k=top_k, hits=[h.get("doc_id") for h in hits])
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
                description="Implement a minimal animated landing page + contact form",
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
            html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <meta name="theme-color" content="#0b1020" />
    <title>NovaFlow — ship better, faster</title>
    <link rel="stylesheet" href="./styles.css" />
  </head>
  <body>
    <div class="bg" aria-hidden="true">
      <div class="orb orb-a"></div>
      <div class="orb orb-b"></div>
      <div class="grid"></div>
    </div>

    <header class="header">
      <a class="brand" href="#top" aria-label="NovaFlow home">
        <span class="brand-mark" aria-hidden="true"></span>
        <span class="brand-name">NovaFlow</span>
      </a>
      <nav class="nav" aria-label="Primary">
        <a href="#features">Features</a>
        <a href="#security">Security</a>
        <a class="btn btn-ghost" href="#contact">Contact</a>
      </nav>
    </header>

    <main id="top" class="main">
      <section class="hero">
        <div class="hero-copy">
          <p class="pill" data-animate>Animated SaaS landing page • minimal API</p>
          <h1 class="headline" data-animate>Make every release feel effortless.</h1>
          <p class="subhead" data-animate>
            NovaFlow gives your team a calm, fast path from idea → shipped. Beautiful UX, smooth motion, and a contact flow
            that actually works.
          </p>
          <div class="hero-cta" data-animate>
            <a class="btn" href="#contact">Request a demo</a>
            <a class="btn btn-ghost" href="#features">See features</a>
          </div>
          <div class="stats" data-animate>
            <div class="stat"><span class="stat-n">2.3×</span><span class="stat-l">faster cycles</span></div>
            <div class="stat"><span class="stat-n">99.9%</span><span class="stat-l">uptime targets</span></div>
            <div class="stat"><span class="stat-n">1</span><span class="stat-l">simple API</span></div>
          </div>
        </div>

        <div class="hero-card" data-animate>
          <div class="card-top">
            <div class="card-dot"></div><div class="card-dot"></div><div class="card-dot"></div>
          </div>
          <div class="card-body">
            <div class="card-row shimmer"></div>
            <div class="card-row shimmer w-80"></div>
            <div class="card-row shimmer w-60"></div>
            <div class="card-row chart">
              <div class="bar" style="--h: 30%"></div>
              <div class="bar" style="--h: 65%"></div>
              <div class="bar" style="--h: 42%"></div>
              <div class="bar" style="--h: 78%"></div>
              <div class="bar" style="--h: 56%"></div>
            </div>
          </div>
        </div>
      </section>

      <section id="features" class="section">
        <h2 class="h2" data-animate>Built for focus</h2>
        <p class="p" data-animate>Motion that guides. Interfaces that breathe. A workflow your team can trust.</p>
        <div class="cards">
          <article class="card" data-animate>
            <h3>Guided pipelines</h3>
            <p>Turn messy work into clear stages. Ship with confidence and fewer surprises.</p>
          </article>
          <article class="card" data-animate>
            <h3>Delightful motion</h3>
            <p>Micro-interactions that feel premium—without the performance penalty.</p>
          </article>
          <article class="card" data-animate>
            <h3>Fast contact flow</h3>
            <p>A real POST /contact endpoint with validation and clean JSON responses.</p>
          </article>
        </div>
      </section>

      <section id="security" class="section">
        <h2 class="h2" data-animate>Secure by default</h2>
        <div class="split">
          <div class="panel" data-animate>
            <h3>Simple surface area</h3>
            <p>Keep the API minimal. Add more later—only when you need it.</p>
            <ul class="list">
              <li>Input validation</li>
              <li>Basic rate limiting</li>
              <li>CORS headers for local dev</li>
            </ul>
          </div>
          <div class="panel panel-accent" data-animate>
            <h3>Operational clarity</h3>
            <p>Readable logs, deterministic behavior, and a smoke script to validate end-to-end.</p>
            <p class="muted">Tip: set <code>API_BASE_URL</code> in <code>frontend/app.js</code> if your API is hosted elsewhere.</p>
          </div>
        </div>
      </section>

      <section id="contact" class="section">
        <h2 class="h2" data-animate>Contact</h2>
        <p class="p" data-animate>Send a note—we’ll reply quickly.</p>

        <form id="contactForm" class="form" novalidate>
          <label class="field">
            <span>Name</span>
            <input name="name" autocomplete="name" required maxlength="100" />
          </label>
          <label class="field">
            <span>Email</span>
            <input name="email" type="email" autocomplete="email" required maxlength="254" />
          </label>
          <label class="field">
            <span>Message</span>
            <textarea name="message" required maxlength="2000" rows="5"></textarea>
          </label>
          <div class="form-row">
            <button class="btn" type="submit">Send</button>
            <div id="formStatus" class="status" role="status" aria-live="polite"></div>
          </div>
        </form>
      </section>

      <footer class="footer">
        <span>© <span id="year"></span> NovaFlow</span>
        <a href="#top">Back to top</a>
      </footer>
    </main>

    <script src="./app.js"></script>
  </body>
</html>
"""
            css = """:root{
  --bg:#0b1020;
  --fg:#eaf0ff;
  --muted:#a9b6d6;
  --card:rgba(255,255,255,.06);
  --accent:#8b5cf6;
  --accent2:#22d3ee;
  --shadow: 0 22px 70px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0;
  font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, \"Apple Color Emoji\",\"Segoe UI Emoji\";
  color:var(--fg);
  background: radial-gradient(1200px 700px at 20% 10%, rgba(139,92,246,.25), transparent 60%),
              radial-gradient(1000px 700px at 80% 20%, rgba(34,211,238,.18), transparent 55%),
              var(--bg);
  overflow-x:hidden;
}
code{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, \"Liberation Mono\", \"Courier New\", monospace}
a{color:inherit;text-decoration:none}
.bg{position:fixed;inset:0;pointer-events:none;z-index:-1}
.grid{
  position:absolute;inset:-20%;
  background-image: linear-gradient(rgba(255,255,255,.06) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(255,255,255,.06) 1px, transparent 1px);
  background-size: 72px 72px;
  transform: perspective(900px) rotateX(55deg) translateY(-10%);
  opacity:.18;
}
.orb{position:absolute;filter:blur(40px);opacity:.8;mix-blend-mode:screen}
.orb-a{width:520px;height:520px;left:-120px;top:-120px;background:radial-gradient(circle at 30% 30%, rgba(139,92,246,.9), transparent 60%);animation: float 9s ease-in-out infinite}
.orb-b{width:520px;height:520px;right:-160px;top:40px;background:radial-gradient(circle at 30% 30%, rgba(34,211,238,.85), transparent 60%);animation: float 11s ease-in-out infinite reverse}
@keyframes float{0%,100%{transform:translate3d(0,0,0)}50%{transform:translate3d(0,24px,0)}}

.header{
  position:sticky;top:0;z-index:10;
  display:flex;align-items:center;justify-content:space-between;
  padding: 14px 20px;
  background: rgba(11,16,32,.55);
  backdrop-filter: blur(10px);
  border-bottom:1px solid rgba(255,255,255,.08);
}
.brand{display:flex;gap:10px;align-items:center;font-weight:700;letter-spacing:.2px}
.brand-mark{
  width:14px;height:14px;border-radius:6px;
  background: linear-gradient(135deg,var(--accent),var(--accent2));
  box-shadow: 0 0 0 6px rgba(139,92,246,.18);
}
.nav{display:flex;gap:14px;align-items:center}
.nav a{opacity:.9}
.nav a:hover{opacity:1}

.main{max-width:1100px;margin:0 auto;padding: 0 20px 48px}
.hero{
  display:grid;
  grid-template-columns: 1.05fr .95fr;
  gap: 26px;
  padding: 54px 0 30px;
  align-items:center;
}
.pill{
  display:inline-flex;align-items:center;gap:10px;
  padding: 7px 12px;border-radius:999px;
  background: rgba(255,255,255,.06);
  border:1px solid rgba(255,255,255,.10);
  color: var(--muted);
}
.headline{font-size: clamp(2.1rem, 4vw, 3.3rem);line-height:1.08;margin: 14px 0 10px}
.subhead{color:var(--muted);max-width: 52ch;margin: 0 0 18px}
.hero-cta{display:flex;gap:12px;flex-wrap:wrap;margin: 8px 0 16px}
.btn{
  display:inline-flex;align-items:center;justify-content:center;
  padding: 10px 14px;border-radius: 12px;
  background: linear-gradient(135deg,var(--accent),var(--accent2));
  color:#071024;font-weight:700;border:0;
  box-shadow: 0 12px 30px rgba(34,211,238,.16);
  transition: transform .18s ease, filter .18s ease;
}
.btn:hover{transform: translateY(-1px);filter:saturate(1.06)}
.btn:active{transform: translateY(0)}
.btn-ghost{
  background: rgba(255,255,255,.06);
  border:1px solid rgba(255,255,255,.14);
  box-shadow:none;color:var(--fg);font-weight:600
}
.btn-ghost:hover{background: rgba(255,255,255,.09)}

.stats{display:flex;gap:14px;flex-wrap:wrap}
.stat{padding:10px 12px;border-radius: 14px;background: var(--card);border:1px solid rgba(255,255,255,.10)}
.stat-n{display:block;font-weight:800;font-size:1.05rem}
.stat-l{display:block;color:var(--muted);font-size:.9rem}

.hero-card{
  border-radius: 18px;
  background: linear-gradient(180deg, rgba(255,255,255,.08), rgba(255,255,255,.04));
  border:1px solid rgba(255,255,255,.12);
  box-shadow: var(--shadow);
  overflow:hidden;
}
.card-top{display:flex;gap:8px;padding: 12px 14px;border-bottom:1px solid rgba(255,255,255,.08);background: rgba(0,0,0,.18)}
.card-dot{width:10px;height:10px;border-radius:99px;background: rgba(255,255,255,.22)}
.card-body{padding: 16px}
.card-row{height:14px;border-radius:10px;background: rgba(255,255,255,.08);margin:10px 0}
.w-80{width:80%}.w-60{width:60%}
.shimmer{position:relative;overflow:hidden}
.shimmer::after{
  content:\"\"; position:absolute; inset:-2px;
  transform: translateX(-110%);
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.10), transparent);
  animation: shimmer 1.6s ease-in-out infinite;
}
@keyframes shimmer{0%{transform:translateX(-110%)}50%,100%{transform:translateX(110%)}}
.chart{display:flex;gap:10px;align-items:flex-end;height:110px;margin-top:18px;padding: 8px 0}
.bar{width:18%;height: var(--h);border-radius: 12px;background: linear-gradient(180deg, rgba(139,92,246,.95), rgba(34,211,238,.75));opacity:.9}

.section{padding: 34px 0}
.h2{font-size: clamp(1.5rem, 2.4vw, 2.1rem); margin: 0 0 8px}
.p{color:var(--muted);margin:0 0 18px}
.cards{display:grid;grid-template-columns: repeat(3, 1fr); gap: 14px}
.card{
  padding: 16px 16px 14px;
  border-radius: 16px;
  background: var(--card);
  border:1px solid rgba(255,255,255,.10);
}
.card h3{margin:0 0 6px}
.card p{margin:0;color:var(--muted)}

.split{display:grid;grid-template-columns: 1fr 1fr; gap: 14px}
.panel{padding: 16px;border-radius: 16px;background: var(--card);border:1px solid rgba(255,255,255,.10)}
.panel-accent{background: linear-gradient(180deg, rgba(139,92,246,.14), rgba(34,211,238,.10));}
.list{margin: 10px 0 0 18px;color:var(--muted)}
.muted{color:var(--muted)}

.form{max-width: 620px}
.field{display:block;margin: 12px 0}
.field span{display:block;margin: 0 0 6px;color: var(--muted);font-size:.92rem}
input,textarea{
  width:100%;
  color:var(--fg);
  background: rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.14);
  border-radius: 12px;
  padding: 10px 12px;
  outline:none;
}
input:focus,textarea:focus{border-color: rgba(34,211,238,.55); box-shadow: 0 0 0 4px rgba(34,211,238,.12)}
.form-row{display:flex;gap:12px;align-items:center;margin-top: 10px}
.status{color:var(--muted);font-size:.95rem;min-height: 1.2em}
.status.ok{color:#86efac}
.status.err{color:#fca5a5}

.footer{display:flex;justify-content:space-between;align-items:center;padding-top: 26px;border-top:1px solid rgba(255,255,255,.08);color:var(--muted)}
.footer a{opacity:.9}
.footer a:hover{opacity:1}

[data-animate]{opacity:0;transform: translateY(12px);transition: opacity .55s ease, transform .55s ease}
[data-animate].in{opacity:1;transform: translateY(0)}

@media (max-width: 980px){
  .hero{grid-template-columns: 1fr; padding-top: 34px}
  .cards{grid-template-columns: 1fr}
  .split{grid-template-columns: 1fr}
  .nav{gap:10px}
}
"""
            js = """(() => {
  const year = document.getElementById('year');
  if (year) year.textContent = String(new Date().getFullYear());

  const prefersReduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const animated = Array.from(document.querySelectorAll('[data-animate]'));

  if (!prefersReduced && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          e.target.classList.add('in');
          io.unobserve(e.target);
        }
      }
    }, { threshold: 0.18 });
    animated.forEach((el, idx) => {
      el.style.transitionDelay = `${Math.min(240, idx * 40)}ms`;
      io.observe(el);
    });
  } else {
    animated.forEach((el) => el.classList.add('in'));
  }

  const form = document.getElementById('contactForm');
  const statusEl = document.getElementById('formStatus');

  const API_BASE_URL = (window.API_BASE_URL || '').replace(/\\/$/, '');
  const endpoint = () => `${API_BASE_URL}/contact`;

  const setStatus = (text, kind) => {
    if (!statusEl) return;
    statusEl.textContent = text || '';
    statusEl.classList.remove('ok', 'err');
    if (kind) statusEl.classList.add(kind);
  };

  const validate = (payload) => {
    const errors = [];
    const name = (payload.name || '').trim();
    const email = (payload.email || '').trim();
    const message = (payload.message || '').trim();
    if (!name) errors.push('Name is required.');
    if (!email) errors.push('Email is required.');
    if (email && !/^\\S+@\\S+\\.\\S+$/.test(email)) errors.push('Email looks invalid.');
    if (!message) errors.push('Message is required.');
    if (name.length > 100) errors.push('Name is too long.');
    if (email.length > 254) errors.push('Email is too long.');
    if (message.length > 2000) errors.push('Message is too long.');
    return errors;
  };

  if (form) {
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(form);
      const payload = {
        name: String(fd.get('name') || ''),
        email: String(fd.get('email') || ''),
        message: String(fd.get('message') || ''),
      };

      const errs = validate(payload);
      if (errs.length) {
        setStatus(errs[0], 'err');
        return;
      }

      setStatus('Sending…');
      try {
        const res = await fetch(endpoint(), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.success === false) {
          const msg = data.message || 'Request failed. Please try again.';
          setStatus(msg, 'err');
          return;
        }
        setStatus('Thanks — we got your message.', 'ok');
        form.reset();
      } catch (e) {
        setStatus('Network error. Is the API running?', 'err');
      }
    });
  }
})();"""
            readme = """# Frontend

Static animated landing page (vanilla HTML/CSS/JS).

## Run locally

Serve these files with any static server. For example from the repo/workspace root:

```bash
python -m http.server 5173 --directory frontend
```

Then open `http://localhost:5173/`.

## API base URL

By default the form POSTs to `/contact` on the same origin. To point at a different API, set in the browser console:

```js
window.API_BASE_URL = \"http://127.0.0.1:8000\"
```
"""
            refs: List[ArtifactRef] = []
            refs.append(self._write_artifact(os.path.join("frontend", "index.html"), html, tags=["frontend"]))
            refs.append(self._write_artifact(os.path.join("frontend", "styles.css"), css, tags=["frontend"]))
            refs.append(self._write_artifact(os.path.join("frontend", "app.js"), js, tags=["frontend"]))
            refs.append(self._write_artifact(os.path.join("frontend", "README.md"), readme, tags=["frontend"]))

            self._last_report = AgentReport(
                agent=self.name,
                status="ok",
                progress="frontend scaffold",
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
            app_py = r'''import json
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
    server_version = "NovaFlowHTTP/1.0"

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
            readme = """# Backend

Minimal backend API (stdlib) implementing `POST /contact`.

## Run locally

From the repo/workspace root:

```bash
python backend/app.py
```

By default the server listens on `http://127.0.0.1:8000`.

Environment variables:
- `HOST` (default `127.0.0.1`)
- `PORT` (default `8000`)
"""
            refs: List[ArtifactRef] = []
            refs.append(self._write_artifact(os.path.join("backend", "app.py"), app_py, tags=["backend"]))
            refs.append(self._write_artifact(os.path.join("backend", "README.md"), readme, tags=["backend"]))
            refs.append(self._write_artifact(os.path.join("backend", "__init__.py"), "", tags=["backend"]))

            self._last_report = AgentReport(
                agent=self.name,
                status="ok",
                progress="backend scaffold",
                artifacts=[r.path for r in refs],
            )
            return refs

        _ = self._rag_search("OpenAPI contract")
        messages = [
            {"role": "system", "content": "You are the BackendAgent. Output compact code/specs. Use a single backend stack and keep the contract aligned to what will be implemented."},
            {"role": "user", "content": f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\nTasks:\n1) Propose an API contract (OpenAPI YAML) for the target app domain described in the goal.\n2) Provide a brief domain model and endpoints list. Return YAML between ```yaml fences."},
        ]
        out = self._run_with_tools(messages)
        yaml_text: Optional[str] = None
        if "```yaml" in out:
            parts = out.split("```yaml", 1)
            if len(parts) > 1:
                y = parts[1].split("```", 1)[0]
                yaml_text = y.strip() + "\n"
        refs2: List[ArtifactRef] = []
        if yaml_text:
            refs2.append(self._write_artifact(os.path.join("contracts", "openapi.yaml"), yaml_text, tags=["contract", "openapi"]))
        refs2.append(self._write_artifact(os.path.join("backend", "README.md"), out, tags=["backend"]))

        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="API contract + docs" if yaml_text else "backend docs (no OpenAPI parsed)",
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

        smoke_py = r'''import json
import threading
import time
import urllib.error
import urllib.request


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

        refs: List[ArtifactRef] = []
        refs.append(self._write_artifact(os.path.join("tests", "smoke_post_contact.py"), smoke_py, tags=["tests", "smoke"]))
        refs.append(
            self._write_artifact(
                os.path.join("contracts", "tests", "contract_tests.json"),
                json.dumps(contract_tests, indent=2),
                tags=["tests", "contracts"],
            )
        )

        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="Smoke + contract tests written",
            artifacts=[r.path for r in refs],
        )
        return refs

    def needs_huddle(self, context: Dict[str, Any]) -> bool:
        return False
