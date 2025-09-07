from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from .runlog import RunLogger


@dataclass
class ContractTestResult:
    id: str
    status: str  # passed|failed
    metrics: Dict[str, Any]
    evidence: List[Dict[str, Any]]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _read_json_or_list(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = f.read()
    obj = json.loads(data)
    if isinstance(obj, list):
        return [o for o in obj if isinstance(o, dict)]
    if isinstance(obj, dict):
        return [obj]
    return []


def _validate_openapi_rough(text: str) -> Dict[str, Any]:
    score = 0
    has_openapi = bool(re.search(r"^\s*(openapi|swagger)\s*:\s*", text, re.IGNORECASE | re.MULTILINE))
    has_paths = bool(re.search(r"^\s*paths\s*:\s*", text, re.IGNORECASE | re.MULTILINE))
    if has_openapi:
        score += 1
    if has_paths:
        score += 1
    components = bool(re.search(r"^\s*components\s*:\s*", text, re.IGNORECASE | re.MULTILINE))
    if components:
        score += 1
    return {"schema_valid": has_openapi and has_paths, "score": score}


class ContractRunner:
    def __init__(self, run_dir: str, logger: RunLogger) -> None:
        self.run_dir = run_dir
        self.logger = logger
        self.results_dir = os.path.join(run_dir, "artifacts", "contracts", "results")
        os.makedirs(self.results_dir, exist_ok=True)

    def _save_result(self, result: ContractTestResult) -> str:
        path = os.path.join(self.results_dir, f"{result.id}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(result.to_json())
        self.logger.log("contract_test_result", id=result.id, status=result.status, path=path)
        return path

    def run_test(self, spec: Dict[str, Any]) -> ContractTestResult:
        tid = spec.get("id") or "contract_test"
        ttype = (spec.get("type") or "").lower()
        spec_path = spec.get("spec_path")
        metrics: Dict[str, Any] = {}
        evidence: List[Dict[str, Any]] = []
        status = "failed"

        try:
            if ttype == "schema":
                abs_path = os.path.join(self.run_dir, spec_path)
                if not os.path.exists(abs_path):
                    evidence.append({"path": abs_path, "message": "spec file not found"})
                else:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read(256_000)
                    m = _validate_openapi_rough(text)
                    metrics.update(m)
                    if m.get("schema_valid"):
                        status = "passed"
                    else:
                        evidence.append({"path": abs_path, "message": "heuristic validation failed"})
            elif ttype == "http":
                examples = spec.get("examples") or []
                good = 0
                for ex in examples:
                    try:
                        json.dumps(ex)
                        good += 1
                    except Exception:
                        evidence.append({"path": "(inline)", "message": "invalid example JSON"})
                metrics["examples_ok"] = good
                status = "passed" if good == len(examples) else "failed"
            elif ttype == "unit":
                assertions = spec.get("assertions") or []
                ok = 0
                total = 0
                for a in assertions:
                    total += 1
                    passed = False
                    if isinstance(a, bool):
                        passed = bool(a)
                    elif isinstance(a, dict):
                        kind = (a.get("kind") or "").lower()
                        if kind == "file_exists":
                            rel = a.get("path") or ""
                            candidates = [rel]
                            if not rel.startswith("artifacts/"):
                                candidates.append(os.path.join("artifacts", rel))
                            for c in candidates:
                                abs_c = os.path.join(self.run_dir, c)
                                if os.path.exists(abs_c):
                                    passed = True
                                    break
                            if not passed:
                                evidence.append({"path": rel, "message": "file not found"})
                    if passed:
                        ok += 1
                metrics["assertions_ok"] = ok
                metrics["assertions_total"] = total
                status = "passed" if ok == total else "failed"
            else:
                evidence.append({"path": "-", "message": f"unknown test type: {ttype}"})
        except Exception as e:
            evidence.append({"path": "-", "message": f"error: {e}"})
            status = "failed"

        res = ContractTestResult(id=tid, status=status, metrics=metrics, evidence=evidence)
        self._save_result(res)
        return res

    def run_from_file(self, path: str) -> List[ContractTestResult]:
        try:
            tests = _read_json_or_list(path)
        except Exception as e:
            self.logger.log("contract_test_error", path=path, error=str(e))
            return []
        results: List[ContractTestResult] = []
        for t in tests:
            self.logger.log("contract_test_run", spec=t)
            results.append(self.run_test(t))
        return results

    def scan_and_run(self) -> List[ContractTestResult]:
        base = os.path.join(self.run_dir, "artifacts", "contracts", "tests")
        results: List[ContractTestResult] = []
        for p in glob.glob(os.path.join(base, "**", "*.json"), recursive=True):
            results.extend(self.run_from_file(p))
        return results
