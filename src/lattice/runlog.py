import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .secrets import redact_secrets


class RunLogger:
    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir
        self.log_path = os.path.join(run_dir, "run.jsonl")
        os.makedirs(run_dir, exist_ok=True)

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def log(self, event: str, **fields: Any) -> None:
        rec: Dict[str, Any] = {"ts": self._ts(), "event": event}
        # Redact any sensitive info before writing to disk
        rec.update(redact_secrets(fields))
        line = json.dumps(rec, ensure_ascii=False)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def path(self) -> str:
        return self.log_path
