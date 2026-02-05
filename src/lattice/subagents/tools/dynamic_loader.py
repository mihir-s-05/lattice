from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable, Dict

from ..registry import AgentRegistry


@dataclass
class DynamicTool:
    name: str
    description: str
    parameters: Dict[str, Any]
    run: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


def load_dynamic_tools(*, codebase_root: str) -> Dict[str, DynamicTool]:
    reg = AgentRegistry(codebase_root=codebase_root)
    lib = reg.list_library()
    dyn = lib.get("dynamic_tools") if isinstance(lib, dict) else {}
    if not isinstance(dyn, dict):
        return {}
    if dyn.get("enabled") is False:
        return {}
    modules = dyn.get("modules")
    if not isinstance(modules, dict):
        return {}
    out: Dict[str, DynamicTool] = {}
    for name, spec in modules.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(spec, dict):
            continue
        path = spec.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        abs_path = os.path.expanduser(path)
        if not os.path.isabs(abs_path):
            abs_path = os.path.abspath(abs_path)
        if not os.path.isfile(abs_path):
            continue
        mod = _load_module(f"lattice_dyn_tool_{name}", abs_path)
        tool_spec = getattr(mod, "TOOL_SPEC", None)
        run_fn = getattr(mod, "run", None)
        if not isinstance(tool_spec, dict):
            continue
        if not callable(run_fn):
            continue
        desc = tool_spec.get("description") or spec.get("description") or ""
        params = tool_spec.get("parameters") if isinstance(tool_spec.get("parameters"), dict) else {}
        out[name] = DynamicTool(
            name=name,
            description=str(desc),
            parameters=params,
            run=run_fn,
        )
    return out


def _load_module(name: str, path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
