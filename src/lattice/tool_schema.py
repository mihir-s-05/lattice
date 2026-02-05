from __future__ import annotations

from typing import Any, Dict


def tool_schema(name: str, desc: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": params,
        },
    }
