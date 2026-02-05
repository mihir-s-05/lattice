from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolContext:
    runner: Any
    tool_call_id: Optional[str]
    goal: str
    messages: List[Dict[str, Any]]
    transcript: Any
    agents: Dict[str, Any]
    ensure_agent: Callable[[str], Any]
    get_agent: Callable[[str], Any]
    decisions: List[Any]
    injected_by_target: Dict[str, List[str]]
    evaluator: Any
    contract_runner: Any
    gates: List[Any]
    plan_graph: Any
    unread_huddles: List[Dict[str, Any]]
    current_step: str
    stage_order: List[str]


class ToolRegistry:
    _handlers: Dict[str, Callable[[ToolContext, Dict[str, Any]], Dict[str, Any]]] = {}

    @classmethod
    def register(cls, name: str):
        def _decorator(fn: Callable[[ToolContext, Dict[str, Any]], Dict[str, Any]]):
            cls._handlers[name] = fn
            return fn

        return _decorator

    @classmethod
    def execute(cls, name: str, ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        fn = cls._handlers.get(name)
        if not fn:
            return {"error": "tool_unavailable", "tool": name}
        return fn(ctx, args or {})


from . import agents as _agents
from . import execution as _execution
from . import files as _files
from . import finalization as _finalization
from . import huddles as _huddles
from . import information as _information
from . import testing as _testing
from . import workflow as _workflow
