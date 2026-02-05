from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from ..artifacts import ArtifactStore
from ..config import RunConfig
from ..huddle import (
    DecisionSummary,
    parse_decision_summaries,
    save_decisions,
    save_huddle,
    decision_injection_text,
    ensure_unique_ids,
    dedupe_decisions,
    ensure_provenance_links,
    validate_decision_integrity,
)
from ..ids import ulid
from ..rag import RagIndex
from ..runlog import RunLogger
from ..router_llm import RouterLLM
from ..transcript import RunningTranscript

def huddle_topic(goal: str) -> str:
    g = (goal or "").strip()
    if not g:
        return "Align API contract"
    return f"Align API contract for: {g}"

def execute_huddle(
    runner,
    topic: str,
    questions: List[str],
    proposed_contract: Optional[str],
    transcript: RunningTranscript,
    agents: Dict[str, Any],
    decisions_so_far: List[DecisionSummary],
    *,
    include_agents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    self = runner
    if include_agents:
        agent_attendees = [str(n) for n in include_agents if str(n) in agents]
    else:
        agent_attendees = [n for n in ("backend", "frontend", "llmapi", "tests") if n in agents]
    attendees = ["router"] + agent_attendees
    hud_id = f"hud_{ulid()}"
    self.logger.log("huddle_request", requester="router", attendees=attendees, topic=topic, questions=questions)
    self.logger.log("huddle_open", id=hud_id, topic=topic, requester="router", attendees=attendees, mode=self.cfg.huddles_mode)
    transcript.add_meeting(topic=topic, attendees=attendees, questions=questions or [])

    rllm = RouterLLM(self.cfg, self.logger, tools=self._build_tools_manifest())
    prov = None
    model = None
    message_events: List[Dict[str, str]] = []
    t0 = time.time()

    def _run_huddle_synthesis(huddle_msgs: List[Dict[str, Any]], *, allow_tools: bool) -> tuple[Optional[str], Optional[str], Optional[str], str]:
        out_text = ""
        out_provider: Optional[str] = None
        out_model: Optional[str] = None
        out_api: Optional[str] = None
        huddle_tools = []
        if allow_tools:
            for t in (self._build_tools_manifest() or []):
                name = (t.get("function", {}) or {}).get("name")
                if name in ("web_search", "rag_search"):
                    huddle_tools.append(t)

        for _round in range(4):
            if allow_tools and huddle_tools and self.cfg.web_search_enabled:
                out_obj = rllm._call_with_tools(huddle_msgs, tools=huddle_tools, phase="huddle", tool_choice="auto")
            else:
                out_obj = rllm._call(huddle_msgs, phase="huddle")

            out_provider = out_obj.get("provider")
            out_model = out_obj.get("model")
            out_api = out_obj.get("api")
            tool_calls = out_obj.get("tool_calls") or []

            if allow_tools and tool_calls:
                assistant_msg: Dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": tool_calls}
                if out_api == "responses" and out_obj.get("prev_response_id_enabled"):
                    assistant_msg["_skip_for_responses"] = True
                huddle_msgs.append(assistant_msg)
                for tc in tool_calls:
                    tname = (tc or {}).get("function", {}).get("name") or ""
                    targs_s = (tc or {}).get("function", {}).get("arguments") or "{}"
                    try:
                        targs = json.loads(targs_s)
                    except json.JSONDecodeError:
                        targs = {}
                    obs: Dict[str, Any] = {}
                    if tname == "web_search":
                        q = targs.get("query") or ""
                        k = int(targs.get("top_k") or 5)
                        tr = targs.get("time_range")
                        eng = targs.get("engines")
                        lang = targs.get("language")
                        pageno = targs.get("pageno")
                        obs = self._web_search_exec(q, k, tr, eng, lang, pageno)
                        entry = {"ts": time.time(), "huddle_id": hud_id, "query": q, "obs": obs}
                        self._web_recent.append(entry)
                        if len(self._web_recent) > 5:
                            self._web_recent = self._web_recent[-5:]
                    elif tname == "rag_search":
                        q = targs.get("query") or ""
                        k = int(targs.get("top_k") or 5)
                        hits = self.rag.search_rag(q, top_k=k)
                        obs = {"hits": hits}
                    else:
                        obs = {"error": "tool_unavailable", "note": f"Unsupported huddle tool: {tname}"}
                    huddle_msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": (tc.get("id") if isinstance(tc, dict) else None),
                            "name": tname,
                            "content": json.dumps(obs, ensure_ascii=False),
                        }
                    )
                continue

            out_text = out_obj.get("text") or ""
            break

        return out_provider, out_model, out_api, out_text
    if (self.cfg.huddles_mode or "dialog") == "synthesis":
        huddle_sys = (
            "You are facilitating a Huddle. Return 1-3 DecisionSummary JSON objects"
            " with fields: id (optional), topic, options[], decision, rationale, risks[], actions[], contracts[], links[], sources[]."
            " You MAY also include meta:{mode:'ladder'|'tracks'|null, stage_order?:string[], note?:string} for operational changes."
            " Requirements:"
            " - options: array (objects or strings). If objects, include id and description."
            " - sources: array with at least 3 external entries, each {type:'external',url:'<url>',title:'<title>'}."
            " Use web_search tool if available to find relevant sources."
            " Output only JSON (array or one object)."
        )
        lines = [f"Huddle Topic: {topic}"]
        if questions:
            lines.append("Questions:")
            lines += [f"- {q}" for q in questions]
        if proposed_contract:
            lines.append("Proposed contract excerpt:\n" + proposed_contract[:3000])
        huddle_msgs: List[Dict[str, Any]] = [{"role": "system", "content": huddle_sys}, {"role": "user", "content": "\n".join(lines)}]
        prov, model, _api, out = _run_huddle_synthesis(huddle_msgs, allow_tools=True)
        transcript.add_model_call(title="Huddle", provider=prov or "?", model=model or "?", messages=huddle_msgs[-6:], output=out)
        decisions = parse_decision_summaries(out)
        rec, t_rel, r_rel = save_huddle(
            run_dir=self.run_dir,
            artifacts=self.artifacts,
            rag_index=self.rag,
            requester="router",
            attendees=attendees,
            topic=topic,
            questions=questions or [],
            notes="DecisionSummaries produced by LLM.",
            decisions=decisions,
            hud_id=hud_id,
            mode="synthesis",
            auto_decision=True,
            messages=None,
        )
        try:
            decisions = ensure_unique_ids(decisions)
            decisions = dedupe_decisions(decisions)
            decisions = ensure_provenance_links(decisions, default_link={"title": "Huddle Transcript", "url": os.path.join(self.run_dir, t_rel)})
            validate_decision_integrity(decisions)
        except Exception as e:
            self.logger.log("decision_integrity_error", error=str(e))
        saved = save_decisions(self.run_dir, self.artifacts, self.rag, decisions)
        for d, rel in saved:
            self.logger.log("decision_summary", decision_id=d.id, topic=d.topic, decision=d.decision, path=os.path.join(self.run_dir, rel))
            self.logger.log("huddle_decision", huddle_id=hud_id, decision_summary_id=d.id, synthesis_provider=prov, model=model)
        dt = int((time.time() - t0) * 1000)
        self.logger.log("huddle_close", huddle_id=hud_id, duration_ms=dt, message_count=0)
        self.logger.log(
            "huddle_complete",
            huddle_id=hud_id,
            decisions=[d.id for d in decisions],
            transcript_path=os.path.join(self.run_dir, t_rel),
            router_llm_provider=prov,
            router_llm_model=model,
        )
        return {"huddle_id": hud_id, "decisions": decisions, "transcript_path": t_rel, "summary_path": getattr(rec, "summary_path", None)}
    else:
        from datetime import datetime, timezone
        import re
        def _now() -> str:
            return datetime.now(timezone.utc).isoformat()
        def _summarize_transcript(msgs: List[Dict[str, str]], max_chars: int = 5500) -> str:
            blocks: List[str] = []
            for m in msgs[-20:]:
                who = m.get('from', '?')
                content = str(m.get('content', '')).strip()
                blocks.append(f"{who}:\n{content}")
            text = "\n\n".join(blocks)
            return text if len(text) <= max_chars else text[-max_chars:]
        agree_state: Dict[str, Dict[str, Any]] = {name: {"agree": False, "blockers": []} for name in agent_attendees}
        def _parse_consensus(name: str, content: str) -> None:
            txt = content or ""
            m = re.search(r"(?im)\bAGREE\s*:\s*(yes|no)\b", txt)
            if m:
                agree_state[name]["agree"] = (m.group(1).lower() == "yes")
            else:
                if re.search(r"(?i)no\s+blockers|ready\s+to\s+proceed|lgtm|looks\s+good", txt):
                    agree_state[name]["agree"] = True
            blocks = []
            for mm in re.finditer(r"(?im)^\s*(BLOCKERS?|BLOCKING)\s*:\s*(.+)$", txt):
                blocks.append(mm.group(2).strip())
            if blocks:
                agree_state[name]["blockers"].extend(blocks)
            if any(b and b.lower() not in ("none", "n/a", "na") for b in agree_state[name]["blockers"]):
                agree_state[name]["agree"] = False

        router_intro = (
            "Huddle opened. Please reply with: interface deltas, constraints, blocking questions, and a consensus signal.\n"
            "Format: end with `AGREE: yes|no` and optionally `BLOCKERS: …` if no. Keep it concise (<= 8 bullets)."
        )
        self.logger.log("huddle_message", **{"huddle_id": hud_id, "from": "router", "content_preview": router_intro[:500], "content_ref": None})
        message_events.append({"ts": _now(), "from": "router", "content": router_intro})

        inject = decision_injection_text(decisions_so_far) if decisions_so_far else ""
        max_rounds = 5
        round_idx = 0
        while round_idx < max_rounds:
            round_idx += 1
            for name in agent_attendees:
                agent = agents.get(name)
                if agent is None:
                    continue
                goal = (self._goal or "").strip()
                goal_line = f"Goal: {goal}\n" if goal else "Goal: (not provided)\n"
                sys_msg = (
                    f"You are the {name.capitalize()}Agent in a huddle for the current goal.\n"
                    + goal_line +
                    "Reply concisely with markdown bullets (<= 8). Include at end: `AGREE: yes|no` and, if no, `BLOCKERS: <brief>`\n"
                    "Focus on interface deltas, constraints, and blocking questions. Ignore unrelated existing repo context unless explicitly relevant to the goal."
                )
                user_lines = [
                    f"Agenda: {topic}",
                    "",
                    (f"Goal: {goal}" if goal else "Goal: (not provided)"),
                    "",
                    "Current facts:",
                    inject or "(none)",
                    "",
                    "Transcript so far:",
                    _summarize_transcript(message_events),
                    "",
                    "Open questions:",
                ] + [f"- {q}" for q in (questions or [])]
                messages = [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": "\n".join(user_lines)},
                ]
                try:
                    out = agent._model(messages, temperature=0.1)
                except ProviderError as e:
                    out = f"(error during huddle: {e})"
                ts = _now()
                self.logger.log("huddle_message", **{"huddle_id": hud_id, "from": name, "content_preview": (str(out)[:500]), "content_ref": None})
                message_events.append({"ts": ts, "from": name, "content": str(out)})
                _parse_consensus(name, str(out))

            all_agree = all(agree_state[n]["agree"] for n in agent_attendees)
            if all_agree:
                break

            unresolved = [n for n in agent_attendees if not agree_state[n]["agree"]]
            follow_lines = [
                f"Round {round_idx} summary: awaiting consensus from {', '.join(unresolved)}.",
                "Please address blockers and confirm readiness with `AGREE: yes|no`.",
            ]
            for n in unresolved:
                bl = agree_state[n]["blockers"]
                if bl:
                    follow_lines.append(f"- {n} blockers: " + "; ".join(bl)[:300])
            router_msg = "\n".join(follow_lines)
            self.logger.log("huddle_message", **{"huddle_id": hud_id, "from": "router", "content_preview": router_msg[:500], "content_ref": None})
            message_events.append({"ts": _now(), "from": "router", "content": router_msg})

        transcript_blocks: List[str] = []
        for m in message_events:
            transcript_blocks.append(f"{m['from']} says:\n{m['content']}")
        dialog_context = "\n\n".join(transcript_blocks)

        huddle_sys = (
            "You are facilitating a Huddle. Return 1-3 DecisionSummary JSON objects"
            " with fields: id (optional), topic, options[], decision, rationale, risks[], actions[], contracts[], links[], sources[]."
            " You MAY also include meta:{mode:'ladder'|'tracks'|null, stage_order?:string[], note?:string} for operational changes."
            " Requirements:"
            " - options: array (objects or strings). If objects, include id and description."
            " - sources: array with at least 3 external entries, each {type:'external',url:'<url>',title:'<title>'}."
            " Use web_search tool if available to find relevant sources."
            " Output only JSON (array or one object)."
        )
        huddle_msgs: List[Dict[str, Any]] = [
            {"role": "system", "content": huddle_sys},
            {"role": "user", "content": f"Huddle Topic: {topic}\n\nTranscript follows:\n\n{dialog_context}"},
        ]
        prov, model, _api, out = _run_huddle_synthesis(huddle_msgs, allow_tools=True)
        transcript.add_model_call(title="Huddle Synthesis", provider=prov or "?", model=model or "?", messages=huddle_msgs[-6:], output=out)

        decisions = parse_decision_summaries(out)
        notes_text = "Dialog concluded with consensus." if all(agree_state[n]["agree"] for n in agent_attendees) else "Dialog concluded. Proceeding with best-effort consensus."
        rec, t_rel, r_rel = save_huddle(
            run_dir=self.run_dir,
            artifacts=self.artifacts,
            rag_index=self.rag,
            requester="router",
            attendees=attendees,
            topic=topic,
            questions=questions or [],
            notes=notes_text,
            decisions=decisions,
            hud_id=hud_id,
            mode="dialog",
            auto_decision=False,
            messages=message_events,
        )
        try:
            rec_abs = os.path.join(self.run_dir, r_rel)
            with open(rec_abs, "r", encoding="utf-8") as f:
                rec_obj = json.load(f)
            rec_obj["synth_provider"] = prov
            rec_obj["synth_model"] = model
            with open(rec_abs, "w", encoding="utf-8") as f:
                json.dump(rec_obj, f, indent=2)
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            pass
        for d in decisions:
            d.links = (d.links or []) + [{"title": "Huddle Transcript", "url": os.path.join(self.run_dir, t_rel)}]
        saved = save_decisions(self.run_dir, self.artifacts, self.rag, decisions)
        for d, rel in saved:
            self.logger.log("decision_summary", decision_id=d.id, topic=d.topic, decision=d.decision, path=os.path.join(self.run_dir, rel))
            self.logger.log("huddle_decision", huddle_id=hud_id, decision_summary_id=d.id, synthesis_provider=prov, model=model)
        dt = int((time.time() - t0) * 1000)
        self.logger.log("huddle_close", huddle_id=hud_id, duration_ms=dt, message_count=len(message_events))
        self.logger.log(
            "huddle_complete",
            huddle_id=hud_id,
            decisions=[d.id for d in decisions],
            transcript_path=os.path.join(self.run_dir, t_rel),
            router_llm_provider=prov,
            router_llm_model=model,
        )
        return {"huddle_id": hud_id, "decisions": decisions, "transcript_path": t_rel, "summary_path": getattr(rec, "summary_path", None)}



def web_search_exec(*, cfg: RunConfig, logger: RunLogger, web_disabled: bool, providers: Any, query: str, top_k: int, time_range: Optional[str], engines: Optional[str], language: Optional[str], pageno: Optional[int]) -> Dict[str, Any]:
    if web_disabled:
        logger.log("web_search_unavailable", reason="disabled_by_flag")
        return {"error": "tool_unavailable", "reason": "web_search disabled (disabled_by_flag)"}
    if not getattr(cfg, "web_search_enabled", False):
        logger.log("web_search_unavailable", reason="disabled_by_config")
        return {"error": "tool_unavailable", "reason": "disabled_by_config"}

    from ..providers import provider_web_search_via_llm

    prov = (cfg.router_provider_order[0] if cfg.router_provider_order else None) or "openai"
    prov = {"lmstudio": "local"}.get(prov, prov)
    model = (
        (cfg.router_model_default or "").strip()
        or (getattr(providers.get(prov), "model", None) if isinstance(providers, dict) else None)
        or ""
    )
    return provider_web_search_via_llm(
        providers=providers,
        provider=prov,
        model=model,
        query=query,
        top_k=top_k,
        language=language,
        time_range=time_range,
        engines=engines,
        pageno=pageno,
        logger=logger,
        caller="router",
    )

class HuddleExecutor:
    def __init__(self, *, run_dir: str, cfg: Optional[RunConfig], logger: RunLogger, artifacts: ArtifactStore, rag: RagIndex, tools_builder, web_disabled: bool = False, web_recent: Optional[List[Dict[str, Any]]] = None) -> None:
        self.run_dir = run_dir
        self.cfg = cfg
        self.logger = logger
        self.artifacts = artifacts
        self.rag = rag
        self.tools_builder = tools_builder
        self.web_disabled = web_disabled
        self.web_recent = web_recent if isinstance(web_recent, list) else []

    def _build_tools_manifest(self) -> List[Dict[str, Any]]:
        return list(self.tools_builder() or [])

    def huddle_topic(self, goal: str) -> str:
        return huddle_topic(goal)

    def execute(self, *, runner, topic: str, questions: List[str], proposed_contract: Optional[str], transcript: RunningTranscript, agents: Dict[str, Any], decisions_so_far: List[DecisionSummary], include_agents: Optional[List[str]] = None) -> Dict[str, Any]:
        return execute_huddle(runner, topic, questions, proposed_contract, transcript, agents, decisions_so_far, include_agents=include_agents)

    def web_search_exec(self, query: str, top_k: int, time_range: Optional[str], engines: Optional[str], language: Optional[str], pageno: Optional[int]) -> Dict[str, Any]:
        if self.cfg is None:
            raise AssertionError("cfg is required")
        return web_search_exec(cfg=self.cfg, logger=self.logger, web_disabled=bool(self.web_disabled), providers=self.cfg.providers, query=query, top_k=top_k, time_range=time_range, engines=engines, language=language, pageno=pageno)
