from __future__ import annotations

import glob
import os
import shutil
import time
from typing import Optional, Set

from ..constants import get_runs_base_dir
from ..runlog import RunLogger


class WorkspaceManager:
    def __init__(self, source_root: str, run_dir: str, logger: RunLogger) -> None:
        self.source_root = source_root
        self.run_dir = run_dir
        self.logger = logger

        self._workspace_root = os.path.join(run_dir, "workspace")
        self._touched_files: set[str] = set()
        self._seeded_test_files: set[str] = set()
        self._baseline_at: Optional[float] = None

    @property
    def workspace_root(self) -> str:
        return self._workspace_root

    @property
    def touched_files(self) -> Set[str]:
        return self._touched_files

    @property
    def seeded_test_files(self) -> Set[str]:
        return self._seeded_test_files

    @property
    def baseline_at(self) -> Optional[float]:
        return self._baseline_at

    def prepare(self) -> None:
        os.makedirs(self._workspace_root, exist_ok=True)
        seed = (os.environ.get("LATTICE_WORKSPACE_SEED") or "copy").strip().lower()
        if seed in ("0", "false", "none", "empty", "skip"):
            self.logger.log("workspace_seed", mode=seed, source=self.source_root, dest=self._workspace_root)
            self._baseline_at = time.time() + 0.25
            self._seeded_test_files = set()
            return
        try:
            if any(os.scandir(self._workspace_root)):
                self.logger.log("workspace_seed", mode="reuse", source=self.source_root, dest=self._workspace_root)
                self._baseline_at = None
                self._seeded_test_files = set()
                return
        except OSError:
            pass

        ignore_dirs = {
            ".git",
            ".lattice",
            "node_modules",
            "venv",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".cache",
            "dist",
            "build",
            "reports",
            "artifacts",
        }
        root_ignore_names: set[str] = set()
        root_real = os.path.normcase(os.path.realpath(os.path.abspath(self.source_root)))
        try:
            runs_root = os.path.normcase(os.path.realpath(os.path.abspath(get_runs_base_dir())))
            if os.path.commonpath([root_real, runs_root]) == root_real:
                rel = os.path.relpath(runs_root, root_real)
                top = rel.split(os.sep, 1)[0].strip()
                if top and top not in (".", os.curdir):
                    root_ignore_names.add(top)
        except (OSError, ValueError):
            pass
        try:
            gi = os.path.join(self.source_root, ".gitignore")
            if os.path.exists(gi):
                with open(gi, "r", encoding="utf-8", errors="ignore") as f:
                    for raw in f.read().splitlines():
                        s = (raw or "").strip()
                        if not s or s.startswith("#"):
                            continue
                        if s.startswith("!"):
                            continue
                        if not s.startswith("/"):
                            continue
                        if any(ch in s for ch in ("*", "?", "[", "]")):
                            continue
                        name = s.strip("/").strip()
                        if name and ("/" not in name):
                            root_ignore_names.add(name)
        except OSError:
            root_ignore_names = set()

        def _ignore(_dir: str, names: list[str]) -> set[str]:
            out: set[str] = set()
            try:
                if root_ignore_names:
                    d_real = os.path.normcase(os.path.realpath(os.path.abspath(_dir)))
                    if d_real == root_real:
                        for n in names:
                            if n in root_ignore_names:
                                out.add(n)
            except (OSError, ValueError):
                pass
            for n in names:
                if n in ignore_dirs:
                    out.add(n)
                    continue
                if n == ".env":
                    out.add(n)
                    continue
                if n.startswith(".env.") and n != ".env.example":
                    out.add(n)
                    continue
                if n.endswith((".pyc", ".pyo", ".log")):
                    out.add(n)
                    continue
            return out

        try:
            t0 = time.time()
            self.logger.log("workspace_seed_start", mode="copy", source=self.source_root, dest=self._workspace_root)
            shutil.copytree(self.source_root, self._workspace_root, dirs_exist_ok=True, ignore=_ignore)
            dt = int((time.time() - t0) * 1000)
            self.logger.log("workspace_seed", mode="copy", source=self.source_root, dest=self._workspace_root, duration_ms=dt)
            self._baseline_at = time.time() + 0.25
            self._seeded_test_files = set()
            try:
                for pat in (
                    os.path.join(self._workspace_root, "tests", "test_*.py"),
                    os.path.join(self._workspace_root, "backend", "tests", "test_*.py"),
                ):
                    for fp in glob.glob(pat):
                        try:
                            rel = os.path.relpath(fp, self._workspace_root).replace("\\", "/")
                            self._seeded_test_files.add(rel)
                        except ValueError:
                            continue
            except (OSError, ValueError):
                self._seeded_test_files = set()
        except (OSError, shutil.Error) as e:
            self.logger.log("workspace_seed", mode="copy_failed", source=self.source_root, dest=self._workspace_root, error=str(e))
            self._baseline_at = time.time() + 0.25
            self._seeded_test_files = set()

    def record_touched(self, path: str) -> None:
        try:
            rel = os.path.relpath(path, self._workspace_root)
            if rel and not rel.startswith(".."):
                self._touched_files.add(os.path.normpath(rel))
        except ValueError:
            return

    def is_within_cwd(self, path: str) -> bool:
        root = os.path.normcase(os.path.realpath(os.path.abspath(self._workspace_root)))
        target = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        try:
            return os.path.commonpath([root, target]) == root
        except ValueError:
            return target == root or target.startswith(root + os.sep)

    def resolve_path(self, path: str) -> str:
        if not path or not str(path).strip():
            raise ValueError("path is required")
        raw = str(path)
        if not os.path.isabs(raw):
            raw = os.path.join(self._workspace_root, raw)
        abs_path = os.path.realpath(os.path.abspath(raw))
        if not self.is_within_cwd(abs_path):
            raise ValueError("path escapes workspace root")
        return abs_path

