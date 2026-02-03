from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CoherenceFinding:
    id: str
    severity: str  # "low"|"medium"|"high"
    message: str
    paths: List[str]
    suggested_attendees: List[str]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _norm(p: str) -> str:
    return os.path.normpath(str(p)).replace("\\", "/")


def _suggest_attendees(paths: List[str]) -> List[str]:
    roles: List[str] = ["router"]
    for p in paths or []:
        rp = _norm(p).lower()
        if rp.startswith("backend/") and "backend" not in roles:
            roles.append("backend")
        if (rp.startswith("frontend/") or rp.startswith("fe/") or rp.startswith("public/")) and "frontend" not in roles:
            roles.append("frontend")
        if rp.startswith("contracts/") and "backend" not in roles:
            roles.append("backend")
        if rp.startswith("tests/") and "tests" not in roles:
            roles.append("tests")
    return roles


def _find_repo_files(root: str, max_files: int = 4000) -> List[str]:
    out: List[str] = []
    for r, dirs, files in os.walk(root):
        rel_root = os.path.relpath(r, root)
        if rel_root.startswith(".git") or rel_root.startswith("venv") or rel_root.startswith("__pycache__"):
            dirs[:] = []
            continue
        for fn in files:
            if fn.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".md", ".html")):
                rel = os.path.normpath(os.path.join(rel_root, fn))
                if rel.startswith(".") and rel != ".":
                    continue
                out.append(_norm(rel))
                if len(out) >= max_files:
                    return out
    return out


def _load_json(path: str) -> Optional[Dict[str, object]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _looks_like_openapi_spec(abs_path: str) -> bool:
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(50_000)
    except OSError:
        return False
    txt = head.strip()
    if not txt:
        return False
    if re.search(r"(?im)^\s*(openapi|swagger)\s*:\s*", txt):
        return True
    if re.search(r"(?im)^\s*\\{", txt) and re.search(r"(?i)\"(openapi|swagger)\"\\s*:", txt):
        return True
    return False


def _detect_duplicate_openapi(root: str, files: List[str]) -> List[CoherenceFinding]:
    candidates = []
    for p in files:
        pl = p.lower()
        if pl.endswith(("openapi.yaml", "openapi.yml", "openapi.json")):
            candidates.append(p)
        if pl.endswith(("swagger.yaml", "swagger.yml", "swagger.json")):
            candidates.append(p)
    uniq = []
    for rel in sorted(set(candidates)):
        abs_path = os.path.join(root, rel)
        if os.path.exists(abs_path) and _looks_like_openapi_spec(abs_path):
            uniq.append(rel)
    if len(uniq) <= 1:
        return []
    msg = "Multiple OpenAPI/Swagger specs detected; choose one canonical source of truth to avoid drift."
    return [CoherenceFinding(
        id="duplicate_openapi",
        severity="high",
        message=msg,
        paths=uniq[:10],
        suggested_attendees=_suggest_attendees(uniq[:10]),
    )]


def _is_vite_project(pkg: Dict[str, object]) -> bool:
    scripts = (pkg.get("scripts") or {}) if isinstance(pkg.get("scripts"), dict) else {}
    if any("vite" in str(v).lower() for v in scripts.values()):
        return True
    deps = (pkg.get("devDependencies") or {}) if isinstance(pkg.get("devDependencies"), dict) else {}
    return "vite" in {str(k).lower() for k in deps.keys()}


def _is_cra_project(pkg: Dict[str, object]) -> bool:
    deps = (pkg.get("dependencies") or {}) if isinstance(pkg.get("dependencies"), dict) else {}
    dev = (pkg.get("devDependencies") or {}) if isinstance(pkg.get("devDependencies"), dict) else {}
    keys = {str(k).lower() for k in list(deps.keys()) + list(dev.keys())}
    return "react-scripts" in keys


def _detect_frontend_env_mismatch(root: str, files: List[str]) -> List[CoherenceFinding]:
    findings: List[CoherenceFinding] = []
    for frontend_root in ("frontend", "fe"):
        pkg_path = os.path.join(root, frontend_root, "package.json")
        if not os.path.exists(pkg_path):
            continue
        pkg = _load_json(pkg_path)
        if not isinstance(pkg, dict):
            continue
        is_vite = _is_vite_project(pkg)
        is_cra = _is_cra_project(pkg)
        if not (is_vite or is_cra):
            continue
        prefix = f"{frontend_root}/"
        src_files = [p for p in files if p.startswith(prefix) and p.endswith((".js", ".jsx", ".ts", ".tsx"))]
        bad_paths: List[str] = []
        for rel in src_files[:800]:
            abs_path = os.path.join(root, rel)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(200_000)
            except OSError:
                continue
            if is_vite and re.search(r"\bprocess\.env\.REACT_APP_", text):
                bad_paths.append(rel)
                if len(bad_paths) >= 5:
                    break
            if is_cra and re.search(r"\bimport\.meta\.env\.VITE_", text):
                bad_paths.append(rel)
                if len(bad_paths) >= 5:
                    break
        if bad_paths:
            if is_vite:
                msg = f"{frontend_root} appears to be a Vite app but uses CRA-style env vars (process.env.REACT_APP_*). Use import.meta.env.VITE_* instead."
                sev = "high"
            else:
                msg = f"{frontend_root} appears to be a CRA app but uses Vite-style env vars (import.meta.env.VITE_*). Use process.env.REACT_APP_* instead."
                sev = "high"
            findings.append(CoherenceFinding(
                id=f"frontend_env_mismatch:{frontend_root}",
                severity=sev,
                message=msg,
                paths=[_norm(os.path.relpath(pkg_path, root))] + bad_paths,
                suggested_attendees=_suggest_attendees([f"{frontend_root}/package.json"] + bad_paths),
            ))
    return findings


def _normalize_input_paths(root: str, paths: List[str]) -> List[str]:
    out: List[str] = []
    base = os.path.normcase(os.path.realpath(os.path.abspath(root)))
    for p in paths or []:
        raw = str(p or "").strip()
        if not raw:
            continue
        if os.path.isabs(raw):
            abs_p = os.path.normcase(os.path.realpath(os.path.abspath(raw)))
            try:
                within = os.path.commonpath([base, abs_p]) == base
            except ValueError:
                within = abs_p == base or abs_p.startswith(base + os.sep)
            if not within:
                continue
            rel = os.path.relpath(abs_p, base)
            out.append(_norm(rel))
        else:
            out.append(_norm(raw))
    return sorted(set(out))


def run_coherence_checks(
    workspace_root: str,
    *,
    paths: Optional[List[str]] = None,
    changed_since: Optional[float] = None,
    max_files: int = 4000,
) -> List[Dict[str, object]]:
    root = os.path.realpath(os.path.abspath(workspace_root))
    if paths:
        files = _normalize_input_paths(root, paths)
    else:
        files = _find_repo_files(root, max_files=max_files)
    if changed_since is not None:
        kept: List[str] = []
        for rel in files:
            abs_p = os.path.join(root, rel)
            try:
                if os.path.getmtime(abs_p) >= float(changed_since):
                    kept.append(rel)
            except OSError:
                continue
        files = kept
    findings: List[CoherenceFinding] = []
    findings += _detect_duplicate_openapi(root, files)
    findings += _detect_frontend_env_mismatch(root, files)
    return [f.to_dict() for f in findings]
