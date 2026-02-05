from __future__ import annotations

import os
from typing import Any, Dict, List

from ..base import AgentPlan, AgentReport, ArtifactRef, BaseAgent
from ...huddle import decision_injection_text


class LLMApiAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        plan = AgentPlan(
            step="llm_api",
            description="Draft or implement an LLM API adapter aligned to the system architecture",
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
        messages = [
            {"role": "system", "content": "You are the LLMApiAgent. Output concise adapters. Do not invent tools unrelated to the goal."},
            {"role": "user", "content": f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\nDraft a minimal adapter module and usage notes."},
        ]
        out = self._run_with_tools(messages)
        refs: List[ArtifactRef] = []
        refs.append(self._write_artifact(os.path.join("llm", "adapter.md"), out, tags=["llm", "adapter"]))
        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="llm adapter notes",
            artifacts=[r.path for r in refs],
        )
        return refs

