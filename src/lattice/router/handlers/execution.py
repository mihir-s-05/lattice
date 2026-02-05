from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List

from ...coherence import run_coherence_checks
from ...errors import ProviderError
from ..command_utils import validate_command_for_run
from . import ToolContext, ToolRegistry


@ToolRegistry.register("schedule_slice")
def schedule_slice(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    evaluator = ctx.evaluator
    gates = ctx.gates
    contract_runner = ctx.contract_runner

    actives = [str(x) for x in (args.get("active_agents") or [])]
    parallel = bool(args.get("parallel")) or (runner.mode == "tracks")
    timeout_raw = args.get("timeout_sec")
    slice_timeout_sec = int(timeout_raw) if isinstance(timeout_raw, (int, float)) else 300
    slice_timeout_sec = max(5, min(1800, slice_timeout_sec))

    artifacts_written: List[str] = []
    artifact_refs_out: List[Dict[str, Any]] = []
    reports: List[Dict[str, Any]] = []
    errors: List[str] = []
    auto_added: List[str] = []
    skipped_agents: List[str] = []

    if not ctx.agents:
        runner.logger.log("slice_blocked", reason="agents_not_spawned")
        ctx.messages.append({"role": "system", "content": "Guardrail: schedule_slice requires spawn_agents first. Spawn agents and retry."})
        return {"error": "schedule_slice_requires_agents", "note": "Call spawn_agents before schedule_slice."}

    if len(actives) > runner._max_slice_agents:
        skipped_agents = actives[runner._max_slice_agents :]
        actives = actives[: runner._max_slice_agents]
        runner.logger.log("slice_limit", max_agents=runner._max_slice_agents, skipped=skipped_agents)

    def _run_one(an: str) -> Dict[str, Any]:
        role = an.replace("agent:", "").strip()
        ag = ctx.get_agent(role)
        if ag is None:
            return {"agent": an, "error": f"agent not spawned: {an}", "artifacts": [], "report": None, "needs_huddle": False}

        if runner.mode == "tracks":
            pol = runner._agent_permissions.get(role)
            if isinstance(pol, dict):
                ag.set_write_policy(allow_globs=pol.get("allow_globs"), deny_globs=pol.get("deny_globs"))

        act_ctx = runner._agent_context(ctx.goal, ctx.decisions)
        plan_err = None
        act_err = None
        refs = []
        hud_reqs: List[Dict[str, Any]] = []
        try:
            _ = ag.plan(ctx.current_step, act_ctx)
        except ProviderError as e:
            plan_err = str(e)
        try:
            refs = ag.act(act_ctx)
        except ProviderError as e:
            act_err = str(e)
        if hasattr(ag, "drain_huddle_requests"):
            hud_reqs = list(getattr(ag, "drain_huddle_requests")() or [])
        rep = asdict(ag.report())
        nh = False
        if hasattr(ag, "needs_huddle") and ag.needs_huddle(act_ctx):
            nh = True
        return {
            "agent": an,
            "role": role,
            "plan_error": plan_err,
            "act_error": act_err,
            "artifacts": [r.path for r in refs],
            "report": rep,
            "needs_huddle": nh,
            "huddle_requests": hud_reqs,
            "refs": refs,
        }

    results_one: List[Dict[str, Any]] = []
    import concurrent.futures

    max_workers = len(actives) if (parallel and len(actives) > 1) else 1
    timed_out = False
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    fut_map: Dict[concurrent.futures.Future, str] = {}
    try:
        for an in actives:
            fut_map[ex.submit(_run_one, an)] = an
        done, not_done = concurrent.futures.wait(set(fut_map.keys()), timeout=slice_timeout_sec)
        for fut in done:
            try:
                results_one.append(fut.result())
            except Exception as e:
                results_one.append({"agent": fut_map.get(fut, "unknown"), "error": str(e), "artifacts": [], "report": None, "needs_huddle": False})
        if not_done:
            timed_out = True
            for fut in not_done:
                an = fut_map.get(fut, "unknown")
                fut.cancel()
                results_one.append({"agent": an, "error": f"slice_timeout_after_{slice_timeout_sec}s", "artifacts": [], "report": None, "needs_huddle": False})
            runner.logger.log("slice_timeout", timeout_sec=slice_timeout_sec, agents=[fut_map.get(f, "unknown") for f in not_done])
    finally:
        try:
            ex.shutdown(wait=(not timed_out), cancel_futures=timed_out)
        except TypeError:
            ex.shutdown(wait=(not timed_out))

    for rr in results_one:
        if rr.get("error"):
            errors.append(str(rr.get("error")))
            continue
        if rr.get("plan_error"):
            errors.append(f"plan error {rr.get('agent')}: {rr.get('plan_error')}")
        if rr.get("act_error"):
            errors.append(f"act error {rr.get('agent')}: {rr.get('act_error')}")
        arts = rr.get("artifacts") or []
        artifacts_written.extend([str(p) for p in arts])
        refs = rr.get("refs") or []
        for ref in refs:
            artifact_refs_out.append(
                {
                    "agent": str(rr.get("role") or rr.get("agent") or "?"),
                    "path": getattr(ref, "path", None),
                    "sha256": getattr(ref, "sha256", None),
                    "tags": getattr(ref, "tags", None),
                    "meta": getattr(ref, "meta", None),
                }
            )
            runner.logger.log(
                "slice_artifact",
                agent=str(rr.get("role") or rr.get("agent") or "?"),
                path=getattr(ref, "path", None),
                sha256=getattr(ref, "sha256", None),
                tags=getattr(ref, "tags", None),
                meta=getattr(ref, "meta", None),
            )
        for p in arts:
            runner._record_touched(p)
        role = rr.get("role") or rr.get("agent") or "?"
        runner.logger.log("agent_turn", agent=str(role), artifacts=list(arts))
        if rr.get("report"):
            reports.append(rr.get("report"))
        if rr.get("needs_huddle"):
            ctx.unread_huddles.append({"from": rr.get("agent"), "topic": runner._huddle_topic(ctx.goal)})
        for rq in (rr.get("huddle_requests") or []):
            if isinstance(rq, dict) and rq.get("topic"):
                ctx.unread_huddles.append(rq)

    try:
        _ = contract_runner.scan_and_run(allow_commands=True, command_validator=(lambda c: validate_command_for_run(c, runner.cfg)))
        gate_results = evaluator.evaluate(gates)
        runner._latest_gate_results = gate_results
    except Exception as e:
        errors.append(f"contract_runner_error: {e}")

    if errors:
        ctx.messages.append({"role": "system", "content": "Guardrail: schedule_slice returned errors. Fix missing agents or failures, then retry."})
        obs = {
            "error": "schedule_slice_errors",
            "errors": errors,
            "partial": {
                "artifacts_written": artifacts_written,
                "reports": reports,
                "auto_added": auto_added,
                "skipped_agents": skipped_agents,
            },
        }
    else:
        obs = {
            "artifacts_written": artifacts_written,
            "artifact_refs": artifact_refs_out[-200:],
            "reports": reports,
            "errors": errors,
            "auto_added": auto_added,
            "skipped_agents": skipped_agents,
        }
        try:
            findings = run_coherence_checks(
                runner.cwd,
                paths=sorted(list(runner._touched_files)) if runner._touched_files else None,
                changed_since=(runner._workspace_baseline_at or runner._run_started_at),
            )
            highs = [f for f in findings if isinstance(f, dict) and f.get("severity") == "high"]
            if highs:
                for f in highs[:3]:
                    ctx.unread_huddles.append(
                        {
                            "from": "coherence",
                            "topic": f"Coherence issue: {f.get('id')}",
                            "questions": [str(f.get("message") or "")],
                            "attendees": f.get("suggested_attendees"),
                            "urgency": "high",
                        }
                    )
                runner.logger.log("coherence_issues", findings=highs[:10])
                ctx.messages.append(
                    {
                        "role": "system",
                        "content": "Coherence checks found high-severity issues. Consider open_huddle to resolve: " + json.dumps([h.get("id") for h in highs[:3]]),
                    }
                )
        except OSError as e:
            runner.logger.log("coherence_checks_failed", error=str(e))
        failing = [g.id for g in (runner._latest_gate_results or []) if g.status != "passed"]
        if failing or ctx.unread_huddles:
            ctx.messages.append(
                {
                    "role": "system",
                    "content": "Manager note: consider open_huddle to adapt plan/mode. failing_gates=" + json.dumps(failing) + " unread_huddles=" + json.dumps(ctx.unread_huddles[-3:]),
                }
            )

    runner._sync_checklist_state()
    runner._save_checklist()
    return obs
