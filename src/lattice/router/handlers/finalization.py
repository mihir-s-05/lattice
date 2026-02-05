from __future__ import annotations

import os
from typing import Any, Dict

from ...finalize import run_finalization
from . import ToolContext, ToolRegistry


@ToolRegistry.register("finalize_run")
def finalize_run(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    allowed, missing = runner._finalization_allowed(ctx.evaluator, runner._finalization_gates(ctx.gates))
    if not allowed:
        runner.logger.log("finalization_blocked", reason="guardrails", missing=missing)
        ctx.messages.append({"role": "system", "content": "Guardrail: finalize_run blocked. Complete required steps and retry."})
        return {"error": "finalization_blocked", "missing": missing, "finalized": False}

    report = run_finalization(
        runner.run_dir,
        runner.artifacts,
        runner.logger,
        ctx.decisions,
        ctx.evaluator,
        workspace_root=runner.cwd,
        run_started_at=runner._workspace_baseline_at,
    )
    deliverables = report.get("deliverables", [None])[0]
    return {"deliverables": deliverables, "report": os.path.join("artifacts", "finalization", "report.json"), "finalized": True}
