import json
import os
import random
import string
from datetime import datetime
import hashlib
import glob
from typing import Dict, Optional, List

from .artifacts import ArtifactStore
from .config import RunConfig, load_run_config
from .providers import call_with_fallback, ProviderError
from .rag import RagIndex
from .runlog import RunLogger


def gen_run_id() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"run-{ts}-{suffix}"


class WorkerRunner:
    def __init__(self, cwd: str, run_id: Optional[str] = None) -> None:
        self.cwd = cwd
        self.run_id = run_id or gen_run_id()
        self.run_dir = os.path.join(cwd, "runs", self.run_id)
        os.makedirs(self.run_dir, exist_ok=True)
        self.logger = RunLogger(self.run_dir)
        self.artifacts = ArtifactStore(self.run_dir)
        self.rag_index = RagIndex(self.run_dir)
        self.cfg: Optional[RunConfig] = None

    def _snapshot_env(self) -> Dict[str, str]:
        keys = [
            "LATTICE_PROVIDER_ORDER",
            "LATTICE_PROVIDER",
            "LATTICE_MODEL",
            "LATTICE_BASE_URL",
            "LATTICE_USE_RAG",
            "LATTICE_TEMPERATURE",
            "LATTICE_MAX_TOKENS",
            "GROQ_BASE_URL",
            "GROQ_API_KEY",
            "GROQ_MODEL",
            "GEMINI_BASE_URL",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "LMSTUDIO_BASE_URL",
            "LMSTUDIO_API_KEY",
            "LMSTUDIO_MODEL",
        ]
        snap = {}
        for k in keys:
            v = os.environ.get(k)
            if v is not None:
                if "KEY" in k and len(v) > 8:
                    snap[k] = v[:4] + "…" + v[-4:]
                else:
                    snap[k] = v
        return snap

    def run(self, prompt: str, use_rag: Optional[bool] = None) -> Dict[str, str]:
        self.cfg = load_run_config(self.run_id, prompt)
        if use_rag is not None:
            self.cfg.use_rag = use_rag

        cfg_json = self.cfg.to_json()
        with open(os.path.join(self.run_dir, "config.json"), "w", encoding="utf-8") as f:
            f.write(cfg_json)

        self.logger.log(
            "run_start",
            run_id=self.run_id,
            run_dir=self.run_dir,
            config=json.loads(cfg_json),
            env=self._snapshot_env(),
        )

        if self.cfg.use_rag:
            self._pre_ingest_repo_files()

        rag_used = False
        rag_queries = []
        rag_hits = []
        context_text = ""
        if self.cfg.use_rag:
            q = prompt
            rag_queries.append(q)
            hits = self.rag_index.search(q, top_k=3)
            rag_hits = hits
            ctx_parts = []
            if hits:
                rag_used = True
                for h in hits:
                    path = h.get("path")
                    abs_path = None
                    if path:
                        if os.path.isabs(path):
                            abs_path = path
                        elif str(path).startswith("artifacts/"):
                            abs_path = os.path.join(self.run_dir, path)
                        else:
                            abs_path = os.path.join(self.cwd, path)
                    snippet = h.get("snippet")
                    if abs_path and os.path.exists(abs_path):
                        try:
                            with open(abs_path, "r", encoding="utf-8") as f:
                                content = f.read(1000)
                            snippet = content
                        except Exception:
                            pass
                    if snippet:
                        ctx_parts.append(f"From {path}:\n{snippet}")

            if not ctx_parts and any(tok in q.lower() for tok in ["readme", "repo readme"]):
                discovered = []
                candidates = [
                    "README.md",
                    "README",
                    "README.txt",
                    "Readme.md",
                    "readme.md",
                    os.path.join("docs", "README.md"),
                ]
                for rel in candidates:
                    abs_p = os.path.join(self.cwd, rel)
                    if os.path.isfile(abs_p):
                        discovered.append((rel, abs_p))
                if not discovered:
                    try:
                        for name in os.listdir(self.cwd):
                            if name.lower().startswith("readme") and os.path.isfile(os.path.join(self.cwd, name)):
                                rel = name
                                abs_p = os.path.join(self.cwd, name)
                                discovered.append((rel, abs_p))
                    except Exception:
                        pass

                if discovered:
                    rag_used = True
                    for rel, abs_p in discovered[:3]:
                        try:
                            with open(abs_p, "r", encoding="utf-8") as f:
                                content = f.read(2000)
                        except Exception:
                            continue
                        ctx_parts.append(f"From {rel}:\n{content}")
                        try:
                            did = hashlib.sha256((rel + abs_p).encode("utf-8")).hexdigest()[:16]
                            self.rag_index.ingest_text(did, content, path=abs_p)
                            rag_hits.append({
                                "doc_id": did,
                                "score": 1.0,
                                "path": abs_p,
                                "snippet": content[:300],
                            })
                        except Exception:
                            pass

            if ctx_parts:
                context_text = ("\n\n".join(ctx_parts))[:2000]

        messages = []
        system_preamble = (
            "You are LATTICE worker runner. Be concise, practical, and accurate."
        )
        messages.append({"role": "system", "content": system_preamble})
        if context_text:
            messages.append({
                "role": "system",
                "content": f"Context from prior artifacts (may be partial):\n{context_text}",
            })
        messages.append({"role": "user", "content": prompt})

        try:
            provider_name, base_url, model, raw, attempts = call_with_fallback(
                providers=self.cfg.providers,
                order=self.cfg.provider_order,
                messages=messages,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                logger=self.logger,
            )
        except ProviderError as e:
            self.logger.log(
                "run_error",
                error=str(e),
                rag_used=rag_used,
                rag_queries=rag_queries,
                rag_hits=rag_hits,
            )
            raise

        text = ""
        try:
            text = raw["choices"][0]["message"].get("content") or ""
        except Exception:
            text = str(raw)

        artifact_name = "output.txt"
        art = self.artifacts.add_text(
            artifact_name,
            text,
            tags=["output", "llm"],
            meta={
                "provider": provider_name,
                "model": model,
            },
        )
        try:
            self.rag_index.ingest_text(art.id, text, art.path)
        except Exception as e:
            self.logger.log("rag_error", error=str(e))

        self.logger.log(
            "run_complete",
            artifact_path=os.path.join(self.run_dir, art.path),
            log_path=self.logger.path(),
            rag_used=rag_used,
            rag_queries=rag_queries,
            rag_hits=rag_hits,
        )

        return {
            "artifact_path": os.path.join(self.run_dir, art.path),
            "log_path": self.logger.path(),
            "run_id": self.run_id,
        }

    def _pre_ingest_repo_files(self) -> None:
        patterns = [
            "README*",
            "readme*",
            os.path.join("docs", "**", "*.md"),
            os.path.join("docs", "**", "*.txt"),
            "documentation.txt",
        ]
        max_files = int(os.environ.get("LATTICE_RAG_MAX_INGEST", "20"))
        candidates: List[str] = []
        for pat in patterns:
            for p in glob.glob(os.path.join(self.cwd, pat), recursive=True):
                if os.path.isfile(p):
                    candidates.append(p)
        seen = set()
        unique: List[str] = []
        for p in candidates:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        for path in unique[:max_files]:
            try:
                with open(path, "rb") as f:
                    raw = f.read(1024 * 1024)
                digest = hashlib.sha256(raw + path.encode("utf-8")).hexdigest()
                doc_id = digest[:16]
            except Exception as e:
                self.logger.log("rag_ingest_error", path=path, error=str(e))
                continue
            try:
                self.rag_index.ingest_file(path, doc_id)
                self.logger.log("rag_ingest", path=path, doc_id=doc_id, bytes=min(len(raw), 1024 * 1024))
            except Exception as e:
                self.logger.log("rag_ingest_error", path=path, error=str(e))
