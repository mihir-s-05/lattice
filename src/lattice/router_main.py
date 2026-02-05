from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from .artifacts import ArtifactStore
from .config import RunConfig, load_run_config
from .huddle import (
    DecisionSummary,
)
from .rag import RagIndex
from .runlog import RunLogger
from .stage_gates import GateEvaluator, StageGate
from .transcript import RunningTranscript
from .worker import gen_run_id
from .plan import PlanGraph
from .constants import get_runs_base_dir
from .router.tools import build_tools_manifest, ROUTER_SYSTEM_PROMPT
from .router.checklist_manager import ChecklistManager
from .router.workspace import WorkspaceManager
from .router.state import StateManager
from .router.gates import finalization_allowed as gates_finalization_allowed
from .router.gates import finalization_gates as gates_finalization_gates
from .router.huddle_executor import HuddleExecutor
from .router.runners import run_agentic as run_agentic_runner
from .router.runners import run_classic as run_classic_runner
from .checklist import Checklist


class RouterRunner:
    def __init__(self, cwd: str, run_id: Optional[str] = None, mode: Optional[str] = None, no_websearch: bool = False) -> None:
        self.source_root = cwd
        self.run_id = run_id or gen_run_id()
        self.run_dir = os.path.join(get_runs_base_dir(), self.run_id)
        os.makedirs(self.run_dir, exist_ok=True)
        self._run_started_at = time.time()
        self._workspace_baseline_at: Optional[float] = None
        self.logger = RunLogger(self.run_dir)
        self.artifacts = ArtifactStore(self.run_dir)
        self.rag = RagIndex(self.run_dir)
        self._workspace_manager = WorkspaceManager(source_root=self.source_root, run_dir=self.run_dir, logger=self.logger)
        self.workspace_root = self._workspace_manager.workspace_root
        self._prepare_workspace()
        self._workspace_baseline_at = self._workspace_manager.baseline_at
        self.cwd = self.workspace_root
        self._touched_files: set[str] = self._workspace_manager.touched_files
        self._seeded_test_files: set[str] = self._workspace_manager.seeded_test_files
        self.cfg: Optional[RunConfig] = None
        self._goal: Optional[str] = None
        default_mode = os.environ.get("LATTICE_MODE")
        if not mode and not default_mode:
            default_mode = "weave"
        self.mode = (mode or default_mode or "ladder").strip().lower()
        if self.mode not in ("ladder", "tracks", "weave"):
            self.mode = "ladder"

        self._decisions: List[DecisionSummary] = []
        self._provider_usage: Dict[str, int] = {}
        self._max_slice_agents: int = 3
        self._max_open_huddles: int = 2
        self._cooldown_threshold: int = 2
        self._cooldown_seconds: float = 5.0
        self._gate_failures: Dict[str, Tuple[int, float]] = {}
        self._web_recent: List[Dict[str, Any]] = []
        self._web_disabled_by_flag: bool = bool(no_websearch)
        self._huddle_executor: Optional[HuddleExecutor] = None
        self._latest_gate_results: List[StageGate] = []
        self._checklist_manager = ChecklistManager(self.run_dir, self.artifacts, self.logger)
        self._checklist: Checklist = self._checklist_manager.checklist
        self._state = StateManager(self.run_dir, self.logger)
        self._checkins_by_stage: Dict[str, Dict[str, Any]] = {}
        self._agent_permissions: Dict[str, Dict[str, Any]] = {
            "backend": {"allow_globs": ["backend/**", "contracts/openapi.yaml"], "deny_globs": []},
            "frontend": {"allow_globs": ["fe/**", "frontend/**"], "deny_globs": []},
            "llmapi": {"allow_globs": ["llm/**"], "deny_globs": []},
            "tests": {"allow_globs": ["tests/**", "contracts/tests/**"], "deny_globs": []},
        }

    def _prepare_workspace(self) -> None:
        self._workspace_manager.prepare()
        self._workspace_baseline_at = self._workspace_manager.baseline_at

    def _ensure_huddle_executor(self) -> HuddleExecutor:
        if self._huddle_executor is None:
            self._huddle_executor = HuddleExecutor(
                run_dir=self.run_dir,
                cfg=self.cfg,
                logger=self.logger,
                artifacts=self.artifacts,
                rag=self.rag,
                tools_builder=self._build_tools_manifest,
                web_disabled=self._web_disabled_by_flag,
                web_recent=self._web_recent,
            )
        else:
            self._huddle_executor.cfg = self.cfg
            self._huddle_executor.web_disabled = self._web_disabled_by_flag
        return self._huddle_executor

    def _huddle_topic(self, goal: str) -> str:
        return self._ensure_huddle_executor().huddle_topic(goal)

    def _agent_context(self, goal: str, decisions: List[DecisionSummary]) -> Dict[str, Any]:
        return self._state.agent_context(goal, decisions, self._checklist, self._touched_files)

    def _parse_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        return self._state.parse_json_object(text)

    def _apply_plan_updates_from_decisions(self, decisions: List[DecisionSummary]) -> Dict[str, Any]:
        out = self._state.apply_plan_updates(decisions, current_mode=self.mode)
        desired_mode = out.get("mode")
        if isinstance(desired_mode, str) and desired_mode and desired_mode != self.mode:
            prev = self.mode
            self.mode = desired_mode
            self.logger.log("mode_decision", previous=prev, current=self.mode, reason="huddle_decision")
        return out

    def _checklist_relpath(self) -> str:
        return self._checklist_manager.relpath()

    def _load_or_init_checklist(self) -> Checklist:
        return self._checklist_manager.checklist

    def _save_checklist(self, checklist: Optional[Checklist] = None) -> None:
        _ = checklist
        self._checklist_manager.save()

    def _record_touched(self, path: str) -> None:
        self._workspace_manager.record_touched(path)

    def _sync_checklist_state(self) -> None:
        self._checklist_manager.sync_state(self._latest_gate_results or [])

    def _is_within_cwd(self, path: str) -> bool:
        return self._workspace_manager.is_within_cwd(path)

    def _resolve_workspace_path(self, path: str) -> str:
        return self._workspace_manager.resolve_path(path)

    def _ensure_initial_checklist(self, goal: str) -> None:
        self._checklist_manager.ensure_initial(goal)

    def _finalization_allowed(
        self,
        evaluator: Optional[GateEvaluator] = None,
        gates: Optional[List[StageGate]] = None,
    ) -> Tuple[bool, List[str]]:
        return gates_finalization_allowed(self._checklist, self._checklist_manager.created, evaluator, gates)

    def _finalization_gates(self, gates: List[StageGate]) -> List[StageGate]:
        return gates_finalization_gates(gates, self._state.stage_order)

    def _execute_huddle(
        self,
        topic: str,
        questions: List[str],
        proposed_contract: Optional[str],
        transcript: RunningTranscript,
        agents: Dict[str, Any],
        decisions_so_far: List[DecisionSummary],
        *,
        include_agents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        hx = self._ensure_huddle_executor()
        return hx.execute(
            runner=self,
            topic=topic,
            questions=questions,
            proposed_contract=proposed_contract,
            transcript=transcript,
            agents=agents,
            decisions_so_far=decisions_so_far,
            include_agents=include_agents,
        )

    def run(self, goal: str) -> Dict[str, Any]:
        self._goal = goal
        self.cfg = load_run_config(self.run_id, goal)
        self._ensure_initial_checklist(goal)

        cfg_public = self.cfg.to_public_dict()
        with open(os.path.join(self.run_dir, "config.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(cfg_public, indent=2))
        self.logger.log("run_start", run_id=self.run_id, run_dir=self.run_dir, mode=self.mode, config=cfg_public)

        if getattr(self.cfg, "router_policy", "llm") == "llm":
            return self._run_agentic(goal)

        return self._run_classic(goal)

    def _run_classic(self, goal: str) -> Dict[str, Any]:
        return run_classic_runner(self, goal)

    def _build_tools_manifest(self) -> List[Dict[str, Any]]:
        """Build the tools manifest using the extracted module."""
        return build_tools_manifest()

    def _router_system_prompt(self) -> str:
        """Get the system prompt for the Router LLM."""
        return ROUTER_SYSTEM_PROMPT

    def _snapshot_state(
        self,
        plan_graph: PlanGraph,
        evaluator: GateEvaluator,
        decisions: List[DecisionSummary],
        unread_huddles: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return self._state.snapshot_state(
            plan_graph=plan_graph,
            evaluator=evaluator,
            decisions=decisions,
            unread_huddles=unread_huddles,
            tools=tools,
            mode=self.mode,
            checklist=self._checklist,
        )

    def _web_search_exec(self, query: str, top_k: int, time_range: Optional[str], engines: Optional[str], language: Optional[str], pageno: Optional[int]) -> Dict[str, Any]:
        hx = self._ensure_huddle_executor()
        return hx.web_search_exec(query, top_k, time_range, engines, language, pageno)

    def _run_agentic(self, goal: str) -> Dict[str, Any]:
        return run_agentic_runner(self, goal)

    def _build_summary(self, agents: Dict[str, Any], evaluator: GateEvaluator, decisions: List[DecisionSummary]) -> Dict[str, Any]:
        evaluator.load_test_results()
        reports_dir = os.path.join(self.run_dir, "artifacts", "contracts", "results")
        reports = []
        if os.path.isdir(reports_dir):
            for n in os.listdir(reports_dir):
                if n.endswith(".json"):
                    reports.append(os.path.join("artifacts", "contracts", "results", n))
        agent_reports: Dict[str, Any] = {}
        for k, a in agents.items():
            rep = a.report()
            agent_reports[k] = asdict(rep)
        providers = (self.cfg.to_public_dict().get('providers') if self.cfg else {})
        plan_snapshot_path = os.path.join("artifacts", "plans", "snapshot.json")
        return {
            "artifacts_root": os.path.join("artifacts"),
            "contract_reports": reports,
            "test_statuses": evaluator.latest_tests,
            "decisions": [asdict(d) for d in decisions],
            "agent_reports": agent_reports,
            "providers": providers,
            "router_provider_order": self.cfg.router_provider_order if self.cfg else [],
            "agent_provider_order": self.cfg.agent_provider_order if self.cfg else [],
            "mode": self.mode,
            "plan_snapshots": plan_snapshot_path,
            "scaffolds": {"backend": os.path.join("artifacts", "backend"), "frontend": os.path.join("artifacts", "frontend")},
            "checklist": [i.to_dict() for i in self._checklist.items],
            "checklist_complete": self._checklist.is_complete(),
        }
