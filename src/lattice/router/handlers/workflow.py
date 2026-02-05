from __future__ import annotations

from typing import Any, Dict, List

from . import ToolContext, ToolRegistry


@ToolRegistry.register("set_mode")
def set_mode(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    target = str(args.get("target_mode") or "").strip().lower()
    reason = args.get("reason") or ""
    applied = target in ("ladder", "tracks", "weave")
    if not applied:
        return {"applied": False, "current_mode": runner.mode, "note": "invalid target_mode"}

    prev = runner.mode
    runner.mode = target
    ctx.plan_graph.mode_by_segment = ({"critical": "ladder", "docs": "tracks"} if runner.mode == "weave" else {"main": runner.mode})
    runner.logger.log("mode_decision", previous=prev, current=runner.mode, reason=reason)
    ctx.transcript.add_info("Router Decision", f"Mode set to {runner.mode}. Reason: {reason}")
    return {"applied": True, "current_mode": runner.mode, "note": "mode updated"}


@ToolRegistry.register("create_checklist")
def create_checklist(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    items = args.get("items") or []
    if not isinstance(items, list):
        items = []
    created = runner._checklist_manager.replace_items(items)
    runner._save_checklist()
    runner.logger.log("checklist_created", items=created)
    ctx.transcript.add_info("Router Decision", f"Created checklist with {len(created)} items.")
    return {"created": created, "checklist": [i.to_dict() for i in runner._checklist.items]}


@ToolRegistry.register("update_checklist")
def update_checklist(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    if not runner._checklist_manager.created:
        runner.logger.log("checklist_update_blocked", reason="not_created")
        ctx.messages.append({"role": "system", "content": "Guardrail: create_checklist must be called before update_checklist."})
        return {"error": "checklist_not_created", "note": "Call create_checklist first."}
    updates = args.get("items") or []
    changed = runner._checklist_manager.update_items(updates if isinstance(updates, list) else [], allow_create=True)
    runner._save_checklist()
    ctx.transcript.add_info("Router Decision", f"Checklist updated: {', '.join(changed) if changed else 'no changes'}")
    return {"updated": changed, "checklist": [i.to_dict() for i in runner._checklist.items]}


@ToolRegistry.register("propose_advance_step")
def propose_advance_step(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    evaluator = ctx.evaluator
    contract_runner = ctx.contract_runner
    gates = ctx.gates

    current_step = ctx.current_step
    stage_order = ctx.stage_order

    step_id_raw = args.get("step_id") or current_step
    gate_to_step = {
        "sg_api_contract": "contracts",
        "sg_be_scaffold": "backend_scaffold",
        "sg_fe_scaffold": "frontend_scaffold",
        "sg_smoke": "smoke_tests",
    }
    requested_step_id = gate_to_step.get(step_id_raw, step_id_raw)

    def _canonical_step_for_gates(step: str) -> str:
        s = (step or "").strip().lower()
        if s in ("contracts", "backend_scaffold", "frontend_scaffold", "smoke_tests"):
            return s
        if any(k in s for k in ("contract", "plan", "arch", "design", "spec")):
            return "contracts"
        if "front" in s or "ui" in s or "web" in s:
            return "frontend_scaffold"
        if "back" in s or "api" in s or "server" in s:
            return "backend_scaffold"
        if "test" in s or "smoke" in s or "deploy" in s or "qa" in s:
            return "smoke_tests"
        return ""

    step_id_for_gates = _canonical_step_for_gates(str(requested_step_id))
    valid_steps = list(stage_order) if isinstance(stage_order, list) and stage_order else ["contracts", "backend_scaffold", "frontend_scaffold", "smoke_tests"]
    if requested_step_id not in valid_steps:
        runner.logger.log("advance_rejected", reason="unknown_step", requested=step_id_raw)
        return {"advanced": False, "error": "unknown_step", "step_id": step_id_raw}

    def gates_for(step: str) -> List[Any]:
        if step == "contracts":
            return [g for g in gates if g.id == "sg_api_contract"]
        if step == "backend_scaffold":
            return [g for g in gates if g.id in ("sg_api_contract", "sg_be_scaffold")]
        if step == "frontend_scaffold":
            return [g for g in gates if g.id in ("sg_fe_scaffold",)]
        if step == "smoke_tests":
            return [g for g in gates if g.id in ("sg_smoke", "sg_fe_scaffold", "sg_be_scaffold", "sg_api_contract")]
        return []

    contract_runner.scan_and_run(allow_commands=False, command_validator=None)
    gres = evaluator.evaluate(gates_for(step_id_for_gates))
    if all(g.status == "passed" for g in gres):
        chk = runner._checkins_by_stage.get(requested_step_id) or {}
        when = str(chk.get("when") or "").strip().lower()
        if when in ("after", "both"):
            ctopic = (chk.get("topic") or "").strip() or f"Check-in after {requested_step_id}"
            ctx.unread_huddles.append({"from": "scheduled", "topic": ctopic, "questions": [f"Check-in after stage {requested_step_id}: status, blockers, mode/plan changes?"]})
            runner.logger.log("huddle_checkin_scheduled", stage=requested_step_id, when=when, topic=ctopic)

        order = valid_steps
        try:
            idx = order.index(requested_step_id)
            next_step = order[min(idx + 1, len(order) - 1)]
        except ValueError:
            next_step = requested_step_id

        runner._state.set_current_step(next_step)
        if requested_step_id in runner._gate_failures:
            runner._gate_failures.pop(requested_step_id, None)
        return {"advanced": True, "next_step": next_step, "order": order, "gates_step": step_id_for_gates}

    import time as _t

    now = _t.time()
    cnt, last = runner._gate_failures.get(requested_step_id, (0, 0.0))
    cnt = cnt + 1
    runner._gate_failures[requested_step_id] = (cnt, now)
    cooldown_active = (cnt >= runner._cooldown_threshold) and ((now - last) <= 60.0)
    retry_after_ms = int(runner._cooldown_seconds * 1000) if cooldown_active else 0
    if cooldown_active:
        runner.logger.log("router_cooldown", step=requested_step_id, failures=cnt, retry_after_ms=retry_after_ms)

    chk = runner._checkins_by_stage.get(requested_step_id) or {}
    when = str(chk.get("when") or "").strip().lower()
    if when in ("on_blocked", "both"):
        ctopic = (chk.get("topic") or "").strip() or f"Check-in blocked at {requested_step_id}"
        ctx.unread_huddles.append({"from": "scheduled", "topic": ctopic, "questions": [f"Blocked at stage {requested_step_id}: resolve failing gates and decide mode/plan changes."]})
        runner.logger.log("huddle_checkin_scheduled", stage=requested_step_id, when=when, topic=ctopic)

    return {
        "advanced": False,
        "failed_gates": [{"id": g.id, "status": g.status, "evidence": g.evidence} for g in gres if g.status != "passed"],
        "cooldown_active": cooldown_active,
        "retry_after_ms": retry_after_ms,
        "gates_step": step_id_for_gates,
    }

