from __future__ import annotations

import os
from typing import Any, Dict, List

from ..base import AgentPlan, AgentReport, ArtifactRef, BaseAgent
from ...huddle import decision_injection_text


class TestAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        plan = AgentPlan(
            step="tests",
            description="Write tests and contract test specs, then run smoke checks",
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
        sys = (
            "You are the TestAgent. Generate test files and a contract test manifest.\n"
            "Output only fenced file blocks. Each fence must include the file path in the fence info.\n"
            "Example: ```file:tests/test_smoke.py\n...\n```\n"
        )
        user = (
            f"Goal: {goal}\n\n{huddle}{inject}{checklist}\n\n"
            "Generate:\n"
            "- tests/test_smoke.py\n"
            "- contracts/tests/contract_tests.json\n"
        )
        out = self._run_with_tools([{"role": "system", "content": sys}, {"role": "user", "content": user}])
        refs = []
        refs.extend(self._parse_and_write_fenced_files(out, "tests"))
        refs.extend(self._parse_and_write_fenced_files(out, "contracts"))
        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="tests generated" if refs else "no files written",
            artifacts=[r.path for r in refs],
        )
        return refs

