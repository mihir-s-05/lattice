from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..base import AgentPlan, AgentReport, ArtifactRef, BaseAgent
from ...huddle import decision_injection_text


class BackendAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        step = str(step_or_goal or "").strip().lower()
        is_contract = any(k in step for k in ("contract", "openapi", "schema", "spec"))
        is_build = any(k in step for k in ("scaffold", "implement", "integration", "backend", "api", "server"))
        if is_contract and (not is_build):
            plan = AgentPlan(
                step="api_contract",
                description="Draft an OpenAPI contract and contract tests",
                contracts=[],
            )
        else:
            plan = AgentPlan(
                step="be_scaffold",
                description="Implement a minimal backend scaffold aligned to the contract",
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
        mode = getattr(self._last_plan, "step", "be_scaffold") if self._last_plan else "be_scaffold"

        if mode == "be_scaffold":
            sys = (
                "You are the BackendAgent. Generate a minimal backend as a set of files.\n"
                "Output only fenced file blocks. Each fence must include the file path in the fence info.\n"
                "Example: ```file:backend/app.py\n...\n```\n"
            )
            user = (
                f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\n"
                "Generate these files:\n"
                "- backend/app.py\n"
                "- backend/requirements.txt\n"
                "- backend/README.md\n\n"
                "Behavior:\n"
                "- Expose POST /contact that accepts JSON {name,email,message}.\n"
                "- Return JSON {ok:true} on success.\n"
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

        messages = [
            {"role": "system", "content": "You are the BackendAgent. Draft an OpenAPI contract aligned to the goal. Output YAML only (no fenced blocks)."},
            {"role": "user", "content": f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\nDraft OpenAPI for a minimal service with POST /contact and a health endpoint."},
        ]
        out = self._run_with_tools(messages)
        refs2: List[ArtifactRef] = []
        refs2.append(self._write_artifact(os.path.join("contracts", "openapi.yaml"), out, tags=["contracts", "openapi"]))
        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="openapi draft",
            artifacts=[r.path for r in refs2],
        )
        return refs2

