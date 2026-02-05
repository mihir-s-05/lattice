from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict
from typing import Any, Dict, List

from ..command_utils import (
    rewrite_leading_cd,
    shell_exec_args,
    should_use_shell,
    split_command_args,
    validate_command_for_run,
)
from . import ToolContext, ToolRegistry


@ToolRegistry.register("run_contract_tests")
def run_contract_tests(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    evaluator = ctx.evaluator
    gates = ctx.gates
    contract_runner = ctx.contract_runner

    tests = [str(t) for t in (args.get("tests") or [])]
    specs = contract_runner.scan_specs()
    available_ids = [s.get("id") for s in specs if isinstance(s, dict) and s.get("id")]
    selected_specs = specs
    if tests:
        want = set(tests)
        selected_specs = [s for s in specs if (s.get("id") in want)]

    results: List[Any] = []
    want_api_contract = (not tests) or ("api_contract" in tests)
    if want_api_contract:
        results.extend(contract_runner.scan_and_run(allow_commands=False, command_validator=None))
        if tests:
            want = set(tests)
            results = [r for r in results if (r.id in want) or (r.id == "api_contract")]

    if not specs and not results:
        results = contract_runner.scan_and_run(allow_commands=False, command_validator=None)
        if tests:
            want = set(tests)
            results = [r for r in results if r.id in want]
    elif specs:
        extra = (
            contract_runner.run_specs(selected_specs, allow_commands=True, command_validator=(lambda c: validate_command_for_run(c, runner.cfg)))
            if selected_specs
            else []
        )
        results.extend(extra)

    if not results:
        runner.logger.log("contract_tests_missing", requested=tests, available=available_ids)
        ctx.messages.append(
            {
                "role": "system",
                "content": "Guardrail: run_contract_tests found no matching tests. If you haven't created contracts/tests/*.json yet, write contracts/openapi.yaml first and retry (auto schema check will run), or generate explicit contract tests via the tests agent.",
            }
        )
        obs = {
            "error": "no_contract_tests",
            "note": "No contract tests matched. Create a contract test manifest in contracts/tests/*.json or ensure an OpenAPI spec exists for auto schema validation.",
            "requested_tests": tests,
            "available_tests": available_ids,
        }
    else:
        gate_results = evaluator.evaluate(gates)
        runner._latest_gate_results = gate_results
        obs = {"results": [asdict(r) for r in results], "created_tests": False}

    runner._sync_checklist_state()
    runner._save_checklist()
    return obs


@ToolRegistry.register("run_command")
def run_command(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner

    cmd = args.get("command") or ""
    reason = args.get("reason") or ""
    cwd = args.get("cwd")
    timeout_sec = int(args.get("timeout_sec") or 120)
    if not str(reason).strip():
        raise ValueError("reason is required for run_command")

    runner.logger.log(
        "command_request",
        requester="router",
        tool_call_id=ctx.tool_call_id,
        command=cmd,
        cwd=cwd or runner.cwd,
        reason=reason,
    )
    if not cwd:
        cd_cwd, rewritten = rewrite_leading_cd(cmd, resolve_path=runner._resolve_workspace_path)
        if cd_cwd:
            cwd = cd_cwd
            cmd = rewritten

    deny_reason = validate_command_for_run(cmd, runner.cfg)
    if deny_reason:
        runner.logger.log(
            "command_blocked",
            requester="router",
            tool_call_id=ctx.tool_call_id,
            command=cmd,
            reason=deny_reason,
        )
        return {"error": deny_reason}

    run_cwd = runner._resolve_workspace_path(cwd) if cwd else runner.cwd
    if not os.path.isdir(run_cwd):
        runner.logger.log(
            "command_failed",
            requester="router",
            tool_call_id=ctx.tool_call_id,
            command=cmd,
            cwd=run_cwd,
            error="cwd_not_found",
        )
        return {"error": f"cwd does not exist: {run_cwd}"}

    use_shell = should_use_shell(cmd)
    env = None
    if "pytest" in str(cmd or "").lower():
        env = dict(os.environ)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    try:
        run_args = cmd if use_shell else split_command_args(cmd)
    except ValueError:
        use_shell = True
        run_args = cmd

    try:
        proc = subprocess.run(
            run_args,
            shell=use_shell,
            cwd=run_cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
    except FileNotFoundError:
        proc = subprocess.run(
            shell_exec_args(cmd),
            shell=False,
            cwd=run_cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )

    runner.logger.log(
        "command_executed",
        requester="router",
        tool_call_id=ctx.tool_call_id,
        command=cmd,
        cwd=run_cwd,
        returncode=proc.returncode,
    )
    obs: Dict[str, Any] = {
        "command": cmd,
        "cwd": run_cwd,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-4000:],
    }

    cmd_l = str(cmd or "").lower()
    if "pytest" in cmd_l or "smoke" in cmd_l:
        status = "passed" if proc.returncode == 0 else "failed"
        payload = {
            "id": "smoke_suite",
            "status": status,
            "metrics": {"test_type": "command", "command": cmd},
            "evidence": [
                {"message": "stdout_tail", "text": (proc.stdout or "")[-1500:]},
                {"message": "stderr_tail", "text": (proc.stderr or "")[-1500:]},
            ],
        }
        try:
            runner.artifacts.add_text(
                os.path.join("contracts", "results", "smoke_suite.json"),
                json.dumps(payload, ensure_ascii=False, indent=2),
                tags=["contracts", "result"],
                meta={"kind": "ContractTestResult", "id": "smoke_suite", "source": "run_command"},
            )
            if "pytest" in cmd_l and ("test_api_contract.py" in cmd_l or "contract/test_api_contract.py" in cmd_l):
                payload2 = {
                    "id": "api_contract",
                    "status": status,
                    "metrics": {"test_type": "schema", "source": "pytest", "command": cmd},
                    "evidence": payload["evidence"],
                }
                runner.artifacts.add_text(
                    os.path.join("contracts", "results", "api_contract.json"),
                    json.dumps(payload2, ensure_ascii=False, indent=2),
                    tags=["contracts", "result"],
                    meta={"kind": "ContractTestResult", "id": "api_contract", "source": "run_command"},
                )
        except OSError as e:
            runner.logger.log("command_test_result_write_failed", error=str(e))

    return obs
