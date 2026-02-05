from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Set

from ..checklist import Checklist
from ..constants import DEFAULT_HUDDLE_DIR, DEFAULT_STAGE_GATES
from ..runlog import RunLogger
from ..stage_gates import GateEvaluator


class StateManager:
    def __init__(self, run_dir: str, logger: RunLogger) -> None:
        self.run_dir = run_dir
        self.logger = logger

        self._stage_order: List[str] = []
        self._current_step: Optional[str] = None

    @property
    def stage_order(self) -> List[str]:
        return self._stage_order

    @property
    def current_step(self) -> Optional[str]:
        return self._current_step

    def set_stage_order(self, stage_order: List[str]) -> None:
        self._stage_order = list(stage_order or [])

    def set_current_step(self, step: Optional[str]) -> None:
        self._current_step = step

    def agent_context(
        self,
        goal: str,
        decisions: List[Any],
        checklist: Checklist,
        touched_files: Set[str],
    ) -> Dict[str, Any]:
        huddle_summaries: List[str] = []
        try:
            hud_dir = os.path.join(self.run_dir, DEFAULT_HUDDLE_DIR)
            if os.path.isdir(hud_dir):
                paths = [os.path.join(hud_dir, n) for n in os.listdir(hud_dir) if n.endswith(".summary.md")]
                paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                for p in paths[:3]:
                    try:
                        with open(p, "r", encoding="utf-8", errors="replace") as f:
                            huddle_summaries.append(f.read(3000))
                    except OSError:
                        continue
        except OSError:
            huddle_summaries = []
        return {
            "goal": goal,
            "decisions": decisions,
            "checklist_prompt": checklist.prompt_summary(),
            "workspace_files": sorted(list(touched_files))[:200],
            "huddle_summaries": huddle_summaries,
        }

    def snapshot_state(
        self,
        plan_graph: Any,
        evaluator: GateEvaluator,
        decisions: List[Any],
        unread_huddles: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        mode: str,
        checklist: Checklist,
    ) -> Dict[str, Any]:
        try:
            evaluator.load_test_results()
        except (OSError, ValueError):
            pass
        tool_manifest = [(t.get("function", {}) or {}).get("name") for t in tools]
        active_gates = [{"id": g["id"], "name": g["name"]} for g in DEFAULT_STAGE_GATES]
        return {
            "plan_graph": plan_graph.snapshot(),
            "mode": mode,
            "stage_order": list(self._stage_order or []),
            "current_step": self._current_step,
            "latest_tests": evaluator.latest_tests,
            "active_gates": active_gates,
            "checklist": [i.to_dict() for i in checklist.items],
            "unread_huddles": unread_huddles,
            "recent_decisions": [asdict(d) for d in decisions[-5:]],
            "tools": tool_manifest,
        }

    def parse_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        if not isinstance(text, str) or not text.strip():
            return None
        s = text.strip()
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        try:
            start = s.find("{")
            end = s.rfind("}")
            if start != -1 and end != -1 and end > start:
                frag = s[start : end + 1]
                obj = json.loads(frag)
                return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        return None

    def apply_plan_updates(self, decisions: List[Any], current_mode: str) -> Dict[str, Any]:
        if not decisions:
            return {}
        desired_mode: Optional[str] = None
        desired_stage_order: Optional[List[str]] = None
        for d in decisions:
            meta_mode = (getattr(d, "meta", None) or {}).get("mode")
            if isinstance(meta_mode, str) and meta_mode.strip().lower() in ("ladder", "tracks", "weave"):
                desired_mode = meta_mode.strip().lower()
            meta_order = (getattr(d, "meta", None) or {}).get("stage_order")
            if isinstance(meta_order, list) and meta_order:
                vals = [str(x).strip() for x in meta_order if str(x).strip()]
                if vals:
                    desired_stage_order = vals
        if desired_mode is None:
            for d in decisions:
                txt = " ".join([str(getattr(d, "topic", "") or ""), str(getattr(d, "decision", "") or "")]).lower()
                if "mode" in txt and "tracks" in txt:
                    desired_mode = "tracks"
                    break
                if "mode" in txt and "ladder" in txt:
                    desired_mode = "ladder"
                    break
        out: Dict[str, Any] = {}
        if desired_mode and desired_mode != (current_mode or ""):
            out["mode"] = desired_mode
        if desired_stage_order:
            seen = set()
            cleaned: List[str] = []
            for s in desired_stage_order:
                if s in seen:
                    continue
                seen.add(s)
                cleaned.append(s)
            if cleaned:
                self._stage_order = cleaned[:10]
                out["stage_order"] = list(self._stage_order)
        return out

