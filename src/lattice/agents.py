from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

from .artifacts import ArtifactStore
from .config import RunConfig
from .providers import call_with_fallback, ProviderError
from .rag import RagIndex
from .runlog import RunLogger
from .huddle import decision_injection_text


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
    type: str  # schema|http|unit
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
    status: str  # ok|blocked|needs_huddle|error
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
    ) -> None:
        self.name = name
        self.cfg = cfg
        self.logger = logger
        self.artifacts = artifacts
        self.rag = rag
        self._last_artifacts: List[ArtifactRef] = []
        self._last_plan: Optional[AgentPlan] = None
        self._last_report: Optional[AgentReport] = None
        self._provider_usage: List[Tuple[str, str]] = []  # (provider, model)
        self._rag_queries: List[Dict[str, Any]] = []

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

    def _model(self, messages: List[Dict[str, str]], temperature: Optional[float] = None) -> str:
        t0 = time.time()
        try:
            provider, base_url, model, raw, attempts = call_with_fallback(
                providers=self.cfg.providers,
                order=self.cfg.agent_provider_order,
                messages=messages,
                temperature=temperature if temperature is not None else self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                logger=self.logger,
            )
        except ProviderError as e:
            self.logger.log("agent_error", agent=self.name, error=str(e))
            raise
        dt = time.time() - t0
        out = ""
        try:
            out = raw["choices"][0]["message"].get("content") or ""
        except Exception:
            out = str(raw)
        self._provider_usage.append((provider, model))
        self.logger.log(
            "agent_model_turn",
            agent=self.name,
            provider=provider,
            model=model,
            latency_ms=int(dt * 1000),
            prompt_messages=messages,
            output_preview=(out[:500] if isinstance(out, str) else str(out)[:500]),
        )
        return out

    def _write_artifact(self, rel_path: str, text: str, tags: Optional[List[str]] = None, meta: Optional[Dict[str, Any]] = None) -> ArtifactRef:
        art = self.artifacts.add_text(rel_path, text, tags=tags or [], meta=meta or {})
        ref = ArtifactRef(path=art.path, sha256=art.sha256, tags=art.tags, mime=art.mime, meta=art.meta)
        self._last_artifacts.append(ref)
        self.logger.log(
            "artifact_write",
            agent=self.name,
            path=art.path,
            sha256=art.sha256,
            tags=art.tags,
            meta=art.meta,
        )
        try:
            self.rag.ingest_text(art.id, text, art.path)
            self.logger.log("rag_ingest_agent", agent=self.name, doc_id=art.id, path=art.path)
        except Exception:
            pass
        return ref

    def _rag_search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        hits = self.rag.search(query, top_k=top_k)
        self._rag_queries.append({"q": query, "top_k": top_k, "hits": [h.get("doc_id") for h in hits]})
        self.logger.log("rag_search", agent=self.name, q=query, top_k=top_k, hits=[h.get("doc_id") for h in hits])
        return hits


class FrontendAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
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
        _ = self._rag_search("API contract")
        messages = [
            {"role": "system", "content": "You are the FrontendAgent. Create concise, actionable artifacts."},
            {"role": "user", "content": f"Goal: {goal}\n\n{inject}\n\nProduce: (1) wireframes/UX notes (markdown), (2) a minimal UI schema JSON describing key views and components."},
        ]
        out = self._model(messages)
        wire, schema = out, "{\n  \"views\": []\n}"
        if "{" in out and "}" in out:
            try:
                start = out.index("{")
                end = out.rindex("}") + 1
                schema = out[start:end]
                wire = (out[:start] + "\n\n" + out[end:]).strip()
            except Exception:
                pass
        refs: List[ArtifactRef] = []
        refs.append(self._write_artifact(os.path.join("fe", "wireframes.md"), wire, tags=["fe", "wireframes"]))
        refs.append(self._write_artifact(os.path.join("fe", "ui_schema.json"), schema, tags=["fe", "schema"]))
        index_html = """<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Notes App</title>
    <link rel=\"stylesheet\" href=\"styles.css\" />
  </head>
  <body>
    <header><h1>Notes</h1><button id=\"newBtn\">+</button></header>
    <main>
      <ul id=\"list\"></ul>
      <section id=\"detail\" hidden>
        <input id=\"title\" placeholder=\"Title\" />
        <textarea id=\"content\" placeholder=\"Content\"></textarea>
        <button id=\"saveBtn\">Save</button>
      </section>
    </main>
    <script src=\"script.js\"></script>
  </body>
  </html>
"""
        script_js = """const list = document.getElementById('list');
const detail = document.getElementById('detail');
const titleEl = document.getElementById('title');
const contentEl = document.getElementById('content');
const saveBtn = document.getElementById('saveBtn');
const newBtn = document.getElementById('newBtn');

let notes = [];
function render(){
  list.innerHTML = notes.map(n => `<li data-id="${n.id}"><strong>${n.title}</strong> – ${n.content.slice(0,40)}</li>`).join('');
}
newBtn.onclick = () => { detail.hidden = false; titleEl.value=''; contentEl.value=''; };
saveBtn.onclick = () => {
  const n = { id: String(Date.now()), title: titleEl.value, content: contentEl.value };
  notes.unshift(n); detail.hidden = true; render();
};
list.onclick = (e) => {
  const li = e.target.closest('li'); if(!li) return; const n = notes.find(x=>x.id===li.dataset.id);
  alert((n && n.content) || 'not found');
};
render();
"""
        styles_css = """body{font-family:system-ui;margin:0} header{display:flex;align-items:center;gap:8px;padding:12px;border-bottom:1px solid #ddd} #newBtn{margin-left:auto} main{display:flex;gap:16px;padding:12px} ul{list-style:none;padding:0;margin:0;width:50%} li{padding:8px;border-bottom:1px solid #eee;cursor:pointer} #detail{display:flex;flex-direction:column;gap:8px;width:50%} textarea{min-height:200px}
"""
        run_sh = """#!/usr/bin/env sh
set -eu
PORT=${PORT:-5173}
cd "$(dirname "$0")"/app
python3 -m http.server "$PORT"
"""
        app_title = (goal or "App").strip().title() or "App"
        index_html = f"""<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>{app_title}</title>
    <link rel=\"stylesheet\" href=\"styles.css\" />
  </head>
  <body>
    <header><h1>{app_title}</h1></header>
    <main>
      <section>
        <p>This is a minimal front-end scaffold. Wire it to your API and components defined in ui_schema.json.</p>
      </section>
    </main>
    <script src=\"script.js\"></script>
  </body>
  </html>
"""
        script_js = """console.log('Frontend scaffold ready');
"""
        styles_css = """body{font-family:system-ui;margin:0} header{display:flex;align-items:center;gap:8px;padding:12px;border-bottom:1px solid #ddd} main{padding:12px}
"""
        refs.append(self._write_artifact(os.path.join("frontend", "app", "index.html"), index_html, tags=["frontend", "scaffold"]))
        refs.append(self._write_artifact(os.path.join("frontend", "app", "script.js"), script_js, tags=["frontend", "scaffold"]))
        refs.append(self._write_artifact(os.path.join("frontend", "app", "styles.css"), styles_css, tags=["frontend", "scaffold"]))
        refs.append(self._write_artifact(os.path.join("frontend", "run.sh"), run_sh, tags=["frontend", "scaffold"]))
        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="wireframes + ui schema + FE scaffold",
            artifacts=[r.path for r in refs],
        )
        return refs

    def needs_huddle(self, context: Dict[str, Any]) -> bool:
        return False


class BackendAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        contracts = [
            ContractSpec(
                id="api_contract",
                subject="API",
                type="schema",
                spec_path="artifacts/contracts/openapi.yaml",
                runner="local",
                pass_criteria={"schema_valid": True},
            )
        ]
        plan = AgentPlan(
            step="be_contract",
            description="Draft minimal OpenAPI contract and scaffold endpoints",
            contracts=contracts,
        )
        self._last_plan = plan
        return plan

    def act(self, inputs: Dict[str, Any]) -> List[ArtifactRef]:
        goal = inputs.get("goal", "")
        decisions = inputs.get("decisions", [])
        inject = decision_injection_text(decisions) if decisions else ""
        _ = self._rag_search("OpenAPI contract")
        messages = [
            {"role": "system", "content": "You are the BackendAgent. Output compact code/specs."},
            {"role": "user", "content": f"Goal: {goal}\n\n{inject}\n\nTasks:\n1) Propose a minimal API contract (OpenAPI YAML) for the target app domain described in the goal.\n2) Provide a brief domain model and endpoints list. Return YAML between ```yaml fences."},
        ]
        out = self._model(messages)
        yaml_text = "openapi: 3.1.0\ninfo:\n  title: App API\n  version: 0.1.0\npaths: {}\ncomponents: {}\n"
        if "```yaml" in out:
            try:
                y = out.split("```yaml", 1)[1].split("```", 1)[0]
                yaml_text = y.strip() + "\n"
            except Exception:
                pass
        refs: List[ArtifactRef] = []
        refs.append(self._write_artifact(os.path.join("contracts", "openapi.yaml"), yaml_text, tags=["contract", "openapi"]))
        refs.append(self._write_artifact(os.path.join("backend", "README.md"), out, tags=["backend"]))
        main_py = """from fastapi import FastAPI
from pydantic import BaseModel
from typing import List

app = FastAPI()

class Note(BaseModel):
    id: str
    title: str
    content: str
    createdAt: str
    updatedAt: str

class NoteInput(BaseModel):
    title: str
    content: str

_NOTES: List[Note] = []

@app.get('/health')
def health():
    return { 'ok': True }

@app.get('/notes', response_model=List[Note])
def list_notes():
    return _NOTES

@app.post('/notes', response_model=Note, status_code=201)
def create_note(inp: NoteInput):
    from datetime import datetime
    now = datetime.utcnow().isoformat()+'Z'
    n = Note(id=str(len(_NOTES)+1), title=inp.title, content=inp.content, createdAt=now, updatedAt=now)
    _NOTES.append(n)
    return n

@app.get('/notes/{id}', response_model=Note)
def get_note(id: str):
    for n in _NOTES:
        if n.id == id:
            return n
    return Note(id='0', title='not found', content='', createdAt='', updatedAt='')

@app.put('/notes/{id}', response_model=Note)
def update_note(id: str, inp: NoteInput):
    from datetime import datetime
    for i, n in enumerate(_NOTES):
        if n.id == id:
            upd = n.copy(update={'title': inp.title, 'content': inp.content, 'updatedAt': datetime.utcnow().isoformat()+'Z'})
            _NOTES[i] = upd
            return upd
    return get_note(id)

@app.delete('/notes/{id}', status_code=204)
def delete_note(id: str):
    global _NOTES
    _NOTES = [n for n in _NOTES if n.id != id]
    return None
"""
        reqs_txt = "fastapi\nuvicorn\n"
        run_sh = """#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"/app
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
"""
        try:
            import re as _re
            def _extract_paths(yaml_src: str) -> Dict[str, List[str]]:
                paths: Dict[str, List[str]] = {}
                in_paths = False
                current = None
                for line in yaml_src.splitlines():
                    if not in_paths:
                        if line.strip().startswith("paths:"):
                            in_paths = True
                        continue
                    if _re.match(r"^\s{2}/", line):
                        current = line.strip().split(":",1)[0]
                        paths[current] = []
                        continue
                    m = _re.match(r"^\s{4}(get|post|put|delete|patch|options|head):", line)
                    if m and current:
                        paths[current].append(m.group(1).lower())
                    if in_paths and line and not line.startswith(" ") and not line.strip().startswith("#"):
                        break
                return paths
            def _params(path: str) -> List[str]:
                return _re.findall(r"{([^}]+)}", path)
            def _fn(method: str, path: str) -> str:
                safe = path.strip('/').replace('/','_').replace('{','').replace('}','') or 'root'
                return f"{method}_{safe}"
            _paths = _extract_paths(yaml_text)
            if not _paths:
                _paths = {"/echo": ["get"]}
            blocks: List[str] = []
            for p, methods in _paths.items():
                if not methods:
                    methods = ["get"]
                params = _params(p)
                for mth in methods:
                    blocks.append(f"@app.{mth}('{p}')")
                    sig = ", ".join([f"{n}: str" for n in params])
                    if sig: sig = ", " + sig
                    blocks.append(f"def {_fn(mth,p)}(request: dict = None{sig}):")
                    blocks.append(f"    return {{'path': '{p}', 'method': '{mth}', 'params': {{{', '.join([f'\"{n}\": {n}' for n in params])}}}}}")
                    blocks.append("")
            main_py = (
                "from fastapi import FastAPI\n\n"
                "app = FastAPI()\n\n"
                "@app.get('/health')\n"
                "def health():\n"
                "    return {'ok': True}\n\n"
                + "\n".join(blocks)
            )
        except Exception:
            pass
        refs.append(self._write_artifact(os.path.join("backend", "app", "main.py"), main_py, tags=["backend", "scaffold"]))
        refs.append(self._write_artifact(os.path.join("backend", "requirements.txt"), reqs_txt, tags=["backend", "scaffold"]))
        refs.append(self._write_artifact(os.path.join("backend", "run.sh"), run_sh, tags=["backend", "scaffold"]))
        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="API contract + BE scaffold",
            artifacts=[r.path for r in refs],
        )
        return refs

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
        _ = self._rag_search("LLM adapters")
        messages = [
            {"role": "system", "content": "You are the LLMApiAgent. Output concise adapters."},
            {"role": "user", "content": f"Goal: {goal}\n\n{inject}\n\nProduce: (1) adapter notes (markdown) for LLM requests, (2) minimal prompt IO schema JSON aligned with the goal domain."},
        ]
        out = self._model(messages)
        schema = {
            "name": "NotesLLMAdapter",
            "tools": [
                {"name": "summarize_note", "input": {"note_text": "string"}, "output": {"summary": "string"}},
                {"name": "list_notes", "input": {"limit": "int"}, "output": {"notes": "Note[]"}},
            ],
        }
        refs: List[ArtifactRef] = []
        refs.append(self._write_artifact(os.path.join("llm", "adapters.md"), out, tags=["llm", "adapters"]))
        # Derive schema from output or fallback to domain-specific defaults
        def _extract_json_block(text: str) -> Optional[Dict[str, Any]]:
            try:
                if "```json" in text:
                    frag = text.split("```json", 1)[1].split("```", 1)[0]
                    return json.loads(frag)
                s = text[text.find("{") : text.rfind("}") + 1]
                return json.loads(s)
            except Exception:
                return None
        schema = _extract_json_block(out)
        if not schema or not isinstance(schema, dict) or not schema.get("tools"):
            gl = (goal or "").lower()
            if "weather" in gl:
                schema = {
                    "name": "WeatherLLMAdapter",
                    "tools": [
                        {"name": "get_current_weather", "input": {"city": "string"}, "output": {"temperature": "number", "description": "string"}},
                        {"name": "search_cities", "input": {"query": "string"}, "output": {"matches": "string[]"}},
                    ],
                }
            else:
                schema = {
                    "name": "GenericAdapter",
                    "tools": [
                        {"name": "summarize", "input": {"text": "string"}, "output": {"summary": "string"}},
                        {"name": "list_items", "input": {"limit": "int"}, "output": {"items": "any[]"}},
                    ],
                }
        refs.append(self._write_artifact(os.path.join("llm", "prompt_io.json"), json.dumps(schema, indent=2), tags=["llm", "schema"]))
        stub_lines: List[str] = []
        for tool in schema.get("tools", []):
            try:
                name = tool.get("name") or "tool"
                inputs = tool.get("input") or {}
                args = ", ".join([f"{k}: str" for k in inputs.keys()])
                if args:
                    args = ", " + args
                stub_lines.append(f"def {name}(context: dict = None{args}) -> dict:")
                stub_lines.append("    return {}  # TODO: implement")
                stub_lines.append("")
            except Exception:
                continue
        refs.append(self._write_artifact(os.path.join("backend", "app", "adapters", "llm", "adapter.py"), "\n".join(stub_lines) + "\n", tags=["backend", "llm", "scaffold"]))
        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="LLM adapters + IO schema + stubs",
            artifacts=[r.path for r in refs],
        )
        return refs

    def needs_huddle(self, context: Dict[str, Any]) -> bool:
        return not bool((context or {}).get("decisions"))


class TestAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        plan = AgentPlan(
            step="contract_tests",
            description="Propose minimal contract tests (schema + unit)",
            contracts=[
                ContractSpec(
                    id="api_contract",
                    subject="NotesAPI",
                    type="schema",
                    spec_path="artifacts/contracts/openapi.yaml",
                    runner="local",
                    pass_criteria={"schema_valid": True},
                )
            ],
        )
        self._last_plan = plan
        return plan

    def act(self, inputs: Dict[str, Any]) -> List[ArtifactRef]:
        tests = [
            {
                "id": "api_contract",
                "subject": "API",
                "type": "schema",
                "spec_path": "artifacts/contracts/openapi.yaml",
                "runner": "local",
                "pass_criteria": {"schema_valid": True},
            },
            {
                "id": "smoke_suite",
                "subject": "Scaffolds",
                "type": "unit",
                "runner": "local",
                "assertions": [
                    {"kind": "file_exists", "path": "backend/app/main.py"},
                    {"kind": "file_exists", "path": "frontend/app/index.html"},
                ]
            }
        ]
        text = json.dumps(tests, indent=2)
        refs: List[ArtifactRef] = []
        refs.append(self._write_artifact(os.path.join("contracts", "tests", "contract_tests.json"), text, tags=["tests", "contracts"]))
        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="Contract + smoke tests defined",
            artifacts=[r.path for r in refs],
        )
        return refs

    def needs_huddle(self, context: Dict[str, Any]) -> bool:
        return False
