from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .provenance import EvidenceRef, evidence_list_to_jsonable


@dataclass
class PlanNode:
    id: str
    name: str
    modeSegment: str
    evidence: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PlanGraph:
    nodes: List[PlanNode] = field(default_factory=list)
    edges: List[List[str]] = field(default_factory=list)
    mode_by_segment: Dict[str, str] = field(default_factory=dict)
    reasons: List[Dict[str, Any]] = field(default_factory=list)

    def add_node(self, node: PlanNode) -> None:
        self.nodes.append(node)

    def add_edge(self, a: str, b: str) -> None:
        self.edges.append([a, b])

    def add_reason(self, reason_type: str, details: str) -> None:
        self.reasons.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": reason_type,
            "details": details,
        })

    def snapshot(self) -> Dict[str, Any]:
        return {
            "nodes": [asdict(n) for n in self.nodes],
            "edges": self.edges,
            "mode_by_segment": dict(self.mode_by_segment),
            "reasons": list(self.reasons),
        }

    def save(self, run_dir: str, rel_path: str = os.path.join("artifacts", "plans", "plan_graph.json")) -> str:
        abs_path = os.path.join(run_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(self.snapshot(), f, indent=2)
        return rel_path