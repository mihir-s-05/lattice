from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from .config import RunConfig
from .providers import call_with_fallback, ProviderError
from .runlog import RunLogger


class RouterLLM:
    def __init__(self, cfg: RunConfig, logger: RunLogger) -> None:
        self.cfg = cfg
        self.logger = logger

    def _call(self, messages: List[Dict[str, str]], phase: str) -> Dict[str, Any]:
        order = self.cfg.router_provider_order
        model_overrides = {}
        if self.cfg.router_model_default:
            if order:
                model_overrides[order[0]] = self.cfg.router_model_default
        t0 = time.time()
        try:
            provider, base_url, model, raw, attempts = call_with_fallback(
                providers=self.cfg.providers,
                order=order,
                messages=messages,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                logger=self.logger,
                tool_choice="none",
                model_overrides=model_overrides,
            )
        except ProviderError as e:
            self.logger.log(
                "router_llm_turn",
                role="router",
                plan_phase=phase,
                provider=None,
                model=None,
                base_url=None,
                request_prompt=messages,
                response_text=None,
                latency_ms=None,
                error=str(e),
                fallback_from=order[0] if order else None,
            )
            raise
        dt = int((time.time() - t0) * 1000)
        text = ""
        try:
            text = raw["choices"][0]["message"].get("content") or ""
        except Exception:
            text = str(raw)
        self.logger.log(
            "router_llm_turn",
            role="router",
            plan_phase=phase,
            provider=provider,
            model=model,
            base_url=base_url,
            request_prompt=messages,
            response_text=text,
            latency_ms=dt,
            error=None,
            fallback_from=(order[0] if order and provider != order[0] else None),
        )
        return {"provider": provider, "model": model, "text": text}

    def _call_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        phase: str,
        tool_choice: Optional[str] = "auto",
    ) -> Dict[str, Any]:
        order = self.cfg.router_provider_order
        model_overrides = {}
        if self.cfg.router_model_default:
            if order:
                model_overrides[order[0]] = self.cfg.router_model_default
        t0 = time.time()
        try:
            provider, base_url, model, raw, attempts = call_with_fallback(
                providers=self.cfg.providers,
                order=order,
                messages=messages,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                logger=self.logger,
                tools=tools,
                tool_choice=tool_choice,
                model_overrides=model_overrides,
            )
        except ProviderError as e:
            self.logger.log(
                "router_llm_turn",
                role="router",
                plan_phase=phase,
                provider=None,
                model=None,
                base_url=None,
                request_prompt=messages,
                response_text=None,
                latency_ms=None,
                error=str(e),
                tools=[t.get("function", {}).get("name") for t in tools or []],
                tool_choice=tool_choice,
            )
            raise
        dt = int((time.time() - t0) * 1000)
        text = None
        try:
            text = raw["choices"][0]["message"].get("content")
        except Exception:
            text = None
        tool_calls = []
        try:
            tool_calls = raw["choices"][0]["message"].get("tool_calls") or []
        except Exception:
            tool_calls = []
        self.logger.log(
            "router_llm_turn",
            role="router",
            plan_phase=phase,
            provider=provider,
            model=model,
            base_url=base_url,
            request_prompt=messages,
            response_text=text,
            latency_ms=dt,
            error=None,
            tools=[t.get("function", {}).get("name") for t in tools or []],
            tool_choice=tool_choice,
            tool_calls=[
                {
                    "name": (tc or {}).get("function", {}).get("name"),
                    "arguments": (tc or {}).get("function", {}).get("arguments"),
                }
                for tc in tool_calls
            ],
        )
        return {"provider": provider, "model": model, "text": text, "raw": raw}

    def plan_init(self, goal: str, context_text: Optional[str] = None) -> Dict[str, Any]:
        sys = (
            "You are the Router LLM for a multi-agent system."
            " Propose Ladder vs. Tracks and outline 3-6 concise steps with goals and risks."
            " Output a compact PlanSpec in markdown with a single code fence labeled plan."
        )
        user = f"Goal: {goal}"
        if context_text:
            user += f"\n\nContext:\n{context_text[:1500]}"
        messages = [{"role": "system", "content": sys}, {"role": "user", "content": user}]
        return self._call(messages, phase="init")

    def refine_step(self, summary: str) -> Dict[str, Any]:
        sys = (
            "You are the Router LLM. Given the current state summary (artifacts, tests, gates),"
            " produce the next-step guidance in 3-5 bullets."
        )
        messages = [{"role": "system", "content": sys}, {"role": "user", "content": summary[:3000]}]
        return self._call(messages, phase="refine")

    def huddle(self, topic: str, questions: List[str], proposed_contract: Optional[str]) -> Dict[str, Any]:
        sys = (
            "You are facilitating a Huddle. Return 1-3 DecisionSummary JSON objects"
            " with fields: id (optional), topic, options[], decision, rationale, risks[], actions[], contracts[], links[]."
            " Output only JSON (array or one object)."
        )
        lines = [f"Huddle Topic: {topic}"]
        if questions:
            lines.append("Questions:")
            lines += [f"- {q}" for q in questions]
        if proposed_contract:
            lines.append("Proposed contract excerpt:\n" + proposed_contract[:3000])
        messages = [{"role": "system", "content": sys}, {"role": "user", "content": "\n".join(lines)}]
        return self._call(messages, phase="huddle")

    def inject(self, decision_summaries_text: str) -> Dict[str, Any]:
        sys = (
            "Compress DecisionSummaries into a short injection block for sub-agents."
            " Keep it <= 12 lines with key interface deltas only."
        )
        messages = [{"role": "system", "content": sys}, {"role": "user", "content": decision_summaries_text[:4000]}]
        return self._call(messages, phase="inject")
