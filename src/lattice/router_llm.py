from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from .config import RunConfig
from .providers import call_with_fallback, ProviderError
from .runlog import RunLogger


class RouterLLM:
    def __init__(self, cfg: RunConfig, logger: RunLogger, tools: Optional[List[Dict[str, Any]]] = None) -> None:
        self.cfg = cfg
        self.logger = logger
        self.tools = tools
        self._previous_response_id: Optional[str] = None
        self._previous_messages_len: Optional[int] = None

    def _delta_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self._previous_messages_len is None:
            return messages
        start = min(self._previous_messages_len, len(messages))
        delta = messages[start:]
        if not delta:
            return messages[-1:]
        return delta

    def _call(self, messages: List[Dict[str, str]], phase: str) -> Dict[str, Any]:
        order = self.cfg.router_provider_order
        model_overrides = {}
        if self.cfg.router_model_default:
            if order:
                model_overrides[order[0]] = self.cfg.router_model_default
        t0 = time.time()
        try:
            result = call_with_fallback(
                providers=self.cfg.providers,
                order=order,
                messages=messages,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                logger=self.logger,
                retries=self.cfg.limits.retry_count,
                http_timeout=self.cfg.limits.http_timeout,
                connect_timeout=self.cfg.limits.connect_timeout,
                max_retry_delay=self.cfg.limits.max_retry_delay,
                tool_choice="none",
                model_overrides=model_overrides,
                caller="router",
                stage=phase,
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
        text = result.text or ""
        self.logger.log(
            "router_llm_turn",
            role="router",
            plan_phase=phase,
            provider=result.provider,
            model=result.model,
            base_url=result.base_url,
            request_prompt=messages,
            response_text=text,
            latency_ms=dt,
            error=None,
            fallback_from=(order[0] if order and result.provider != order[0] else None),
        )
        return {"provider": result.provider, "model": result.model, "text": text, "api": result.api}

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
        msg_payload = messages
        if self._previous_response_id:
            msg_payload = self._delta_messages(messages)
            msg_payload = [m for m in msg_payload if not m.get("_skip_for_responses")]
        try:
            result = call_with_fallback(
                providers=self.cfg.providers,
                order=order,
                messages=msg_payload,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                logger=self.logger,
                retries=self.cfg.limits.retry_count,
                http_timeout=self.cfg.limits.http_timeout,
                connect_timeout=self.cfg.limits.connect_timeout,
                max_retry_delay=self.cfg.limits.max_retry_delay,
                tools=tools,
                tool_choice=tool_choice,
                model_overrides=model_overrides,
                previous_response_id=self._previous_response_id,
                caller="router",
                stage=phase,
            )
            if result.response_id:
                self._previous_response_id = result.response_id
                self._previous_messages_len = len(messages)
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
        text = result.text if isinstance(result.text, str) else None
        tool_calls = result.tool_calls or []
        self.logger.log(
            "router_llm_turn",
            role="router",
            plan_phase=phase,
            provider=result.provider,
            model=result.model,
            base_url=result.base_url,
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
        return {
            "provider": result.provider,
            "model": result.model,
            "text": text,
            "raw": result.raw,
            "tool_calls": tool_calls,
            "api": result.api,
            "response_items": result.response_items,
        }

    def plan_init(self, goal: str, context_text: Optional[str] = None) -> Dict[str, Any]:
        sys = (
            "You are the Router LLM for a multi-agent system. Produce an initial execution plan as STRICT JSON only.\n"
            "Schema:\n"
            "{\n"
            "  \"mode\": \"ladder\"|\"tracks\",\n"
            "  \"mode_reason\": string,\n"
            "  \"stages\": [\n"
            "    {\n"
            "      \"id\": string,  # short, e.g. contracts|backend_scaffold|frontend_scaffold|smoke_tests\n"
            "      \"goal\": string,\n"
            "      \"active_agents\": [\"backend\"|\"frontend\"|\"llmapi\"|\"tests\"],\n"
            "      \"may_parallelize\": boolean,\n"
            "      \"checkin\": {\"when\": \"after\"|\"on_blocked\"|\"both\", \"topic\": string}\n"
            "    }\n"
            "  ],\n"
            "  \"risks\": [string]\n"
            "}\n"
            "Guidelines:\n"
            "- ladder: milestone/stage-driven; tracks: multi-agent parallel-first.\n"
            "- Even in ladder, stages may_parallelize=true when multiple agents can work in parallel within the stage.\n"
            "- Prefer 3-6 stages.\n"
            "Return ONLY the JSON object, no markdown."
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
            " with fields: id (optional), topic, options[], decision, rationale, risks[], actions[], contracts[], links[], sources[]."
            " You MAY also include an optional meta object for operational decisions: meta:{mode:'ladder'|'tracks'|null, stage_order?:string[], note?:string}."
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
        messages = [{"role": "system", "content": sys}, {"role": "user", "content": "\n".join(lines)}]

        if self.tools and self.cfg.web_search_enabled:
            return self._call_with_tools(messages, tools=self.tools, phase="huddle")
        else:
            return self._call(messages, phase="huddle")

    def inject(self, decision_summaries_text: str) -> Dict[str, Any]:
        sys = (
            "Compress DecisionSummaries into a short injection block for sub-agents."
            " Keep it <= 12 lines with key interface deltas only."
        )
        messages = [{"role": "system", "content": sys}, {"role": "user", "content": decision_summaries_text[:4000]}]
        return self._call(messages, phase="inject")
