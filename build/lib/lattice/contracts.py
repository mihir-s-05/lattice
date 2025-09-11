from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple, Set
import importlib.util
import types

from .runlog import RunLogger


@dataclass
class ContractTestResult:
    id: str
    status: str
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

    def _abs(self, rel: str) -> str:
        return os.path.join(self.run_dir, rel if rel.startswith("artifacts/") else os.path.join("artifacts", rel))

    def _load_openapi(self, spec_path: str) -> Optional[Dict[str, Any]]:
        abs_path = self._abs(spec_path)
        if not os.path.exists(abs_path):
            return None
        try:
            text = ""
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(512_000)
            try:
                import yaml  # type: ignore

                return yaml.safe_load(text)
            except Exception:
                return json.loads(text)
        except Exception as e:
            self.logger.log("contract_openapi_read_error", path=abs_path, error=str(e))
            try:
                base_dir = os.path.dirname(abs_path)
                cand = os.path.join(base_dir, "openapi.json")
                if os.path.exists(cand):
                    with open(cand, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                pass
            return None

    def _extract_spec(self, spec: Dict[str, Any]) -> Tuple[Set[Tuple[str, str]], Optional[Tuple[str, Dict[str, Any]]]]:
        """Return (endpoints, primary_schema) where endpoints is set of (method, path).
        primary_schema is (name, schema_dict) choosing the first object schema under components.schemas.
        """
        eps: Set[Tuple[str, str]] = set()
        try:
            paths = spec.get("paths") or {}
            if isinstance(paths, dict):
                for p, v in paths.items():
                    if not isinstance(v, dict):
                        continue
                    for m in list(v.keys()):
                        ml = str(m).lower()
                        if ml in ("get", "post", "put", "patch", "delete"):
                            eps.add((ml, p))
        except Exception:
            pass
        primary: Optional[Tuple[str, Dict[str, Any]]] = None
        try:
            comps = (spec.get("components") or {}).get("schemas") or {}
            if isinstance(comps, dict):
                for k, v in comps.items():
                    if isinstance(v, dict):
                        primary = (str(k), v)
                        break
        except Exception:
            pass
        return eps, primary

    def _scan_backend(self) -> Dict[str, Any]:
        """Scan backend FastAPI scaffold to find endpoints and Pydantic models/fields.
        Returns dict with keys: endpoints (set[(method,path)]), models (set[str]), model_fields (dict[name]->set[str]).
        """
        info: Dict[str, Any] = {"endpoints": set(), "models": set(), "model_fields": {}}
        try:
            main_abs = self._abs("backend/app/main.py")
            if not os.path.exists(main_abs):
                return info
            with open(main_abs, "r", encoding="utf-8", errors="ignore") as f:
                code = f.read()
        except Exception:
            return info

        for m in ("get", "post", "put", "patch", "delete"):
            for match in re.finditer(rf"@app\.{m}\\(\s*['\"]([^'\"]+)['\"]", code):
                try:
                    path = match.group(1)
                    info["endpoints"].add((m, path))
                except Exception:
                    continue

        class_iter = re.finditer(r"(?m)^class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\(BaseModel\):\s*$", code)
        for cm in class_iter:
            name = cm.group("name")
            info["models"].add(name)
            start = cm.end()
            block_match = re.search(r"(?ms)^(?=[^\s])", code[start:])
            block = code[start: start + block_match.start()] if block_match else code[start:]
            fields: Set[str] = set()
            for fm in re.finditer(r"(?m)^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*", block):
                fields.add(fm.group(1))
            info.setdefault("model_fields", {})[name] = fields
        return info

    def _compare_spec_backend(self, spec: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], List[Dict[str, Any]]]:
        eps_spec, primary = self._extract_spec(spec)
        be = self._scan_backend()
        eps_be: Set[Tuple[str, str]] = be.get("endpoints", set())
        models_be: Set[str] = be.get("models", set())
        fields_be: Dict[str, Set[str]] = be.get("model_fields", {})

        metrics: Dict[str, Any] = {
            "endpoint_count_spec": len(eps_spec),
            "endpoint_count_backend": len(eps_be),
            "primary_schema": (primary[0] if primary else None),
        }
        evidence: List[Dict[str, Any]] = []
        ok = True

        missing_eps: List[Tuple[str, str]] = []
        for m, p in eps_spec:
            if (m, p) not in eps_be:
                missing_eps.append((m, p))
        if missing_eps:
            ok = False
            evidence.append({
                "message": "Missing endpoints in backend",
                "missing": [f"{m.upper()} {p}" for m, p in missing_eps],
            })

        if primary:
            name, schema = primary
            metrics["model_in_backend"] = name in models_be
            if name not in models_be:
                ok = False
                evidence.append({"message": "Primary schema model not defined in backend", "model": name})
            props = set()
            try:
                props = set((schema.get("properties") or {}).keys())
            except Exception:
                props = set()
            metrics["primary_schema_property_count"] = len(props)
            if name in fields_be:
                be_fields = fields_be[name]
                missing_fields = sorted(list(props - be_fields))
                extra_fields = sorted(list(be_fields - props))
                if missing_fields or extra_fields:
                    ok = False
                    evidence.append({
                        "message": "Model fields mismatch",
                        "model": name,
                        "missing_in_backend": missing_fields,
                        "extra_in_backend": extra_fields,
                    })
        else:
            evidence.append({"message": "No primary schema found in OpenAPI.components.schemas"})

        return ok, metrics, evidence

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
            elif ttype in ("consistency", "api_consistency"):
                spec_rel = spec.get("spec_path") or "artifacts/contracts/openapi.yaml"
                spec_obj = self._load_openapi(spec_rel)
                if not spec_obj:
                    evidence.append({"path": spec_rel, "message": "unable to load OpenAPI spec"})
                else:
                    ok, met, ev = self._compare_spec_backend(spec_obj)
                    metrics.update(met)
                    evidence.extend(ev)
                    status = "passed" if ok else "failed"
            elif ttype in ("deps", "dependencies"):
                req_rel = spec.get("requirements_path") or "artifacts/backend/requirements.txt"
                req_abs = self._abs(req_rel)
                required = set(spec.get("required", ["fastapi", "uvicorn"]))
                found: Set[str] = set()
                if os.path.exists(req_abs):
                    try:
                        with open(req_abs, "r", encoding="utf-8") as f:
                            lines = [ln.strip().split("==")[0].lower() for ln in f if ln.strip() and not ln.strip().startswith("#")]
                        found = set(lines)
                    except Exception:
                        evidence.append({"path": req_rel, "message": "error reading requirements"})
                else:
                    evidence.append({"path": req_rel, "message": "requirements file not found"})
                missing = sorted(list(required - found))
                metrics["requirements_found"] = sorted(list(found))
                metrics["requirements_missing"] = missing
                status = "passed" if not missing else "failed"
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
                        if kind in ("file_exists", "file_exists_optional"):
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
                                if kind == "file_exists":
                                    evidence.append({"path": rel, "message": "file not found"})
                                else:
                                    passed = True
                    if passed:
                        ok += 1
                metrics["assertions_ok"] = ok
                metrics["assertions_total"] = total
                status = "passed" if ok == total else "failed"
            elif ttype in ("fastapi", "fastapi_app"):
                app_path = spec.get("app_path") or "artifacts/backend/app/main.py"
                abs_app = self._abs(app_path)
                checks = spec.get("checks") or []
                try:
                    from starlette.testclient import TestClient  # type: ignore
                except Exception as e:
                    evidence.append({"message": "TestClient not available (install fastapi/starlette)", "error": str(e)})
                    status = "failed"
                    res = ContractTestResult(id=tid, status=status, metrics=metrics, evidence=evidence)
                    self._save_result(res)
                    return res
                if not os.path.exists(abs_app):
                    evidence.append({"path": app_path, "message": "FastAPI app module not found"})
                else:
                    try:
                        specm = importlib.util.spec_from_file_location("generated_app", abs_app)
                        modu = importlib.util.module_from_spec(specm)  # type: ignore
                        assert specm and specm.loader
                        specm.loader.exec_module(modu)  # type: ignore
                        app = getattr(modu, "app", None)
                        if app is None:
                            evidence.append({"path": app_path, "message": "No 'app' found in module"})
                        else:
                            client = TestClient(app)
                            passed = 0
                            for c in checks:
                                method = (c.get("method") or "get").lower()
                                path = c.get("path") or "/health"
                                expect = int(c.get("expect_status") or 200)
                                try:
                                    resp = getattr(client, method)(path)
                                    ok = resp.status_code == expect
                                    metrics.setdefault("checks", []).append({
                                        "method": method,
                                        "path": path,
                                        "status": resp.status_code,
                                        "ok": ok,
                                    })
                                    if ok:
                                        passed += 1
                                    else:
                                        evidence.append({"message": "unexpected status", "path": path, "got": resp.status_code, "want": expect})
                                except Exception as e:
                                    evidence.append({"message": "request error", "path": path, "error": str(e)})
                            metrics["checks_ok"] = passed
                            metrics["checks_total"] = len(checks)
                            status = "passed" if passed == len(checks) and len(checks) > 0 else "failed"
                    except Exception as e:
                        evidence.append({"message": "error loading app", "error": str(e)})
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
