from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from ...huddle import decision_injection_text, parse_decision_summaries
from . import ToolContext, ToolRegistry


@ToolRegistry.register("open_huddle")
def open_huddle(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    topic = args.get("topic") or runner._huddle_topic(ctx.goal)
    raw_att = args.get("attendees") or []
    agenda = args.get("agenda") or ""
    q_in = args.get("questions")
    questions: List[str] = []
    if isinstance(q_in, list):
        questions = [str(q).strip() for q in q_in if str(q).strip()]
    if (not raw_att) and ctx.unread_huddles:
        last = ctx.unread_huddles[-1]
        if isinstance(last, dict):
            if last.get("attendees"):
                raw_att = last.get("attendees") or []
            if not questions:
                last_q = last.get("questions") or []
                if isinstance(last_q, list):
                    questions = [str(x).strip() for x in last_q if str(x).strip()]

    norm_att: List[str] = ["router"]
    missing_agents: List[str] = []
    include_roles: List[str] = []
    for a in raw_att:
        s = str(a).strip()
        if not s:
            continue
        if s == "router":
            if "router" not in norm_att:
                norm_att.append("router")
            continue
        if not s.startswith("agent:"):
            s = f"agent:{s}"
        if s not in norm_att:
            norm_att.append(s)
        role = s.split("agent:", 1)[-1]
        if role and role not in ctx.agents:
            missing_agents.append(s)
        if role and role not in include_roles:
            include_roles.append(role)

    if missing_agents or not ctx.agents:
        runner.logger.log("huddle_blocked", reason="agents_not_spawned", missing=missing_agents)
        ctx.messages.append({"role": "system", "content": "Guardrail: open_huddle requires spawn_agents first. Spawn missing agents, then retry."})
        return {"error": "huddle_requires_agents", "missing_agents": missing_agents, "note": "Call spawn_agents before open_huddle."}

    if len(ctx.unread_huddles) >= runner._max_open_huddles:
        runner.logger.log("huddle_limit", max_open=runner._max_open_huddles, topic=topic, attendees=norm_att, note="executing check-in anyway")

    agent_reports: List[Dict[str, Any]] = []
    for role, ag in ctx.agents.items():
        agent_reports.append({"agent": role, **asdict(ag.report())})
    state_blob = {
        "mode": runner.mode,
        "current_step": ctx.current_step,
        "checklist": [i.to_dict() for i in runner._checklist.items],
        "latest_tests": ctx.evaluator.latest_tests,
        "latest_gates": [asdict(g) for g in (runner._latest_gate_results or [])],
        "agent_reports": agent_reports,
    }
    proposed = "Manager check-in context:\n" + json.dumps(state_blob, ensure_ascii=False)[:6000]
    hud = runner._execute_huddle(
        topic=topic,
        questions=(questions or ([agenda] if agenda else ["Status? blockers? do we need to switch ladder vs tracks?"])),
        proposed_contract=proposed,
        transcript=ctx.transcript,
        agents=ctx.agents,
        decisions_so_far=ctx.decisions,
        include_agents=(include_roles or None),
    )
    new_decisions = hud.get("decisions") or []
    if isinstance(new_decisions, list):
        ctx.decisions.extend(new_decisions)
        upd = runner._apply_plan_updates_from_decisions(new_decisions)
        if upd.get("mode"):
            ctx.plan_graph.add_reason("mode_change", f"huddle switched mode to {upd.get('mode')}")
        if upd.get("stage_order"):
            ctx.plan_graph.add_reason("plan_change", "huddle updated stage_order")
        ctx.unread_huddles.clear()

    return {
        "huddle_id": (hud.get("huddle_id") or None),
        "decisions": [getattr(d, "id", None) for d in (new_decisions or [])],
        "applied_mode": runner.mode,
        "transcript_path": hud.get("transcript_path"),
        "summary_path": hud.get("summary_path"),
    }


@ToolRegistry.register("request_huddle")
def request_huddle(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    topic = str(args.get("topic") or "").strip() or runner._huddle_topic(ctx.goal)
    q = args.get("questions") or []
    if isinstance(q, str) and q.strip():
        questions = [q.strip()]
    elif isinstance(q, list):
        questions = [str(x).strip() for x in q if str(x).strip()]
    else:
        questions = []
    att = args.get("attendees")
    attendees = None
    if isinstance(att, str) and att.strip():
        attendees = [att.strip()]
    elif isinstance(att, list):
        attendees = [str(x).strip() for x in att if str(x).strip()]
    agenda = args.get("agenda")
    if agenda and not questions:
        questions = [str(agenda).strip()]
    frm = args.get("from") or "router"
    urgency = args.get("urgency") or "normal"
    req = {"from": str(frm), "topic": topic, "questions": questions, "attendees": attendees, "urgency": str(urgency)}
    ctx.unread_huddles.append(req)
    runner.logger.log("huddle_request_queued", **req)
    return {"queued": True, "pending": len(ctx.unread_huddles), "topic": topic, "attendees": attendees}


@ToolRegistry.register("record_decision_summary")
def record_decision_summary(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    d_obj = {
        "topic": args.get("topic") or "",
        "options": args.get("options") or [],
        "decision": args.get("decision"),
        "rationale": args.get("rationale"),
        "risks": args.get("risks") or [],
        "actions": args.get("actions") or [],
        "contracts": args.get("contracts") or [],
        "links": args.get("links") or [],
        "sources": args.get("sources") or None,
    }
    hud_id = args.get("huddle_id")
    if not hud_id and ctx.unread_huddles:
        last_hud = ctx.unread_huddles[-1]
        if isinstance(last_hud, dict):
            hud_id = (last_hud or {}).get("huddle_id")
    transcript_rel = None
    if hud_id:
        rec_path = os.path.join(runner.run_dir, "artifacts", "huddles", f"{hud_id}.json")
        if os.path.exists(rec_path):
            try:
                with open(rec_path, "r", encoding="utf-8") as f:
                    rec_obj = json.load(f)
                transcript_rel = rec_obj.get("transcript_path")
            except (OSError, ValueError):
                transcript_rel = None

    ds = parse_decision_summaries(json.dumps(d_obj))[0]
    from ...huddle import _normalize_sources, persist_decision_summary

    base_sources = _normalize_sources(ds.sources)
    externals = [s for s in base_sources if s.get("type") == "external"]
    if len(externals) < 3 and runner._web_recent:
        picks: List[Dict[str, Any]] = []
        seen = {("external", s.get("url")) for s in externals if s.get("url")}
        for r in list(runner._web_recent)[-5:]:
            obsr = r.get("obs") or {}
            for it in (obsr.get("results") or [])[:5]:
                u = (it or {}).get("url")
                if not u:
                    continue
                key = ("external", u)
                if key in seen:
                    continue
                seen.add(key)
                title = (it or {}).get("title") or None
                picks.append({"type": "external", "url": u, **({"title": title} if title else {})})
                if len(externals) + len(picks) >= 3:
                    break
            if len(externals) + len(picks) >= 3:
                break
        if picks:
            base_sources = base_sources + picks
            ds.sources = base_sources
            if not isinstance(ds.meta, dict):
                ds.meta = {}
            ds.meta["auto_populated_sources"] = True

    if transcript_rel:
        ds.links = (ds.links or []) + [{"type": "artifact", "id": transcript_rel, "title": "Huddle Transcript"}]
        base_sources2 = list(ds.sources) if isinstance(ds.sources, list) else []
        base_sources2.append({"type": "artifact", "id": transcript_rel})
        ds.sources = base_sources2

    def _autopopulate_from_recent(ds_obj):
        if ds_obj.sources:
            return ds_obj
        recent = list(runner._web_recent)[-3:]
        if not recent:
            return ds_obj
        urls_ranked: List[Dict[str, Any]] = []
        for r in recent:
            obsr = r.get("obs") or {}
            res = obsr.get("results") or []
            exs = obsr.get("extracts") or []
            ok_urls = {e.get("url") for e in exs if isinstance(e, dict) and str(e.get("status")) == "200" and (e.get("content_md") or "")}
            for it in res:
                u = (it or {}).get("url")
                if not u:
                    continue
                title = (it or {}).get("title") or None
                ts_val = (it or {}).get("time?") or None
                score = 1 + (5 if u in ok_urls else 0)
                urls_ranked.append({"url": u, "title": title, "ts": ts_val, "score": score})
        if not urls_ranked:
            return ds_obj
        seen: set = set()
        picks: List[Dict[str, Any]] = []
        for it in sorted(urls_ranked, key=lambda x: x.get("score", 0), reverse=True):
            u = it.get("url")
            if u in seen:
                continue
            seen.add(u)
            picks.append({"type": "external", "url": u, **({"title": it.get("title")} if it.get("title") else {}), **({"ts": it.get("ts")} if it.get("ts") else {})})
            if len(picks) >= 5:
                break
        if picks:
            ds_obj.sources = (ds_obj.sources or []) + picks
            if not isinstance(ds_obj.meta, dict):
                ds_obj.meta = {}
            ds_obj.meta["auto_populated_sources"] = True
        return ds_obj

    if not ds.sources:
        ds = _autopopulate_from_recent(ds)

    ds, ds_rel = persist_decision_summary(runner.run_dir, runner.artifacts, runner.rag, ds)
    ctx.decisions.append(ds)
    runner.logger.log("decision_summary_updated", ds_id=ds.id, fields_updated=["sources", "links", "meta"], path=os.path.join(runner.run_dir, ds_rel))
    if hud_id:
        rec_path = os.path.join(runner.run_dir, "artifacts", "huddles", f"{hud_id}.json")
        if os.path.exists(rec_path):
            with open(rec_path, "r", encoding="utf-8") as f:
                rec_obj = json.load(f)
            decs = rec_obj.get("decisions") or []
            if ds.id not in decs:
                decs.append(ds.id)
            rec_obj["decisions"] = decs
            with open(rec_path, "w", encoding="utf-8") as f:
                json.dump(rec_obj, f, indent=2)
    runner.logger.log("decision_summary", decision_id=ds.id, topic=ds.topic, decision=ds.decision)

    injected = ["router"]
    ctx.transcript.add_decision_injection(decision_injection_text([ds]))
    summary_line = f"{ds.topic}: {ds.decision or ''}".strip()
    ctx.transcript.add_info("Router Decision", summary_line)
    for role in list(ctx.agents.keys()):
        injected.append(f"agent:{role}")
    runner.logger.log("decision_injected", decision_id=ds.id, targets=injected)
    return {"decision_id": ds.id, "injected_into": injected}


@ToolRegistry.register("inject_summary")
def inject_summary(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    runner = ctx.runner
    ds_id = args.get("decision_id") or ""
    targets = args.get("targets") or []
    for t in targets:
        ctx.injected_by_target.setdefault(t, [])
        if ds_id not in ctx.injected_by_target[t]:
            ctx.injected_by_target[t].append(ds_id)
    dd = next((d for d in ctx.decisions if getattr(d, "id", None) == ds_id), None)
    if dd:
        ctx.transcript.add_decision_injection(decision_injection_text([dd]))
    runner.logger.log("decision_injected", decision_id=ds_id, targets=targets)
    return {"targets_injected": list(targets)}
