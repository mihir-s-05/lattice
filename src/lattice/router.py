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
from .huddle import DecisionSummary, parse_decision_summaries, save_decisions, save_huddle, decision_injection_text
from .ids import ulid
from .providers import call_with_fallback, ProviderError
from .rag import RagIndex
from .runlog import RunLogger
from .stage_gates import GateEvaluator, StageGate
from .transcript import RunningTranscript
from .worker import gen_run_id
from .router_llm import RouterLLM


class RouterRunner:
    def __init__(self, cwd: str, run_id: Optional[str] = None, mode: Optional[str] = None) -> None:
        self.cwd = cwd
        self.run_id = run_id or gen_run_id()
        self.run_dir = os.path.join(cwd, "runs", self.run_id)
        os.makedirs(self.run_dir, exist_ok=True)
        self.logger = RunLogger(self.run_dir)
        self.artifacts = ArtifactStore(self.run_dir)
        self.rag = RagIndex(self.run_dir)
        self.cfg: Optional[RunConfig] = None
        self.mode = (mode or os.environ.get("LATTICE_MODE") or random.choice(["ladder", "tracks"]).lower()).strip()
        if self.mode not in ("ladder", "tracks"):
            self.mode = "ladder"

        self._decisions: List[DecisionSummary] = []
        self._provider_usage: Dict[str, int] = {}

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

        rllm = RouterLLM(self.cfg, self.logger)
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
                for m in msgs[-20:]:  # last 20 messages
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
            from .worker import WorkerRunner

            wr = WorkerRunner(self.cwd, self.run_id)
            wr._pre_ingest_repo_files()
        except Exception:
            pass

        transcript = RunningTranscript(self.run_id)

        rllm = RouterLLM(self.cfg, self.logger)
        fe = FrontendAgent("frontend", self.cfg, self.logger, self.artifacts, self.rag)
        be = BackendAgent("backend", self.cfg, self.logger, self.artifacts, self.rag)
        llm = LLMApiAgent("llmapi", self.cfg, self.logger, self.artifacts, self.rag)
        tst = TestAgent("tests", self.cfg, self.logger, self.artifacts, self.rag)
        agents = {"frontend": fe, "backend": be, "llmapi": llm, "tests": tst}

        decisions: List[DecisionSummary] = []
        runner = ContractRunner(self.run_dir, self.logger)
        gates: List[StageGate] = [
            StageGate(
                id="sg_api_contract",
                name="API contract passes",
                conditions=["tests.pass('api_contract')"],
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
                conditions=["tests.pass('smoke_suite')"],
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

        else:  # tracks
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

        try:
            self.artifacts.add_text(os.path.join("plans", "snapshot.json"), json.dumps(plan_snapshots, indent=2), tags=["plan", "snapshot"])  # type: ignore[arg-type]
        except Exception:
            pass

        summary = self._build_summary(agents, evaluator, decisions)
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
