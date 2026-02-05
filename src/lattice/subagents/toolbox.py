from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .base import AgentPlan, AgentReport, ArtifactRef, BaseAgent
from .registry import ToolboxVariantSpec


class ToolboxAgent(BaseAgent):
    def __init__(self, *args, spec: ToolboxVariantSpec, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.spec = spec
        self.set_write_policy(allow_globs=spec.write_policy.allow_globs, deny_globs=spec.write_policy.deny_globs)

    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        text = str(step_or_goal or "").strip()
        plan = AgentPlan(step="toolbox", description=(self.spec.description or text or "Toolbox task"), contracts=[])
        self._last_plan = plan
        return plan

    def act(self, inputs: Dict[str, Any]) -> List[ArtifactRef]:
        goal = inputs.get("goal", "")
        task = inputs.get("task") or inputs.get("step") or inputs.get("current_step") or ""
        decisions = inputs.get("decisions", [])
        from ..huddle import decision_injection_text

        inject = decision_injection_text(decisions) if decisions else ""
        huddle = self._huddle_summaries_prompt(inputs)
        checklist = self._checklist_prompt(inputs)
        prompt = (
            (self.spec.prompt_prelude or "").strip()
            + "\n\n"
            + "Task: "
            + str(task or "").strip()
            + "\n\nGoal: "
            + str(goal or "").strip()
            + "\n\n"
            + (huddle + inject + checklist).strip()
        ).strip()
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": "You are a configurable toolbox subagent. Use tools to accomplish the task."},
            {"role": "user", "content": prompt},
        ]
        out = self._run_with_tools(
            messages,
            max_iters=self.spec.max_tool_iters,
            tool_choice=self.spec.tool_choice,
            allowed_tools=(list(self.spec.tool_names or []) if (self.spec.tool_names or []) else None),
            temperature_override=self.spec.temperature,
            model_overrides_override=self.spec.model_overrides,
        )
        refs = self._artifact_refs_from_tool_writes(messages)
        self._last_report = AgentReport(
            agent=self.name,
            status="ok",
            progress="toolbox completed",
            artifacts=[r.path for r in refs],
        )
        _ = out
        return refs

    def _artifact_refs_from_tool_writes(self, messages: List[Dict[str, Any]]) -> List[ArtifactRef]:
        paths: List[str] = []
        for m in messages:
            if not isinstance(m, dict) or m.get("role") != "tool":
                continue
            if m.get("name") != "write_file":
                continue
            content = m.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            try:
                obs = json.loads(content)
            except json.JSONDecodeError:
                continue
            if not isinstance(obs, dict):
                continue
            p = obs.get("path")
            if isinstance(p, str) and p.strip():
                paths.append(p)
        uniq: List[str] = []
        seen = set()
        for p in paths:
            if p not in seen:
                uniq.append(p)
                seen.add(p)
        refs: List[ArtifactRef] = []
        for abs_path in uniq:
            try:
                with open(abs_path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            sha = hashlib.sha256(data).hexdigest()
            refs.append(ArtifactRef(path=abs_path, sha256=sha, tags=[self.name, "tool_write"], mime="text/plain", meta={}))
        return refs
