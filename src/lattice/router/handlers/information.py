from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from ...coherence import run_coherence_checks
from . import ToolContext, ToolRegistry


@ToolRegistry.register("rag_search")
def rag_search(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    q = args.get("query") or ""
    k = int(args.get("top_k") or 5)
    where = args.get("where") if isinstance(args, dict) else None
    hits = runner.rag.search_rag(q, top_k=k, where=(where if isinstance(where, dict) else None))
    runner.logger.log("rag_search", role="router", q=q, top_k=k, hits=[h.get("doc_id") for h in hits])
    return {
        "hits": [
            {
                "doc_id": h.get("doc_id"),
                "score": h.get("score"),
                "path": h.get("path"),
                "tags": h.get("tags"),
                "meta": h.get("meta"),
                "snippet_or_path": (h.get("path") or h.get("snippet")),
            }
            for h in hits
        ]
    }


@ToolRegistry.register("coherence_check")
def coherence_check(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    mx = args.get("max_findings")
    try:
        mxn = int(mx) if mx is not None else 20
    except (TypeError, ValueError):
        mxn = 20
    paths = args.get("paths") if isinstance(args, dict) else None
    changed_since = args.get("changed_since") if isinstance(args, dict) else None
    try:
        cs = float(changed_since) if changed_since is not None else None
    except (TypeError, ValueError):
        cs = None
    findings = run_coherence_checks(runner.cwd, paths=(paths if isinstance(paths, list) else None), changed_since=cs)
    return {"findings": findings[: max(1, min(mxn, 50))]}


@ToolRegistry.register("web_search")
def web_search(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    q = args.get("query") or ""
    k = int(args.get("top_k") or 5)
    tr = args.get("time_range")
    eng = args.get("engines")
    lang = args.get("language")
    pageno = args.get("pageno")
    obs = runner._web_search_exec(q, k, tr, eng, lang, pageno)
    hud_ctx = None
    if ctx.unread_huddles:
        last_hud = ctx.unread_huddles[-1]
        if isinstance(last_hud, dict):
            hud_ctx = (last_hud or {}).get("huddle_id")
    entry = {"ts": time.time(), "huddle_id": hud_ctx, "query": q, "obs": obs}
    runner._web_recent.append(entry)
    if len(runner._web_recent) > 5:
        runner._web_recent[:] = runner._web_recent[-5:]
    return obs
