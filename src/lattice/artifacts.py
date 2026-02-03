import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _path_for_containment_check(path: str) -> str:
    path = os.path.normcase(path)
    if os.name == "nt":
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path[len("\\\\?\\UNC\\") :]
        elif path.startswith("\\\\?\\"):
            path = path[len("\\\\?\\") :]
    return os.path.normpath(path)


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
        self._lock = threading.Lock()
        ensure_dir(self.art_dir)
        if not os.path.exists(self.index_path):
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump({"artifacts": []}, f)

    def _normalize_relpath(self, filename: str) -> str:
        raw = str(filename or "").strip()
        if not raw:
            raise ValueError("filename is required")
        raw = raw.replace("\\", "/")
        if raw.startswith("artifacts/"):
            raw = raw[len("artifacts/") :]
        raw = raw.lstrip("/")
        norm = os.path.normpath(raw)
        if os.path.isabs(norm):
            raise ValueError("absolute paths are not allowed for artifacts")
        if norm == ".." or norm.startswith(".." + os.sep):
            raise ValueError("artifact path escapes artifacts directory")
        return norm

    def _artifact_abspath(self, filename: str) -> str:
        rel = self._normalize_relpath(filename)
        base_real = os.path.realpath(self.art_dir)
        abs_real = os.path.realpath(os.path.join(self.art_dir, rel))
        base = _path_for_containment_check(base_real)
        abs_path = _path_for_containment_check(abs_real)
        try:
            within = os.path.commonpath([base, abs_path]) == base
        except ValueError:
            within = abs_path == base or abs_path.startswith(base + os.sep)
        if not within:
            raise ValueError("artifact path escapes artifacts directory")
        return abs_real

    def _load_index(self) -> Dict[str, Any]:
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("artifacts", []), list):
                return data
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return {"artifacts": []}
        return {"artifacts": []}

    def _save_index(self, data: Dict[str, Any]) -> None:
        ensure_dir(os.path.dirname(self.index_path))
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="index.", suffix=".tmp", dir=os.path.dirname(self.index_path))
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.index_path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                return

    def add_text(self, filename: str, text: str, tags: Optional[List[str]] = None, meta: Optional[Dict[str, Any]] = None) -> Artifact:
        tags = tags or []
        meta = meta or {}
        safe_name = self._normalize_relpath(filename)
        rel_path = os.path.join("artifacts", safe_name)
        abs_path = self._artifact_abspath(safe_name)
        with self._lock:
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
            arts = idx.get("artifacts", [])
            new_list: List[Dict[str, Any]] = []
            for a in arts:
                if isinstance(a, dict):
                    if a.get("path") != rel_path:
                        new_list.append(a)
            arts = new_list
            arts.append(asdict(artifact))
            idx["artifacts"] = arts
            self._save_index(idx)
            return artifact

    def list(self) -> List[Artifact]:
        idx = self._load_index()
        out: List[Artifact] = []
        for a in idx.get("artifacts", []):
            out.append(Artifact(**a))
        return out
