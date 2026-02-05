from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .config import RunConfig
from .rag import RagIndex
from .runlog import RunLogger
from .subagents.tools.builtins import register_builtins
from .subagents.tools.registry import AgentToolContext, AgentToolRegistry
from .subagents.tools.dynamic_loader import load_dynamic_tools
from .subagents.tools.schemas import build_manifest
from .subagents.workspace import WorkspaceAccess


def build_agent_tools_manifest(
    *,
    allowed_tools: Optional[List[str]] = None,
    cfg: Optional[RunConfig] = None,
    codebase_root: Optional[str] = None,
    include_dynamic: bool = False,
) -> List[Dict[str, Any]]:
    return build_manifest(
        allowed_tools=allowed_tools,
        cfg=cfg,
        codebase_root=codebase_root,
        include_dynamic=include_dynamic,
    )


class AgentToolExecutor:
    def __init__(
        self,
        *,
        agent_name: str,
        cfg: RunConfig,
        logger: RunLogger,
        rag: RagIndex,
        workspace_root: str,
        allow_write_globs: Optional[List[str]] = None,
        deny_write_globs: Optional[List[str]] = None,
        request_huddle_cb: Optional[Any] = None,
        codebase_root: Optional[str] = None,
        include_dynamic: bool = False,
    ) -> None:
        self.agent_name = agent_name
        self.cfg = cfg
        self.logger = logger
        self.rag = rag
        self.workspace_root = workspace_root
        self.codebase_root = codebase_root or workspace_root
        self.allow_write_globs = [str(x) for x in (allow_write_globs or []) if str(x).strip()] if allow_write_globs is not None else None
        self.deny_write_globs = [str(x) for x in (deny_write_globs or []) if str(x).strip()]
        self._request_huddle_cb = request_huddle_cb
        self._workspace = WorkspaceAccess(self.workspace_root)
        reg = AgentToolRegistry()
        register_builtins(reg)
        if include_dynamic:
            dyn = load_dynamic_tools(codebase_root=self.codebase_root)
            for name, tool in dyn.items():
                reg.register(name, _dynamic_wrapper(reg, tool.run))
        self._registry = reg

    def execute(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        ctx = AgentToolContext(
            agent_name=self.agent_name,
            cfg=self.cfg,
            logger=self.logger,
            rag=self.rag,
            workspace_root=self.workspace_root,
            codebase_root=self.codebase_root,
            allow_write_globs=self.allow_write_globs,
            deny_write_globs=self.deny_write_globs,
            request_huddle_cb=self._request_huddle_cb,
            workspace=self._workspace,
        )
        return self._registry.execute(name, ctx, args if isinstance(args, dict) else {})


def _dynamic_wrapper(reg: AgentToolRegistry, fn):
    def _handler(ctx: AgentToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
        dctx = {
            "agent_name": ctx.agent_name,
            "workspace_root": ctx.workspace_root,
            "codebase_root": ctx.codebase_root,
            "logger": ctx.logger,
            "rag": ctx.rag,
            "tool": lambda name, a=None: reg.execute(str(name), ctx, (a if isinstance(a, dict) else {})),
        }
        out = fn(dctx, args or {})
        return out if isinstance(out, dict) else {"error": "invalid_dynamic_tool_return"}

    return _handler


def append_tool_result_message(messages: List[Dict[str, Any]], tool_call: Dict[str, Any], obs: Dict[str, Any]) -> None:
    call_id = tool_call.get("id") if isinstance(tool_call, dict) else None
    name = (tool_call.get("function") or {}).get("name") if isinstance(tool_call, dict) else None
    messages.append({
        "role": "tool",
        "tool_call_id": call_id,
        "name": name or "",
        "content": json.dumps(obs, ensure_ascii=False),
    })
