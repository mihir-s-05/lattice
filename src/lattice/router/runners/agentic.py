from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ...agents import AgentRegistry, BackendAgent, FrontendAgent, LLMApiAgent, TestAgent, ToolboxAgent
from ...contracts import ContractRunner
from ...errors import ProviderError
from ...finalize import run_finalization
from ...huddle import DecisionSummary
from ...plan import PlanGraph, PlanNode
from ...router.handlers import ToolContext, ToolRegistry
from ...router_llm import RouterLLM
from ...stage_gates import GateEvaluator, StageGate
from ...transcript import RunningTranscript, generate_run_transcript

if TYPE_CHECKING:
    from ...router_main import RouterRunner


def run_agentic(runner: "RouterRunner", goal: str) -> Dict[str, Any]:
    runner._ensure_initial_checklist(goal)

    from ...worker import WorkerRunner

    WorkerRunner(runner.cwd, runner.run_id)._pre_ingest_repo_files()

    transcript = RunningTranscript(runner.run_id)
    rllm = RouterLLM(runner.cfg, runner.logger, tools=runner._build_tools_manifest())

    plan_graph = PlanGraph()
    plan_graph.mode_by_segment = {"main": runner.mode}

    stage_order: List[str] = ["contracts", "backend_scaffold", "frontend_scaffold", "smoke_tests"]
    try:
        init = rllm.plan_init(goal)
    except ProviderError as e:
        runner.logger.log("router_plan_init_failed", error=str(e))
        init = None
    plan_txt = (init or {}).get("text") or ""
    plan_obj = runner._parse_json_object(plan_txt)
    if isinstance(plan_obj, dict):
        rec_mode = str(plan_obj.get("mode") or "").strip().lower()
        if rec_mode in ("ladder", "tracks"):
            prev = runner.mode
            runner.mode = rec_mode
            plan_graph.mode_by_segment = {"main": runner.mode}
            runner.logger.log("mode_decision", previous=prev, current=runner.mode, reason=str(plan_obj.get("mode_reason") or "plan_init"))
        stages = plan_obj.get("stages")
        if isinstance(stages, list) and stages:
            ids: List[str] = []
            for st in stages:
                if isinstance(st, dict) and st.get("id"):
                    sid = str(st.get("id")).strip()
                    if sid:
                        ids.append(sid)
                    chk = st.get("checkin")
                    if isinstance(chk, dict) and sid:
                        when = str(chk.get("when") or "").strip().lower()
                        if when in ("after", "on_blocked", "both"):
                            runner._checkins_by_stage[sid] = {"when": when, "topic": chk.get("topic") or ""}
            if ids:
                stage_order = ids
    try:
        runner.artifacts.add_text(os.path.join("plans", "router_plan.json"), json.dumps(plan_obj, indent=2), tags=["plan", "router"])
    except OSError as e:
        runner.logger.log("router_plan_write_failed", error=str(e))
    runner._state.set_stage_order(list(stage_order))

    plan_graph.nodes = []
    for sid in stage_order[:10]:
        plan_graph.add_node(PlanNode(id=f"n_{sid}", name=sid, modeSegment="main"))
    for a, b in zip(stage_order, stage_order[1:]):
        plan_graph.add_edge(f"n_{a}", f"n_{b}")

    gates: List[StageGate] = [
        StageGate(id="sg_api_contract", name="API contract passes", conditions=["tests.pass('api_contract')"]),
        StageGate(id="sg_be_scaffold", name="Backend scaffold present", conditions=["tests.pass('api_contract') and artifact.exists('backend/**')"]),
        StageGate(id="sg_fe_scaffold", name="Frontend scaffold present", conditions=["artifact.exists('frontend/**') or artifact.exists('public/index.html')"]),
        StageGate(id="sg_smoke", name="Smoke tests pass", conditions=["tests.pass('smoke_suite')"]),
    ]
    evaluator = GateEvaluator(
        runner.run_dir,
        runner.artifacts,
        runner.logger,
        workspace_root=runner.cwd,
        run_started_at=runner._workspace_baseline_at,
    )
    contract_runner = ContractRunner(
        runner.run_dir,
        runner.logger,
        workspace_root=runner.cwd,
        run_started_at=runner._workspace_baseline_at,
    )

    agents: Dict[str, Any] = {}
    def ensure_agent(role: str):
        r = str(role or "").replace("agent:", "").strip()
        if not r:
            return None
        if r in agents:
            return agents[r]
        if r == "frontend":
            agents[r] = FrontendAgent("frontend", runner.cfg, runner.logger, runner.artifacts, runner.rag, workspace_root=runner.cwd, codebase_root=runner.cwd)
            return agents[r]
        if r == "backend":
            agents[r] = BackendAgent("backend", runner.cfg, runner.logger, runner.artifacts, runner.rag, workspace_root=runner.cwd, codebase_root=runner.cwd)
            return agents[r]
        if r == "llmapi":
            agents[r] = LLMApiAgent("llmapi", runner.cfg, runner.logger, runner.artifacts, runner.rag, workspace_root=runner.cwd, codebase_root=runner.cwd)
            return agents[r]
        if r == "tests":
            agents[r] = TestAgent("tests", runner.cfg, runner.logger, runner.artifacts, runner.rag, workspace_root=runner.cwd, codebase_root=runner.cwd)
            return agents[r]
        variant_id = r if r.startswith("toolbox/") else f"toolbox/{r}"
        spec = AgentRegistry(codebase_root=runner.cwd).materialize_toolbox_variant(variant_id)
        if spec is None:
            return None
        agents[r] = ToolboxAgent(r, runner.cfg, runner.logger, runner.artifacts, runner.rag, workspace_root=runner.cwd, codebase_root=runner.cwd, spec=spec)
        return agents[r]

    def get_agent(role: str):
        r = str(role or "").replace("agent:", "").strip()
        return agents.get(r)

    decisions: List[DecisionSummary] = []
    injected_by_target: Dict[str, List[str]] = {}
    unread_huddles: List[Dict[str, Any]] = []
    current_step: str = stage_order[0] if stage_order else "contracts"
    runner._state.set_current_step(current_step)

    tools = runner._build_tools_manifest()

    system_msg = {"role": "system", "content": runner._router_system_prompt()}
    init_state = runner._snapshot_state(plan_graph, evaluator, decisions, unread_huddles, tools)
    user_msg = {
        "role": "user",
        "content": (
            "Goal: " + (goal or "") + "\n\n"
            + "State: " + json.dumps(init_state, ensure_ascii=False) + "\n\n"
            + "Manager plan: stages=" + json.dumps(stage_order) + "; current_step=" + str(current_step) + "; mode=" + str(runner.mode) + "\n"
            + "Instruction: Use ladder-style milestone progression via propose_advance_step, but feel free to run tracks (schedule_slice with multiple agents) within a stage when helpful. Use open_huddle as manager check-ins when blocked or after major slices to decide whether to adapt mode/plan."
        ),
    }
    messages: List[Dict[str, Any]] = [system_msg, user_msg]

    finalized = False
    max_steps = getattr(runner.cfg, "router_max_steps", 32) or 32
    step_idx = 0
    no_tool_streak = 0

    while step_idx < max_steps and not finalized:
        step_idx += 1
        runner._state.set_current_step(current_step)
        out = rllm._call_with_tools(messages, tools=tools, phase="agentic", tool_choice="auto")
        tool_calls = out.get("tool_calls") or []
        transcript.add_model_call(
            title=f"Router Agentic Turn #{step_idx}",
            provider=out.get("provider") or "?",
            model=out.get("model") or "?",
            messages=messages[-6:],
            output=out.get("text"),
            tools_offered=tools,
            tool_choice="auto",
            tool_calls=tool_calls,
        )

        if not tool_calls:
            no_tool_streak += 1
            if no_tool_streak >= 2:
                messages.append({"role": "system", "content": "Reminder: You must act via tools. Pick exactly one tool now."})
            if out.get("api") == "responses" and out.get("prev_response_id_enabled"):
                messages.append({"role": "assistant", "content": out.get("text"), "_skip_for_responses": True})
            else:
                messages.append({"role": "assistant", "content": out.get("text")})
            continue
        no_tool_streak = 0

        if out.get("api") == "responses" and out.get("prev_response_id_enabled"):
            messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls, "_skip_for_responses": True})
        else:
            messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})

        for tc in tool_calls:
            tool_name = (tc or {}).get("function", {}).get("name") or ""
            tool_args_s = (tc or {}).get("function", {}).get("arguments") or "{}"
            try:
                tool_args = json.loads(tool_args_s)
            except json.JSONDecodeError:
                tool_args = {}

            def _finish_tool(_obs: Dict[str, Any], _err: Optional[str]) -> None:
                runner.logger.log(
                    "router_tool_call",
                    tool_name=tool_name,
                    params=tool_args,
                    observation=(_obs if len(str(_obs)) < 5000 else {"note": "obs too large"}),
                    error=_err,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": (tc.get("id") if isinstance(tc, dict) else None),
                        "name": tool_name,
                        "content": json.dumps(_obs, ensure_ascii=False),
                    }
                )

            obs: Dict[str, Any] = {}
            err: Optional[str] = None
            try:
                if not runner._checklist_manager.created and tool_name not in ("create_checklist", "set_mode"):
                    obs = {"error": "checklist_required", "note": "Call create_checklist before other tools."}
                    runner.logger.log("checklist_required", tool=tool_name)
                    messages.append({"role": "system", "content": "Guardrail: create_checklist must be called before other tools."})
                    _finish_tool(obs, None)
                    continue
                ctx_obj = ToolContext(
                    runner=runner,
                    tool_call_id=(tc.get("id") if isinstance(tc, dict) else None),
                    goal=goal,
                    messages=messages,
                    transcript=transcript,
                    agents=agents,
                    ensure_agent=ensure_agent,
                    get_agent=get_agent,
                    decisions=decisions,
                    injected_by_target=injected_by_target,
                    evaluator=evaluator,
                    contract_runner=contract_runner,
                    gates=gates,
                    plan_graph=plan_graph,
                    unread_huddles=unread_huddles,
                    current_step=current_step,
                    stage_order=stage_order,
                )
                obs = ToolRegistry.execute(tool_name, ctx_obj, tool_args)
                if isinstance(obs, dict) and obs.get("next_step"):
                    current_step = str(obs.get("next_step"))
                    runner._state.set_current_step(current_step)
                if isinstance(obs, dict) and obs.get("finalized"):
                    finalized = True
                stage_order = list(runner._state.stage_order) if runner._state.stage_order else stage_order
            except Exception as e:
                err = str(e)
                obs = {"error": err}
            _finish_tool(obs, err)

        if step_idx % 3 == 0 or finalized:
            snap = runner._snapshot_state(plan_graph, evaluator, decisions, unread_huddles, tools)
            messages.append({"role": "system", "content": "State update: " + json.dumps(snap, ensure_ascii=False)})

    if not finalized:
        allowed, missing = runner._finalization_allowed(evaluator, runner._finalization_gates(gates))
        if allowed:
            run_finalization(
                runner.run_dir,
                runner.artifacts,
                runner.logger,
                decisions,
                evaluator,
                workspace_root=runner.cwd,
                run_started_at=runner._workspace_baseline_at,
            )
            runner.logger.log("router_finalize_auto", reason="budget_exhausted", steps=step_idx)
        else:
            runner.logger.log("finalization_blocked", reason="guardrails_auto", missing=missing)

    try:
        plan_graph.save(runner.run_dir)
    except OSError as e:
        runner.logger.log("plan_graph_save_failed", error=str(e))

    try:
        snap_path = os.path.join(runner.run_dir, "artifacts", "plans", "snapshot.json")
        os.makedirs(os.path.dirname(snap_path), exist_ok=True)
        if not os.path.exists(snap_path):
            with open(snap_path, "w", encoding="utf-8") as f:
                json.dump([], f)
    except OSError as e:
        runner.logger.log("plan_snapshot_init_failed", error=str(e))

    try:
        transcript_md = transcript.render_markdown()
        runner.artifacts.add_text(
            "transcript.md",
            transcript_md,
            tags=["transcript", "router"],
            meta={},
        )
    except OSError as e:
        runner.logger.log("transcript_write_failed", error=str(e))

    summary = runner._build_summary(agents or {}, evaluator, decisions)
    if runner._web_disabled_by_flag:
        summary["web_search"] = "disabled_by_flag"
    final_report_rel = os.path.join("artifacts", "finalization", "report.json")
    if os.path.exists(os.path.join(runner.run_dir, final_report_rel)):
        summary["finalization_report"] = final_report_rel
    else:
        summary["finalization_report"] = None
        allowed, missing = runner._finalization_allowed(evaluator, runner._finalization_gates(gates))
        if not allowed:
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
