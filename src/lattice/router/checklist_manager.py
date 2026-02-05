from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..artifacts import ArtifactStore
from ..checklist import Checklist, default_router_checklist
from ..runlog import RunLogger
from ..stage_gates import StageGate


class ChecklistManager:
    def __init__(self, run_dir: str, artifacts: ArtifactStore, logger: RunLogger) -> None:
        self.run_dir = run_dir
        self.artifacts = artifacts
        self.logger = logger

        self._created: bool = False
        self._checklist: Checklist = self._load_or_init()

    @property
    def checklist(self) -> Checklist:
        return self._checklist

    @property
    def created(self) -> bool:
        return self._created

    def relpath(self) -> str:
        return os.path.join("artifacts", "checklist.json")

    def _load_or_init(self) -> Checklist:
        rel = self.relpath()
        abs_path = os.path.join(self.run_dir, rel)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    chk = Checklist.from_json(f.read())
                    self._created = True
                    return chk
            except (OSError, ValueError, TypeError):
                return default_router_checklist()
        return default_router_checklist()

    def save(self) -> None:
        self.artifacts.add_text("checklist.json", self._checklist.to_json(), tags=["checklist"])
        self._created = True
        self.logger.log("checklist_saved", path=self.relpath())

    def sync_state(self, latest_gate_results: List[StageGate]) -> None:
        if not self._checklist or not self._checklist.items:
            return
        gate_status = {g.id: g.status for g in (latest_gate_results or [])}
        mapping = {
            "sg_api_contract": "contracts",
            "sg_be_scaffold": "backend",
            "sg_fe_scaffold": "frontend",
            "sg_smoke": "tests",
        }
        updates: List[Dict[str, Any]] = []
        for gate_id, item_id in mapping.items():
            if gate_status.get(gate_id) == "passed":
                updates.append({"id": item_id, "status": "done", "note": f"auto: {gate_id} passed"})
        if updates:
            self._checklist.update_items(updates, allow_create=False)

    def ensure_initial(self, goal: str) -> None:
        _ = goal
        if self._created and self._checklist.items:
            return
        items = [
            {"id": "contracts", "description": "Define API contract + contract tests", "status": "pending", "required": True, "owner": "router"},
            {"id": "backend", "description": "Implement backend scaffold aligned to contract", "status": "pending", "required": True, "owner": "backend", "parent_id": "contracts"},
            {"id": "frontend", "description": "Implement frontend scaffold aligned to contract", "status": "pending", "required": True, "owner": "frontend", "parent_id": "contracts"},
            {"id": "tests", "description": "Run contract/smoke validation", "status": "pending", "required": True, "owner": "tests"},
            {"id": "finalize", "description": "Finalize deliverables + run summary", "status": "pending", "required": False, "owner": "router"},
        ]
        self.replace_items(items)
        self.save()

    def replace_items(self, items: List[Dict]) -> List[str]:
        created = self._checklist.replace_items(items)
        self._created = True
        return created

    def update_items(self, updates: List[Dict], allow_create: bool = True) -> List[str]:
        return self._checklist.update_items(updates, allow_create=allow_create)
