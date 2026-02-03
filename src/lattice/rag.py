import json
import math
import os
import re
import threading
import hashlib
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple
from .constants import (
    DEFAULT_RAG_TOKEN_LIMIT,
    DEFAULT_RAG_SNIPPET_LENGTH,
    DEFAULT_RAG_TOP_K,
    DEFAULT_RAG_MAX_FILE_SIZE
)


WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in WORD_RE.findall(text)]


class RagIndex:
    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir
        self.idx_path = os.path.join(run_dir, "rag_index.json")
        self._lock = threading.Lock()
        self.docs: Dict[str, Dict[str, Any]] = {}
        self.vocab: Dict[str, int] = {}
        self.idf: Dict[int, float] = {}
        self.doc_vectors: Dict[str, Dict[int, float]] = {}
        self.semantic_dim: int = 128
        self.doc_semantic_vectors: Dict[str, List[float]] = {}
        self.loaded = False
        if os.path.exists(self.idx_path):
            self._load()

    def _load(self) -> None:
        with self._lock:
            with open(self.idx_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            docs = data.get("docs")
            self.docs = docs if isinstance(docs, dict) else {}
            for doc in self.docs.values():
                if isinstance(doc, dict):
                    doc.setdefault("tags", [])
                    doc.setdefault("meta", {})
            self.vocab = {k: int(v) for k, v in data.get("vocab", {}).items()}
            self.idf = {int(k): float(v) for k, v in data.get("idf", {}).items()}
            self.doc_vectors = {
                doc_id: {int(i): float(w) for i, w in vec.items()} for doc_id, vec in data.get("doc_vectors", {}).items()
            }
            self.semantic_dim = int(data.get("semantic_dim") or self.semantic_dim)
            sem = data.get("doc_semantic_vectors") or {}
            if isinstance(sem, dict):
                self.doc_semantic_vectors = {
                    str(doc_id): [float(x) for x in (vec or [])] for doc_id, vec in sem.items() if isinstance(vec, list)
                }
            self.loaded = True

    def _save(self) -> None:
        data = {
            "docs": self.docs,
            "vocab": {k: v for k, v in self.vocab.items()},
            "idf": {str(k): v for k, v in self.idf.items()},
            "doc_vectors": {doc_id: {str(i): w for i, w in vec.items()} for doc_id, vec in self.doc_vectors.items()},
            "semantic_dim": self.semantic_dim,
            "doc_semantic_vectors": {doc_id: vec for doc_id, vec in self.doc_semantic_vectors.items()},
        }
        with self._lock:
            with open(self.idx_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

    def _recompute_idf(self) -> None:
        N = max(1, len(self.docs))
        df: Dict[int, int] = defaultdict(int)
        for doc in self.docs.values():
            terms = set(doc.get("tokens", []))
            for t in terms:
                idx = self.vocab.setdefault(t, len(self.vocab))
                df[idx] += 1
        self.idf = {}
        for t, idx in self.vocab.items():
            dfi = df.get(idx, 0)
            self.idf[idx] = math.log((N + 1) / (dfi + 1)) + 1.0

    def _tfidf(self, tokens: List[str]) -> Dict[int, float]:
        counts = Counter(tokens)
        vec: Dict[int, float] = {}
        if not counts:
            return vec
        max_tf = max(counts.values())
        for t, tf in counts.items():
            idx = self.vocab.setdefault(t, len(self.vocab))
            tf_norm = 0.5 + 0.5 * (tf / max_tf)
            idf = self.idf.get(idx, 1.0)
            vec[idx] = tf_norm * idf
        return vec

    def _cosine(self, a: Dict[int, float], b: Dict[int, float]) -> float:
        if not a or not b:
            return 0.0
        dot = 0.0
        if len(a) < len(b):
            small, large = a, b
        else:
            small, large = b, a
        for i, w in small.items():
            bw = large.get(i)
            if bw is not None:
                dot += w * bw
        na = math.sqrt(sum(w * w for w in a.values()))
        nb = math.sqrt(sum(w * w for w in b.values()))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def _semantic_vec(self, tokens: List[str]) -> List[float]:
        if not tokens:
            return [0.0] * self.semantic_dim
        vec = [0.0] * self.semantic_dim
        for t in tokens:
            h = hashlib.md5(t.encode("utf-8")).digest()
            idx = int.from_bytes(h[:2], "big") % self.semantic_dim
            sign = -1.0 if (h[2] & 1) else 1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def _cosine_dense(self, a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        n = min(len(a), len(b))
        if n == 0:
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for i in range(n):
            av = float(a[i])
            bv = float(b[i])
            dot += av * bv
            na += av * av
            nb += bv * bv
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / math.sqrt(na * nb)

    def ingest_text(self, doc_id: str, text: str, path: str, *, tags: Optional[List[str]] = None, meta: Optional[Dict[str, Any]] = None) -> None:
        tokens = tokenize(text)
        limited_tokens = tokens[:DEFAULT_RAG_TOKEN_LIMIT]
        with self._lock:
            self.docs[doc_id] = {
                "path": path,
                "tokens": limited_tokens,
                "snippet": text[:DEFAULT_RAG_SNIPPET_LENGTH],
                "tags": [str(t) for t in (tags or []) if str(t).strip()],
                "meta": (meta or {}),
            }
            self._recompute_idf()
            self.doc_vectors[doc_id] = self._tfidf(limited_tokens)
            self.doc_semantic_vectors[doc_id] = self._semantic_vec(limited_tokens)
        try:
            self._save()
        except OSError:
            return

    def _matches_where(self, doc_id: str, meta: Dict[str, Any], where: Optional[Dict[str, Any]]) -> bool:
        if not where:
            return True
        doc = self.docs.get(doc_id)
        if isinstance(doc, dict):
            path = str(doc.get("path") or "")
            tags = doc.get("tags") or []
        else:
            path = ""
            tags = []
        if not isinstance(tags, list):
            tags = []

        doc_id_prefix = where.get("doc_id_prefix")
        if doc_id_prefix and not str(doc_id).startswith(str(doc_id_prefix)):
            return False
        path_prefix = where.get("path_prefix")
        if path_prefix and not path.startswith(str(path_prefix)):
            return False
        path_contains = where.get("path_contains")
        if path_contains and str(path_contains) not in path:
            return False

        tags_any = where.get("tags_any") or []
        if tags_any:
            want = {str(t) for t in tags_any if str(t).strip()}
            have = {str(t) for t in tags if str(t).strip()}
            if want and not (want & have):
                return False
        tags_all = where.get("tags_all") or []
        if tags_all:
            want = {str(t) for t in tags_all if str(t).strip()}
            have = {str(t) for t in tags if str(t).strip()}
            if want and not want.issubset(have):
                return False

        kind = where.get("kind")
        if kind:
            dmeta = (self.docs.get(doc_id) or {}).get("meta") or {}
            if str(dmeta.get("kind") or "") != str(kind):
                return False
        return True

    def search(self, query: str, top_k: int = DEFAULT_RAG_TOP_K, where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        tokens = tokenize(query)
        qvec = self._tfidf(tokens)
        scored: List[Tuple[str, float]] = []
        for doc_id, dvec in self.doc_vectors.items():
            if not self._matches_where(doc_id, self.docs.get(doc_id, {}), where):
                continue
            s = self._cosine(qvec, dvec)
            if s > 0:
                scored.append((doc_id, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        out: List[Dict[str, Any]] = []
        for doc_id, score in scored[:top_k]:
            meta = self.docs.get(doc_id, {})
            out.append({
                "doc_id": doc_id,
                "score": score,
                "path": meta.get("path"),
                "snippet": meta.get("snippet"),
                "tags": meta.get("tags"),
                "meta": meta.get("meta"),
            })
        return out

    def search_semantic(self, query: str, top_k: int = DEFAULT_RAG_TOP_K, where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        tokens = tokenize(query)
        qvec = self._semantic_vec(tokens)
        scored: List[Tuple[str, float]] = []
        for doc_id, dvec in self.doc_semantic_vectors.items():
            if not self._matches_where(doc_id, self.docs.get(doc_id, {}), where):
                continue
            s = self._cosine_dense(qvec, dvec)
            if s > 0:
                scored.append((doc_id, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        out: List[Dict[str, Any]] = []
        for doc_id, score in scored[:top_k]:
            meta = self.docs.get(doc_id, {})
            out.append({
                "doc_id": doc_id,
                "score": score,
                "path": meta.get("path"),
                "snippet": meta.get("snippet"),
                "tags": meta.get("tags"),
                "meta": meta.get("meta"),
            })
        return out

    def ingest_file(self, path: str, doc_id: str, max_bytes: int = DEFAULT_RAG_MAX_FILE_SIZE, *, tags: Optional[List[str]] = None, meta: Optional[Dict[str, Any]] = None) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(max_bytes)
        except OSError:
            return
        self.ingest_text(doc_id, text, path, tags=tags, meta=meta)
