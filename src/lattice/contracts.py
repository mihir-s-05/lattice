from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple, Set, Callable
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
        for key in ("tests", "cases", "items"):
            val = obj.get(key)
            if isinstance(val, list):
                return [o for o in val if isinstance(o, dict)]
        return [obj]
    return []


def _validate_openapi_rough(text: str) -> Dict[str, Any]:
    score = 0
    has_openapi = bool(re.search(r"^\s*(openapi|swagger)\s*:\s*", text, re.IGNORECASE | re.MULTILINE))
    has_paths = bool(re.search(r"^\s*paths\s*:\s*", text, re.IGNORECASE | re.MULTILINE))
    if not has_openapi:
        has_openapi = bool(re.search(r"\"(openapi|swagger)\"\s*:\s*", text, re.IGNORECASE))
    if not has_paths:
        has_paths = bool(re.search(r"\"paths\"\s*:\s*\{", text, re.IGNORECASE))
    if has_openapi:
        score += 1
    if has_paths:
        score += 1
    components = bool(re.search(r"^\s*components\s*:\s*", text, re.IGNORECASE | re.MULTILINE)) or bool(re.search(r"\"components\"\s*:\s*\{", text, re.IGNORECASE))
    if components:
        score += 1
    return {"schema_valid": has_openapi and has_paths, "score": score}


class ContractRunner:
    def __init__(
        self,
        run_dir: str,
        logger: RunLogger,
        workspace_root: Optional[str] = None,
        run_started_at: Optional[float] = None,
    ) -> None:
        self.run_dir = run_dir
        self.logger = logger
        self.workspace_root = workspace_root or os.getcwd()
        self.run_started_at = run_started_at
        self.results_dir = os.path.join(run_dir, "artifacts", "contracts", "results")
        os.makedirs(self.results_dir, exist_ok=True)

    def _abs(self, rel: str) -> str:
        if os.path.isabs(rel):
            return rel
        art_path = os.path.join(self.run_dir, rel if rel.startswith("artifacts/") else os.path.join("artifacts", rel))
        if os.path.exists(art_path):
            return art_path
        ws_path = os.path.join(self.workspace_root, rel.replace("artifacts" + os.sep, "").replace("artifacts/", ""))
        if os.path.exists(ws_path):
            return ws_path
        return art_path

    def _within_workspace(self, path: str) -> bool:
        root = os.path.normcase(os.path.realpath(self.workspace_root))
        target = os.path.normcase(os.path.realpath(path))
        try:
            return os.path.commonpath([root, target]) == root
        except ValueError:
            return target == root or target.startswith(root + os.sep)

    def _load_openapi(self, spec_path: str) -> Optional[Dict[str, Any]]:
        abs_path = self._abs(spec_path)
        if not os.path.exists(abs_path):
            return None
        try:
            text = ""
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(512_000)
            try:
                import yaml
            except ImportError:
                return json.loads(text)
            try:
                return yaml.safe_load(text)
            except yaml.YAMLError:
                return json.loads(text)
        except (OSError, ValueError) as e:
            self.logger.log("contract_openapi_read_error", path=abs_path, error=str(e))
            try:
                base_dir = os.path.dirname(abs_path)
                cand = os.path.join(base_dir, "openapi.json")
                if os.path.exists(cand):
                    with open(cand, "r", encoding="utf-8") as f:
                        return json.load(f)
            except (OSError, ValueError):
                pass
            return None

    def _extract_spec(self, spec: Dict[str, Any]) -> Tuple[Set[Tuple[str, str]], Optional[Tuple[str, Dict[str, Any]]]]:
        """Return (endpoints, primary_schema) where endpoints is set of (method, path).
        primary_schema is (name, schema_dict) choosing the first object schema under components.schemas.
        """
        eps: Set[Tuple[str, str]] = set()
        paths = spec.get("paths") or {}
        if isinstance(paths, dict):
            for p, v in paths.items():
                if not isinstance(v, dict):
                    continue
                for m in list(v.keys()):
                    ml = str(m).lower()
                    if ml in ("get", "post", "put", "patch", "delete"):
                        eps.add((ml, p))
        primary: Optional[Tuple[str, Dict[str, Any]]] = None
        comps = (spec.get("components") or {}).get("schemas") or {}
        if isinstance(comps, dict):
            for k, v in comps.items():
                if isinstance(v, dict):
                    primary = (str(k), v)
                    break
        return eps, primary

    def _iter_backend_files(self) -> List[str]:
        roots = [
            os.path.join(self.workspace_root, "backend"),
            os.path.join(self.run_dir, "artifacts", "backend"),
            os.path.join(self.run_dir, "backend"),
        ]
        seen: Set[str] = set()
        files: List[str] = []
        for root in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, filenames in os.walk(root):
                for fn in filenames:
                    if not fn.endswith((".py", ".js", ".ts", ".mjs", ".cjs")):
                        continue
                    path = os.path.join(dirpath, fn)
                    if path in seen:
                        continue
                    seen.add(path)
                    files.append(path)
        return files

    def _scan_backend(self) -> Dict[str, Any]:
        """Scan backend code to find endpoints and basic model definitions.
        Returns dict with keys: endpoints (set[(method,path)]), models (set[str]), model_fields (dict[name]->set[str]), languages (set[str]).
        """
        info: Dict[str, Any] = {"endpoints": set(), "models": set(), "model_fields": {}, "languages": set()}
        files = self._iter_backend_files()
        if not files:
            return info
        node_markers = [
            os.path.join(self.workspace_root, "backend", "package.json"),
            os.path.join(self.run_dir, "artifacts", "backend", "package.json"),
            os.path.join(self.run_dir, "backend", "package.json"),
        ]
        py_markers = [
            os.path.join(self.workspace_root, "backend", "requirements.txt"),
            os.path.join(self.workspace_root, "backend", "pyproject.toml"),
            os.path.join(self.run_dir, "artifacts", "backend", "requirements.txt"),
            os.path.join(self.run_dir, "backend", "requirements.txt"),
        ]
        has_node = any(os.path.exists(p) for p in node_markers)
        has_py = any(os.path.exists(p) for p in py_markers)
        has_js_files = any(p.endswith((".js", ".ts", ".mjs", ".cjs")) for p in files)
        has_py_files = any(p.endswith(".py") for p in files)
        if has_node and has_js_files:
            files = [p for p in files if p.endswith((".js", ".ts", ".mjs", ".cjs"))]
        elif has_py and has_py_files:
            files = [p for p in files if p.endswith(".py")]

        backend_roots = [
            os.path.join(self.workspace_root, "backend"),
            os.path.join(self.run_dir, "artifacts", "backend"),
            os.path.join(self.run_dir, "backend"),
        ]

        def _infer_serverless_route(abs_path: str) -> Optional[str]:
            norm = os.path.normpath(abs_path)
            for root in backend_roots:
                try:
                    if not root or not os.path.isdir(root):
                        continue
                    root_norm = os.path.normpath(root)
                    if not os.path.commonpath([os.path.normcase(os.path.realpath(root_norm)), os.path.normcase(os.path.realpath(norm))]).startswith(
                        os.path.normcase(os.path.realpath(root_norm))
                    ):
                        continue
                except ValueError:
                    continue
                api_dir = os.path.join(root_norm, "api") + os.sep
                if api_dir in norm:
                    rel = norm.split(api_dir, 1)[1]
                    rel_no_ext = os.path.splitext(rel)[0]
                    rel_parts = rel_no_ext.replace("\\", "/").split("/")
                    if rel_parts and rel_parts[-1] == "index":
                        rel_parts = rel_parts[:-1]
                    suffix = "/".join([p for p in rel_parts if p])
                    return ("/api" + ("/" + suffix if suffix else ""))
            return None

        def _infer_methods_from_handler(code: str) -> Optional[Set[str]]:
            m = re.search(r"req\.method\s*!==\s*['\"]([A-Z]+)['\"]", code)
            if m and re.search(r"\b405\b|Method Not Allowed", code, re.IGNORECASE):
                return {m.group(1).lower()}
            methods = set()
            for mm in re.finditer(r"req\.method\s*===\s*['\"]([A-Z]+)['\"]", code):
                methods.add(mm.group(1).lower())
            for mm in re.finditer(r"case\s+['\"]([A-Z]+)['\"]\s*:", code):
                methods.add(mm.group(1).lower())
            return methods or None

        for path in files:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    code = f.read(512_000)
            except OSError:
                continue
            if path.endswith(".py"):
                info["languages"].add("python")
                for m in ("get", "post", "put", "patch", "delete"):
                    try:
                        pat = rf"@app\.{m}\(\s*['\"]([^'\"]+)['\"]"
                        for match in re.finditer(pat, code):
                            info["endpoints"].add((m, match.group(1)))
                    except re.error:
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
            else:
                info["languages"].add("javascript")
                for match in re.finditer(r"\b(app|router)\.(get|post|put|patch|delete)\s*\(\s*['\"`]?([^'\"`\s]+)['\"`]?", code):
                    method = match.group(2).lower()
                    path_val = match.group(3)
                    if path_val.startswith("/"):
                        info["endpoints"].add((method, path_val))
                for match in re.finditer(r"\.route\(\s*['\"`]([^'\"`]+)['\"`]\s*\)\s*\.\s*(get|post|put|patch|delete)", code):
                    method = match.group(2).lower()
                    path_val = match.group(1)
                    info["endpoints"].add((method, path_val))
                route = _infer_serverless_route(path)
                if route:
                    inferred = _infer_methods_from_handler(code)
                    if inferred:
                        for m in inferred:
                            info["endpoints"].add((m, route))
                    else:
                        info["endpoints"].add(("post", route))
        return info

    def _canonicalize_openapi(self, obj: Any) -> str:
        """Canonicalize an OpenAPI document for stable comparisons."""
        try:
            return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            return str(obj)

    def _compare_openapi_files(
        self,
        canonical_path: str,
        other_paths: List[str],
        *,
        match_required: bool = True,
    ) -> Tuple[bool, Dict[str, Any], List[Dict[str, Any]]]:
        metrics: Dict[str, Any] = {
            "test_type": "openapi_match",
            "canonical": canonical_path,
            "other_paths": list(other_paths),
            "match_required": bool(match_required),
        }
        evidence: List[Dict[str, Any]] = []
        can_obj = self._load_openapi(canonical_path)
        if not can_obj:
            evidence.append({"path": canonical_path, "message": "unable to load canonical OpenAPI spec"})
            return (False, metrics, evidence)
        can_s = self._canonicalize_openapi(can_obj)
        ok_all = True
        for op in other_paths:
            other_obj = self._load_openapi(op)
            if not other_obj:
                ok_all = False
                evidence.append({"path": op, "message": "unable to load OpenAPI spec"})
                continue
            other_s = self._canonicalize_openapi(other_obj)
            if other_s != can_s:
                ok_all = False
                evidence.append({"path": op, "message": "OpenAPI spec differs from canonical"})
        if match_required:
            return (ok_all, metrics, evidence)
        return (True, metrics, evidence)

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
            metrics["primary_schema"] = name
            metrics["model_in_backend"] = name in models_be
            props = set((schema.get("properties") or {}).keys()) if isinstance(schema, dict) else set()
            metrics["primary_schema_property_count"] = len(props)
            if models_be:
                if name not in models_be:
                    ok = False
                    evidence.append({"message": "Primary schema model not defined in backend", "model": name})
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
                metrics["model_checks_skipped"] = True
        else:
            evidence.append({"message": "No primary schema found in OpenAPI.components.schemas"})

        return ok, metrics, evidence

    def _save_result(self, result: ContractTestResult) -> str:
        path = os.path.join(self.results_dir, f"{result.id}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(result.to_json())
        self.logger.log("contract_test_result", id=result.id, status=result.status, path=path)
        return path

    def run_test(
        self,
        spec: Dict[str, Any],
        allow_commands: bool = False,
        command_validator: Optional[Callable[[str], Optional[str]]] = None,
    ) -> ContractTestResult:
        tid = spec.get("id") or "contract_test"
        ttype = (spec.get("type") or "").lower()
        if str(tid).strip() == "api_contract" and ttype not in ("schema", "openapi", "swagger"):
            tid = f"api_contract_{ttype or 'custom'}"
        spec_path = spec.get("spec_path")
        metrics: Dict[str, Any] = {"test_type": ttype}
        evidence: List[Dict[str, Any]] = []
        status = "failed"

        try:
            if ttype == "schema":
                spec_rel = spec_path or spec.get("path") or "artifacts/contracts/openapi.yaml"
                abs_path = self._abs(spec_rel)
                if not os.path.exists(abs_path):
                    evidence.append({"path": spec_rel, "message": "spec file not found"})
                else:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read(256_000)
                    m = _validate_openapi_rough(text)
                    metrics.update(m)
                    if m.get("schema_valid"):
                        status = "passed"
                    else:
                        evidence.append({"path": spec_rel, "message": "heuristic validation failed"})
            elif ttype in ("consistency", "api_consistency"):
                if spec.get("canonical") or spec.get("other_paths"):
                    canonical_path = str(spec.get("canonical") or "")
                    other_paths = [str(x) for x in (spec.get("other_paths") or [])]
                    match_required = bool(spec.get("match_required", True))
                    ok, met, ev = self._compare_openapi_files(canonical_path, other_paths, match_required=match_required)
                    metrics.update(met)
                    evidence.extend(ev)
                    status = "passed" if ok else "failed"
                else:
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
                req_rel = spec.get("requirements_path") or spec.get("package_json") or "artifacts/backend/requirements.txt"
                req_abs = self._abs(req_rel)
                required = set(spec.get("required", ["fastapi", "uvicorn"]))
                found: Set[str] = set()
                if os.path.exists(req_abs):
                    try:
                        if os.path.basename(req_abs) == "package.json":
                            with open(req_abs, "r", encoding="utf-8") as f:
                                pkg = json.load(f)
                            deps = (pkg.get("dependencies") or {})
                            dev = (pkg.get("devDependencies") or {})
                            for k in list(deps.keys()) + list(dev.keys()):
                                found.add(str(k).lower())
                        else:
                            with open(req_abs, "r", encoding="utf-8") as f:
                                lines = [ln.strip().split("==")[0].lower() for ln in f if ln.strip() and not ln.strip().startswith("#")]
                            found = set(lines)
                    except (OSError, json.JSONDecodeError):
                        evidence.append({"path": req_rel, "message": "error reading requirements"})
                else:
                    evidence.append({"path": req_rel, "message": "requirements file not found"})
                missing = sorted(list(required - found))
                metrics["requirements_found"] = sorted(list(found))
                metrics["requirements_missing"] = missing
                status = "passed" if not missing else "failed"
            elif ttype == "command":
                cmd = spec.get("command") or ""
                cwd = spec.get("cwd")
                timeout_sec = int(spec.get("timeout_sec") or 120)
                reason = spec.get("reason") or ""
                expected = int(spec.get("expected_exit_code") or 0)
                if not allow_commands:
                    evidence.append({"message": "command tests disabled", "command": cmd})
                elif not cmd:
                    evidence.append({"message": "missing command"})
                elif not reason:
                    evidence.append({"message": "missing reason for command"})
                else:
                    deny = command_validator(cmd) if command_validator else None
                    if deny:
                        evidence.append({"message": "command blocked", "reason": deny})
                    else:
                        if cwd and os.path.isabs(str(cwd)):
                            run_cwd = str(cwd)
                        elif cwd:
                            run_cwd = os.path.abspath(os.path.join(self.workspace_root, str(cwd)))
                        else:
                            run_cwd = self.workspace_root
                        if not self._within_workspace(run_cwd):
                            evidence.append({"message": "cwd escapes workspace root", "cwd": run_cwd})
                        else:
                            try:
                                self.logger.log("contract_command_run", command=cmd, cwd=run_cwd, timeout_sec=timeout_sec)
                                proc = subprocess.run(
                                    cmd,
                                    shell=True,
                                    cwd=run_cwd,
                                    capture_output=True,
                                    text=True,
                                    timeout=timeout_sec,
                                )
                                metrics["returncode"] = proc.returncode
                                metrics["stdout_tail"] = proc.stdout[-2000:]
                                metrics["stderr_tail"] = proc.stderr[-2000:]
                                status = "passed" if proc.returncode == expected else "failed"
                                self.logger.log("contract_command_result", command=cmd, returncode=proc.returncode, status=status)
                            except (subprocess.TimeoutExpired, OSError, ValueError) as e:
                                evidence.append({"message": "command execution error", "error": str(e)})
            elif ttype == "http":
                examples = spec.get("examples") or []
                good = 0
                for ex in examples:
                    try:
                        json.dumps(ex)
                        good += 1
                    except (TypeError, ValueError):
                        evidence.append({"path": "(inline)", "message": "invalid example JSON"})
                metrics["examples_ok"] = good
                status = "passed" if good == len(examples) else "failed"
            elif ttype == "unit":
                assertions = spec.get("assertions")
                if isinstance(assertions, dict):
                    assertions = [assertions]
                elif isinstance(assertions, list):
                    assertions = assertions
                elif assertions is None:
                    assertions = []
                else:
                    evidence.append({"path": "-", "message": "invalid assertions type; expected list or dict"})
                    assertions = []
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
                                abs_c = self._abs(c)
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
                status = "passed" if total > 0 and ok == total else "failed"
            elif ttype in ("fastapi", "fastapi_app"):
                app_path = spec.get("app_path") or "artifacts/backend/app/main.py"
                abs_app = self._abs(app_path)
                checks = spec.get("checks") or []
                try:
                    from starlette.testclient import TestClient
                except ImportError as e:
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
                        modu = importlib.util.module_from_spec(specm)
                        assert specm and specm.loader
                        specm.loader.exec_module(modu)
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
            elif ttype == "pytest":
                if not allow_commands:
                    status = "skipped"
                    evidence.append({"message": "commands not allowed for pytest test type"})
                else:
                    import sys
                    target = spec.get("test_target") or spec.get("nodeid") or spec.get("target")
                    if not target:
                        evidence.append({"message": "pytest test_target missing"})
                    else:
                        timeout_sec = int(spec.get("timeout_sec") or 120)
                        cmd = f"\"{sys.executable}\" -m pytest -q {target}"
                        try:
                            if command_validator:
                                why = command_validator(cmd)
                                if why:
                                    evidence.append({"message": "command rejected", "reason": why, "command": cmd})
                                else:
                                    env = dict(os.environ)
                                    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
                                    p = subprocess.run(
                                        cmd,
                                        shell=True,
                                        cwd=self.workspace_root,
                                        capture_output=True,
                                        text=True,
                                        timeout=timeout_sec,
                                        env=env,
                                    )
                                    metrics["command"] = cmd
                                    metrics["returncode"] = p.returncode
                                    tail_n = 2000
                                    evidence.append({"message": "stdout_tail", "text": (p.stdout or "")[-tail_n:]})
                                    evidence.append({"message": "stderr_tail", "text": (p.stderr or "")[-tail_n:]})
                                    status = "passed" if p.returncode == 0 else "failed"
                        except (subprocess.TimeoutExpired, OSError, ValueError) as e:
                            evidence.append({"message": "pytest execution error", "error": str(e), "command": cmd})
            else:
                evidence.append({"path": "-", "message": f"unknown test type: {ttype}"})
        except Exception as e:
            evidence.append({"path": "-", "message": f"error: {e}"})
            status = "failed"

        res = ContractTestResult(id=tid, status=status, metrics=metrics, evidence=evidence)
        self._save_result(res)
        return res

    def run_from_file(
        self,
        path: str,
        allow_commands: bool = False,
        command_validator: Optional[Callable[[str], Optional[str]]] = None,
    ) -> List[ContractTestResult]:
        try:
            tests = _read_json_or_list(path)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            self.logger.log("contract_test_error", path=path, error=str(e))
            return []
        results: List[ContractTestResult] = []
        seen_ids: Set[str] = set()
        base = os.path.splitext(os.path.basename(path))[0]
        for idx, t in enumerate(tests, start=1):
            if not isinstance(t, dict):
                continue
            tid = t.get("id")
            if not tid:
                ttype = (t.get("type") or "").lower()
                if ttype == "schema" and "api_contract" not in seen_ids:
                    t["id"] = "api_contract"
                elif ttype == "api_consistency" and "api_consistency" not in seen_ids:
                    t["id"] = "api_consistency"
                else:
                    t["id"] = f"{base}_{idx}"
                tid = t["id"]
            else:
                ttype = (t.get("type") or "").lower()
                if str(tid).strip() == "api_contract" and ttype not in ("schema", "openapi", "swagger"):
                    t["id"] = f"{base}_api_contract_{idx}"
                    tid = t["id"]
            if tid in seen_ids:
                t["id"] = f"{tid}_{idx}"
            seen_ids.add(t["id"])
            self.logger.log("contract_test_run", spec=t)
            results.append(self.run_test(t, allow_commands=allow_commands, command_validator=command_validator))
        return results

    def scan_and_run(
        self,
        allow_commands: bool = False,
        command_validator: Optional[Callable[[str], Optional[str]]] = None,
    ) -> List[ContractTestResult]:
        bases = [
            os.path.join(self.run_dir, "artifacts", "contracts", "tests"),
            os.path.join(self.workspace_root, "contracts", "tests"),
        ]
        results: List[ContractTestResult] = []
        seen: Set[str] = set()
        for base in bases:
            if not os.path.isdir(base):
                continue
            for p in glob.glob(os.path.join(base, "**", "*.json"), recursive=True):
                if p in seen:
                    continue
                if base.startswith(self.workspace_root) and self.run_started_at:
                    try:
                        if os.path.getmtime(p) < (self.run_started_at - 0.1):
                            continue
                    except OSError:
                        pass
                seen.add(p)
                results.extend(self.run_from_file(p, allow_commands=allow_commands, command_validator=command_validator))
        if not any(getattr(r, "id", None) == "api_contract" for r in results):
            candidates = [
                os.path.join("contracts", "openapi.yaml"),
                os.path.join("contracts", "openapi.yml"),
                os.path.join("contracts", "openapi.json"),
                os.path.join("artifacts", "contracts", "openapi.yaml"),
                os.path.join("artifacts", "contracts", "openapi.json"),
                os.path.join("api", "openapi.yaml"),
                os.path.join("api", "openapi.yml"),
                os.path.join("api", "openapi.json"),
                "openapi.yaml",
                "openapi.yml",
                "openapi.json",
            ]
            chosen = None
            for rel in candidates:
                abs_p = self._abs(rel)
                if not os.path.exists(abs_p):
                    continue
                if self.run_started_at:
                    try:
                        if os.path.getmtime(abs_p) < (self.run_started_at - 0.1):
                            continue
                    except OSError:
                        continue
                spec = self._load_openapi(rel)
                if isinstance(spec, dict) and (spec.get("openapi") or spec.get("swagger")) and isinstance(spec.get("paths"), dict):
                    chosen = rel
                    break
            if chosen:
                self.logger.log("contract_test_auto", reason="ensure_api_contract", spec_path=chosen)
                results.append(self.run_test({"id": "api_contract", "type": "schema", "spec_path": chosen, "reason": "auto schema check"}, allow_commands=allow_commands, command_validator=command_validator))
        return results

    def scan_specs(self) -> List[Dict[str, Any]]:
        bases = [
            os.path.join(self.run_dir, "artifacts", "contracts", "tests"),
            os.path.join(self.workspace_root, "contracts", "tests"),
        ]
        specs: List[Dict[str, Any]] = []
        seen_files: Set[str] = set()
        seen_ids: Set[str] = set()
        for base in bases:
            if not os.path.isdir(base):
                continue
            for p in glob.glob(os.path.join(base, "**", "*.json"), recursive=True):
                if p in seen_files:
                    continue
                if base.startswith(self.workspace_root) and self.run_started_at:
                    try:
                        if os.path.getmtime(p) < (self.run_started_at - 0.1):
                            continue
                    except OSError:
                        pass
                seen_files.add(p)
                try:
                    tests = _read_json_or_list(p)
                except (OSError, ValueError, TypeError):
                    continue
                base_name = os.path.splitext(os.path.basename(p))[0]
                local_seen: Set[str] = set()
                for idx, t in enumerate(tests, start=1):
                    if not isinstance(t, dict):
                        continue
                    spec = dict(t)
                    tid = spec.get("id")
                    if not tid:
                        ttype = (spec.get("type") or "").lower()
                        if ttype == "schema" and "api_contract" not in local_seen:
                            tid = "api_contract"
                        elif ttype == "api_consistency" and "api_consistency" not in local_seen:
                            tid = "api_consistency"
                        else:
                            tid = f"{base_name}_{idx}"
                        spec["id"] = tid
                    if spec["id"] in local_seen:
                        spec["id"] = f"{spec['id']}_{idx}"
                    local_seen.add(spec["id"])
                    if spec["id"] in seen_ids:
                        spec["id"] = f"{spec['id']}_{len(seen_ids) + 1}"
                    seen_ids.add(spec["id"])
                    specs.append(spec)
        return specs

    def run_specs(
        self,
        specs: List[Dict[str, Any]],
        allow_commands: bool = False,
        command_validator: Optional[Callable[[str], Optional[str]]] = None,
    ) -> List[ContractTestResult]:
        results: List[ContractTestResult] = []
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            self.logger.log("contract_test_run", spec=spec)
            results.append(self.run_test(spec, allow_commands=allow_commands, command_validator=command_validator))
        return results
