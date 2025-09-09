from __future__ import annotations

import json
import os
import random
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from .agents import (
    AgentPlan,
    AgentReport,
    ArtifactRef,
    BackendAgent,
    FrontendAgent,
    LLMApiAgent,
    TestAgent,
)
from .artifacts import ArtifactStore
from .config import RunConfig, load_run_config
from .contracts import ContractRunner
from .huddle import (
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
from .ids import ulid
from .providers import call_with_fallback, ProviderError
from .rag import RagIndex
from .runlog import RunLogger
from .stage_gates import GateEvaluator, StageGate
from .transcript import RunningTranscript
from .worker import gen_run_id
from .router_llm import RouterLLM
from .plan import PlanGraph, PlanNode
from .knowledge import KnowledgeBus
from .provenance import evidence_from_artifact_path
from .finalize import run_finalization


class RouterRunner:
    def __init__(self, cwd: str, run_id: Optional[str] = None, mode: Optional[str] = None, no_websearch: bool = False) -> None:
        self.cwd = cwd
        self.run_id = run_id or gen_run_id()
        self.run_dir = os.path.join(cwd, "runs", self.run_id)
        os.makedirs(self.run_dir, exist_ok=True)
        self.logger = RunLogger(self.run_dir)
        self.artifacts = ArtifactStore(self.run_dir)
        self.rag = RagIndex(self.run_dir)
        self.cfg: Optional[RunConfig] = None
        default_mode = "weave" if (os.environ.get("LATTICE_MODE") is None) else os.environ.get("LATTICE_MODE")
        if not mode and not default_mode:
            default_mode = random.choice(["ladder", "tracks", "weave"])
        self.mode = (mode or default_mode or "ladder").strip().lower()
        if self.mode not in ("ladder", "tracks", "weave"):
            self.mode = "ladder"

        self._decisions: List[DecisionSummary] = []
        self._provider_usage: Dict[str, int] = {}
        self._max_slice_agents: int = 3
        self._max_open_huddles: int = 2
        self._cooldown_threshold: int = 2
        self._cooldown_seconds: float = 5.0
        self._gate_failures: Dict[str, Tuple[int, float]] = {}
        self._web_recent: List[Dict[str, Any]] = []
        self._web_disabled_by_flag: bool = bool(no_websearch)

    def _huddle_topic(self, goal: str) -> str:
        g = (goal or "").strip()
        if not g:
            return "Align API contract"
        return f"Align API contract for: {g}"

    def _execute_huddle(
        self,
        topic: str,
        questions: List[str],
        proposed_contract: Optional[str],
        transcript: RunningTranscript,
        agents: Dict[str, Any],
        decisions_so_far: List[DecisionSummary],
    ) -> Dict[str, Any]:
        assert self.cfg is not None
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
        t0 = __import__("time").time()
        if (self.cfg.huddles_mode or "dialog") == "synthesis":
            out_obj = rllm.huddle(topic, questions, proposed_contract)
            prov = out_obj.get("provider")
            model = out_obj.get("model")
            out = out_obj.get("text") or ""
            transcript.add_model_call(title="Huddle", provider=prov or "?", model=model or "?", messages=[{"role":"system","content":"(router_llm)"}], output=out)
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
            dt = int((__import__("time").time() - t0) * 1000)
            self.logger.log("huddle_close", huddle_id=hud_id, duration_ms=dt, message_count=0)
            self.logger.log(
                "huddle_complete",
                huddle_id=hud_id,
                decisions=[d.id for d in decisions],
                transcript_path=os.path.join(self.run_dir, t_rel),
                router_llm_provider=prov,
                router_llm_model=model,
            )
            return {"decisions": decisions, "transcript_path": t_rel}
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
                    sys_msg = (
                        f"You are the {name.capitalize()}Agent in a huddle. "
                        "Reply concisely with markdown bullets (<= 8). Include at end: `AGREE: yes|no` and, if no, `BLOCKERS: <brief>`\n"
                        "Focus on interface deltas, constraints, and blocking questions."
                    )
                    user_lines = [
                        f"Agenda: {topic}",
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
                    except Exception as e:
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

            out_obj = rllm.huddle(topic, questions, f"Transcript follows:\n\n{dialog_context}")
            prov = out_obj.get("provider")
            model = out_obj.get("model")
            out = out_obj.get("text") or ""
            transcript.add_model_call(title="Huddle Synthesis", provider=prov or "?", model=model or "?", messages=[{"role":"system","content":"(router_llm)"}], output=out)

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
            except Exception:
                pass
            for d in decisions:
                try:
                    d.links = (d.links or []) + [{"title": "Huddle Transcript", "url": os.path.join(self.run_dir, t_rel)}]
                except Exception:
                    pass
            saved = save_decisions(self.run_dir, self.artifacts, self.rag, decisions)
            for d, rel in saved:
                self.logger.log("decision_summary", decision_id=d.id, topic=d.topic, decision=d.decision, path=os.path.join(self.run_dir, rel))
                self.logger.log("huddle_decision", huddle_id=hud_id, decision_summary_id=d.id, synthesis_provider=prov, model=model)
            dt = int((__import__("time").time() - t0) * 1000)
            self.logger.log("huddle_close", huddle_id=hud_id, duration_ms=dt, message_count=len(message_events))
            self.logger.log(
                "huddle_complete",
                huddle_id=hud_id,
                decisions=[d.id for d in decisions],
                transcript_path=os.path.join(self.run_dir, t_rel),
                router_llm_provider=prov,
                router_llm_model=model,
            )
            return {"decisions": decisions, "transcript_path": t_rel}

    def run(self, goal: str) -> Dict[str, Any]:
        self.cfg = load_run_config(self.run_id, goal)

        cfg_public = self.cfg.to_public_dict()
        with open(os.path.join(self.run_dir, "config.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(cfg_public, indent=2))
        self.logger.log("run_start", run_id=self.run_id, run_dir=self.run_dir, mode=self.mode, config=cfg_public)

        try:
            if getattr(self.cfg, "router_policy", "llm") == "llm":
                return self._run_agentic(goal)
        except Exception:
            pass

        try:
            if isinstance(goal, str) and ("readme" in goal.lower() or "docs" in goal.lower()) and self.mode in ("ladder", "tracks"):
                self.logger.log("plan_switch", from_mode=self.mode, to_mode="weave", reason_type="scope_change", details="goal mentions README/docs", decisions=[])
                self.mode = "weave"
        except Exception:
            pass

        try:
            from .worker import WorkerRunner

            wr = WorkerRunner(self.cwd, self.run_id)
            wr._pre_ingest_repo_files()
        except Exception:
            pass

        transcript = RunningTranscript(self.run_id)
        kbus = KnowledgeBus(self.run_dir, self.logger)

        rllm = RouterLLM(self.cfg, self.logger, tools=self._build_tools_manifest())
        fe = FrontendAgent("frontend", self.cfg, self.logger, self.artifacts, self.rag)
        be = BackendAgent("backend", self.cfg, self.logger, self.artifacts, self.rag)
        llm = LLMApiAgent("llmapi", self.cfg, self.logger, self.artifacts, self.rag)
        tst = TestAgent("tests", self.cfg, self.logger, self.artifacts, self.rag)
        agents = {"frontend": fe, "backend": be, "llmapi": llm, "tests": tst}

        decisions: List[DecisionSummary] = []
        plan_graph = PlanGraph()
        if self.mode == "weave":
            plan_graph.mode_by_segment = {"critical": "ladder", "docs": "tracks"}
        else:
            plan_graph.mode_by_segment = {"main": self.mode}
        runner = ContractRunner(self.run_dir, self.logger)
        gates: List[StageGate] = [
            StageGate(
                id="sg_api_contract",
                name="API contract passes",
                conditions=["tests.pass('api_contract') and tests.pass('api_consistency')"],
            ),
            StageGate(
                id="sg_be_scaffold",
                name="Backend scaffold present",
                conditions=["tests.pass('api_contract') and artifact.exists('backend/**')"],
            ),
            StageGate(
                id="sg_fe_scaffold",
                name="Frontend scaffold present",
                conditions=["artifact.exists('frontend/**')"],
            ),
            StageGate(
                id="sg_smoke",
                name="Smoke tests pass",
                conditions=["tests.pass('smoke_suite') and tests.pass('fastapi_app')"],
            ),
        ]
        evaluator = GateEvaluator(self.run_dir, self.artifacts, self.logger)

        plan_snapshots: List[Dict[str, Any]] = []

        try:
            plan_init = rllm.plan_init(goal)
            if plan_init and isinstance(plan_init.get("text"), str):
                self.artifacts.add_text(os.path.join("plans", "router_plan.txt"), plan_init["text"], tags=["plan", "router"])
        except Exception:
            pass

        if self.mode == "ladder":
            active = [be, llm, tst]
            plans = [a.plan("contracts", {"goal": goal, "decisions": decisions}) for a in active]
            self.logger.log("router_plans", mode=self.mode, step="contracts", plans=[asdict(p) for p in plans])
            for a in active:
                refs = a.act({"goal": goal, "decisions": decisions})
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            if any(a.needs_huddle({"goal": goal, "decisions": decisions}) for a in active):
                hud = self._execute_huddle(
                    topic=self._huddle_topic(goal),
                    questions=["Resource fields?", "Endpoints & DTOs?", "Error model?"],
                    proposed_contract=None,
                    transcript=transcript,
                    agents=agents,
                    decisions_so_far=decisions,
                )
                decisions = hud.get("decisions", [])
                transcript.add_decision_injection(decision_injection_text(decisions))

            results = runner.scan_and_run()
            gate_results = evaluator.evaluate([g for g in gates if g.id == 'sg_api_contract'])
            retries = 0
            while not all(g.status == "passed" for g in gate_results) and retries < 3:
                self.logger.log("router_block", step="contracts", reason="gate_failed", gates=[asdict(g) for g in gate_results])
                try:
                    rllm.refine_step(json.dumps({
                        "step": "contracts",
                        "tests": [asdict(r) for r in results],
                        "gates": [asdict(g) for g in gate_results],
                    }))
                except Exception:
                    pass
                results = runner.scan_and_run()
                gate_results = evaluator.evaluate([g for g in gates if g.id == 'sg_api_contract'])
                retries += 1

            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "contracts",
                    "gates": [asdict(g) for g in gate_results],
                    "tests": [asdict(r) for r in results],
                }
            )

            active = [be, llm]
            plans = [a.plan("backend_scaffold", {"goal": goal, "decisions": decisions}) for a in active]
            self.logger.log("router_plans", mode=self.mode, step="backend_scaffold", plans=[asdict(p) for p in plans])
            for a in active:
                refs = a.act({"goal": goal, "decisions": decisions, "phase": "backend_scaffold"})
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            results2 = runner.scan_and_run()
            gate_results2 = evaluator.evaluate([g for g in gates if g.id in ('sg_api_contract','sg_be_scaffold')])
            retries = 0
            while not all(g.status == "passed" for g in gate_results2) and retries < 3:
                self.logger.log("router_block", step="backend_scaffold", reason="gate_failed", gates=[asdict(g) for g in gate_results2])
                results2 = runner.scan_and_run()
                gate_results2 = evaluator.evaluate([g for g in gates if g.id in ('sg_api_contract','sg_be_scaffold')])
                retries += 1
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "backend_scaffold",
                    "gates": [asdict(g) for g in gate_results2],
                    "tests": [asdict(r) for r in (results + results2)],
                }
            )

            active = [fe]
            plans = [a.plan("frontend_scaffold", {"goal": goal, "decisions": decisions}) for a in active]
            self.logger.log("router_plans", mode=self.mode, step="frontend_scaffold", plans=[asdict(p) for p in plans])
            for a in active:
                refs = a.act({"goal": goal, "decisions": decisions, "phase": "frontend_scaffold"})
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            results3 = runner.scan_and_run()
            gate_results3 = evaluator.evaluate([g for g in gates if g.id in ('sg_fe_scaffold','sg_be_scaffold','sg_api_contract')])
            retries = 0
            while not all(g.status == "passed" for g in gate_results3) and retries < 3:
                self.logger.log("router_block", step="frontend_scaffold", reason="gate_failed", gates=[asdict(g) for g in gate_results3])
                results3 = runner.scan_and_run()
                gate_results3 = evaluator.evaluate([g for g in gates if g.id in ('sg_fe_scaffold','sg_be_scaffold','sg_api_contract')])
                retries += 1
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "frontend_scaffold",
                    "gates": [asdict(g) for g in gate_results3],
                    "tests": [asdict(r) for r in (results + results2 + results3)],
                }
            )

            active = [tst]
            plans = [a.plan("smoke_tests", {"goal": goal, "decisions": decisions}) for a in active]
            self.logger.log("router_plans", mode=self.mode, step="smoke_tests", plans=[asdict(p) for p in plans])
            for a in active:
                refs = a.act({"goal": goal, "decisions": decisions, "phase": "smoke_tests"})
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            results4 = runner.scan_and_run()
            gate_results4 = evaluator.evaluate([g for g in gates if g.id in ('sg_smoke','sg_fe_scaffold','sg_be_scaffold','sg_api_contract')])
            retries = 0
            while not all(g.status == "passed" for g in gate_results4) and retries < 3:
                self.logger.log("router_block", step="smoke_tests", reason="gate_failed", gates=[asdict(g) for g in gate_results4])
                results4 = runner.scan_and_run()
                gate_results4 = evaluator.evaluate([g for g in gates if g.id in ('sg_smoke','sg_fe_scaffold','sg_be_scaffold','sg_api_contract')])
                retries += 1
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "smoke_tests",
                    "gates": [asdict(g) for g in gate_results4],
                    "tests": [asdict(r) for r in (results + results2 + results3 + results4)],
                }
            )

        elif self.mode == "tracks":
            slice_active = [fe, be, llm, tst]
            plans = [a.plan("tracks", {"goal": goal, "decisions": decisions}) for a in slice_active]
            self.logger.log("router_plans", mode=self.mode, step="slice-1", plans=[asdict(p) for p in plans])
            for a in slice_active:
                refs = a.act({"goal": goal, "decisions": decisions})
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            results = runner.scan_and_run()
            if any(a.needs_huddle({"goal": goal, "decisions": decisions}) for a in slice_active):
                hud = self._execute_huddle(
                    topic=self._huddle_topic(goal),
                    questions=["Resource fields?", "Endpoints & DTOs?", "Error model?"],
                    proposed_contract=None,
                    transcript=transcript,
                    agents=agents,
                    decisions_so_far=decisions,
                )
                decisions = hud.get("decisions", [])
                transcript.add_decision_injection(decision_injection_text(decisions))
            gate_results = evaluator.evaluate(gates)
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "sync-1",
                    "gates": [asdict(g) for g in gate_results],
                    "tests": [asdict(r) for r in results],
                }
            )
        else:
            plan_graph.add_node(PlanNode(id="n_contracts", name="API contracts", modeSegment="critical"))
            plan_graph.add_node(PlanNode(id="n_backend", name="Backend scaffold", modeSegment="critical"))
            plan_graph.add_node(PlanNode(id="n_smoke", name="Smoke tests", modeSegment="critical"))
            plan_graph.add_edge("n_contracts", "n_backend")
            plan_graph.add_edge("n_backend", "n_smoke")

            plan_graph.add_node(PlanNode(id="n_docs", name="Docs/README", modeSegment="docs"))

            active_crit = [be, llm, tst]
            plans = [a.plan("contracts", {"goal": goal, "decisions": decisions}) for a in active_crit]
            self.logger.log("router_plans", mode=self.mode, step="contracts", plans=[asdict(p) for p in plans])
            for a in active_crit:
                refs = a.act({"goal": goal, "decisions": decisions})
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            try:
                doc_out = llm._model([
                    {"role": "system", "content": "You are the Docs agent. Write a concise README for the generated CLI app."},
                    {"role": "user", "content": f"Goal: {goal}\n\nWrite a minimal README with: Overview, Quickstart, Commands, and Notes."},
                ])
                readme_art = self.artifacts.add_text("README.md", doc_out, tags=["docs", "readme"], meta={"segment": "docs"})
                plan_graph.nodes[-1].evidence.append({"type": "artifact", "id": readme_art.path, "hash": f"sha256:{readme_art.sha256}"})
                self.logger.log("agent_turn", agent="docs", artifacts=[readme_art.path])
            except Exception:
                pass

            results = runner.scan_and_run()
            if any(a.needs_huddle({"goal": goal, "decisions": decisions}) for a in active_crit):
                hud = self._execute_huddle(
                    topic=self._huddle_topic(goal),
                    questions=["Resource fields?", "Endpoints & DTOs?", "Error model?"],
                    proposed_contract=None,
                    transcript=transcript,
                    agents=agents,
                    decisions_so_far=decisions,
                )
                decisions = hud.get("decisions", [])
                transcript.add_decision_injection(decision_injection_text(decisions))
            gate_results = evaluator.evaluate([g for g in gates if g.id == 'sg_api_contract'])

            try:
                sim_rel = os.path.join(self.run_dir, "artifacts", "knowledge", "sim_update.json")
                try:
                    openapi_rel = os.path.join("artifacts", "contracts", "openapi.yaml")
                    if os.path.exists(os.path.join(self.run_dir, openapi_rel)) and (not os.path.exists(sim_rel)):
                        with open(sim_rel, "w", encoding="utf-8") as f:
                            json.dump({
                                "source": "artifact",
                                "refs": [{"type": "artifact", "id": openapi_rel, "hash": f"sha256:{__import__('hashlib').sha256(open(os.path.join(self.run_dir, openapi_rel),'rb').read()).hexdigest()}"}],
                            }, f, indent=2)
                except Exception:
                    pass
                new_events = kbus.ingest_local_dropins()
                if new_events:
                    plan_graph.add_reason("knowledge_update", f"{len(new_events)} new knowledge signal(s)")
                    self.logger.log("plan_switch", from_mode=self.mode, to_mode="weave", reason_type="knowledge_update", details=f"{len(new_events)} knowledge events", decisions=[d.id for d in decisions])
                    hud = self._execute_huddle(
                        topic="Replan due to knowledge update",
                        questions=["Do we need to adjust contracts or scaffolds?", "Any new risks from the evidence?"],
                        proposed_contract=None,
                        transcript=transcript,
                        agents=agents,
                        decisions_so_far=decisions,
                    )
                    new_ds = hud.get("decisions", [])
                    refs = []
                    try:
                        for ev in new_events:
                            refs.extend(ev.refs)
                    except Exception:
                        pass
                    for dsum in new_ds:
                        if not getattr(dsum, "sources", None):
                            try:
                                dsum.sources = refs[:]
                            except Exception:
                                pass
                    decisions = decisions + new_ds
                    transcript.add_decision_injection(decision_injection_text(decisions))
            except Exception:
                pass

            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "contracts/weave_docs",
                    "gates": [asdict(g) for g in gate_results],
                    "tests": [asdict(r) for r in results],
                }
            )

            active = [be, llm]
            plans2 = [a.plan("backend_scaffold", {"goal": goal, "decisions": decisions}) for a in active]
            self.logger.log("router_plans", mode=self.mode, step="backend_scaffold", plans=[asdict(p) for p in plans2])
            for a in active:
                refs = a.act({"goal": goal, "decisions": decisions, "phase": "backend_scaffold"})
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            results2 = runner.scan_and_run()
            gate_results2 = evaluator.evaluate([g for g in gates if g.id in ('sg_api_contract','sg_be_scaffold')])
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "backend_scaffold",
                    "gates": [asdict(g) for g in gate_results2],
                    "tests": [asdict(r) for r in (results + results2)],
                }
            )

            active_fe = [fe]
            plans3 = [a.plan("frontend_scaffold", {"goal": goal, "decisions": decisions}) for a in active_fe]
            self.logger.log("router_plans", mode=self.mode, step="frontend_scaffold", plans=[asdict(p) for p in plans3])
            for a in active_fe:
                refs = a.act({"goal": goal, "decisions": decisions, "phase": "frontend_scaffold"})
                self.logger.log("agent_turn", agent=a.name, artifacts=[r.path for r in refs])
            results3 = runner.scan_and_run()
            gate_results3 = evaluator.evaluate([g for g in gates if g.id in ('sg_fe_scaffold',)])

            results4 = runner.scan_and_run()
            gate_results4 = evaluator.evaluate([g for g in gates if g.id in ('sg_smoke','sg_fe_scaffold','sg_be_scaffold','sg_api_contract')])
            plan_snapshots.append(
                {
                    "mode": self.mode,
                    "step": "smoke_tests",
                    "gates": [asdict(g) for g in gate_results4],
                    "tests": [asdict(r) for r in (results + results2 + results3 + results4)],
                }
            )

        try:
            plan_graph.save(self.run_dir)
        except Exception:
            pass

        try:
            self.artifacts.add_text(os.path.join("plans", "snapshot.json"), json.dumps(plan_snapshots, indent=2), tags=["plan", "snapshot"])
        except Exception:
            pass

        try:
            pre_results = runner.scan_and_run()
            pre_gate_results = evaluator.evaluate(gates)
            self.logger.log("pre_finalization_validation", tests=[asdict(r) for r in pre_results], gates=[asdict(g) for g in pre_gate_results])
        except Exception as e:
            self.logger.log("pre_finalization_error", error=str(e))

        final_report = run_finalization(self.run_dir, self.artifacts, self.logger, decisions, evaluator)

        summary = self._build_summary(agents, evaluator, decisions)
        summary["finalization_report"] = os.path.join("artifacts", "finalization", "report.json")
        self.artifacts.add_text("run_summary.json", json.dumps(summary, indent=2), tags=["summary"])
        self.logger.log("run_complete", summary_path=os.path.join(self.run_dir, "artifacts", "run_summary.json"))

        return {
            "artifact_dir": os.path.join(self.run_dir, "artifacts"),
            "log_path": self.logger.path(),
            "run_id": self.run_id,
            "summary_path": os.path.join(self.run_dir, "artifacts", "run_summary.json"),
        }

    def _build_tools_manifest(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        def fn(name: str, desc: str, params: Dict[str, Any]) -> Dict[str, Any]:
            return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}

        tools.append(fn(
            "set_mode",
            "Select Router execution mode (ladder|tracks|weave) with rationale.",
            {
                "type": "object",
                "properties": {
                    "target_mode": {"type": "string", "enum": ["ladder", "tracks", "weave"]},
                    "reason": {"type": "string"},
                },
                "required": ["target_mode", "reason"],
            },
        ))
        tools.append(fn(
            "open_huddle",
            "Open a huddle to align interfaces/contracts and record a transcript start.",
            {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "agenda": {"type": "string"},
                },
                "required": ["topic"],
            },
        ))
        tools.append(fn(
            "record_decision_summary",
            "Persist a DecisionSummary JSON and update decision log.",
            {
                "type": "object",
                "properties": {
                    "huddle_id": {"type": ["string", "null"]},
                    "topic": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "decision": {"type": ["string", "null"]},
                    "rationale": {"type": ["string", "null"]},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "owner": {"type": "string"},
                                "task": {"type": "string"},
                                "due": {"type": ["string", "null"]},
                            },
                            "required": ["owner", "task"],
                        },
                    },
                    "contracts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "schema_hash": {"type": "string"},
                            },
                            "required": ["name", "schema_hash"],
                        },
                    },
                    "sources": {"type": "array", "items": {"type": "object"}},
                    "links": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["topic", "options"],
            },
        ))
        tools.append(fn(
            "inject_summary",
            "Inject a compact DecisionSummary snippet into target contexts (router or agents).",
            {
                "type": "object",
                "properties": {
                    "decision_id": {"type": "string"},
                    "targets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["decision_id", "targets"],
            },
        ))
        tools.append(fn(
            "spawn_agents",
            "Create missing agent instances if not already active.",
            {
                "type": "object",
                "properties": {
                    "roles": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["frontend", "backend", "llmapi", "tests"]},
                    },
                    "reason": {"type": "string"},
                },
                "required": ["roles", "reason"],
            },
        ))
        tools.append(fn(
            "schedule_slice",
            "Run one concurrent slice across selected agents (plan/act/report) and persist outputs.",
            {
                "type": "object",
                "properties": {
                    "active_agents": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": ["string", "null"]},
                },
                "required": ["active_agents"],
            },
        ))
        tools.append(fn(
            "rag_search",
            "Query the run-scoped vector index for relevant artifacts/transcripts.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query", "top_k"],
            },
        ))
        tools.append({
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Perform a web search via Groq browser_search or a local adapter.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                        "time_range": {"type": ["string", "null"], "enum": ["d", "w", "m", "y", None]},
                        "engines": {"type": ["string", "null"]},
                        "language": {"type": ["string", "null"]},
                        "pageno": {"type": ["integer", "null"], "minimum": 1},
                    },
                    "required": ["query", "top_k"],
                },
            },
        })
        tools.append(fn(
            "run_contract_tests",
            "Run selected contract tests by id and return structured results.",
            {
                "type": "object",
                "properties": {
                    "tests": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["tests"],
            },
        ))
        tools.append(fn(
            "propose_advance_step",
            "Request advancement to the next step; Router enforces stage gates.",
            {
                "type": "object",
                "properties": {
                    "step_id": {"type": "string"},
                    "note": {"type": ["string", "null"]},
                },
                "required": ["step_id"],
            },
        ))
        tools.append(fn(
            "write_artifact",
            "Write an artifact under artifacts/ and index it.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "mime": {"type": ["string", "null"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["path", "content"],
            },
        ))
        tools.append(fn(
            "read_artifact",
            "Read an artifact content by path under artifacts/.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ))
        tools.append(fn(
            "finalize_run",
            "Finalize the run: run tests/linters, consolidate citations, create deliverables.",
            {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        ))
        return tools

    def _router_system_prompt(self) -> str:
        return (
            "You are the Router LLM: you decide modes (ladder|tracks|weave), open huddles, write DecisionSummaries, "
            "spawn/schedule agents, run tests, and finalize. You must act ONLY via tools — do not claim to edit files or advance steps without tools. "
            "Keep contexts lean: inject only DecisionSummary snippets; use rag_search/web_search for details. "
            "When decisions depend on artifacts or retrieval, include EvidenceRef sources in record_decision_summary.sources. "
            "Ask for a huddle when interfaces/ownership are ambiguous. "
            "NEVER bypass stage gates: request propose_advance_step and accept failures to replan or huddle. "
            "End by calling finalize_run with a concise run summary and pointers to key artifacts."
        )

    def _snapshot_state(self, plan_graph: PlanGraph, evaluator: GateEvaluator, decisions: List[DecisionSummary], unread_huddles: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            evaluator.load_test_results()
        except Exception:
            pass
        tool_manifest = [ (t.get("function",{}) or {}).get("name") for t in tools ]
        return {
            "plan_graph": plan_graph.snapshot(),
            "mode": self.mode,
            "latest_tests": evaluator.latest_tests,
            "active_gates": [
                {"id": "sg_api_contract", "name": "API contract passes"},
                {"id": "sg_be_scaffold", "name": "Backend scaffold present"},
                {"id": "sg_fe_scaffold", "name": "Frontend scaffold present"},
                {"id": "sg_smoke", "name": "Smoke tests pass"},
            ],
            "unread_huddles": unread_huddles,
            "recent_decisions": [asdict(d) for d in decisions[-5:]],
            "tools": tool_manifest,
        }

    def _web_search_exec(self, query: str, top_k: int, time_range: Optional[str], engines: Optional[str], language: Optional[str], pageno: Optional[int]) -> Dict[str, Any]:
        assert self.cfg is not None
        if self._web_disabled_by_flag:
            self.logger.log(
                "web_search_unavailable",
                provider="lmstudio",
                router_mode="local",
                config={"adapter_enabled": False, "mcp": False},
                reason="disabled_by_flag",
            )
            self.logger.log(
                "web_search",
                source="unavailable",
                query=query,
                params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
                results_count=0,
                urls_fetched=0,
                latency_ms=None,
                error="disabled_by_flag",
            )
            return {"error": "tool_unavailable", "reason": "web_search disabled (disabled_by_flag)"}
        if not getattr(self.cfg, "web_search_enabled", False):
            self.logger.log(
                "web_search_unavailable",
                provider="lmstudio",
                router_mode="local",
                config={"adapter_enabled": False, "mcp": False},
                reason="disabled_by_config",
            )
            self.logger.log(
                "web_search",
                source="unavailable",
                query=query,
                params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
                results_count=0,
                urls_fetched=0,
                latency_ms=None,
                error="tool_unavailable",
            )
            return {"error": "tool_unavailable", "reason": "disabled_by_config"}


        from datetime import datetime, timezone
        def _now_iso() -> str:
            return datetime.now(timezone.utc).isoformat()


        def _map_time_range(tr: Optional[str]) -> Optional[str]:
            if not tr:
                return None
            m = {"d": "day", "w": "week", "m": "month", "y": "year"}
            return m.get(tr, None)


        try:
            router_primary = (self.cfg.router_provider_order[0] if self.cfg.router_provider_order else None) or None
        except Exception:
            router_primary = None
        router_model = (self.cfg.router_model_default or "").strip()
        groq_eligible_models = {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
        wants_groq = (router_primary == "groq") and (router_model in groq_eligible_models)


        adapter_cfg = getattr(self.cfg, "websearch_adapter", None) or {}
        adapter_enabled = bool(adapter_cfg.get("enabled")) and bool(adapter_cfg.get("search_base_url"))


        if wants_groq:
            import time as _t
            t0 = _t.time()
            try:
                rllm = RouterLLM(self.cfg, self.logger, tools=self._build_tools_manifest())
                sys = (
                    "You are a web research assistant. Use the browser_search tool to find relevant sources,"
                    " then return STRICT JSON with keys: query, source, results[], extracts[]."
                    " Shape: {\"query\":str, \"source\":\"groq\", \"results\":[{\"title\":str,\"url\":str,\"snippet\":str,\"engine\":str,\"time?\":str}], \"extracts\":[{\"url\":str,\"content_md\":str,\"status\":200,\"fetched_at\":str}]}."
                    " Only output JSON."
                )
                msg = (
                    f"Query: {query}\nTopK: {top_k}\nTimeRange: {time_range or '-'}\n"
                    f"Engines: {engines or '-'}\nLanguage: {language or '-'}\nPageNo: {pageno or 1}"
                )
                messages = [{"role": "system", "content": sys}, {"role": "user", "content": msg}]
                raw_obj = rllm._call_with_tools(messages, tools=[{"type": "browser_search"}], phase="web_search", tool_choice="required")
                dt_ms = int((_t.time() - t0) * 1000)
                text = (raw_obj.get("text") or "").strip()
                obs: Dict[str, Any]
                try:
                    obs = json.loads(text)

                    obs["query"] = query
                    obs["source"] = "groq"
                    obs.setdefault("results", [])
                    obs.setdefault("extracts", [])
                except Exception:

                    obs = {
                        "query": query,
                        "source": "groq",
                        "results": [],
                        "extracts": [
                            {"url": "", "content_md": text, "status": 200, "fetched_at": _now_iso()}
                        ],
                    }
                obs["note"] = "source: groq"
                self.logger.log(
                    "web_search",
                    source="groq",
                    query=query,
                    params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
                    results_count=len(obs.get("results", [])),
                    urls_fetched=len(obs.get("extracts", [])),
                    latency_ms={"llm_call_ms": dt_ms},
                )
                return obs
            except Exception as e:

                self.logger.log("web_search_error", source="groq", query=query, error=str(e))
                if adapter_enabled:
                    self.logger.log("provider_switch", from_provider="groq", to_provider="adapter", reason="groq_search_failed")
                else:
                    self.logger.log(
                        "web_search",
                        source="unavailable",
                        query=query,
                        params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
                        results_count=0,
                        urls_fetched=0,
                        latency_ms=None,
                        error=str(e),
                    )
                    return {"error": "tool_unavailable", "reason": f"web_search error: {e}"}


        if not adapter_enabled:
            self.logger.log(
                "web_search_unavailable",
                provider="lmstudio",
                router_mode="local",
                config={"adapter_enabled": False, "mcp": False},
            )
            self.logger.log(
                "web_search",
                source="unavailable",
                query=query,
                params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
                results_count=0,
                urls_fetched=0,
                latency_ms=None,
                error="adapter_not_enabled",
            )
            return {"error": "tool_unavailable", "reason": "adapter_not_enabled"}

        import time as _t
        import hashlib
        import requests
        t_search = _t.time()
        search_base = (adapter_cfg.get("search_base_url") or "").rstrip("/")
        searx_params = {
            "format": "json",
            "q": query,
            "language": (language or adapter_cfg.get("language") or "en"),
        }
        if engines:
            searx_params["engines"] = engines
        elif adapter_cfg.get("default_engines"):
            searx_params["engines"] = adapter_cfg.get("default_engines")
        tr_full = _map_time_range(time_range) or adapter_cfg.get("time_range") or None
        if tr_full:
            searx_params["time_range"] = tr_full
        if pageno and pageno >= 1:
            searx_params["pageno"] = int(pageno)
        try:
            resp = requests.get(f"{search_base}/search", params=searx_params, timeout=30)
            data = resp.json() if resp.ok else {"results": []}
        except Exception:
            data = {"results": []}
        search_ms = int((_t.time() - t_search) * 1000)
        raw_results = data.get("results", []) or []

        results: List[Dict[str, Any]] = []
        for r in raw_results[: max(1, min(int(top_k or 5), 10))]:
            try:
                results.append(
                    {
                        "title": r.get("title") or "",
                        "url": r.get("url") or r.get("link") or "",
                        "snippet": (r.get("content") or r.get("summary") or "")[:500],
                        "engine": r.get("engine") or r.get("source") or "",
                        "time?": r.get("publishedDate") or r.get("published_time") or None,
                    }
                )
            except Exception:
                continue


        fetch_type = (adapter_cfg.get("fetch_type") or "trafilatura").lower()
        deny = set([d.lower() for d in (adapter_cfg.get("denylist_domains") or [])])
        max_k = int(adapter_cfg.get("k") or 5)
        urls = []
        for it in results:
            u = (it.get("url") or "").strip()
            if not u:
                continue
            try:
                host = u.split("//", 1)[-1].split("/", 1)[0].lower()
            except Exception:
                host = ""
            if host and any(host.endswith(d) or host == d for d in deny):
                continue
            urls.append(u)
            if len(urls) >= max_k:
                break

        cache_dir = adapter_cfg.get("cache_dir") or os.path.join(self.run_dir, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        extracts: List[Dict[str, Any]] = []
        t_fetch_total = _t.time()
        for u in urls:
            url_hash = hashlib.sha256(u.encode("utf-8")).hexdigest()
            cache_path = os.path.join(cache_dir, f"{url_hash}.md")
            cached = False
            content_md = ""
            status = 0
            start = _t.time()
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8", errors="ignore") as f:
                        content_md = f.read()
                    cached = True
                    status = 200
                except Exception:
                    cached = False
            if not cached:
                if fetch_type == "firecrawl" and adapter_cfg.get("firecrawl_base_url"):
                    fc_base = adapter_cfg.get("firecrawl_base_url").rstrip("/")
                    url_fc = f"{fc_base}/scrape" if fc_base.endswith("/v1") else f"{fc_base}/v1/scrape"
                    headers = {"Content-Type": "application/json"}
                    if adapter_cfg.get("firecrawl_api_key"):
                        headers["Authorization"] = f"Bearer {adapter_cfg['firecrawl_api_key']}"
                    try:
                        r = requests.post(url_fc, headers=headers, json={"url": u, "formats": ["markdown", "html"]}, timeout=45)
                        jd = r.json() if r.ok else {}

                        content_md = jd.get("markdown") or (jd.get("data", {}) or {}).get("markdown") or ""
                        status = r.status_code
                    except Exception:
                        content_md = ""
                        status = 502
                else:
                    try:
                        import trafilatura
                        downloaded = trafilatura.fetch_url(u)
                        if downloaded is None:
                            status = 404
                            content_md = ""
                        else:
                            extracted = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
                            content_md = extracted or ""
                            status = 200 if content_md else 204
                    except Exception:
                        content_md = ""
                        status = 503

                try:
                    if content_md:
                        with open(cache_path, "w", encoding="utf-8") as f:
                            f.write(content_md)
                except Exception:
                    pass
            dur_ms = int((_t.time() - start) * 1000)
            self.logger.log(
                "adapter_fetch",
                url=u,
                fetcher=("firecrawl" if (fetch_type == "firecrawl" and adapter_cfg.get("firecrawl_base_url")) else "trafilatura"),
                status=status,
                bytes=len(content_md.encode("utf-8")) if content_md else 0,
                cached=cached,
                duration_ms=dur_ms,
            )
            extracts.append({"url": u, "content_md": content_md, "status": status, "fetched_at": _now_iso()})
        fetch_ms = int((_t.time() - t_fetch_total) * 1000)

        obs = {
            "query": query,
            "source": "adapter",
            "results": results,
            "extracts": extracts,
            "note": "source: adapter",
        }
        self.logger.log(
            "web_search",
            source="adapter",
            query=query,
            params={"top_k": top_k, "time_range": time_range, "engines": engines, "language": language, "pageno": pageno or 1},
            results_count=len(results),
            urls_fetched=len(extracts),
            latency_ms={"searxng_ms": search_ms, "fetch_total_ms": fetch_ms},
        )
        return obs

    def _run_agentic(self, goal: str) -> Dict[str, Any]:
        try:
            from .worker import WorkerRunner
            wr = WorkerRunner(self.cwd, self.run_id)
            wr._pre_ingest_repo_files()
        except Exception:
            pass

        transcript = RunningTranscript(self.run_id)
        kbus = KnowledgeBus(self.run_dir, self.logger)
        rllm = RouterLLM(self.cfg, self.logger, tools=self._build_tools_manifest())

        plan_graph = PlanGraph()
        plan_graph.mode_by_segment = {"main": self.mode}
        gates: List[StageGate] = [
            StageGate(id="sg_api_contract", name="API contract passes", conditions=["tests.pass('api_contract')"]),
            StageGate(id="sg_be_scaffold", name="Backend scaffold present", conditions=["tests.pass('api_contract') and artifact.exists('backend/**')"]),
            StageGate(id="sg_fe_scaffold", name="Frontend scaffold present", conditions=["artifact.exists('frontend/**')"]),
            StageGate(id="sg_smoke", name="Smoke tests pass", conditions=["tests.pass('smoke_suite')"]),
        ]
        evaluator = GateEvaluator(self.run_dir, self.artifacts, self.logger)
        runner = ContractRunner(self.run_dir, self.logger)

        agents: Dict[str, Any] = {}
        def ensure_agent(role: str):
            if role in agents:
                return agents[role]
            if role == "frontend":
                agents[role] = FrontendAgent("frontend", self.cfg, self.logger, self.artifacts, self.rag)
            elif role == "backend":
                agents[role] = BackendAgent("backend", self.cfg, self.logger, self.artifacts, self.rag)
            elif role == "llmapi":
                agents[role] = LLMApiAgent("llmapi", self.cfg, self.logger, self.artifacts, self.rag)
            elif role == "tests":
                agents[role] = TestAgent("tests", self.cfg, self.logger, self.artifacts, self.rag)
            return agents.get(role)

        decisions: List[DecisionSummary] = []
        injected_by_target: Dict[str, List[str]] = {}
        unread_huddles: List[Dict[str, Any]] = []
        current_step: str = "contracts"

        tools = self._build_tools_manifest()

        system_msg = {"role": "system", "content": self._router_system_prompt()}
        init_state = self._snapshot_state(plan_graph, evaluator, decisions, unread_huddles, tools)
        user_msg = {
            "role": "user",
            "content": (
                "Goal: " + (goal or "") + "\n\n" +
                "State: " + json.dumps(init_state, ensure_ascii=False)
            )
        }
        messages: List[Dict[str, Any]] = [system_msg, user_msg]

        finalized = False
        max_steps = getattr(self.cfg, "router_max_steps", 32) or 32
        step_idx = 0
        no_tool_streak = 0

        while step_idx < max_steps and not finalized:
            step_idx += 1
            out = rllm._call_with_tools(messages, tools=tools, phase="agentic", tool_choice="auto")
            raw = out.get("raw") or {}
            msg_obj = None
            try:
                msg_obj = raw.get("choices")[0]["message"]
            except Exception:
                msg_obj = None
            tool_calls = []
            if msg_obj:
                try:
                    tool_calls = msg_obj.get("tool_calls") or []
                except Exception:
                    tool_calls = []
            transcript.add_model_call(
                title=f"Router Agentic Turn #{step_idx}",
                provider=out.get("provider") or "?",
                model=out.get("model") or "?",
                messages=messages[-6:],
                output=out.get("text"),
                tools_offered=tools,
                tool_choice="auto",
                tool_calls=tool_calls,
            )

            if not tool_calls:
                no_tool_streak += 1
                if no_tool_streak >= 2:
                    messages.append({"role": "system", "content": "Reminder: You must act via tools. Pick exactly one tool now."})
                if msg_obj:
                    messages.append({"role": "assistant", "content": msg_obj.get("content")})
                continue
            no_tool_streak = 0

            tc = tool_calls[0]
            tool_name = (tc or {}).get("function", {}).get("name") or ""
            tool_args_s = (tc or {}).get("function", {}).get("arguments") or "{}"
            try:
                tool_args = json.loads(tool_args_s)
            except Exception:
                tool_args = {}
            messages.append({"role": "assistant", "content": None, "tool_calls": [tc]})

            obs: Dict[str, Any] = {}
            err: Optional[str] = None
            try:
                if tool_name == "set_mode":
                    target = (tool_args.get("target_mode") or "").strip().lower()
                    reason = tool_args.get("reason") or ""
                    applied = target in ("ladder", "tracks", "weave")
                    if applied:
                        prev = self.mode
                        self.mode = target
                        plan_graph.mode_by_segment = ({"critical": "ladder", "docs": "tracks"} if self.mode == "weave" else {"main": self.mode})
                        self.logger.log("mode_decision", previous=prev, current=self.mode, reason=reason)
                        obs = {"applied": True, "current_mode": self.mode, "note": "mode updated"}
                    else:
                        obs = {"applied": False, "current_mode": self.mode, "note": "invalid target_mode"}
                elif tool_name == "open_huddle":
                    topic = tool_args.get("topic") or self._huddle_topic(goal)
                    raw_att = tool_args.get("attendees") or []
                    agenda = tool_args.get("agenda") or ""
                    norm_att: List[str] = ["router"]
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
                    if len(unread_huddles) >= self._max_open_huddles:
                        obs = {"error": "huddle_limit_reached", "note": f"max_open_huddles={self._max_open_huddles}"}
                        self.logger.log("huddle_limit", max_open=self._max_open_huddles, topic=topic, attendees=norm_att)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": (tc.get("id") if isinstance(tc, dict) else None),
                            "name": tool_name,
                            "content": json.dumps(obs, ensure_ascii=False),
                        })
                        continue
                    hud_id = f"hud_{ulid()}"
                    self.logger.log("huddle_open", id=hud_id, topic=topic, requester="router", attendees=norm_att, agenda=agenda)
                    rec, t_rel, r_rel = save_huddle(
                        run_dir=self.run_dir, artifacts=self.artifacts, rag_index=self.rag,
                        requester="router", attendees=norm_att, topic=topic, questions=[agenda] if agenda else [],
                        notes="Huddle opened by Router LLM.", decisions=[], hud_id=hud_id, mode="dialog", auto_decision=False, messages=[],
                    )
                    unread_huddles.append({"huddle_id": hud_id, "topic": topic})
                    obs = {"huddle_id": hud_id}
                elif tool_name == "record_decision_summary":
                    d_obj = {
                        "topic": tool_args.get("topic") or "",
                        "options": tool_args.get("options") or [],
                        "decision": tool_args.get("decision"),
                        "rationale": tool_args.get("rationale"),
                        "risks": tool_args.get("risks") or [],
                        "actions": tool_args.get("actions") or [],
                        "contracts": tool_args.get("contracts") or [],
                        "links": tool_args.get("links") or [],
                        "sources": tool_args.get("sources") or None,
                    }
                    hud_id = tool_args.get("huddle_id")
                    if not hud_id and unread_huddles:
                        try:
                            last_hud = unread_huddles[-1]
                            if isinstance(last_hud, dict):
                                hud_id = (last_hud or {}).get("huddle_id")
                            else:
                                hud_id = None
                        except Exception:
                            hud_id = None
                    transcript_rel = None
                    if hud_id:
                        try:
                            rec_path = os.path.join(self.run_dir, "artifacts", "huddles", f"{hud_id}.json")
                            if os.path.exists(rec_path):
                                with open(rec_path, "r", encoding="utf-8") as f:
                                    rec_obj = json.load(f)
                                transcript_rel = rec_obj.get("transcript_path")
                            else:
                                rec_obj = None
                        except Exception:
                            rec_obj = None
                    ds = parse_decision_summaries(json.dumps(d_obj))[0]
                    try:
                        from .huddle import _normalize_sources  # type: ignore
                        base_sources = _normalize_sources(ds.sources)
                        externals = [s for s in base_sources if s.get("type") == "external"]
                        if len(externals) < 3 and self._web_recent:
                            picks: List[Dict[str, Any]] = []
                            seen = set([ ("external", s.get("url")) for s in externals if s.get("url") ])
                            for r in list(self._web_recent)[-5:]:
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
                                try:
                                    if not isinstance(ds.meta, dict):
                                        ds.meta = {}
                                except Exception:
                                    ds.meta = {}
                                ds.meta["auto_populated_sources"] = True
                    except Exception:
                        pass
                    if transcript_rel:
                        try:
                            ds.links = (ds.links or []) + [{"type": "artifact", "id": transcript_rel, "title": "Huddle Transcript"}]
                        except Exception:
                            pass
                        try:
                            base_sources = (ds.sources or [])
                            base_sources = list(base_sources) if isinstance(base_sources, list) else []
                            base_sources.append({"type": "artifact", "id": transcript_rel})
                            ds.sources = base_sources
                        except Exception:
                            pass
                    def _autopopulate_from_recent(ds_obj):
                        try:
                            if ds_obj.sources:
                                return ds_obj
                            recent = list(self._web_recent)[-3:]
                            if not recent:
                                return ds_obj
                            urls_ranked: List[Dict[str, Any]] = []
                            for r in recent:
                                obsr = r.get("obs") or {}
                                res = obsr.get("results") or []
                                exs = obsr.get("extracts") or []
                                ok_urls = set([e.get("url") for e in exs if isinstance(e, dict) and str(e.get("status")) == "200" and (e.get("content_md") or "")])
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
                            seen = set()
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
                                try:
                                    if not isinstance(ds_obj.meta, dict):
                                        ds_obj.meta = {}
                                except Exception:
                                    ds_obj.meta = {}
                                ds_obj.meta["auto_populated_sources"] = True
                        except Exception:
                            pass
                        return ds_obj

                    if not ds.sources:
                        ds = _autopopulate_from_recent(ds)

                    from .huddle import persist_decision_summary
                    ds, ds_rel = persist_decision_summary(self.run_dir, self.artifacts, self.rag, ds)
                    decisions.append(ds)
                    try:
                        self.logger.log("decision_summary_updated", ds_id=ds.id, fields_updated=["sources", "links", "meta"], path=os.path.join(self.run_dir, ds_rel))
                    except Exception:
                        pass
                    if hud_id:
                        try:
                            rec_path = os.path.join(self.run_dir, "artifacts", "huddles", f"{hud_id}.json")
                            if os.path.exists(rec_path):
                                with open(rec_path, "r", encoding="utf-8") as f:
                                    rec_obj = json.load(f)
                                decs = rec_obj.get("decisions") or []
                                if ds.id not in decs:
                                    decs.append(ds.id)
                                rec_obj["decisions"] = decs
                                with open(rec_path, "w", encoding="utf-8") as f:
                                    json.dump(rec_obj, f, indent=2)
                        except Exception:
                            pass
                    self.logger.log("decision_summary", decision_id=ds.id, topic=ds.topic, decision=ds.decision)
                    injected = ["router"]
                    try:
                        transcript.add_decision_injection(decision_injection_text([ds]))
                    except Exception:
                        pass
                    try:
                        for role in list(agents.keys()):
                            injected.append(f"agent:{role}")
                    except Exception:
                        pass
                    self.logger.log("decision_injected", decision_id=ds.id, targets=injected)
                    obs = {"decision_id": ds.id, "injected_into": injected}
                elif tool_name == "inject_summary":
                    ds_id = tool_args.get("decision_id") or ""
                    targets = tool_args.get("targets") or []
                    for t in targets:
                        injected_by_target.setdefault(t, [])
                        if ds_id not in injected_by_target[t]:
                            injected_by_target[t].append(ds_id)
                    try:
                        dd = next((d for d in decisions if getattr(d, "id", None) == ds_id), None)
                        if dd:
                            transcript.add_decision_injection(decision_injection_text([dd]))
                    except Exception:
                        pass
                    self.logger.log("decision_injected", decision_id=ds_id, targets=targets)
                    obs = {"targets_injected": list(targets)}
                elif tool_name == "spawn_agents":
                    roles = [str(r) for r in (tool_args.get("roles") or [])]
                    spawned: List[str] = []
                    already: List[str] = []
                    for r in roles:
                        a = ensure_agent(r)
                        if a is None:
                            continue
                        key = f"agent:{r}"
                        if key in spawned or key in already:
                            already.append(key)
                        else:
                            spawned.append(key)
                    self.logger.log("agents_spawned", spawned=spawned, already_active=already)
                    obs = {"spawned": spawned, "already_active": already}
                elif tool_name == "schedule_slice":
                    actives = [str(x) for x in (tool_args.get("active_agents") or [])]
                    artifacts_written: List[str] = []
                    reports: List[Dict[str, Any]] = []
                    errors: List[str] = []
                    auto_added: List[str] = []
                    skipped_agents: List[str] = []
                    ctx = {"goal": goal, "decisions": decisions}
                    try:
                        if "agent:tests" not in actives and ("tests" in agents or ensure_agent("tests") is not None):
                            actives.append("agent:tests")
                            auto_added.append("agent:tests")
                    except Exception:
                        pass
                    if len(actives) > self._max_slice_agents:
                        skipped_agents = actives[self._max_slice_agents:]
                        actives = actives[: self._max_slice_agents]
                        self.logger.log("slice_limit", max_agents=self._max_slice_agents, skipped=skipped_agents)
                    for an in actives:
                        role = an.replace("agent:", "").strip()
                        ag = ensure_agent(role)
                        if ag is None:
                            errors.append(f"unknown agent: {an}")
                            continue
                        try:
                            _ = ag.plan(current_step, ctx)
                        except Exception as e:
                            errors.append(f"plan error {an}: {e}")
                        try:
                            refs = ag.act(ctx)
                            artifacts_written.extend([r.path for r in refs])
                            self.logger.log("agent_turn", agent=ag.name, artifacts=[r.path for r in refs])
                        except Exception as e:
                            errors.append(f"act error {an}: {e}")
                        try:
                            rep = ag.report()
                            reports.append(asdict(rep))
                        except Exception:
                            pass
                        try:
                            if hasattr(ag, "needs_huddle") and ag.needs_huddle(ctx):
                                unread_huddles.append({"from": f"agent:{role}", "topic": self._huddle_topic(goal)})
                        except Exception:
                            pass
                    try:
                        results = runner.scan_and_run()
                        _ = evaluator.evaluate(gates)
                    except Exception:
                        results = []
                    obs = {"artifacts_written": artifacts_written, "reports": reports, "errors": errors, "auto_added": auto_added, "skipped_agents": skipped_agents}
                elif tool_name == "rag_search":
                    q = tool_args.get("query") or ""
                    k = int(tool_args.get("top_k") or 5)
                    hits = self.rag.search(q, top_k=k)
                    self.logger.log("rag_search", role="router", q=q, top_k=k, hits=[h.get("doc_id") for h in hits])
                    obs = {"hits": [
                        {
                            "doc_id": h.get("doc_id"),
                            "score": h.get("score"),
                            "snippet_or_path": (h.get("path") or h.get("snippet")),
                        } for h in hits
                    ]}
                elif tool_name == "web_search":
                    q = tool_args.get("query") or ""
                    k = int(tool_args.get("top_k") or 5)
                    tr = tool_args.get("time_range")
                    eng = tool_args.get("engines")
                    lang = tool_args.get("language")
                    pageno = tool_args.get("pageno")
                    obs = self._web_search_exec(q, k, tr, eng, lang, pageno)
                    hud_ctx = None
                    try:
                        if unread_huddles:
                            last_hud = unread_huddles[-1]
                            hud_ctx = (last_hud or {}).get("huddle_id")
                    except Exception:
                        hud_ctx = None
                    try:
                        entry = {"ts": __import__("time").time(), "huddle_id": hud_ctx, "query": q, "obs": obs}
                        self._web_recent.append(entry)
                        if len(self._web_recent) > 5:
                            self._web_recent = self._web_recent[-5:]
                    except Exception:
                        pass
                elif tool_name == "run_contract_tests":
                    tests = [str(t) for t in (tool_args.get("tests") or [])]
                    results = runner.scan_and_run()
                    created_tests = False
                    if not results:
                        try:
                            ta = ensure_agent("tests")
                            if ta is not None:
                                _ = ta.plan("contract_tests", {"goal": goal, "decisions": decisions})
                                _ = ta.act({"goal": goal, "decisions": decisions})
                                results = runner.scan_and_run()
                                created_tests = True
                        except Exception:
                            pass
                    if tests:
                        results = [r for r in results if r.id in tests]
                    obs = {"results": [asdict(r) for r in results], "created_tests": created_tests}
                elif tool_name == "propose_advance_step":
                    step_id_raw = tool_args.get("step_id") or current_step
                    gate_to_step = {
                        "sg_api_contract": "contracts",
                        "sg_be_scaffold": "backend_scaffold",
                        "sg_fe_scaffold": "frontend_scaffold",
                        "sg_smoke": "smoke_tests",
                    }
                    step_id = gate_to_step.get(step_id_raw, step_id_raw)
                    valid_steps = ["contracts", "backend_scaffold", "frontend_scaffold", "smoke_tests"]
                    if step_id not in valid_steps:
                        obs = {"advanced": False, "error": "unknown_step", "step_id": step_id_raw}
                        self.logger.log("advance_rejected", reason="unknown_step", requested=step_id_raw)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": (tc.get("id") if isinstance(tc, dict) else None),
                            "name": tool_name,
                            "content": json.dumps(obs, ensure_ascii=False),
                        })
                        continue
                    def gates_for(step: str) -> List[StageGate]:
                        if step == "contracts":
                            return [g for g in gates if g.id == 'sg_api_contract']
                        if step == "backend_scaffold":
                            return [g for g in gates if g.id in ('sg_api_contract','sg_be_scaffold')]
                        if step == "frontend_scaffold":
                            return [g for g in gates if g.id in ('sg_fe_scaffold',)]
                        if step == "smoke_tests":
                            return [g for g in gates if g.id in ('sg_smoke','sg_fe_scaffold','sg_be_scaffold','sg_api_contract')]
                        return gates
                    gres = evaluator.evaluate(gates_for(step_id))
                    if all(g.status == "passed" for g in gres):
                        order = ["contracts", "backend_scaffold", "frontend_scaffold", "smoke_tests"]
                        try:
                            idx = order.index(step_id)
                            next_step = order[min(idx + 1, len(order) - 1)]
                        except Exception:
                            next_step = step_id
                        current_step = next_step
                        if step_id in self._gate_failures:
                            self._gate_failures.pop(step_id, None)
                        obs = {"advanced": True, "next_step": next_step}
                    else:
                        import time as _t
                        now = _t.time()
                        cnt, last = self._gate_failures.get(step_id, (0, 0.0))
                        cnt = cnt + 1
                        self._gate_failures[step_id] = (cnt, now)
                        cooldown_active = (cnt >= self._cooldown_threshold) and ((now - last) <= 60.0)
                        retry_after_ms = int(self._cooldown_seconds * 1000) if cooldown_active else 0
                        if cooldown_active:
                            self.logger.log("router_cooldown", step=step_id, failures=cnt, retry_after_ms=retry_after_ms)
                        obs = {"advanced": False,
                               "failed_gates": [
                                   {"id": g.id, "status": g.status, "evidence": g.evidence} for g in gres if g.status != "passed"
                               ],
                               "cooldown_active": cooldown_active,
                               "retry_after_ms": retry_after_ms}
                elif tool_name == "write_artifact":
                    rel = tool_args.get("path") or ""
                    content = tool_args.get("content") or ""
                    tags = tool_args.get("tags") or []
                    if not rel.startswith("artifacts/"):
                        rel = os.path.join("artifacts", rel)
                    if not rel.startswith("artifacts/"):
                        raise ValueError("writes must be under artifacts/")
                    art = self.artifacts.add_text(rel.replace("artifacts/", ""), content, tags=tags, meta={"by": "router"})
                    try:
                        self.rag.ingest_text(art.id, content, art.path)
                        self.logger.log("rag_ingest_router", doc_id=art.id, path=art.path)
                    except Exception:
                        pass
                    obs = {"path": art.path, "hash": f"sha256:{art.sha256}", "size": len(content.encode('utf-8'))}
                elif tool_name == "read_artifact":
                    rel = tool_args.get("path") or ""
                    if not rel.startswith("artifacts/"):
                        rel = os.path.join("artifacts", rel)
                    abspath = os.path.join(self.run_dir, rel)
                    with open(abspath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read(200_000)
                    import hashlib
                    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    obs = {"path": rel, "content": content, "mime": "text/plain", "hash": f"sha256:{h}"}
                elif tool_name == "finalize_run":
                    report = run_finalization(self.run_dir, self.artifacts, self.logger, decisions, evaluator)
                    deliverables = report.get("deliverables", [None])[0]
                    obs = {"deliverables": deliverables, "report": os.path.join("artifacts", "finalization", "report.json")}
                    finalized = True
                else:
                    err = f"unknown tool: {tool_name}"
                    obs = {"error": err}
            except Exception as e:
                err = str(e)
                obs = {"error": err}
            self.logger.log("router_tool_call", tool_name=tool_name, params=tool_args, observation=(obs if len(str(obs)) < 5000 else {"note": "obs too large"}), error=err)
            messages.append({
                "role": "tool",
                "tool_call_id": (tc.get("id") if isinstance(tc, dict) else None),
                "name": tool_name,
                "content": json.dumps(obs, ensure_ascii=False),
            })

            if step_idx % 3 == 0 or finalized:
                snap = self._snapshot_state(plan_graph, evaluator, decisions, unread_huddles, tools)
                messages.append({"role": "system", "content": "State update: " + json.dumps(snap, ensure_ascii=False)})

        if not finalized:
            report = run_finalization(self.run_dir, self.artifacts, self.logger, decisions, evaluator)
            deliverables = report.get("deliverables", [None])[0]
            self.logger.log("router_finalize_auto", reason="budget_exhausted", steps=step_idx)

        try:
            plan_graph.save(self.run_dir)
        except Exception:
            pass

        try:
            snap_path = os.path.join(self.run_dir, "artifacts", "plans", "snapshot.json")
            os.makedirs(os.path.dirname(snap_path), exist_ok=True)
            if not os.path.exists(snap_path):
                with open(snap_path, "w", encoding="utf-8") as f:
                    json.dump([], f)
        except Exception:
            pass

        try:
            transcript_md = transcript.render_markdown()
            self.artifacts.add_text(
                "transcript.md",
                transcript_md,
                tags=["transcript", "router"],
                meta={},
            )
        except Exception:
            pass

        summary = self._build_summary(agents or {}, evaluator, decisions)
        if self._web_disabled_by_flag:
            summary["web_search"] = "disabled_by_flag"
        summary["finalization_report"] = os.path.join("artifacts", "finalization", "report.json")
        self.artifacts.add_text("run_summary.json", json.dumps(summary, indent=2), tags=["summary"])
        self.logger.log("run_complete", summary_path=os.path.join(self.run_dir, "artifacts", "run_summary.json"))

        return {
            "artifact_dir": os.path.join(self.run_dir, "artifacts"),
            "log_path": self.logger.path(),
            "run_id": self.run_id,
            "summary_path": os.path.join(self.run_dir, "artifacts", "run_summary.json"),
        }

    def _build_summary(self, agents: Dict[str, Any], evaluator: GateEvaluator, decisions: List[DecisionSummary]) -> Dict[str, Any]:
        evaluator.load_test_results()
        reports_dir = os.path.join(self.run_dir, "artifacts", "contracts", "results")
        reports = []
        if os.path.isdir(reports_dir):
            for n in os.listdir(reports_dir):
                if n.endswith(".json"):
                    reports.append(os.path.join("artifacts", "contracts", "results", n))
        agent_reports: Dict[str, Any] = {}
        for k, a in agents.items():
            rep = a.report()
            agent_reports[k] = asdict(rep)
        providers = (self.cfg.to_public_dict().get('providers') if self.cfg else {})
        plan_snapshot_path = os.path.join("artifacts", "plans", "snapshot.json")
        return {
            "artifacts_root": os.path.join("artifacts"),
            "contract_reports": reports,
            "test_statuses": evaluator.latest_tests,
            "decisions": [asdict(d) for d in decisions],
            "agent_reports": agent_reports,
            "providers": providers,
            "router_provider_order": self.cfg.router_provider_order if self.cfg else [],
            "agent_provider_order": self.cfg.agent_provider_order if self.cfg else [],
            "mode": self.mode,
            "plan_snapshots": plan_snapshot_path,
            "scaffolds": {"backend": os.path.join("artifacts", "backend"), "frontend": os.path.join("artifacts", "frontend")},
        }
