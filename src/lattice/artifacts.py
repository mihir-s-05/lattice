import hashlib
import json
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


@dataclass
class Artifact:
    id: str
    path: str
    mime: str
    sha256: str
    tags: List[str]
    meta: Dict[str, Any]


class ArtifactStore:
    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir
        self.art_dir = os.path.join(run_dir, "artifacts")
        self.index_path = os.path.join(self.art_dir, "index.json")
        ensure_dir(self.art_dir)
        if not os.path.exists(self.index_path):
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump({"artifacts": []}, f)

    def _load_index(self) -> Dict[str, Any]:
        with open(self.index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_index(self, data: Dict[str, Any]) -> None:
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add_text(self, filename: str, text: str, tags: Optional[List[str]] = None, meta: Optional[Dict[str, Any]] = None) -> Artifact:
        tags = tags or []
        meta = meta or {}
        rel_path = os.path.join("artifacts", filename)
        abs_path = os.path.join(self.run_dir, rel_path)
        ensure_dir(os.path.dirname(abs_path))
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(text)
        digest = sha256_bytes(text.encode("utf-8"))
        art_id = digest[:16]
        artifact = Artifact(
            id=art_id,
            path=rel_path,
            mime="text/plain",
            sha256=digest,
            tags=tags,
            meta=meta,
        )
        idx = self._load_index()
        idx["artifacts"].append(asdict(artifact))
        self._save_index(idx)
        return artifact

    def list(self) -> List[Artifact]:
        idx = self._load_index()
        out: List[Artifact] = []
        for a in idx.get("artifacts", []):
            out.append(Artifact(**a))
        return out
