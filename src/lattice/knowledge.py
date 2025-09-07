from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .runlog import RunLogger
from .provenance import EvidenceRef


@dataclass
class KnowledgeEvent:
    ts: str
    source: str  
    refs: List[Dict[str, Any]]


class KnowledgeBus:
    

    def __init__(self, run_dir: str, logger: RunLogger) -> None:
        self.run_dir = run_dir
        self.logger = logger
        self.knowledge_dir = os.path.join(run_dir, "artifacts", "knowledge")
        os.makedirs(self.knowledge_dir, exist_ok=True)
        self.signals_path = os.path.join(self.knowledge_dir, "signals.jsonl")
        self._processed_dropins: set[str] = set()

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def signal(self, payload: Dict[str, Any]) -> KnowledgeEvent:
        src = str(payload.get("source") or "")
        refs = payload.get("refs") or []
        if src not in ("artifact", "rag_doc"):
            src = "artifact"
        ev = KnowledgeEvent(ts=self._ts(), source=src, refs=refs)
        line = json.dumps(asdict(ev), ensure_ascii=False)
        with open(self.signals_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        self.logger.log("knowledge_signal", refs=refs, ts=ev.ts)
        return ev

    def ingest_local_dropins(self) -> List[KnowledgeEvent]:
        events: List[KnowledgeEvent] = []
        for name in os.listdir(self.knowledge_dir):
            if not name.endswith(".json"):
                continue
            abs_path = os.path.join(self.knowledge_dir, name)
            if abs_path in self._processed_dropins:
                continue
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict) and obj.get("refs"):
                    ev = self.signal(obj)
                    events.append(ev)
                    self._processed_dropins.add(abs_path)
            except Exception:
                continue
        return events

    def read_all(self) -> List[KnowledgeEvent]:
        out: List[KnowledgeEvent] = []
        if not os.path.exists(self.signals_path):
            return out
        with open(self.signals_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    out.append(KnowledgeEvent(**obj))
                except Exception:
                    continue
        return out
