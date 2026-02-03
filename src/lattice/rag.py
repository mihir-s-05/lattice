from __future__ import annotations

import json
import math
import os
import re
import threading
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from .constants import (
    DEFAULT_RAG_TOKEN_LIMIT,
    DEFAULT_RAG_SNIPPET_LENGTH,
    DEFAULT_RAG_TOP_K,
    DEFAULT_RAG_MAX_FILE_SIZE
)


_RAW_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+(?:[./:-][A-Za-z0-9_]+)+|[A-Za-z0-9_]+")
_SEP_RE = re.compile(r"[./:-]+")
_CAMEL_1 = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_2 = re.compile(r"([A-Z]+)([A-Z][a-z])")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "if",
    "in",
    "into",
    "is",
    "it",
    "no",
    "not",
    "of",
    "on",
    "or",
    "s",
    "such",
    "t",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "they",
    "this",
    "to",
    "was",
    "were",
    "will",
    "with",
    "you",
    "your",
    "self",
    "cls",
    "args",
    "kwargs",
    "true",
    "false",
    "none",
    "null",
    "def",
    "class",
    "return",
    "import",
    "export",
    "public",
    "private",
    "protected",
    "static",
    "async",
    "await",
    "let",
    "const",
    "var",
    "function",
    "try",
    "except",
    "catch",
    "finally",
    "raise",
    "throw",
    "new",
    "pass",
    "break",
    "continue",
    "elif",
    "else",
    "while",
    "for",
    "in",
    "with",
    "lambda",
    "yield",
    "print",
    "console",
    "log",
}


def _split_camel(piece: str) -> List[str]:
    if not piece:
        return []
    s = _CAMEL_2.sub(r"\1 \2", piece)
    s = _CAMEL_1.sub(r"\1 \2", s)
    return [p for p in s.split() if p]


def _keep_token(tok: str) -> bool:
    if not tok:
        return False
    t = tok.strip().lower()
    if not t:
        return False
    if t in _STOPWORDS:
        return False
    if t.isdigit():
        return False
    if len(t) <= 1:
        return False
    if len(t) > 64:
        return False
    return True


def tokenize(text: str) -> List[str]:
    """
    Code-aware-ish tokenizer for lightweight retrieval:
    - Keeps dotted/slashed identifiers (e.g., self.cfg, app.route, HTTP/1.1) as tokens
    - Also emits subtokens via separator split + snake_case + camelCase splitting
    - Filters obvious stopwords / boilerplate tokens

    This is intentionally dependency-free and optimized for small run-scoped corpora.
    """
    raw = _RAW_TOKEN_RE.findall(text or "")
    out: List[str] = []
    for rt in raw:
        emitted: set[str] = set()

        rt_l = rt.lower()
        emitted.add(rt_l)

        stripped = rt_l.strip("_.:-/")
        if stripped:
            emitted.add(stripped)

        for part in _SEP_RE.split(rt):
            if not part:
                continue
            emitted.add(part.lower())
            for snake in part.split("_"):
                if not snake:
                    continue
                emitted.add(snake.lower())
                for cc in _split_camel(snake):
                    emitted.add(cc.lower())
            for cc in _split_camel(part):
                emitted.add(cc.lower())

        for t in emitted:
            if _keep_token(t):
                out.append(t.lower())
    return out


class RagIndex:
    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir
        self.idx_path = os.path.join(run_dir, "rag_index.json")
        self._lock = threading.Lock()
        self.docs: Dict[str, Dict[str, Any]] = {}
        self.df: Dict[str, int] = {}
        self._avgdl: float = 0.0
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

            df = data.get("df")
            if isinstance(df, dict):
                self.df = {str(k): int(v) for k, v in df.items() if isinstance(k, str) or isinstance(k, int)}
            else:
                self.df = {}

            for doc_id, doc in list(self.docs.items()):
                if not isinstance(doc, dict):
                    continue
                if isinstance(doc.get("tf"), dict):
                    continue
                toks = doc.get("tokens") or []
                if not isinstance(toks, list):
                    toks = []
                tf = Counter([str(t).lower() for t in toks if isinstance(t, str)])
                doc["tf"] = dict(tf)
                doc["dl"] = int(doc.get("dl") or len(toks))

            if not self.df:
                self._rebuild_df()
            self._recompute_avgdl()

    def _save(self) -> None:
        data = {
            "docs": self.docs,
            "df": self.df,
            "avgdl": self._avgdl,
            "version": 2,
        }
        with self._lock:
            with open(self.idx_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

    def _recompute_avgdl(self) -> None:
        total = 0
        n = 0
        for doc in self.docs.values():
            if not isinstance(doc, dict):
                continue
            dl = int(doc.get("dl") or 0)
            if dl <= 0:
                toks = doc.get("tokens") or []
                dl = len(toks) if isinstance(toks, list) else 0
            if dl <= 0:
                continue
            total += dl
            n += 1
        self._avgdl = (float(total) / float(n)) if n else 0.0

    def _rebuild_df(self) -> None:
        df: Dict[str, int] = {}
        for doc in self.docs.values():
            if not isinstance(doc, dict):
                continue
            tf = doc.get("tf")
            if not isinstance(tf, dict):
                toks = doc.get("tokens") or []
                if isinstance(toks, list):
                    tf = dict(Counter([str(t).lower() for t in toks if isinstance(t, str)]))
                else:
                    tf = {}
                doc["tf"] = tf
            terms = set([str(t).lower() for t in tf.keys()])
            for t in terms:
                df[t] = df.get(t, 0) + 1
        self.df = df

    def _bm25_idf(self, term: str, *, N: int) -> float:
        df = int(self.df.get(term, 0))
        return math.log(((N - df + 0.5) / (df + 0.5)) + 1.0)

    def _bm25_score(self, doc_tf: Dict[str, int], doc_len: int, query_terms: List[str], *, k1: float, b: float, N: int) -> float:
        if not query_terms or not doc_tf:
            return 0.0
        avgdl = self._avgdl or 1.0
        denom_norm = (1.0 - b) + b * (float(doc_len) / float(avgdl))
        score = 0.0
        for t in query_terms:
            tf = int(doc_tf.get(t, 0))
            if tf <= 0:
                continue
            idf = self._bm25_idf(t, N=N)
            numer = float(tf) * (k1 + 1.0)
            denom = float(tf) + k1 * denom_norm
            score += idf * (numer / denom)
        return score

    def ingest_text(self, doc_id: str, text: str, path: str, *, tags: Optional[List[str]] = None, meta: Optional[Dict[str, Any]] = None) -> None:
        tokens = tokenize(text)
        limited_tokens = tokens[:DEFAULT_RAG_TOKEN_LIMIT]
        tf = Counter(limited_tokens)
        unique_terms = set(tf.keys())
        with self._lock:
            prev = self.docs.get(doc_id)
            if isinstance(prev, dict):
                prev_tf = prev.get("tf")
                if isinstance(prev_tf, dict):
                    prev_terms = set([str(t).lower() for t in prev_tf.keys()])
                else:
                    prev_tokens = prev.get("tokens") or []
                    prev_terms = set([str(t).lower() for t in prev_tokens]) if isinstance(prev_tokens, list) else set()
                for t in prev_terms:
                    cur = int(self.df.get(t, 0)) - 1
                    if cur <= 0:
                        self.df.pop(t, None)
                    else:
                        self.df[t] = cur

            self.docs[doc_id] = {
                "path": path,
                "tokens": limited_tokens,
                "snippet": text[:DEFAULT_RAG_SNIPPET_LENGTH],
                "tags": [str(t) for t in (tags or []) if str(t).strip()],
                "meta": (meta or {}),
                "tf": dict(tf),
                "dl": len(limited_tokens),
            }
            for t in unique_terms:
                self.df[t] = int(self.df.get(t, 0)) + 1
            self._recompute_avgdl()
        self._save()

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
        qterms = [t for t in tokens if _keep_token(t)]
        if not qterms:
            return []
        N = max(1, len(self.docs))
        k1 = 1.2
        b = 0.75

        scored: List[Tuple[str, float]] = []
        for doc_id, doc in self.docs.items():
            if not self._matches_where(doc_id, doc if isinstance(doc, dict) else {}, where):
                continue
            if not isinstance(doc, dict):
                continue
            doc_tf = doc.get("tf")
            if not isinstance(doc_tf, dict):
                continue
            dl = int(doc.get("dl") or 0)
            if dl <= 0:
                dl = len(doc.get("tokens") or []) if isinstance(doc.get("tokens"), list) else 0
            score = self._bm25_score(doc_tf, dl, qterms, k1=k1, b=b, N=N)
            if score > 0:
                scored.append((doc_id, score))

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
                "mode": "bm25",
            })
        return out

    def search_rag(self, query: str, top_k: int = DEFAULT_RAG_TOP_K, where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        out = self.search(query, top_k=top_k, where=where)
        for r in out:
            r["mode"] = "bm25"
        return out

    def search_semantic(self, query: str, top_k: int = DEFAULT_RAG_TOP_K, where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        out = self.search_rag(query, top_k=top_k, where=where)
        for r in out:
            r["note"] = "semantic_search_deprecated; using rag_search"
        return out

    def ingest_file(self, path: str, doc_id: str, max_bytes: int = DEFAULT_RAG_MAX_FILE_SIZE, *, tags: Optional[List[str]] = None, meta: Optional[Dict[str, Any]] = None) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(max_bytes)
        except OSError:
            return
        self.ingest_text(doc_id, text, path, tags=tags, meta=meta)
