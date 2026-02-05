from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, List, TYPE_CHECKING

from ...errors import ProviderError
from ...execution_modes import ExecutionModeFactory
from ...finalize import run_finalization
from ...huddle import DecisionSummary
from ...plan import PlanGraph
from ...router_llm import RouterLLM
from ...transcript import RunningTranscript, generate_run_transcript

if TYPE_CHECKING:
    from ...router_main import RouterRunner


def run_classic(runner: "RouterRunner", goal: str) -> Dict[str, Any]:
    if isinstance(goal, str) and ("readme" in goal.lower() or "docs" in goal.lower()) and runner.mode in ("ladder", "tracks"):
        runner.logger.log(
            "plan_switch",
            from_mode=runner.mode,
            to_mode="weave",
            reason_type="scope_change",
            details="goal mentions README/docs",
            decisions=[],
        )
        runner.mode = "weave"

    from ...worker import WorkerRunner

    wr = WorkerRunner(runner.cwd, runner.run_id)
    wr._pre_ingest_repo_files()

    transcript = RunningTranscript(runner.run_id)

    rllm = RouterLLM(runner.cfg, runner.logger, tools=runner._build_tools_manifest())
    decisions: List[DecisionSummary] = []
    plan_graph = PlanGraph()
    if runner.mode == "weave":
        plan_graph.mode_by_segment = {"critical": "ladder", "docs": "tracks"}
    else:
        plan_graph.mode_by_segment = {"main": runner.mode}

    plan_init = None
    try:
        plan_init = rllm.plan_init(goal)
    except ProviderError as e:
        runner.logger.log("router_plan_init_failed", error=str(e))
    if plan_init and isinstance(plan_init.get("text"), str):
        try:
            runner.artifacts.add_text(os.path.join("plans", "router_plan.txt"), plan_init["text"], tags=["plan", "router"])
        except OSError:
            pass

    mode_handler = ExecutionModeFactory.create(
        runner.mode,
        runner.run_dir,
        runner.logger,
        runner.artifacts,
        runner.rag,
        runner.cfg,
    )
    started_at = (runner._workspace_baseline_at or runner._run_started_at) - 2
    mode_handler.runner.run_started_at = started_at
    mode_handler.evaluator.run_started_at = started_at

    result = mode_handler.execute(goal, transcript)
    plan_snapshots = result.get("plan_snapshots") or []
    decisions = result.get("decisions") or []
    if isinstance(result.get("plan_graph"), PlanGraph):
        plan_graph = result["plan_graph"]

    try:
        plan_graph.save(runner.run_dir)
    except OSError as e:
        runner.logger.log("plan_graph_save_failed", error=str(e))

    try:
        runner.artifacts.add_text(os.path.join("plans", "snapshot.json"), json.dumps(plan_snapshots, indent=2), tags=["plan", "snapshot"])
    except OSError as e:
        runner.logger.log("plan_snapshot_write_failed", error=str(e))

    try:
        specs = mode_handler.runner.scan_specs()
        if specs:
            pre_results = mode_handler.runner.run_specs(
                specs,
                allow_commands=True,
                command_validator=mode_handler._validate_command,
            )
        else:
            pre_results = mode_handler.runner.scan_and_run(
                allow_commands=True,
                command_validator=mode_handler._validate_command,
            )
        pre_gate_results = mode_handler.evaluator.evaluate(mode_handler.gates)
        runner._latest_gate_results = pre_gate_results
        runner.logger.log(
            "pre_finalization_validation",
            tests=[asdict(r) for r in pre_results],
            gates=[asdict(g) for g in pre_gate_results],
        )
    except Exception as e:
        runner.logger.log("pre_finalization_error", error=str(e))
    else:
        runner._sync_checklist_state()
        runner._save_checklist()

    allowed, missing = runner._finalization_allowed(mode_handler.evaluator, mode_handler.gates)
    if allowed:
        run_finalization(
            runner.run_dir,
            runner.artifacts,
            runner.logger,
            decisions,
            mode_handler.evaluator,
            workspace_root=runner.cwd,
            run_started_at=runner._workspace_baseline_at,
        )
    else:
        runner.logger.log("finalization_blocked", reason="guardrails_policy", missing=missing)

    summary = runner._build_summary(mode_handler.agents, mode_handler.evaluator, decisions)
    final_report_rel = os.path.join("artifacts", "finalization", "report.json")
    if os.path.exists(os.path.join(runner.run_dir, final_report_rel)):
        summary["finalization_report"] = final_report_rel
    else:
        summary["finalization_report"] = None
        summary["finalization_blocked"] = True
        summary["finalization_missing"] = missing
    runner.artifacts.add_text("run_summary.json", json.dumps(summary, indent=2), tags=["summary"])
    runner.logger.log("run_complete", summary_path=os.path.join(runner.run_dir, "artifacts", "run_summary.json"))
    tp = None
    try:
        tp = generate_run_transcript(runner.run_dir)
        runner.logger.log("transcript_generated", path=tp)
    except OSError as e:
        runner.logger.log("transcript_error", error=str(e))

    return {
        "artifact_dir": os.path.join(runner.run_dir, "artifacts"),
        "log_path": runner.logger.path(),
        "run_id": runner.run_id,
        "workspace_dir": runner.workspace_root,
        "summary_path": os.path.join(runner.run_dir, "artifacts", "run_summary.json"),
        "transcript_path": tp or os.path.join(runner.run_dir, "transcript.md"),
    }
