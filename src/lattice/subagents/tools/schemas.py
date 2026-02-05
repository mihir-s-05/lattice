from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...tool_schema import tool_schema
from .dynamic_loader import load_dynamic_tools


def build_manifest(
    *,
    allowed_tools: Optional[List[str]],
    cfg: Optional[Any],
    codebase_root: Optional[str] = None,
    include_dynamic: bool = False,
) -> List[Dict[str, Any]]:
    _ = cfg
    tools: List[Dict[str, Any]] = []
    tools.append(
        tool_schema(
            "read_file",
            "Read a file from the workspace (relative to project root unless absolute).",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_bytes": {"type": "integer", "minimum": 1, "maximum": 500000},
                },
                "required": ["path"],
            },
        )
    )
    tools.append(
        tool_schema(
            "delete_file",
            "Delete a file from the workspace (relative to project root unless absolute). Prefer this over run_command for file cleanup.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "missing_ok": {"type": ["boolean", "null"]},
                },
                "required": ["path"],
            },
        )
    )
    tools.append(
        tool_schema(
            "write_file",
            "Write a file to the workspace (relative to project root unless absolute).",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "mode": {"type": "string", "enum": ["overwrite", "append"]},
                },
                "required": ["path", "content"],
            },
        )
    )
    tools.append(
        tool_schema(
            "run_command",
            "Run a shell command in the workspace. Must be safe and justified.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": ["string", "null"]},
                    "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 600},
                    "reason": {"type": "string"},
                },
                "required": ["command", "reason"],
            },
        )
    )
    tools.append(
        tool_schema(
            "rag_search",
            "Keyword-based search (BM25) over run-scoped artifacts and transcripts.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "where": {
                        "type": ["object", "null"],
                        "properties": {
                            "doc_id_prefix": {"type": ["string", "null"]},
                            "path_prefix": {"type": ["string", "null"]},
                            "path_contains": {"type": ["string", "null"]},
                            "tags_any": {"type": "array", "items": {"type": "string"}},
                            "tags_all": {"type": "array", "items": {"type": "string"}},
                            "kind": {"type": ["string", "null"]},
                        },
                    },
                },
                "required": ["query", "top_k"],
            },
        )
    )
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Perform a web search via the configured local adapter (if enabled).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                        "language": {"type": ["string", "null"]},
                        "time_range": {"type": ["string", "null"], "enum": ["d", "w", "m", "y", None]},
                        "engines": {"type": ["string", "null"]},
                        "pageno": {"type": ["integer", "null"], "minimum": 1},
                    },
                    "required": ["query", "top_k"],
                },
            },
        }
    )
    tools.append(
        tool_schema(
            "request_huddle",
            "Request a huddle/check-in (router will coordinate opening it).",
            {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "questions": {"type": "array", "items": {"type": "string"}},
                    "attendees": {"type": ["array", "null"], "items": {"type": "string"}},
                    "agenda": {"type": ["string", "null"]},
                    "urgency": {"type": ["string", "null"], "enum": ["low", "normal", "high", None]},
                },
                "required": ["topic"],
            },
        )
    )
    allowed = {str(x) for x in (allowed_tools or []) if str(x).strip()}
    if include_dynamic and codebase_root:
        dyn = load_dynamic_tools(codebase_root=codebase_root)
        for name, dt in dyn.items():
            if allowed_tools and name not in allowed:
                continue
            tools.append(tool_schema(name, dt.description, dt.parameters))

    if not allowed_tools:
        return tools
    filtered: List[Dict[str, Any]] = []
    for t in tools:
        fn = t.get("function") if isinstance(t, dict) and t.get("type") == "function" else None
        name = fn.get("name") if isinstance(fn, dict) else None
        if isinstance(name, str) and name in allowed:
            filtered.append(t)
    return filtered
