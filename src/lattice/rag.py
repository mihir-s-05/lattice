import json
import math
import os
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple


WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in WORD_RE.findall(text)]


class RagIndex:
    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir
        self.idx_path = os.path.join(run_dir, "rag_index.json")
        self.docs: Dict[str, Dict[str, Any]] = {}
        self.vocab: Dict[str, int] = {}
        self.idf: Dict[int, float] = {}
        self.doc_vectors: Dict[str, Dict[int, float]] = {}
        self.loaded = False
        if os.path.exists(self.idx_path):
            self._load()

    def _load(self) -> None:
        with open(self.idx_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.docs = data.get("docs", {})
        self.vocab = {k: int(v) for k, v in data.get("vocab", {}).items()}
        self.idf = {int(k): float(v) for k, v in data.get("idf", {}).items()}
        self.doc_vectors = {
            doc_id: {int(i): float(w) for i, w in vec.items()} for doc_id, vec in data.get("doc_vectors", {}).items()
        }
        self.loaded = True

    def _save(self) -> None:
        data = {
            "docs": self.docs,
            "vocab": {k: v for k, v in self.vocab.items()},
            "idf": {str(k): v for k, v in self.idf.items()},
            "doc_vectors": {doc_id: {str(i): w for i, w in vec.items()} for doc_id, vec in self.doc_vectors.items()},
        }
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

    def ingest_text(self, doc_id: str, text: str, path: str) -> None:
        tokens = tokenize(text)
        self.docs[doc_id] = {
            "path": path,
            "tokens": tokens[:50000],
            "snippet": text[:500],
        }
        self._recompute_idf()
        vec = self._tfidf(tokens)
        self.doc_vectors[doc_id] = vec
        self._save()

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        tokens = tokenize(query)
        qvec = self._tfidf(tokens)
        scored: List[Tuple[str, float]] = []
        for doc_id, dvec in self.doc_vectors.items():
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
            })
        return out

    def ingest_file(self, path: str, doc_id: str, max_bytes: int = 1024 * 1024) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(max_bytes)
        except Exception as e:
            return
        self.ingest_text(doc_id, text, path)
