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
        except Exception:
            continue
    return out


def _create_deliverables_zip(run_dir: str) -> str:
    rel = os.path.join("artifacts", "deliverables", DEFAULT_DELIVERABLES_FILE)
    abs_path = os.path.join(run_dir, rel)
    _ensure_dir(os.path.dirname(abs_path))
    roots = [
        os.path.join(run_dir, DEFAULT_BACKEND_DIR),
        os.path.join(run_dir, DEFAULT_FRONTEND_DIR),
        os.path.join(run_dir, DEFAULT_CONTRACTS_DIR),
    ]
    readme = os.path.join(run_dir, "artifacts", "README.md")
    with zipfile.ZipFile(abs_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _, filenames in os.walk(root):
                for fn in filenames:
                    ap = os.path.join(dirpath, fn)
                    rp = os.path.relpath(ap, run_dir)
                    zf.write(ap, rp)
        if os.path.exists(readme):
            zf.write(readme, os.path.relpath(readme, run_dir))
    return rel


def _write_decision_log_and_citations(run_dir: str, decisions: List[DecisionSummary], logger: RunLogger) -> Tuple[str, str]:
    dec_dir = os.path.join(run_dir, DEFAULT_DECISION_DIR)
    _ensure_dir(dec_dir)
    log_rel = os.path.join(DEFAULT_DECISION_DIR, DEFAULT_DECISION_LOG_FILE)
    log_abs = os.path.join(run_dir, log_rel)
    lines: List[str] = ["# Decision Log", ""]
    cite_index: Dict[str, List[Dict[str, Any]]] = {}
    for d in decisions:
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
                try:
                    title = s.get("title")
                    url = s.get("url")
                    if title:
                        lines.append(f"- {title}: {url}")
                    else:
                        lines.append(f"- {url}")
                except Exception:
                    continue
            for s in artifacts:
                try:
                    lines.append(f"- artifact:{s.get('id')} ({s.get('hash','')})")
                except Exception:
                    continue
            for s in rags:
                try:
                    lines.append(f"- rag_doc:{s.get('id')} score={s.get('score')}")
                except Exception:
                    continue
            try:
                if isinstance(getattr(d, "meta", None), dict) and d.meta.get("auto_populated_sources"):
                    lines.append("(sources auto-populated from recent web search)")
            except Exception:
                pass
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


def _compute_drift(run_dir: str, decisions: List[DecisionSummary]) -> List[Dict[str, Any]]:
    drifts: List[Dict[str, Any]] = []
    for d in decisions:
        for s in (d.sources or []):
            try:
                if s.get("type") == "artifact":
                    rel = s.get("id") or ""
                    prev = (s.get("hash") or "").replace("sha256:", "")
                    now = compute_current_sha256(run_dir, rel)
                    if prev and now and prev != now:
                        drifts.append({
                            "type": "evidence_drift",
                            "details": f"Artifact changed: {rel}",
                            "refs": [s],
                        })
            except Exception:
                continue
    current_openapi = os.path.join(DEFAULT_CONTRACTS_DIR, "openapi.yaml")
    current_hash = compute_current_sha256(run_dir, current_openapi)
    if current_hash:
        for d in decisions:
            for c in d.contracts or []:
                try:
                    h = c.get("schema_hash")
                    if h and h != current_hash:
                        drifts.append({
                            "type": "spec_drift",
                            "details": "OpenAPI hash changed since decision",
                            "refs": [{"type": "artifact", "id": current_openapi, "hash": f"sha256:{current_hash}"}],
                        })
                except Exception:
                    continue
    return drifts


def run_finalization(
    run_dir: str,
    artifacts: ArtifactStore,
    logger: RunLogger,
    decisions: List[DecisionSummary],
    evaluator: Optional[GateEvaluator] = None,
) -> Dict[str, Any]:
    if evaluator is None:
        evaluator = GateEvaluator(run_dir, artifacts, logger)
    evaluator.load_test_results()

    tests = _collect_test_results(run_dir)

    linters: List[Dict[str, Any]] = []

    decision_integrity: Dict[str, Any] = {"status": "ok"}
    try:
        decisions = ensure_unique_ids(decisions)
        decisions = dedupe_decisions(decisions)
        decisions = ensure_provenance_links(decisions)
        validate_decision_integrity(decisions)
    except Exception as e:
        decision_integrity = {"status": "error", "error": str(e)}

    drift = _compute_drift(run_dir, decisions)

    zip_rel = _create_deliverables_zip(run_dir)

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
