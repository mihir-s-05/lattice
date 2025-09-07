from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Literal, Optional, Union

from .artifacts import Artifact, ArtifactStore, sha256_bytes


@dataclass
class EvidenceArtifact:
    type: Literal["artifact"] = "artifact"
    id: str = ""  
    hash: str = ""  


@dataclass
class EvidenceRagDoc:
    type: Literal["rag_doc"] = "rag_doc"
    id: str = ""  
    score: float = 0.0
    hash: Optional[str] = None


EvidenceRef = Union[EvidenceArtifact, EvidenceRagDoc]


def evidence_from_artifact_path(run_dir: str, rel_path: str) -> EvidenceArtifact:
    abspath = os.path.join(run_dir, rel_path if rel_path.startswith("artifacts/") else os.path.join("artifacts", rel_path))
    try:
        with open(abspath, "rb") as f:
            data = f.read()
        h = sha256_bytes(data)
    except Exception:
        h = ""
    return EvidenceArtifact(id=(rel_path if rel_path.startswith("artifacts/") else os.path.join("artifacts", rel_path)), hash=f"sha256:{h}")


def evidence_from_artifact(art: Artifact) -> EvidenceArtifact:
    return EvidenceArtifact(id=art.path, hash=f"sha256:{art.sha256}")


def evidence_from_rag(doc_id: str, score: float, hash_val: Optional[str] = None) -> EvidenceRagDoc:
    return EvidenceRagDoc(id=doc_id, score=float(score), hash=hash_val)


def evidence_list_to_jsonable(evs: Optional[List[EvidenceRef]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in evs or []:
        try:
            out.append(asdict(e))
        except Exception:
            if isinstance(e, dict):
                out.append(e)  
    return out


def compute_current_sha256(run_dir: str, rel_path: str) -> Optional[str]:
    try:
        abspath = os.path.join(run_dir, rel_path)
        with open(abspath, "rb") as f:
            return sha256_bytes(f.read())
    except Exception:
        return None
