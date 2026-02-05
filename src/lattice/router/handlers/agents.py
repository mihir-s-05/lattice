from __future__ import annotations

from typing import Any, Dict, List

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
    if role not in ("backend", "frontend", "llmapi", "tests"):
        return {"error": "unknown_role", "role": role}

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

