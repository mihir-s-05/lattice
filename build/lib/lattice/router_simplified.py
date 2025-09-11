from __future__ import annotations

import json
import os
import random
from typing import Any, Dict, List, Optional

from .artifacts import ArtifactStore
from .config import RunConfig, load_run_config
from .execution_modes import ExecutionModeFactory
from .huddle import DecisionSummary
from .knowledge import KnowledgeBus
from .rag import RagIndex
from .runlog import RunLogger
from .transcript import RunningTranscript
from .worker import gen_run_id
from .finalize import run_finalization
from .constants import SUPPORTED_EXECUTION_MODES, get_runs_base_dir


class SimplifiedRouter:
    
    def __init__(self, cwd: str, run_id: Optional[str] = None, mode: Optional[str] = None):
        self.cwd = cwd
        self.run_id = run_id or gen_run_id()
        self.run_dir = os.path.join(get_runs_base_dir(), self.run_id)
        os.makedirs(self.run_dir, exist_ok=True)

        self.logger = RunLogger(self.run_dir)
        self.artifacts = ArtifactStore(self.run_dir)
        self.rag = RagIndex(self.run_dir)
        self.knowledge_bus = KnowledgeBus(self.run_dir, self.logger)

        self.cfg: Optional[RunConfig] = None

        self.mode = self._resolve_execution_mode(mode)
        
    def _resolve_execution_mode(self, mode: Optional[str]) -> str:
        if mode and mode.lower() in SUPPORTED_EXECUTION_MODES:
            return mode.lower()
            
        env_mode = os.environ.get("LATTICE_MODE", "").lower()
        if env_mode in SUPPORTED_EXECUTION_MODES:
            return env_mode

        default_mode = "weave"
        if not env_mode and not mode:
            default_mode = random.choice(SUPPORTED_EXECUTION_MODES)

        return default_mode
        
    def run(self, goal: str) -> Dict[str, Any]:
        self.logger.log("router_start", run_id=self.run_id, mode=self.mode, goal=goal)

        self.cfg = load_run_config(self.run_id, goal)

        transcript = RunningTranscript(self.run_id)

        if self.cfg.use_rag:
            self._pre_ingest_rag()

        try:
            mode_handler = ExecutionModeFactory.create(
                self.mode,
                self.run_dir,
                self.logger,
                self.artifacts,
                self.rag,
                self.cfg
            )

            result = mode_handler.execute(goal, transcript)
            plan_snapshots = result.get("plan_snapshots", [])
            decisions = result.get("decisions", [])

            transcript_md = transcript.render_markdown()
            self.artifacts.add_text(
                "transcript.md",
                transcript_md,
                tags=["transcript", "router"],
                meta={"mode": self.mode}
            )

            finalization_report = run_finalization(
                self.run_dir,
                self.artifacts,
                self.logger,
                decisions
            )
            self.logger.log(
                "router_complete",
                run_id=self.run_id,
                mode=self.mode,
                plan_snapshots=plan_snapshots,
                decisions=[{"id": d.id, "topic": d.topic} for d in decisions],
                finalization=finalization_report
            )
            
            return {
                "run_id": self.run_id,
                "mode": self.mode,
                "plan_snapshots": plan_snapshots,
                "decisions": decisions,
                "finalization": finalization_report,
                "artifacts_path": self.artifacts.art_dir,
                "log_path": self.logger.path(),
                "transcript_path": os.path.join(self.run_dir, "artifacts", "transcript.md")
            }
            
        except Exception as e:
            self.logger.log("router_error", error=str(e), mode=self.mode)
            raise
    
    def _pre_ingest_rag(self):
        from .constants import RAG_INGEST_PATTERNS, DEFAULT_RAG_MAX_INGEST_FILES
        import glob
        import hashlib
        
        patterns = RAG_INGEST_PATTERNS
        max_files = int(os.environ.get("LATTICE_RAG_MAX_INGEST", str(DEFAULT_RAG_MAX_INGEST_FILES)))
        
        candidates = []
        for pattern in patterns:
            for path in glob.glob(os.path.join(self.cwd, pattern), recursive=True):
                if os.path.isfile(path):
                    candidates.append(path)

        unique_candidates = []
        seen = set()
        for path in candidates:
            if path not in seen:
                seen.add(path)
                unique_candidates.append(path)
        
        for path in unique_candidates[:max_files]:
            try:
                with open(path, "rb") as f:
                    raw = f.read(1024 * 1024)
                digest = hashlib.sha256(raw + path.encode("utf-8")).hexdigest()
                doc_id = digest[:16]
                
                self.rag.ingest_file(path, doc_id)
                self.logger.log("rag_ingest", path=path, doc_id=doc_id, bytes=len(raw))
                
            except Exception as e:
                self.logger.log("rag_ingest_error", path=path, error=str(e))


RouterRunner = SimplifiedRouter
