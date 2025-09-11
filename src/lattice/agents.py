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
from .template_loader import get_frontend_templates, get_backend_templates, get_cli_templates


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
    ) -> None:
        self.name = name
        self.cfg = cfg
        self.logger = logger
        self.artifacts = artifacts
        self.rag = rag
        self._last_artifacts: List[ArtifactRef] = []
        self._last_plan: Optional[AgentPlan] = None
        self._last_report: Optional[AgentReport] = None
        self._provider_usage: List[Tuple[str, str]] = []
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
        
        app_title = (goal or "App").strip().title() or "App"
        template_context = {"app_title": app_title}
        templates = get_frontend_templates(template_context)

        refs.append(self._write_artifact(os.path.join("frontend", "app", "index.html"), templates.get("index.html", ""), tags=["frontend", "scaffold"]))
        refs.append(self._write_artifact(os.path.join("frontend", "app", "script.js"), templates.get("script.js", ""), tags=["frontend", "scaffold"]))
        refs.append(self._write_artifact(os.path.join("frontend", "app", "styles.css"), templates.get("styles.css", ""), tags=["frontend", "scaffold"]))
        refs.append(self._write_artifact(os.path.join("frontend", "run.sh"), templates.get("run.sh", ""), tags=["frontend", "scaffold"]))
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
        yaml_text = (
            "openapi: 3.1.0\n"
            "info:\n  title: Items API\n  version: 0.1.0\n"
            "paths:\n"
            "  /health:\n    get:\n      responses:\n        '200':\n          description: OK\n"
            "  /items:\n    get:\n      summary: List items\n      responses:\n        '200':\n          description: OK\n          content:\n            application/json:\n              schema:\n                type: array\n                items:\n                  $ref: '#/components/schemas/Item'\n    post:\n      summary: Create item\n      requestBody:\n        required: true\n        content:\n          application/json:\n            schema:\n              $ref: '#/components/schemas/ItemInput'\n      responses:\n        '201':\n          description: Created\n          content:\n            application/json:\n              schema:\n                $ref: '#/components/schemas/Item'\n"
            "  /items/{item_id}:\n    get:\n      summary: Get item\n      parameters:\n        - in: path\n          name: item_id\n          required: true\n          schema:\n            type: string\n      responses:\n        '200':\n          description: OK\n          content:\n            application/json:\n              schema:\n                $ref: '#/components/schemas/Item'\n        '404':\n          description: Not Found\n    put:\n      summary: Update item\n      parameters:\n        - in: path\n          name: item_id\n          required: true\n          schema:\n            type: string\n      requestBody:\n        required: true\n        content:\n          application/json:\n            schema:\n              $ref: '#/components/schemas/ItemInput'\n      responses:\n        '200':\n          description: OK\n          content:\n            application/json:\n              schema:\n                $ref: '#/components/schemas/Item'\n        '404':\n          description: Not Found\n    delete:\n      summary: Delete item\n      parameters:\n        - in: path\n          name: item_id\n          required: true\n          schema:\n            type: string\n      responses:\n        '204':\n          description: No Content\n        '404':\n          description: Not Found\n"
            "components:\n  schemas:\n    Item:\n      type: object\n      required: [id, name, description, createdAt, updatedAt]\n      properties:\n        id:\n          type: string\n        name:\n          type: string\n        description:\n          type: string\n        createdAt:\n          type: string\n          format: date-time\n        updatedAt:\n          type: string\n          format: date-time\n    ItemInput:\n      type: object\n      required: [name]\n      properties:\n        name:\n          type: string\n        description:\n          type: string\n"
        )
        if "```yaml" in out:
            try:
                y = out.split("```yaml", 1)[1].split("```", 1)[0]
                yaml_text = y.strip() + "\n"
            except Exception:
                pass
        refs: List[ArtifactRef] = []
        refs.append(self._write_artifact(os.path.join("contracts", "openapi.yaml"), yaml_text, tags=["contract", "openapi"]))
        refs.append(self._write_artifact(os.path.join("backend", "README.md"), out, tags=["backend"]))

        backend_templates = get_backend_templates()
        refs.append(self._write_artifact(os.path.join("backend", "app", "main.py"), backend_templates.get("main.py", ""), tags=["backend", "scaffold"]))
        refs.append(self._write_artifact(os.path.join("backend", "requirements.txt"), backend_templates.get("requirements.txt", ""), tags=["backend", "scaffold"]))
        refs.append(self._write_artifact(os.path.join("backend", "run.sh"), backend_templates.get("run.sh", ""), tags=["backend", "scaffold"]))
        if ".env" in backend_templates:
            refs.append(self._write_artifact(os.path.join("backend", ".env"), backend_templates.get(".env", ""), tags=["backend", "scaffold"]))

        cli_templates = get_cli_templates()
        refs.append(self._write_artifact(os.path.join("cli", "main.py"), cli_templates.get("main.py", ""), tags=["cli", "scaffold"]))

        readme_md = f"""# Small CLI + README\n\nGoal: {goal}\n\n## Overview\nA minimal CLI (`artifacts/cli/main.py`) and backend scaffold (FastAPI).\n\n## Quickstart\n- Python 3.9+\n- Optional: `pip install -r artifacts/backend/requirements.txt`\n\nRun the CLI:\n```bash\npython artifacts/cli/main.py list\npython artifacts/cli/main.py create --name example --description demo\n```\n\nRun the backend locally:\n```bash\nsh artifacts/backend/run.sh\n```\n\n## Commands\n- `list`\n- `create --name NAME [--description TEXT]`\n- `get ID`\n- `update ID [--name NAME] [--description TEXT]`\n- `delete ID`\n\n## Notes\n- The CLI stubs simulate IO; integrate with the API as needed.\n"""
        refs.append(self._write_artifact("README.md", readme_md, tags=["docs", "readme"]))
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
                stub_lines.append("    return {}")
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
            description="Propose meaningful contract tests (schema + consistency + deps + app checks)",
            contracts=[
                ContractSpec(
                    id="api_contract",
                    subject="ItemsAPI",
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
                "id": "api_consistency",
                "subject": "API",
                "type": "api_consistency",
                "spec_path": "artifacts/contracts/openapi.yaml",
                "runner": "local"
            },
            {
                "id": "deps",
                "subject": "BackendDeps",
                "type": "deps",
                "requirements_path": "artifacts/backend/requirements.txt",
                "required": ["fastapi", "uvicorn", "python-dotenv"]
            },
            {
                "id": "fastapi_app",
                "subject": "FastAPI",
                "type": "fastapi_app",
                "app_path": "artifacts/backend/app/main.py",
                "checks": [
                    {"method": "get", "path": "/health", "expect_status": 200},
                    {"method": "get", "path": "/items", "expect_status": 200}
                ]
            },
            {
                "id": "smoke_suite",
                "subject": "Scaffolds",
                "type": "unit",
                "runner": "local",
                "assertions": [
                    {"kind": "file_exists", "path": "backend/app/main.py"},
                    {"kind": "file_exists_optional", "path": "frontend/app/index.html"},
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