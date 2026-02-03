from __future__ import annotations

import fnmatch
import glob
import json
import os
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

from .artifacts import ArtifactStore
from .runlog import RunLogger
from .constants import DEFAULT_RESULTS_DIR
from .contracts import ContractRunner


@dataclass
class StageGate:
    id: str
    name: str
    conditions: List[str]
    owner: str = "router"
    status: str = "pending"
    checked_conditions: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


class GateEvaluator:
    def __init__(
        self,
        run_dir: str,
        artifacts: ArtifactStore,
        logger: RunLogger,
        workspace_root: Optional[str] = None,
        run_started_at: Optional[float] = None,
    ) -> None:
        self.run_dir = run_dir
        self.artifacts = artifacts
        self.logger = logger
        self.workspace_root = workspace_root or os.getcwd()
        self.run_started_at = run_started_at
        self.latest_tests: Dict[str, str] = {}
        self.latest_test_metrics: Dict[str, Dict[str, Any]] = {}

    def _active_test_ids(self) -> set[str]:
        try:
            runner = ContractRunner(self.run_dir, self.logger, workspace_root=self.workspace_root, run_started_at=self.run_started_at)
            specs = runner.scan_specs()
            ids = set()
            for s in specs:
                tid = s.get("id")
                if isinstance(tid, str) and tid.strip():
                    ids.add(tid.strip())
            return set(ids)
        except (OSError, ValueError, TypeError):
            return set()

    def load_test_results(self) -> None:
        self.latest_tests = {}
        self.latest_test_metrics = {}
        active_ids = self._active_test_ids()
        bases = [
            os.path.join(self.run_dir, DEFAULT_RESULTS_DIR),
            os.path.join(self.workspace_root, "contracts", "results"),
        ]
        for base in bases:
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                if not name.endswith(".json"):
                    continue
                p = os.path.join(base, name)
                if self.run_started_at:
                    try:
                        if os.path.getmtime(p) < self.run_started_at:
                            continue
                    except OSError:
                        continue
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                    tid = obj.get("id")
                    status = obj.get("status")
                    if isinstance(tid, str) and isinstance(status, str):
                        if active_ids and tid not in active_ids and tid not in ("api_contract", "smoke_suite"):
                            continue
                        self.latest_tests[tid] = status
                        met = obj.get("metrics") if isinstance(obj, dict) else None
                        if isinstance(met, dict):
                            self.latest_test_metrics[tid] = met
                except (OSError, json.JSONDecodeError, ValueError, TypeError):
                    continue

    def _strip_artifacts_prefix(self, pattern: str) -> str:
        if pattern.startswith("artifacts/"):
            return pattern[len("artifacts/") :]
        if pattern.startswith(f"artifacts{os.sep}"):
            return pattern[len("artifacts") + 1 :]
        return pattern

    def _glob_exists(self, root: str, pattern: str) -> bool:
        abs_pat = os.path.join(root, pattern)
        since = self.run_started_at
        has_glob = any(ch in pattern for ch in ["*", "?", "[", "]"])
        candidates: List[str] = []
        if not has_glob:
            candidates = [abs_pat]
        else:
            try:
                candidates = list(glob.glob(abs_pat, recursive=True))
            except OSError:
                candidates = []
        for c in candidates:
            try:
                if not os.path.exists(c):
                    continue
                if since is None:
                    return True
                if os.path.isdir(c):
                    for r, _d, files in os.walk(c):
                        for fn in files:
                            fp = os.path.join(r, fn)
                            try:
                                if os.path.getmtime(fp) >= since:
                                    return True
                            except OSError:
                                continue
                    continue
                if os.path.getmtime(c) >= since:
                    return True
            except OSError:
                continue
        return False

    def _artifact_exists(self, pattern: str) -> bool:
        pats = [pattern]
        if not pattern.startswith("artifacts/"):
            pats.append(os.path.join("artifacts", pattern))
        paths = [a.path for a in self.artifacts.list()]
        for pat in list(pats):
            if "**" in pat:
                prefix = pat.split("**", 1)[0].rstrip("/")
                for p in paths:
                    if p.startswith(prefix + "/") or p == prefix:
                        return True
                base = os.path.join(self.run_dir, prefix)
                if os.path.exists(base):
                    for _root, _dirs, files in os.walk(base):
                        if files:
                            return True
        for pat in pats:
            for p in paths:
                if fnmatch.fnmatch(p, pat):
                    return True
        for pat in pats:
            abspat = os.path.join(self.run_dir, pat)
            for root, _, files in os.walk(os.path.dirname(abspat) or self.run_dir):
                for fn in files:
                    rel = os.path.relpath(os.path.join(root, fn), self.run_dir)
                    if fnmatch.fnmatch(rel, pat):
                        return True
        for pat in pats:
            if self._glob_exists(self.run_dir, pat):
                return True
        for pat in pats:
            ws_pat = self._strip_artifacts_prefix(pat)
            if self._glob_exists(self.workspace_root, ws_pat):
                return True
        return False

    def _tests_pass(self, test_id: str) -> bool:
        status = self.latest_tests.get(test_id)
        if status is not None:
            return status == "passed"
        if test_id == "api_contract":
            contract_types = {"schema", "api_consistency", "consistency", "openapi_match"}
            relevant = [
                tid
                for tid in self.latest_tests.keys()
                if (self.latest_test_metrics.get(tid) or {}).get("test_type") in contract_types
            ]
            if not relevant:
                schema_like = [tid for tid in self.latest_tests.keys() if tid.lower().startswith("schema-")]
                if not schema_like:
                    return False
                return all(self.latest_tests.get(tid) == "passed" for tid in schema_like)
            return all(self.latest_tests.get(tid) == "passed" for tid in relevant)
        if test_id == "smoke_suite":
            smoke_types = {"command", "deps", "dependencies", "unit"}
            relevant = [
                tid
                for tid in self.latest_tests.keys()
                if (self.latest_test_metrics.get(tid) or {}).get("test_type") in smoke_types
            ]
            if not relevant:
                smoke_like = [tid for tid in self.latest_tests.keys() if tid.lower().startswith("smoke-")]
                if not smoke_like:
                    return False
                return all(self.latest_tests.get(tid) == "passed" for tid in smoke_like)
            return all(self.latest_tests.get(tid) == "passed" for tid in relevant)
        if test_id == "api_consistency":
            return any((self.latest_tests.get(tid) == "passed") and ((self.latest_test_metrics.get(tid) or {}).get("test_type") in ("api_consistency", "consistency"))
                       for tid in self.latest_tests.keys())
        return False

    def _eval_atom(self, expr: str) -> Optional[bool]:
        expr = expr.strip()
        if expr.startswith("tests.pass(") and expr.endswith(")"):
            arg = expr[len("tests.pass("):-1].strip("\"' ")
            return self._tests_pass(arg)
        if expr.startswith("artifact.exists(") and expr.endswith(")"):
            arg = expr[len("artifact.exists("):-1].strip("\"' ")
            return self._artifact_exists(arg)
        return None

    def _tokenize(self, s: str) -> List[str]:
        out: List[str] = []
        i = 0
        while i < len(s):
            ch = s[i]
            if ch.isspace():
                i += 1
                continue
            if ch in "()":
                out.append(ch)
                i += 1
                continue
            if s.startswith("and", i):
                out.append("and")
                i += 3
                continue
            if s.startswith("or", i):
                out.append("or")
                i += 2
                continue
            j = i
            depth = 0
            while j < len(s):
                cj = s[j]
                if cj == "(":
                    depth += 1
                if cj == ")":
                    if depth > 0:
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                if depth == 0 and (cj.isspace() or cj in "()"):
                    break
                if depth == 0 and s.startswith(" and ", j):
                    break
                if depth == 0 and s.startswith(" or ", j):
                    break
                j += 1
            out.append(s[i:j])
            i = j
        return out

    def _parse_eval(self, tokens: List[str]) -> bool:
        def prec(tok: str) -> int:
            return 2 if tok == "and" else 1 if tok == "or" else 0
        output: List[str] = []
        ops: List[str] = []
        for t in tokens:
            if t == "and" or t == "or":
                while ops and ops[-1] in ("and", "or") and prec(ops[-1]) >= prec(t):
                    output.append(ops.pop())
                ops.append(t)
            elif t == "(":
                ops.append(t)
            elif t == ")":
                while ops and ops[-1] != "(":
                    output.append(ops.pop())
                if ops and ops[-1] == "(":
                    ops.pop()
            else:
                output.append(t)
        while ops:
            output.append(ops.pop())

        st: List[bool] = []
        for t in output:
            if t in ("and", "or"):
                b = st.pop() if st else False
                a = st.pop() if st else False
                st.append((a and b) if t == "and" else (a or b))
            else:
                val = self._eval_atom(t)
                if val is None:
                    val = False
                st.append(bool(val))
        return bool(st[-1]) if st else False

    def evaluate(self, gates: List[StageGate]) -> List[StageGate]:
        self.load_test_results()
        out: List[StageGate] = []
        for g in gates:
            overall = True
            g.checked_conditions = []
            g.evidence = []
            for cond in g.conditions:
                tokens = self._tokenize(cond)
                ok = self._parse_eval(tokens)
                atoms: List[Dict[str, Any]] = []
                for t in tokens:
                    val = self._eval_atom(t)
                    if val is not None:
                        atoms.append({"expr": t, "value": bool(val)})
                        if t.startswith("tests.pass("):
                            test_id = t[len("tests.pass("):-1].strip("\"' ")
                            rel = os.path.join(DEFAULT_RESULTS_DIR, f"{test_id}.json")
                            if os.path.exists(os.path.join(self.run_dir, rel)):
                                try:
                                    with open(os.path.join(self.run_dir, rel), "rb") as f:
                                        import hashlib
                                        h = hashlib.sha256(f.read()).hexdigest()
                                    g.evidence.append({"type": "artifact", "id": rel, "hash": f"sha256:{h}"})
                                except OSError:
                                    g.evidence.append({"type": "artifact", "id": rel})
                        if t.startswith("artifact.exists("):
                            pat = t[len("artifact.exists("):-1].strip("\"' ")
                            g.evidence.append({"type": "artifact", "id": pat})
                self.logger.log("stage_gate_condition", gate_id=g.id, condition=cond, ok=ok)
                self.logger.log("stage_gate_trace", gate_id=g.id, condition=cond, atoms=atoms, result=ok)
                g.checked_conditions.append({"condition": cond, "atoms": atoms, "result": ok})
                overall = overall and ok
            g.status = "passed" if overall else "failed"
            self.logger.log("stage_gate_result", gate_id=g.id, name=g.name, status=g.status)
            self.logger.log("gate_eval", id=g.id, status=g.status, checked_conditions=g.checked_conditions, evidence=g.evidence)
            out.append(g)
        return out
