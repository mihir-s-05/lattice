from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

from .artifacts import ArtifactStore
from .runlog import RunLogger
from .constants import DEFAULT_RESULTS_DIR


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
    def __init__(self, run_dir: str, artifacts: ArtifactStore, logger: RunLogger) -> None:
        self.run_dir = run_dir
        self.artifacts = artifacts
        self.logger = logger
        self.latest_tests: Dict[str, str] = {}

    def load_test_results(self) -> None:
        base = os.path.join(self.run_dir, DEFAULT_RESULTS_DIR)
        if not os.path.isdir(base):
            return
        for name in os.listdir(base):
            if not name.endswith(".json"):
                continue
            p = os.path.join(base, name)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                tid = obj.get("id")
                status = obj.get("status")
                if isinstance(tid, str) and isinstance(status, str):
                    self.latest_tests[tid] = status
            except Exception:
                continue

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
        return False

    def _tests_pass(self, test_id: str) -> bool:
        return self.latest_tests.get(test_id) == "passed"

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
                                except Exception:
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
