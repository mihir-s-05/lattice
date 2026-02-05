from __future__ import annotations

import os


class WorkspaceAccess:
    def __init__(self, workspace_root: str) -> None:
        self.workspace_root = workspace_root

    def resolve(self, path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            raise ValueError("path is required")
        if not os.path.isabs(raw):
            raw = os.path.join(self.workspace_root, raw)
        abs_path = os.path.realpath(os.path.abspath(raw))
        root = os.path.normcase(os.path.realpath(os.path.abspath(self.workspace_root)))
        target = os.path.normcase(abs_path)
        try:
            within = os.path.commonpath([root, target]) == root
        except ValueError:
            within = target == root or target.startswith(root + os.sep)
        if not within:
            raise ValueError("path escapes workspace root")
        return abs_path

