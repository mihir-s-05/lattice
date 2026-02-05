from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from ..base import AgentPlan, AgentReport, ArtifactRef, BaseAgent
from ...huddle import decision_injection_text


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
            progress="wireframes + schema",
            artifacts=[r.path for r in refs2],
        )
        return refs2

