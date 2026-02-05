from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from ...subagents.registry import AgentRegistry, FeaturesetSpec, ToolboxVariantSpec, WritePolicySpec
from . import ToolContext, ToolRegistry


@ToolRegistry.register("spawn_agents")
def spawn_agents(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    roles = [str(r) for r in (args.get("roles") or [])]
    spawned: List[str] = []
    already: List[str] = []
    for r in roles:
        a = ctx.ensure_agent(r)
        if a is None:
            continue
        key = f"agent:{r}"
        if key in spawned or key in already:
            already.append(key)
        else:
            spawned.append(key)
    runner.logger.log("agents_spawned", spawned=spawned, already_active=already)
    return {"spawned": spawned, "already_active": already}


@ToolRegistry.register("list_agent_library")
def list_agent_library(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    _ = args
    reg = AgentRegistry(codebase_root=ctx.runner.cwd)
    return reg.list_library()


def _normalize_name(name: str) -> str:
    out = []
    for ch in str(name or ""):
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    return s or "tool"


@ToolRegistry.register("register_featureset")
def register_featureset(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    reg = AgentRegistry(codebase_root=runner.cwd)
    fs_id = str(args.get("id") or "").strip()
    tool_names = [str(x) for x in (args.get("tool_names") or []) if str(x).strip()]
    prompt_prelude = str(args.get("prompt_prelude") or "")
    wp_raw = args.get("write_policy") if isinstance(args.get("write_policy"), dict) else None
    write_policy = None
    if wp_raw is not None:
        allow_raw = wp_raw.get("allow_globs")
        allow_globs = None if allow_raw is None else [str(x) for x in (allow_raw or []) if str(x).strip()]
        deny_globs = [str(x) for x in (wp_raw.get("deny_globs") or []) if str(x).strip()]
        write_policy = WritePolicySpec(allow_globs=allow_globs, deny_globs=deny_globs)
    scope = str(args.get("scope") or "codebase").strip().lower()
    spec = FeaturesetSpec(id=fs_id, tool_names=tool_names, prompt_prelude=prompt_prelude, write_policy=write_policy)
    reg.register_featureset(spec, scope=("global" if scope == "global" else "codebase"))
    runner.logger.log("featureset_registered", id=fs_id, scope=scope, tool_names=tool_names)
    return {"id": fs_id, "scope": scope, "tool_names": tool_names}


@ToolRegistry.register("register_toolbox_variant")
def register_toolbox_variant(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    reg = AgentRegistry(codebase_root=runner.cwd)
    vid = str(args.get("id") or "").strip()
    if vid and not vid.startswith("toolbox/"):
        vid = "toolbox/" + vid
    desc = str(args.get("description") or "").strip()
    tool_names = [str(x) for x in (args.get("tool_names") or []) if str(x).strip()]
    featuresets = [str(x) for x in (args.get("featuresets") or []) if str(x).strip()]
    prompt_prelude = str(args.get("prompt_prelude") or "")
    wp_raw = args.get("write_policy") if isinstance(args.get("write_policy"), dict) else None
    if wp_raw is None:
        wp = WritePolicySpec(allow_globs=None, deny_globs=[])
    else:
        allow_raw = wp_raw.get("allow_globs")
        allow_globs = None if allow_raw is None else [str(x) for x in (allow_raw or []) if str(x).strip()]
        deny_globs = [str(x) for x in (wp_raw.get("deny_globs") or []) if str(x).strip()]
        wp = WritePolicySpec(allow_globs=allow_globs, deny_globs=deny_globs)
    max_iters_raw = args.get("max_tool_iters")
    max_iters = int(max_iters_raw) if isinstance(max_iters_raw, (int, float)) else 8
    tool_choice = str(args.get("tool_choice") or "auto")
    temperature = args.get("temperature")
    temp_f = float(temperature) if isinstance(temperature, (int, float)) else None
    mo = args.get("model_overrides") if isinstance(args.get("model_overrides"), dict) else None
    model_overrides = {str(k): str(v) for k, v in (mo or {}).items() if str(k).strip() and isinstance(v, str) and v.strip()} if mo else None
    scope = str(args.get("scope") or "codebase").strip().lower()
    spec = ToolboxVariantSpec(
        id=vid,
        description=desc,
        tool_names=tool_names,
        featuresets=featuresets,
        prompt_prelude=prompt_prelude,
        write_policy=wp,
        max_tool_iters=max_iters,
        tool_choice=tool_choice,
        temperature=temp_f,
        model_overrides=model_overrides,
    )
    reg.register_toolbox_variant(spec, scope=("global" if scope == "global" else "codebase"))
    mat = reg.materialize_toolbox_variant(vid)
    runner.logger.log("toolbox_variant_registered", id=vid, scope=scope, tools=(mat.tool_names if mat else tool_names))
    return {"id": vid, "scope": scope, "materialized": (asdict(mat) if mat else None)}


@ToolRegistry.register("register_dynamic_tool")
def register_dynamic_tool(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    reg = AgentRegistry(codebase_root=runner.cwd)
    name = _normalize_name(str(args.get("name") or ""))
    description = str(args.get("description") or "")
    code = str(args.get("code") or "")
    if "TOOL_SPEC" not in code:
        safe_desc = description.replace("\\", "\\\\").replace("'", "\\'")
        code = (
            "TOOL_SPEC = {'name': '"
            + name
            + "', 'description': '"
            + safe_desc
            + "', 'parameters': {'type':'object','properties':{},'required':[]}}\n\n"
            + code
        )
    scope = str(args.get("scope") or "codebase").strip().lower()
    out = reg.register_dynamic_tool(name=name, code=code, description=description, scope=("global" if scope == "global" else "codebase"))
    runner.logger.log("dynamic_tool_registered", **out)
    return out


@ToolRegistry.register("destroy_agents")
def destroy_agents(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    roles = [str(r).replace("agent:", "").strip() for r in (args.get("roles") or []) if str(r).strip()]
    destroyed: List[str] = []
    missing: List[str] = []
    for r in roles:
        if r in ctx.agents:
            ctx.agents.pop(r, None)
            destroyed.append(f"agent:{r}")
        else:
            missing.append(f"agent:{r}")
    runner.logger.log("agents_destroyed", destroyed=destroyed, missing=missing, reason=args.get("reason"))
    return {"destroyed": destroyed, "missing": missing}


@ToolRegistry.register("set_agent_permissions")
def set_agent_permissions(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    role = str(args.get("role") or "").replace("agent:", "").strip()
    modep = str(args.get("mode") or "set").strip().lower()
    allow = args.get("allow_globs") or []
    deny = args.get("deny_globs") or []
    if not role:
        return {"error": "missing_role"}

    cur = runner._agent_permissions.get(role) or {"allow_globs": [], "deny_globs": []}
    if modep == "set":
        if allow is not None:
            cur["allow_globs"] = [str(x) for x in (allow or []) if str(x).strip()]
        if deny is not None:
            cur["deny_globs"] = [str(x) for x in (deny or []) if str(x).strip()]
    elif modep == "add":
        cur["allow_globs"] = sorted(set(list(cur.get("allow_globs") or []) + [str(x) for x in (allow or []) if str(x).strip()]))
        cur["deny_globs"] = sorted(set(list(cur.get("deny_globs") or []) + [str(x) for x in (deny or []) if str(x).strip()]))
    elif modep == "remove":
        cur["allow_globs"] = [x for x in (cur.get("allow_globs") or []) if x not in set([str(x) for x in (allow or [])])]
        cur["deny_globs"] = [x for x in (cur.get("deny_globs") or []) if x not in set([str(x) for x in (deny or [])])]

    runner._agent_permissions[role] = cur
    if role in ctx.agents and runner.mode == "tracks":
        ctx.agents[role].set_write_policy(allow_globs=cur.get("allow_globs"), deny_globs=cur.get("deny_globs"))
    runner.logger.log(
        "agent_permissions_set",
        role=role,
        mode=modep,
        allow_globs=cur.get("allow_globs"),
        deny_globs=cur.get("deny_globs"),
        reason=args.get("reason"),
    )
    return {"role": role, "permissions": cur, "mode": runner.mode}


@ToolRegistry.register("get_agent_permissions")
def get_agent_permissions(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    role = args.get("role")
    if role:
        role = str(role).replace("agent:", "").strip()
    if role:
        return {"role": role, "permissions": runner._agent_permissions.get(role)}
    return {"permissions": runner._agent_permissions}
