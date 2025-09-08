from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Any, Dict, List, Optional

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
from .config import RunConfig
from .contracts import ContractRunner
from .huddle import DecisionSummary, decision_injection_text
from .rag import RagIndex
from .runlog import RunLogger
from .stage_gates import GateEvaluator, StageGate
from .transcript import RunningTranscript
from .router_llm import RouterLLM
from .plan import PlanGraph, PlanNode
from .constants import DEFAULT_STAGE_GATES


class ExecutionMode(ABC):
    
    def __init__(
        self, 
        run_dir: str, 
        logger: RunLogger, 
        artifacts: ArtifactStore, 
        rag: RagIndex,
        cfg: RunConfig
    ):
        self.run_dir = run_dir
        self.logger = logger
        self.artifacts = artifacts
        self.rag = rag
        self.cfg = cfg

        self.agents = {
            "frontend": FrontendAgent("frontend", cfg, logger, artifacts, rag),
            "backend": BackendAgent("backend", cfg, logger, artifacts, rag),
            "llmapi": LLMApiAgent("llmapi", cfg, logger, artifacts, rag),
            "tests": TestAgent("tests", cfg, logger, artifacts, rag),
        }

        self.runner = ContractRunner(run_dir, logger)
        self.evaluator = GateEvaluator(run_dir, artifacts, logger)
        self.rllm = RouterLLM(cfg, logger)

        self.gates = [
            StageGate(
                id=gate["id"],
                name=gate["name"],
                conditions=gate["conditions"]
            ) for gate in DEFAULT_STAGE_GATES
        ]
        
    @abstractmethod
    def execute(self, goal: str, transcript: RunningTranscript) -> Dict[str, Any]:
        pass
        
    def _execute_huddle(
        self,
        topic: str,
        questions: List[str],
        decisions_so_far: List[DecisionSummary],
        transcript: RunningTranscript
    ) -> List[DecisionSummary]:
        hud_obj = self.rllm.huddle(topic, questions, None)
        decisions_text = hud_obj.get("text", "")

        from .huddle import parse_decision_summaries, save_decisions, save_huddle
        from .ids import ulid

        decisions = parse_decision_summaries(decisions_text)
        attendees = ["router"] + list(self.agents.keys())
        hud_id = f"hud_{ulid()}"

        save_huddle(
            run_dir=self.run_dir,
            artifacts=self.artifacts,
            rag_index=self.rag,
            requester="router",
            attendees=attendees,
            topic=topic,
            questions=questions,
            notes="Auto-generated huddle",
            decisions=decisions,
            hud_id=hud_id
        )

        save_decisions(self.run_dir, self.artifacts, self.rag, decisions)
        
        for d in decisions:
            self.logger.log("decision_summary", decision_id=d.id, topic=d.topic, decision=d.decision)
            
        return decisions


class LadderMode(ExecutionMode):

    def execute(self, goal: str, transcript: RunningTranscript) -> Dict[str, Any]:
        plan_snapshots = []
        decisions: List[DecisionSummary] = []

        active = [self.agents["backend"], self.agents["llmapi"], self.agents["tests"]]
        self._execute_step("contracts", active, goal, decisions, plan_snapshots)

        if any(a.needs_huddle({"goal": goal, "decisions": decisions}) for a in active):
            new_decisions = self._execute_huddle(
                "API contract alignment",
                ["Resource fields?", "Endpoints & DTOs?", "Error model?"],
                decisions,
                transcript
            )
            decisions.extend(new_decisions)
            transcript.add_decision_injection(decision_injection_text(decisions))

        active = [self.agents["backend"], self.agents["llmapi"]]
        self._execute_step("backend_scaffold", active, goal, decisions, plan_snapshots)

        active = [self.agents["frontend"]]
        self._execute_step("frontend_scaffold", active, goal, decisions, plan_snapshots)

        active = [self.agents["tests"]]
        self._execute_step("smoke_tests", active, goal, decisions, plan_snapshots)
        
        return {
            "plan_snapshots": plan_snapshots,
            "decisions": decisions
        }
    
    def _execute_step(
        self,
        step_name: str,
        active_agents: List,
        goal: str,
        decisions: List[DecisionSummary],
        plan_snapshots: List[Dict[str, Any]]
    ):
        plans = [a.plan(step_name, {"goal": goal, "decisions": decisions}) for a in active_agents]
        self.logger.log("router_plans", mode="ladder", step=step_name, plans=[asdict(p) for p in plans])

        for agent in active_agents:
            refs = agent.act({"goal": goal, "decisions": decisions})
            self.logger.log("agent_turn", agent=agent.name, artifacts=[r.path for r in refs])

        results = self.runner.scan_and_run()
        gate_results = self.evaluator.evaluate(self._get_gates_for_step(step_name))

        retries = 0
        while not all(g.status == "passed" for g in gate_results) and retries < 3:
            self.logger.log("router_block", step=step_name, reason="gate_failed",
                          gates=[asdict(g) for g in gate_results])

            results = self.runner.scan_and_run()
            gate_results = self.evaluator.evaluate(self._get_gates_for_step(step_name))
            retries += 1

        plan_snapshots.append({
            "mode": "ladder",
            "step": step_name,
            "gates": [asdict(g) for g in gate_results],
            "tests": [asdict(r) for r in results],
        })
    
    def _get_gates_for_step(self, step_name: str) -> List[StageGate]:
        gate_map = {
            "contracts": ["sg_api_contract"],
            "backend_scaffold": ["sg_api_contract", "sg_be_scaffold"],
            "frontend_scaffold": ["sg_fe_scaffold"],
            "smoke_tests": ["sg_smoke", "sg_fe_scaffold", "sg_be_scaffold", "sg_api_contract"]
        }
        
        gate_ids = gate_map.get(step_name, [])
        return [g for g in self.gates if g.id in gate_ids]


class TracksMode(ExecutionMode):
    
    def execute(self, goal: str, transcript: RunningTranscript) -> Dict[str, Any]:
        plan_snapshots = []
        decisions: List[DecisionSummary] = []

        active = list(self.agents.values())
        plans = [a.plan("tracks", {"goal": goal, "decisions": decisions}) for a in active]
        self.logger.log("router_plans", mode="tracks", step="slice-1", plans=[asdict(p) for p in plans])

        for agent in active:
            refs = agent.act({"goal": goal, "decisions": decisions})
            self.logger.log("agent_turn", agent=agent.name, artifacts=[r.path for r in refs])

        if any(a.needs_huddle({"goal": goal, "decisions": decisions}) for a in active):
            new_decisions = self._execute_huddle(
                "Parallel tracks alignment",
                ["Resource fields?", "Endpoints & DTOs?", "Error model?"],
                decisions,
                transcript
            )
            decisions.extend(new_decisions)
            transcript.add_decision_injection(decision_injection_text(decisions))

        results = self.runner.scan_and_run()
        gate_results = self.evaluator.evaluate(self.gates)
        
        plan_snapshots.append({
            "mode": "tracks",
            "step": "sync-1",
            "gates": [asdict(g) for g in gate_results],
            "tests": [asdict(r) for r in results],
        })
        
        return {
            "plan_snapshots": plan_snapshots,
            "decisions": decisions
        }


class WeaveMode(ExecutionMode):
    
    def execute(self, goal: str, transcript: RunningTranscript) -> Dict[str, Any]:
        plan_snapshots = []
        decisions: List[DecisionSummary] = []

        plan_graph = PlanGraph()
        plan_graph.mode_by_segment = {"critical": "ladder", "docs": "tracks"}

        plan_graph.add_node(PlanNode(id="n_contracts", name="API contracts", modeSegment="critical"))
        plan_graph.add_node(PlanNode(id="n_backend", name="Backend scaffold", modeSegment="critical"))
        plan_graph.add_node(PlanNode(id="n_smoke", name="Smoke tests", modeSegment="critical"))
        plan_graph.add_node(PlanNode(id="n_docs", name="Docs/README", modeSegment="docs"))

        plan_graph.add_edge("n_contracts", "n_backend")
        plan_graph.add_edge("n_backend", "n_smoke")

        active_critical = [self.agents["backend"], self.agents["llmapi"], self.agents["tests"]]
        plans = [a.plan("contracts", {"goal": goal, "decisions": decisions}) for a in active_critical]
        self.logger.log("router_plans", mode="weave", step="contracts", plans=[asdict(p) for p in plans])

        for agent in active_critical:
            refs = agent.act({"goal": goal, "decisions": decisions})
            self.logger.log("agent_turn", agent=agent.name, artifacts=[r.path for r in refs])

        try:
            doc_out = self.agents["llmapi"]._model([
                {"role": "system", "content": "You are the Docs agent. Write a concise README for the generated CLI app."},
                {"role": "user", "content": f"Goal: {goal}\n\nWrite a minimal README with: Overview, Quickstart, Commands, and Notes."},
            ])
            readme_art = self.artifacts.add_text("README.md", doc_out, tags=["docs", "readme"], meta={"segment": "docs"})
            plan_graph.nodes[-1].evidence.append({"type": "artifact", "id": readme_art.path, "hash": f"sha256:{readme_art.sha256}"})
            self.logger.log("agent_turn", agent="docs", artifacts=[readme_art.path])
        except Exception:
            pass

        results = self.runner.scan_and_run()
        if any(a.needs_huddle({"goal": goal, "decisions": decisions}) for a in active_critical):
            new_decisions = self._execute_huddle(
                "Weave mode alignment",
                ["Resource fields?", "Endpoints & DTOs?", "Error model?"],
                decisions,
                transcript
            )
            decisions.extend(new_decisions)
            transcript.add_decision_injection(decision_injection_text(decisions))

        gate_results = self.evaluator.evaluate([g for g in self.gates if g.id == 'sg_api_contract'])

        plan_snapshots.append({
            "mode": "weave",
            "step": "contracts/weave_docs",
            "gates": [asdict(g) for g in gate_results],
            "tests": [asdict(r) for r in results],
        })

        try:
            plan_graph.save(self.run_dir)
        except Exception:
            pass
        
        return {
            "plan_snapshots": plan_snapshots,
            "decisions": decisions,
            "plan_graph": plan_graph
        }


class ExecutionModeFactory:
    
    @staticmethod
    def create(
        mode: str,
        run_dir: str,
        logger: RunLogger,
        artifacts: ArtifactStore,
        rag: RagIndex,
        cfg: RunConfig
    ) -> ExecutionMode:
        mode = mode.lower().strip()
        
        if mode == "ladder":
            return LadderMode(run_dir, logger, artifacts, rag, cfg)
        elif mode == "tracks":
            return TracksMode(run_dir, logger, artifacts, rag, cfg)
        elif mode == "weave":
            return WeaveMode(run_dir, logger, artifacts, rag, cfg)
        else:
            raise ValueError(f"Unknown execution mode: {mode}")
