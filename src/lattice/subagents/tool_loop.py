from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..config import RunConfig
from ..providers import ProviderError, call_with_fallback
from ..runlog import RunLogger


@dataclass
class ToolLoopResult:
    text: str
    prev_response_id: Optional[str]
    prev_messages_len: Optional[int]


class ToolLoop:
    def __init__(self, cfg: RunConfig, logger: RunLogger, *, caller: str) -> None:
        self.cfg = cfg
        self.logger = logger
        self.caller = caller

    def run(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]],
        executor: Any,
        tool_choice: str,
        max_iters: int,
        prev_response_id: Optional[str],
        prev_messages_len: Optional[int],
        order_override: Optional[List[str]] = None,
        model_overrides_override: Optional[Dict[str, str]] = None,
        temperature_override: Optional[float] = None,
    ) -> ToolLoopResult:
        def _delta_messages(full: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if prev_messages_len is None:
                return full
            start = min(prev_messages_len, len(full))
            delta = full[start:]
            if not delta:
                return full[-1:]
            return delta

        def _can_use_prev_response_id() -> bool:
            order = order_override or (self.cfg.agent_provider_order or [])
            if len(order) != 1:
                return False
            if order[0] != "openai":
                return False
            openai_cfg = self.cfg.providers.get("openai")
            base = (openai_cfg.base_url or "").lower() if openai_cfg else ""
            if "openai.com" not in base:
                return False
            model = (self.cfg.agent_model_default or (openai_cfg.model if openai_cfg else "") or "").lower()
            return ("gpt-5" in model) or model.startswith("o3") or model.startswith("o4")

        model_overrides = model_overrides_override or (
            {self.cfg.agent_provider_order[0]: self.cfg.agent_model_default}
            if (self.cfg.agent_model_default and self.cfg.agent_provider_order)
            else None
        )

        local_prev_response_id = prev_response_id
        local_prev_messages_len = prev_messages_len

        for _i in range(max_iters):
            msg_payload = messages
            prev_id = None
            if local_prev_response_id and _can_use_prev_response_id():
                prev_id = local_prev_response_id
                msg_payload = _delta_messages(messages)
                msg_payload = [m for m in msg_payload if not (isinstance(m, dict) and m.get("_skip_for_responses"))]
            try:
                result = call_with_fallback(
                    providers=self.cfg.providers,
                    order=(order_override or self.cfg.agent_provider_order),
                    messages=msg_payload,
                    temperature=(temperature_override if temperature_override is not None else self.cfg.temperature),
                    max_tokens=self.cfg.max_tokens,
                    logger=self.logger,
                    retries=self.cfg.limits.retry_count,
                    http_timeout=self.cfg.limits.http_timeout,
                    connect_timeout=self.cfg.limits.connect_timeout,
                    max_retry_delay=self.cfg.limits.max_retry_delay,
                    tools=tools,
                    tool_choice=tool_choice,
                    model_overrides=model_overrides,
                    previous_response_id=prev_id,
                    caller=self.caller,
                    stage="agent_tools",
                )
            except ProviderError:
                plain = [{"role": m.get("role"), "content": m.get("content") or ""} for m in messages if isinstance(m, dict)]
                out_text = call_with_fallback(
                    providers=self.cfg.providers,
                    order=(order_override or self.cfg.agent_provider_order),
                    messages=plain,
                    temperature=(temperature_override if temperature_override is not None else self.cfg.temperature),
                    max_tokens=self.cfg.max_tokens,
                    logger=self.logger,
                    retries=self.cfg.limits.retry_count,
                    http_timeout=self.cfg.limits.http_timeout,
                    connect_timeout=self.cfg.limits.connect_timeout,
                    max_retry_delay=self.cfg.limits.max_retry_delay,
                    model_overrides=model_overrides,
                    caller=self.caller,
                    stage="agent_model",
                ).text or ""
                return ToolLoopResult(text=out_text, prev_response_id=None, prev_messages_len=None)

            tool_calls = result.tool_calls or []
            out_text = result.text or ""
            if result.api == "responses" and result.response_id:
                local_prev_response_id = result.response_id
                local_prev_messages_len = len(messages)
            else:
                local_prev_response_id = None
                local_prev_messages_len = None
            if not tool_calls:
                return ToolLoopResult(text=out_text, prev_response_id=local_prev_response_id, prev_messages_len=local_prev_messages_len)

            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": (out_text if out_text else None), "tool_calls": tool_calls}
            if result.api == "responses":
                assistant_msg["_skip_for_responses"] = True
            messages.append(assistant_msg)

            for tc in tool_calls:
                tool_name = (tc or {}).get("function", {}).get("name") or ""
                tool_args_s = (tc or {}).get("function", {}).get("arguments") or "{}"
                try:
                    tool_args = json.loads(tool_args_s)
                except json.JSONDecodeError:
                    tool_args = {}
                obs = executor.execute(tool_name, tool_args if isinstance(tool_args, dict) else {})
                self.logger.log(
                    "agent_tool_call",
                    agent=(executor.agent_name if hasattr(executor, "agent_name") else self.caller),
                    tool_name=tool_name,
                    params=tool_args,
                    observation=(obs if len(str(obs)) < 5000 else {"note": "obs too large"}),
                )
                from ..agent_tools import append_tool_result_message

                append_tool_result_message(messages, tc, obs)

        return ToolLoopResult(text="", prev_response_id=local_prev_response_id, prev_messages_len=local_prev_messages_len)
