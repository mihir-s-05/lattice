from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


ALLOWED_STATUSES = {"pending", "done", "skipped", "blocked"}
ALLOWED_ACTIONS = {"add", "update", "remove"}


@dataclass
class ChecklistItem:
    id: str
    description: str
    status: str = "pending"
    required: bool = True
    owner: str = "router"
    note: Optional[str] = None
    parent_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Checklist:
    def __init__(self, items: List[ChecklistItem]) -> None:
        self.items = items

    @classmethod
    def from_json(cls, text: str) -> "Checklist":
        obj = json.loads(text)
        items = []
        for it in obj.get("items", []):
            if not isinstance(it, dict) or "id" not in it:
                continue
            items.append(ChecklistItem(
                id=str(it.get("id")),
                description=str(it.get("description") or ""),
                status=str(it.get("status") or "pending"),
                required=bool(it.get("required", True)),
                owner=str(it.get("owner") or "router"),
                note=it.get("note"),
                parent_id=it.get("parent_id"),
            ))
        return cls(items)

    def to_json(self) -> str:
        return json.dumps({"items": [i.to_dict() for i in self.items]}, indent=2)

    def get(self, item_id: str) -> Optional[ChecklistItem]:
        for it in self.items:
            if it.id == item_id:
                return it
        return None

    def replace_items(self, items: List[Dict[str, Any]]) -> List[str]:
        new_items: List[ChecklistItem] = []
        for it in items:
            if not isinstance(it, dict) or "id" not in it:
                continue
            status = str(it.get("status") or "pending").lower()
            if status not in ALLOWED_STATUSES:
                status = "pending"
            new_items.append(ChecklistItem(
                id=str(it.get("id")),
                description=str(it.get("description") or ""),
                status=status,
                required=bool(it.get("required", True)),
                owner=str(it.get("owner") or "router"),
                note=it.get("note"),
                parent_id=it.get("parent_id"),
            ))
        self.items = new_items
        return [i.id for i in self.items]

    def update_items(self, updates: List[Dict[str, Any]], allow_create: bool = True) -> List[str]:
        changed: List[str] = []
        for upd in updates:
            if not isinstance(upd, dict):
                continue
            item_id = str(upd.get("id") or "")
            if not item_id:
                continue
            action = str(upd.get("action") or "update").lower()
            if action not in ALLOWED_ACTIONS:
                action = "update"
            it = self.get(item_id)
            if action == "remove":
                if it:
                    self.items = [x for x in self.items if x.id != item_id]
                    changed.append(item_id)
                continue
            if not it and allow_create:
                it = ChecklistItem(
                    id=item_id,
                    description=str(upd.get("description") or ""),
                    status="pending",
                    required=bool(upd.get("required", True)),
                    owner=str(upd.get("owner") or "router"),
                    note=upd.get("note"),
                    parent_id=upd.get("parent_id"),
                )
                self.items.append(it)
            if not it:
                continue
            status = str(upd.get("status") or "").lower()
            if status and status in ALLOWED_STATUSES:
                it.status = status
            note = upd.get("note")
            if note is not None:
                it.note = str(note)
            desc = upd.get("description")
            if desc is not None:
                it.description = str(desc)
            if "required" in upd:
                it.required = bool(upd.get("required"))
            owner = upd.get("owner")
            if owner is not None:
                it.owner = str(owner)
            if "parent_id" in upd:
                it.parent_id = upd.get("parent_id")
            changed.append(item_id)
        return changed

    def mark_done(self, item_id: str, note: Optional[str] = None) -> None:
        it = self.get(item_id)
        if not it:
            return
        it.status = "done"
        if note is not None:
            it.note = note

    def mark_blocked(self, item_id: str, note: Optional[str] = None) -> None:
        it = self.get(item_id)
        if not it:
            return
        it.status = "blocked"
        if note is not None:
            it.note = note

    def is_complete(self) -> bool:
        for it in self.items:
            if not it.required:
                continue
            if it.status not in ("done", "skipped"):
                return False
        return True

    def required_incomplete(self) -> List[str]:
        missing: List[str] = []
        for it in self.items:
            if not it.required:
                continue
            if it.status not in ("done", "skipped"):
                missing.append(it.id)
        return missing

    def prompt_summary(self, owner: Optional[str] = None) -> str:
        if not self.items:
            return "Checklist: none defined."
        pending = []
        for it in self.items:
            if owner and it.owner not in (owner, "router", "shared"):
                continue
            if it.status in ("pending", "blocked"):
                pending.append(f"- [{it.status}] {it.id}: {it.description}")
        if not pending:
            return "Checklist: all required items complete."
        return "Checklist (pending):\n" + "\n".join(pending)


def default_router_checklist() -> Checklist:
    return Checklist([])
