from __future__ import annotations

import warnings
from typing import Optional

from .router import RouterRunner as _RouterRunner


class SimplifiedRouter(_RouterRunner):
    def __init__(self, cwd: str, run_id: Optional[str] = None, mode: Optional[str] = None):
        warnings.warn(
            "SimplifiedRouter is deprecated; use lattice.router.RouterRunner",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(cwd=cwd, run_id=run_id, mode=mode)


RouterRunner = SimplifiedRouter

__all__ = ["SimplifiedRouter", "RouterRunner"]
