from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

from .ids import ulid
from .artifacts import ArtifactStore
from .rag import RagIndex
from .constants import DEFAULT_HUDDLE_DIR


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


@dataclass
class DecisionSummary:
    id: str
    topic: str
    options: List[Any] = field(default_factory=list)
    decision: Optional[str] = None
    rationale: Optional[str] = None
    risks: List[str] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    contracts: List[Dict[str, Any]] = field(default_factory=list)
    links: List[Dict[str, Any]] = field(default_factory=list)
    sources: Optional[List[Dict[str, Any]]] = None
    meta: Dict[str, Any] = field(default_factory=dict)


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
    if isinstance(x, str):
        s = x.strip()
        if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
            try:
                val = json.loads(s)
                x = val
            except Exception:
                pass
    if isinstance(x, list):
        return [str(i) for i in x]
    return [str(x)]


def _coerce_list_obj(x: Any) -> List[Dict[str, Any]]:
    if x is None:
        return []
    if isinstance(x, str):
        s = x.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                val = json.loads(s)
                if isinstance(val, list):
                    x = val
            except Exception:
                pass
    if isinstance(x, list):
        out: List[Dict[str, Any]] = []
        for it in x:
            if isinstance(it, dict):
                out.append(it)
            elif isinstance(it, str):
                st = it.strip()
                if st.startswith("{") and st.endswith("}"):
                    try:
                        obj = json.loads(st)
                        if isinstance(obj, dict):
                            out.append(obj)
                            continue
                    except Exception:
                        pass
                out.append({"description": it})
        return out


def _content_key(d: DecisionSummary) -> Tuple[Any, Any, Any]:
    return (d.topic.strip() if d.topic else None, d.decision, d.rationale)


def ensure_unique_ids(decisions: List[DecisionSummary]) -> List[DecisionSummary]:
    """Ensure DecisionSummary IDs are unique by regenerating duplicates."""
    seen: set = set()
    for d in decisions:
        if d.id in seen:
            d.id = f"ds_{ulid()}"
        seen.add(d.id)
    return decisions


def dedupe_decisions(decisions: List[DecisionSummary]) -> List[DecisionSummary]:
    """Deduplicate identical decisions (topic, decision, rationale). Merge sources and links."""
    merged: Dict[Tuple[Any, Any, Any], DecisionSummary] = {}
    for d in decisions:
        key = _content_key(d)
        if key not in merged:
            merged[key] = d
        else:
            base = merged[key]
            try:
                src = _normalize_sources((base.sources or []) + (d.sources or []))
            except Exception:
                src = (base.sources or [])
            base.sources = src
            links: List[Dict[str, Any]] = []
            try:
                existing = {(l.get("title"), l.get("url")) for l in (base.links or []) if isinstance(l, dict)}
                for l in (base.links or []):
                    if isinstance(l, dict):
                        links.append(l)
                for l in (d.links or []):
                    if isinstance(l, dict):
                        k = (l.get("title"), l.get("url"))
                        if k not in existing:
                            links.append(l)
                            existing.add(k)
            except Exception:
                links = base.links or []
            base.links = links
    return list(merged.values())


def ensure_provenance_links(decisions: List[DecisionSummary], default_link: Optional[Dict[str, Any]] = None) -> List[DecisionSummary]:
    """Ensure that if a decision has sources, it also has at least one link. If missing, add default_link if provided."""
    for d in decisions:
        try:
            if d.sources and (not d.links or len(d.links) == 0):
                if default_link:
                    d.links = [default_link]
        except Exception:
            continue
    return decisions


def validate_decision_integrity(decisions: List[DecisionSummary]) -> bool:
    """Validate uniqueness, duplication, and provenance completeness.

    - IDs must be unique
    - Content (topic, decision, rationale) must be unique
    - If sources are present, links must not be empty
    """
    ids = [d.id for d in decisions]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate decision IDs found")
    content = [_content_key(d) for d in decisions]
    if len(content) != len(set(content)):
        raise ValueError("Duplicate decision content found")
    for d in decisions:
        if d.sources and not d.links:
            raise ValueError(f"Decision {d.id} has sources but no links")
    return True


def _normalize_decision_obj(obj: Dict[str, Any]) -> DecisionSummary:
    did = obj.get("id") or f"ds_{ulid()}"
    topic = obj.get("topic") or ""
    raw_opts = obj.get("options")
    opts_list = _coerce_list_obj(raw_opts)
    if opts_list:
        options: List[Any] = opts_list
    else:
        options = _coerce_list_str(raw_opts)
    decision = obj.get("decision")
    rationale = obj.get("rationale")
    risks = _coerce_list_str(obj.get("risks"))
    actions = _coerce_list_obj(obj.get("actions"))
    contracts = _coerce_list_obj(obj.get("contracts"))
    links = _coerce_list_obj(obj.get("links"))
    sources_in = obj.get("sources")
    if isinstance(sources_in, str):
        try:
            parsed = json.loads(sources_in)
            sources = parsed if isinstance(parsed, list) else None
        except Exception:
            sources = None
    else:
        sources = sources_in or None
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
        try:
            d.sources = _normalize_sources(d.sources)
        except Exception:
            pass
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


def _normalize_sources(sources: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not sources:
        return out
    seen_keys: set = set()
    for s in sources:
        if not isinstance(s, dict):
            continue
        t = s.get("type")
        if t == "external":
            url = (s.get("url") or "").strip()
            if not url:
                continue
            title = s.get("title") or None
            ts = s.get("ts") or s.get("time") or None
            key = (t, url)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append({"type": "external", "url": url, **({"title": title} if title else {}), **({"ts": ts} if ts else {})})
        elif t == "artifact":
            _id = (s.get("id") or s.get("path") or "").strip()
            if not _id:
                continue
            h = s.get("hash") or None
            key = (t, _id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append({"type": "artifact", "id": _id, **({"hash": h} if h else {})})
        elif t == "rag_doc":
            _id = (s.get("id") or "").strip()
            if not _id:
                continue
            score = s.get("score")
            h = s.get("hash") or None
            key = (t, _id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            item: Dict[str, Any] = {"type": "rag_doc", "id": _id}
            if score is not None:
                item["score"] = score
            if h:
                item["hash"] = h
            out.append(item)
        else:
            continue
    return out


def persist_decision_summary(
    run_dir: str,
    artifacts: ArtifactStore,
    rag_index: RagIndex,
    ds: DecisionSummary,
) -> Tuple[DecisionSummary, str]:
    """Write-through persistence with source normalization and update logging handled by caller.

    - Ensures artifacts/decisions/{id}.json is updated with normalized sources.
    - Returns (DecisionSummary, rel_path).
    """
    ds.sources = _normalize_sources(ds.sources)
    rel = os.path.join("artifacts", "decisions", f"{ds.id}.json")
    abs_path = os.path.join(run_dir, "artifacts", "decisions", f"{ds.id}.json")
    before: Optional[Dict[str, Any]] = None
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                before = json.load(f)
        except Exception:
            before = None
    ds_dict = asdict(ds)
    art = artifacts.add_text(
        filename=os.path.join("decisions", f"{ds.id}.json"),
        text=json.dumps(ds_dict, ensure_ascii=False, indent=2),
        tags=["decision", "json"],
        meta={"kind": "DecisionSummary", "id": ds.id},
    )
    try:
        rag_index.ingest_text(art.id, json.dumps(ds_dict, ensure_ascii=False), art.path)
    except Exception:
        pass
    return ds, art.path


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
    rel_dir = DEFAULT_HUDDLE_DIR
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
