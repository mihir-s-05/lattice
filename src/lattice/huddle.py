from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

from .ids import ulid
from .artifacts import ArtifactStore
from .rag import RagIndex


DECISION_DIR = "artifacts/decisions"
HUDDLE_DIR = "artifacts/huddles"


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


@dataclass
class DecisionSummary:
    id: str
    topic: str
    options: List[str] = field(default_factory=list)
    decision: Optional[str] = None
    rationale: Optional[str] = None
    risks: List[str] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    contracts: List[Dict[str, Any]] = field(default_factory=list)
    links: List[Dict[str, Any]] = field(default_factory=list)
    sources: Optional[List[Dict[str, Any]]] = None


@dataclass
class HuddleRecord:
    id: str
    requester: str
    attendees: List[str]
    transcript_path: str
    decisions: List[str]
    mode: str = "dialog"
    auto_decision: bool = False


def _coerce_list_str(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x]
    return [str(x)]


def _normalize_decision_obj(obj: Dict[str, Any]) -> DecisionSummary:
    did = obj.get("id") or f"ds_{ulid()}"
    topic = obj.get("topic") or ""
    options = _coerce_list_str(obj.get("options"))
    decision = obj.get("decision")
    rationale = obj.get("rationale")
    risks = _coerce_list_str(obj.get("risks"))
    actions = obj.get("actions") or []
    contracts = obj.get("contracts") or []
    links = obj.get("links") or []
    sources = obj.get("sources") or None
    return DecisionSummary(
        id=did,
        topic=topic,
        options=options,
        decision=decision,
        rationale=rationale,
        risks=risks,
        actions=actions,
        contracts=contracts,
        links=links,
        sources=sources,
    )


def _extract_json_objects(text: str) -> List[Dict[str, Any]]:
    
    objs: List[Dict[str, Any]] = []
    buf = []
    depth = 0
    in_obj = False
    for ch in text:
        if ch == '{':
            depth += 1
            in_obj = True
        if in_obj:
            buf.append(ch)
        if ch == '}':
            depth -= 1
            if in_obj and depth == 0:
                frag = "".join(buf).strip()
                try:
                    obj = json.loads(frag)
                    objs.append(obj)
                except Exception:
                    pass
                buf = []
                in_obj = False
    if not objs:
        try:
            val = json.loads(text)
            if isinstance(val, list):
                for v in val:
                    if isinstance(v, dict):
                        objs.append(v)
            elif isinstance(val, dict):
                objs.append(val)
        except Exception:
            pass
    return objs


def parse_decision_summaries(text: str) -> List[DecisionSummary]:
    objs = _extract_json_objects(text)
    if not objs:
        return [
            DecisionSummary(
                id=f"ds_{ulid()}",
                topic="Underspecified",
                options=[],
                decision=None,
                rationale=None,
                risks=[],
                actions=[],
                contracts=[],
                links=[],
            )
        ]
    out: List[DecisionSummary] = []
    for obj in objs:
        if isinstance(obj, dict):
            out.append(_normalize_decision_obj(obj))
    return out


def save_decisions(
    run_dir: str,
    artifacts: ArtifactStore,
    rag_index: RagIndex,
    decisions: List[DecisionSummary],
) -> List[Tuple[DecisionSummary, str]]:
    
    out: List[Tuple[DecisionSummary, str]] = []
    for d in decisions:
        rel_dir = DECISION_DIR
        abs_dir = os.path.join(run_dir, rel_dir)
        ensure_dir(abs_dir)
        art = artifacts.add_text(
            filename=os.path.join("decisions", f"{d.id}.json"),
            text=json.dumps(asdict(d), ensure_ascii=False, indent=2),
            tags=["decision", "json"],
            meta={"kind": "DecisionSummary", "id": d.id},
        )
        try:
            rag_index.ingest_text(art.id, json.dumps(asdict(d), ensure_ascii=False), art.path)
        except Exception:
            pass
        out.append((d, art.path))
    return out


def save_huddle(
    run_dir: str,
    artifacts: ArtifactStore,
    rag_index: RagIndex,
    requester: str,
    attendees: List[str],
    topic: str,
    questions: List[str],
    notes: str,
    decisions: List[DecisionSummary],
    *,
    hud_id: Optional[str] = None,
    mode: str = "dialog",
    auto_decision: bool = False,
    messages: Optional[List[Dict[str, str]]] = None,
) -> Tuple[HuddleRecord, str, str]:
    
    hud_id = hud_id or f"hud_{ulid()}"
    rel_dir = HUDDLE_DIR
    abs_dir = os.path.join(run_dir, rel_dir)
    ensure_dir(abs_dir)
    transcript_rel = os.path.join(rel_dir, f"{hud_id}.md")
    transcript_abs = os.path.join(run_dir, transcript_rel)
    transcript = [
        f"# Huddle: {topic}",
        f"Attendees: {', '.join(attendees)}",
        f"Mode: {mode}",
        "",
        "## Questions",
        "".join([f"- {q}\n" for q in questions]) if questions else "- (none)\n",
        ]
    if messages:
        transcript += [
            "",
            "## Transcript",
        ]
        for m in messages:
            ts = m.get("ts") or ""
            speaker = m.get("from") or "?"
            content = (m.get("content") or "").rstrip()
            transcript.append(f"- [{ts}] {speaker}: {content}")
    transcript += [
        "",
        "## Notes",
        (notes.strip() + "\n") if notes else "",
    ]
    with open(transcript_abs, "w", encoding="utf-8") as f:
        f.write("\n".join(transcript))
    try:
        rag_index.ingest_file(transcript_abs, doc_id=hud_id)
    except Exception:
        pass

    rec = HuddleRecord(
        id=hud_id,
        requester=requester,
        attendees=attendees,
        transcript_path=transcript_rel,
        decisions=[d.id for d in decisions],
        mode=mode,
        auto_decision=auto_decision,
    )
    record_rel = os.path.join(rel_dir, f"{hud_id}.json")
    record_abs = os.path.join(run_dir, record_rel)
    with open(record_abs, "w", encoding="utf-8") as f:
        json.dump(asdict(rec), f, indent=2)
    artifacts.add_text(
        filename=os.path.join("huddles", f"{hud_id}.json"),
        text=json.dumps(asdict(rec), ensure_ascii=False, indent=2),
        tags=["huddle", "json"],
        meta={"kind": "HuddleRecord", "id": hud_id},
    )

    return rec, transcript_rel, record_rel


def decision_injection_text(decisions: List[DecisionSummary]) -> str:
    lines: List[str] = ["DecisionSummaries from recent Huddle:"]
    for d in decisions:
        lines.append(f"- Topic: {d.topic}")
        if d.decision:
            lines.append(f"  Decision: {d.decision}")
        if d.rationale:
            lines.append(f"  Rationale: {d.rationale}")
        if d.contracts:
            for c in d.contracts:
                nm = c.get("name") or "contract"
                h = c.get("schema_hash") or ""
                lines.append(f"  Contract: {nm} {h}")
        if d.actions:
            for a in d.actions[:3]:
                try:
                    lines.append(f"  Action: {a.get('owner')}: {a.get('task')}")
                except Exception:
                    pass
    return "\n".join(lines)
