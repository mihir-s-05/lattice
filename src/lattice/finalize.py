from __future__ import annotations

import json
import os
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from .artifacts import ArtifactStore
from .runlog import RunLogger
from .stage_gates import GateEvaluator, StageGate
from .huddle import (
    DecisionSummary,
    ensure_unique_ids,
    dedupe_decisions,
    ensure_provenance_links,
    validate_decision_integrity,
)
from .provenance import compute_current_sha256
from .constants import (
    DEFAULT_DECISION_DIR,
    DEFAULT_BACKEND_DIR,
    DEFAULT_FRONTEND_DIR, 
    DEFAULT_CONTRACTS_DIR,
    DEFAULT_RESULTS_DIR,
    DEFAULT_DELIVERABLES_FILE,
    DEFAULT_DECISION_LOG_FILE,
    DEFAULT_CITATIONS_INDEX_FILE,
    DEFAULT_CITATIONS_DIR
)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _collect_test_results(run_dir: str) -> List[Dict[str, Any]]:
    base = os.path.join(run_dir, DEFAULT_RESULTS_DIR)
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(base):
        return out
    for name in os.listdir(base):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(base, name), "r", encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            continue
    return out


def _create_deliverables_zip(run_dir: str, workspace_root: Optional[str] = None, run_started_at: Optional[float] = None) -> str:
    rel = os.path.join("artifacts", "deliverables", DEFAULT_DELIVERABLES_FILE)
    abs_path = os.path.join(run_dir, rel)
    _ensure_dir(os.path.dirname(abs_path))
    workspace_root = workspace_root or os.getcwd()
    roots: List[Tuple[str, str, Optional[float]]] = [
        (os.path.join(run_dir, DEFAULT_BACKEND_DIR), run_dir, None),
        (os.path.join(run_dir, DEFAULT_FRONTEND_DIR), run_dir, None),
        (os.path.join(run_dir, DEFAULT_CONTRACTS_DIR), run_dir, None),
        (os.path.join(workspace_root, "backend"), workspace_root, run_started_at),
        (os.path.join(workspace_root, "frontend"), workspace_root, run_started_at),
        (os.path.join(workspace_root, "public"), workspace_root, run_started_at),
        (os.path.join(workspace_root, "static"), workspace_root, run_started_at),
        (os.path.join(workspace_root, "contracts"), workspace_root, run_started_at),
    ]
    readme_candidates = [
        (os.path.join(run_dir, "artifacts", "README.md"), run_dir),
        (os.path.join(workspace_root, "README.md"), workspace_root),
        (os.path.join(workspace_root, "readme.md"), workspace_root),
    ]
    with zipfile.ZipFile(abs_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, base, since in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _, filenames in os.walk(root):
                parts = os.path.normpath(dirpath).split(os.sep)
                if any(p in ("__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", ".git") for p in parts):
                    continue
                for fn in filenames:
                    ap = os.path.join(dirpath, fn)
                    if fn.endswith((".pyc", ".pyo")):
                        continue
                    if since:
                        try:
                            if os.path.getmtime(ap) < (since - 0.1):
                                continue
                        except OSError:
                            continue
                    rp = os.path.relpath(ap, base)
                    zf.write(ap, rp)
        for readme, base in readme_candidates:
            if os.path.exists(readme):
                if run_started_at and base == workspace_root:
                    try:
                        if os.path.getmtime(readme) < (run_started_at - 0.1):
                            continue
                    except OSError:
                        continue
                zf.write(readme, os.path.relpath(readme, base))
    return rel


def _write_decision_log_and_citations(run_dir: str, decisions: List[DecisionSummary], logger: RunLogger) -> Tuple[str, str]:
    dec_dir = os.path.join(run_dir, DEFAULT_DECISION_DIR)
    _ensure_dir(dec_dir)
    log_rel = os.path.join(DEFAULT_DECISION_DIR, DEFAULT_DECISION_LOG_FILE)
    log_abs = os.path.join(run_dir, log_rel)
    lines: List[str] = ["# Decision Log", ""]
    cite_index: Dict[str, List[Dict[str, Any]]] = {}
    from .huddle import _normalize_sources
    for d in decisions:
        d.sources = _normalize_sources(getattr(d, "sources", None))
        
        lines.append(f"## {d.topic} ({d.id})")
        if d.decision:
            lines.append(f"Decision: {d.decision}")
        if d.rationale:
            lines.append(f"Rationale: {d.rationale}")
        if d.sources:
            lines.append("Sources:")
            externals = [s for s in (d.sources or []) if isinstance(s, dict) and s.get("type") == "external"]
            artifacts = [s for s in (d.sources or []) if isinstance(s, dict) and s.get("type") == "artifact"]
            rags = [s for s in (d.sources or []) if isinstance(s, dict) and s.get("type") == "rag_doc"]
            for s in externals:
                title = s.get("title")
                url = s.get("url")
                if title:
                    lines.append(f"- {title}: {url}")
                else:
                    lines.append(f"- {url}")
            for s in artifacts:
                lines.append(f"- artifact:{s.get('id')} ({s.get('hash','')})")
            for s in rags:
                lines.append(f"- rag_doc:{s.get('id')} score={s.get('score')}")
            if isinstance(getattr(d, "meta", None), dict) and d.meta.get("auto_populated_sources"):
                lines.append("(sources auto-populated from recent web search)")
        lines.append("")
        if d.sources:
            cite_index[d.id] = list(d.sources)
            logger.log("citations_indexed", ds_id=d.id, sources=d.sources)
    with open(log_abs, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    cite_rel = os.path.join(DEFAULT_CITATIONS_DIR, DEFAULT_CITATIONS_INDEX_FILE)
    cite_abs = os.path.join(run_dir, cite_rel)
    _ensure_dir(os.path.dirname(cite_abs))
    with open(cite_abs, "w", encoding="utf-8") as f:
        json.dump(cite_index, f, indent=2)
    return log_rel, cite_rel


def _compute_drift(run_dir: str, decisions: List[DecisionSummary], workspace_root: Optional[str] = None) -> List[Dict[str, Any]]:
    drifts: List[Dict[str, Any]] = []
    workspace_root = workspace_root or os.getcwd()
    def _hash_for(rel_path: str) -> Optional[str]:
        h = compute_current_sha256(run_dir, rel_path)
        if h:
            return h
        rel = rel_path
        if rel.startswith("artifacts/"):
            rel = rel[len("artifacts/") :]
        return compute_current_sha256(workspace_root, rel)
    for d in decisions:
        for s in (d.sources or []):
            if not isinstance(s, dict):
                continue
            if s.get("type") != "artifact":
                continue
            rel = s.get("id") or ""
            prev = (s.get("hash") or "").replace("sha256:", "")
            now = _hash_for(rel)
            if prev and now and prev != now:
                drifts.append({
                    "type": "evidence_drift",
                    "details": f"Artifact changed: {rel}",
                    "refs": [s],
                })
    current_openapi = os.path.join(DEFAULT_CONTRACTS_DIR, "openapi.yaml")
    current_hash = _hash_for(current_openapi)
    if current_hash:
        for d in decisions:
            for c in d.contracts or []:
                if not isinstance(c, dict):
                    continue
                h = c.get("schema_hash")
                if h and h != current_hash:
                    drifts.append({
                        "type": "spec_drift",
                        "details": "OpenAPI hash changed since decision",
                        "refs": [{"type": "artifact", "id": current_openapi, "hash": f"sha256:{current_hash}"}],
                    })
    return drifts


def run_finalization(
    run_dir: str,
    artifacts: ArtifactStore,
    logger: RunLogger,
    decisions: List[DecisionSummary],
    evaluator: Optional[GateEvaluator] = None,
    workspace_root: Optional[str] = None,
    run_started_at: Optional[float] = None,
) -> Dict[str, Any]:
    if evaluator is None:
        evaluator = GateEvaluator(run_dir, artifacts, logger, workspace_root=workspace_root)
    evaluator.load_test_results()

    tests = _collect_test_results(run_dir)

    linters: List[Dict[str, Any]] = []

    decision_integrity: Dict[str, Any] = {"status": "ok"}
    try:
        decisions = ensure_unique_ids(decisions)
        decisions = dedupe_decisions(decisions)
        decisions = ensure_provenance_links(decisions)
        validate_decision_integrity(decisions)
    except (TypeError, ValueError) as e:
        decision_integrity = {"status": "error", "error": str(e)}

    drift = _compute_drift(run_dir, decisions, workspace_root=workspace_root)

    zip_rel = _create_deliverables_zip(run_dir, workspace_root=workspace_root, run_started_at=run_started_at)

    dec_log_rel, cite_rel = _write_decision_log_and_citations(run_dir, decisions, logger)

    report = {
        "linters": linters,
        "tests": tests,
        "drift": drift,
        "deliverables": [zip_rel],
        "decision_log_path": dec_log_rel,
        "citation_index_path": cite_rel,
        "decision_integrity": decision_integrity,
    }
    artifacts.add_text(os.path.join("finalization", "report.json"), json.dumps(report, indent=2), tags=["finalization"])
    return report
