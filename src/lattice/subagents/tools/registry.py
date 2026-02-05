from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..workspace import WorkspaceAccess


@dataclass
class AgentToolContext:
    agent_name: str
    cfg: Any
    logger: Any
    rag: Any
    workspace_root: str
    codebase_root: str
    allow_write_globs: Optional[List[str]]
    deny_write_globs: List[str]
    request_huddle_cb: Optional[Any]
    workspace: WorkspaceAccess


class AgentToolRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, Callable[[AgentToolContext, Dict[str, Any]], Dict[str, Any]]] = {}

    def register(self, name: str, fn: Callable[[AgentToolContext, Dict[str, Any]], Dict[str, Any]]) -> None:
        self._handlers[name] = fn

    def execute(self, name: str, ctx: AgentToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        fn = self._handlers.get(name)
        if not fn:
            return {"error": "tool_unavailable", "reason": f"unknown tool: {name}"}
        return fn(ctx, args or {})

    @property
    def handlers(self) -> Dict[str, Callable[[AgentToolContext, Dict[str, Any]], Dict[str, Any]]]:
        return dict(self._handlers)

