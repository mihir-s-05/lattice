from __future__ import annotations

from typing import List, Optional, Tuple

from ..checklist import Checklist
from ..stage_gates import GateEvaluator, StageGate


def finalization_allowed(
    checklist: Checklist,
    checklist_created: bool,
    evaluator: Optional[GateEvaluator],
    gates: Optional[List[StageGate]],
) -> Tuple[bool, List[str]]:
    missing: List[str] = []
    if not checklist_created:
        missing.append("checklist not created; call create_checklist first")
    if not checklist.items:
        missing.append("checklist has no items")
    if not checklist.is_complete():
        missing.append("checklist incomplete: " + ", ".join(checklist.required_incomplete()))
    if evaluator is not None and gates:
        try:
            gres = evaluator.evaluate(gates)
            failed = [g.id for g in gres if g.status != "passed"]
            if failed:
                missing.append("stage gates not passed: " + ", ".join(failed))
        except Exception as e:
            missing.append(f"stage gates could not be evaluated: {e}")
    return (len(missing) == 0, missing)


def finalization_gates(gates: List[StageGate], stage_order: List[str]) -> List[StageGate]:
    order = [str(s).strip().lower() for s in (stage_order or []) if str(s).strip()]
    want: set[str] = set()
    if "contracts" in order:
        want.add("sg_api_contract")
    if "backend_scaffold" in order:
        want.update({"sg_api_contract", "sg_be_scaffold"})
    if "frontend_scaffold" in order:
        want.add("sg_fe_scaffold")
    if "smoke_tests" in order:
        want.add("sg_smoke")
    if not want:
        return []
    return [g for g in (gates or []) if g.id in want]

