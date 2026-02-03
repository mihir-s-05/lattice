import json
import time
import os
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import ProviderConfig
from .constants import DEFAULT_CONNECT_TIMEOUT, DEFAULT_HTTP_TIMEOUT, DEFAULT_RETRY_COUNT, DEFAULT_MAX_RETRY_DELAY
from .errors import ProviderError, handle_provider_error


def _is_rate_limited(status: int, data: Any) -> bool:
    if status == 429:
        return True
    if isinstance(data, dict):
        msg = json.dumps(data)
        if "rate" in msg.lower():
            return True
    return False


def _is_gpt_oss_model(model: Optional[str]) -> bool:
    return "gpt-oss" in str(model or "").lower()


def _supports_responses_api(cfg: ProviderConfig) -> bool:
    base = (cfg.base_url or "").lower()
    return "openai.com" in base


def _convert_tools_for_responses(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    if not tools:
        return None
    converted: List[Dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") == "function" and "function" in tool:
            fn = tool.get("function") or {}
            entry: Dict[str, Any] = {
                "type": "function",
                "name": fn.get("name"),
                "description": fn.get("description"),
                "parameters": fn.get("parameters"),
            }
            if "strict" in fn:
                entry["strict"] = fn.get("strict")
            converted.append(entry)
        else:
            converted.append(tool)
    return converted


def _convert_messages_to_responses_input(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("_skip_for_responses"):
            continue
        if msg.get("_response_items"):
            response_items = msg.get("_response_items")
            if isinstance(response_items, list):
                items.extend(response_items)
            continue
        role = msg.get("role")
        if role == "tool":
            call_id = msg.get("tool_call_id") or msg.get("call_id") or msg.get("id")
            output = msg.get("content") or ""
            if call_id:
                items.append({"type": "function_call_output", "call_id": call_id, "output": str(output)})
            else:
                items.append({"role": "user", "content": str(output)})
            continue
        content = msg.get("content")
        if content not in (None, ""):
            items.append({"role": role or "user", "content": content})
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name")
            arguments = fn.get("arguments") or tc.get("arguments") or ""
            call_id = tc.get("id") or tc.get("call_id")
            if not name:
                continue
            item = {"type": "function_call", "name": name, "arguments": arguments}
            if call_id:
                item["call_id"] = call_id
            items.append(item)
    return items


def _extract_text_from_responses(raw: Dict[str, Any]) -> str:
    output = raw.get("output", [])
    content_parts: List[str] = []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                content = item.get("content")
                if isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") in ("output_text", "text"):
                            content_parts.append(c.get("text", ""))
                elif isinstance(content, str):
                    content_parts.append(content)
            elif isinstance(item.get("text"), str):
                content_parts.append(item.get("text") or "")
    if not content_parts:
        output_text = raw.get("output_text")
        if isinstance(output_text, str):
            content_parts.append(output_text)
    return "\n".join([p for p in content_parts if p])


def _extract_tool_calls_from_chat(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    first = choices[0]
    if not isinstance(first, dict):
        return []
    msg = first.get("message")
    if not isinstance(msg, dict):
        return []
    tool_calls = msg.get("tool_calls")
    return tool_calls if isinstance(tool_calls, list) else []


def _extract_tool_calls_from_responses(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    tool_calls: List[Dict[str, Any]] = []
    output = raw.get("output", [])
    if not isinstance(output, list):
        return tool_calls
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call":
            continue
        name = item.get("name")
        arguments = item.get("arguments") or ""
        call_id = item.get("call_id") or item.get("id")
        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return tool_calls


def _env_bool(key: str, default: Optional[bool] = None) -> Optional[bool]:
    raw = os.environ.get(key)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _env_list(key: str) -> Optional[List[str]]:
    raw = os.environ.get(key)
    if not raw:
        return None
    items = [x.strip() for x in raw.split(",") if x.strip()]
    return items or None


def _default_reasoning_effort(model: Optional[str]) -> Optional[str]:
    if not model:
        return None
    m = model.lower()
    if "gpt-5" in m or m.startswith("o3") or m.startswith("o4"):
        return "medium"
    return None


def _can_send_sampling_params(model: Optional[str], reasoning_effort: Optional[str]) -> bool:
    if not model:
        return False
    m = model.lower()
    if m.startswith("gpt-5.1"):
        return (reasoning_effort or "").lower() == "none"
    return False


@dataclass
class LLMCallResult:
    provider: str
    base_url: str
    model: str
    raw: Dict[str, Any]
    attempts: int
    text: str
    tool_calls: List[Dict[str, Any]]
    api: str
    response_id: Optional[str] = None
    response_items: Optional[List[Dict[str, Any]]] = None


class OpenAICompatProvider:
    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        if self.cfg.extra_headers:
            headers.update(self.cfg.extra_headers)
        return headers

    def _params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if "generativelanguage.googleapis.com" in self.cfg.base_url and self.cfg.api_key:
            params["key"] = self.cfg.api_key
        if self.cfg.extra_params:
            params.update(self.cfg.extra_params)
        return params

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        timeout_sec: Optional[float] = None,
        connect_timeout_sec: Optional[float] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        model_to_use = model or self.cfg.model

        if _is_gpt_oss_model(model_to_use):
            try:
                gptoss_temp = float(os.environ.get("LATTICE_GPTOSS_TEMPERATURE", "1"))
            except (ValueError, TypeError):
                gptoss_temp = 1.0
            try:
                gptoss_top_k = int(os.environ.get("LATTICE_GPTOSS_TOP_K", "0"))
            except (ValueError, TypeError):
                gptoss_top_k = 0
            try:
                gptoss_min_p = float(os.environ.get("LATTICE_GPTOSS_MIN_P", "0.05"))
            except (ValueError, TypeError):
                gptoss_min_p = 0.05
            try:
                gptoss_top_p = float(os.environ.get("LATTICE_GPTOSS_TOP_P", "1"))
            except (ValueError, TypeError):
                gptoss_top_p = 1.0

            body: Dict[str, Any] = {
                "model": model_to_use,
                "messages": messages,
                "temperature": gptoss_temp,
                "top_p": gptoss_top_p,
            }

            if "groq.com" not in self.cfg.base_url:
                body["top_k"] = gptoss_top_k
                body["min_p"] = gptoss_min_p
        elif _is_responses_api_model(model_to_use):
            body: Dict[str, Any] = {
                "model": model_to_use,
                "messages": messages,
            }
        else:
            body: Dict[str, Any] = {
                "model": model_to_use,
                "messages": messages,
                "temperature": temperature,
            }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice

        effective_timeout = float(timeout_sec) if timeout_sec else float(DEFAULT_HTTP_TIMEOUT)
        connect_timeout = float(connect_timeout_sec) if connect_timeout_sec is not None else float(
            os.environ.get("LATTICE_CONNECT_TIMEOUT")
            or os.environ.get("LATTICE_HTTP_CONNECT_TIMEOUT")
            or str(DEFAULT_CONNECT_TIMEOUT)
        )
        resp = requests.post(
            url,
            headers=self._headers(),
            params=self._params(),
            json=body,
            timeout=(connect_timeout, effective_timeout),
        )
        try:
            data = resp.json()
        except ValueError:
            data = {"text": resp.text}

        if not resp.ok:
            raise ProviderError(
                f"HTTP {resp.status_code}: {data}",
                self.cfg.name,
                context={"status_code": resp.status_code, "response_data": data}
            )
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, AttributeError, TypeError) as e:
            raise ProviderError(
                f"Unexpected response format: {e}; data={data}",
                self.cfg.name,
                context={"response_data": data, "parse_error": str(e)}
            )
        return content, data

    def responses_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        previous_response_id: Optional[str] = None,
        tool_choice: Optional[Any] = None,
        timeout_sec: Optional[float] = None,
        connect_timeout_sec: Optional[float] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        url = self.cfg.base_url.rstrip("/") + "/responses"
        model_to_use = model or self.cfg.model

        body: Dict[str, Any] = {"model": model_to_use}

        input_items = _convert_messages_to_responses_input(messages)
        body["input"] = input_items if input_items else ""

        if previous_response_id:
            body["previous_response_id"] = previous_response_id

        if max_tokens is not None:
            body["max_output_tokens"] = max_tokens
        reasoning_effort = os.environ.get("LATTICE_RESPONSES_REASONING_EFFORT") or os.environ.get("LATTICE_REASONING_EFFORT")
        if not reasoning_effort or not str(reasoning_effort).strip():
            reasoning_effort = _default_reasoning_effort(model_to_use) or "medium"
        if reasoning_effort:
            reasoning_cfg: Dict[str, Any] = {"effort": reasoning_effort}
            reasoning_summary = os.environ.get("LATTICE_RESPONSES_REASONING_SUMMARY")
            if reasoning_summary:
                reasoning_cfg["summary"] = reasoning_summary
            body["reasoning"] = reasoning_cfg

        if temperature is not None and _can_send_sampling_params(model_to_use, reasoning_effort):
            body["temperature"] = temperature

        text_verbosity = os.environ.get("LATTICE_RESPONSES_TEXT_VERBOSITY") or os.environ.get("LATTICE_TEXT_VERBOSITY")
        if text_verbosity:
            body["text"] = {"verbosity": text_verbosity}

        truncation = os.environ.get("LATTICE_RESPONSES_TRUNCATION")
        if truncation:
            body["truncation"] = truncation
        else:
            body["truncation"] = "auto"

        parallel_tools = _env_bool("LATTICE_RESPONSES_PARALLEL_TOOL_CALLS")
        if parallel_tools is not None:
            body["parallel_tool_calls"] = parallel_tools

        store = _env_bool("LATTICE_RESPONSES_STORE")
        if store is not None:
            body["store"] = store

        include = _env_list("LATTICE_RESPONSES_INCLUDE")
        if store is False:
            include = include or []
            if "reasoning.encrypted_content" not in include:
                include.append("reasoning.encrypted_content")
        if include:
            body["include"] = include

        prompt_cache_key = os.environ.get("LATTICE_PROMPT_CACHE_KEY")
        if prompt_cache_key:
            body["prompt_cache_key"] = prompt_cache_key
        prompt_cache_retention = os.environ.get("LATTICE_PROMPT_CACHE_RETENTION")
        if prompt_cache_retention:
            body["prompt_cache_retention"] = prompt_cache_retention

        tools_to_send = _convert_tools_for_responses(tools)
        if tool_choice == "none":
            tools_to_send = None
            tool_choice = None
        if tools_to_send:
            body["tools"] = tools_to_send
        if tool_choice and tool_choice != "auto":
            body["tool_choice"] = tool_choice
        
        effective_timeout = float(timeout_sec) if timeout_sec else float(DEFAULT_HTTP_TIMEOUT)
        connect_timeout = float(connect_timeout_sec) if connect_timeout_sec is not None else float(
            os.environ.get("LATTICE_CONNECT_TIMEOUT")
            or os.environ.get("LATTICE_HTTP_CONNECT_TIMEOUT")
            or str(DEFAULT_CONNECT_TIMEOUT)
        )
        resp = requests.post(
            url,
            headers=self._headers(),
            params=self._params(),
            json=body,
            timeout=(connect_timeout, effective_timeout),
        )
        try:
            data = resp.json()
        except ValueError:
            data = {"text": resp.text}

        if not resp.ok:
            raise ProviderError(
                f"HTTP {resp.status_code}: {data}",
                self.cfg.name,
                context={"status_code": resp.status_code, "response_data": data}
            )
        
        try:
            content = _extract_text_from_responses(data)
        except (KeyError, IndexError, AttributeError, TypeError, ValueError) as e:
            raise ProviderError(
                f"Unexpected response format: {e}; data={data}",
                self.cfg.name,
                context={"response_data": data, "parse_error": str(e)}
            )
        return content, data


def _is_responses_api_model(model: Optional[str]) -> bool:
    """Check if a model should use the Responses API (gpt-5 family)."""
    if not model:
        return False
    model_lower = model.lower()
    return "gpt-5" in model_lower or model_lower.startswith("o3") or model_lower.startswith("o4")


def call_with_fallback(
    providers: Dict[str, ProviderConfig],
    order: List[str],
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: Optional[int],
    logger,
    retries: int = DEFAULT_RETRY_COUNT,
    http_timeout: Optional[float] = None,
    connect_timeout: Optional[float] = None,
    max_retry_delay: Optional[float] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = None,
    model_overrides: Optional[Dict[str, str]] = None,
    previous_response_id: Optional[str] = None,
    caller: Optional[str] = None,
    stage: Optional[str] = None,
) -> LLMCallResult:
    """Call LLM with provider fallback.
    
    Returns: LLMCallResult
    """
    last_err: Optional[str] = None
    max_delay = float(max_retry_delay) if max_retry_delay is not None else float(DEFAULT_MAX_RETRY_DELAY)
    attempt = 0
    env_connect_timeout = os.environ.get("LATTICE_CONNECT_TIMEOUT") or os.environ.get("LATTICE_HTTP_CONNECT_TIMEOUT")
    base_connect_default = float(env_connect_timeout) if env_connect_timeout else float(DEFAULT_CONNECT_TIMEOUT)
    for name in order:
        cfg = providers[name]
        prov = OpenAICompatProvider(cfg)
        model = (model_overrides or {}).get(name) or cfg.model
        attempt = 0
        while attempt <= retries:
            base_timeout = float(http_timeout) if http_timeout else float(DEFAULT_HTTP_TIMEOUT)
            timeout_for_attempt = min(base_timeout * max(1, attempt + 1), 300.0)
            base_connect = float(connect_timeout) if connect_timeout is not None else base_connect_default
            connect_for_attempt = min(base_connect * max(1, attempt + 1), 120.0)
            try:
                t0 = time.time()
                use_responses = _is_responses_api_model(model) and _supports_responses_api(cfg)
                api_endpoint = "responses" if use_responses else "chat/completions"
                prefix = "[LATTICE]"
                if caller:
                    prefix += f"[{caller}]"
                if stage:
                    prefix += f"[{stage}]"
                print(f"{prefix} Calling {name} ({cfg.base_url}) with model={model} via /{api_endpoint}")

                if use_responses:
                    out_text, raw = prov.responses_completion(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=tools,
                        previous_response_id=previous_response_id,
                        tool_choice=tool_choice,
                        timeout_sec=timeout_for_attempt,
                        connect_timeout_sec=connect_for_attempt,
                    )
                    tool_calls = _extract_tool_calls_from_responses(raw)
                    response_id = raw.get("id") if isinstance(raw, dict) else None
                    response_items = raw.get("output") if isinstance(raw.get("output"), list) else None
                else:
                    out_text, raw = prov.chat_completion(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=tools,
                        tool_choice=tool_choice,
                        timeout_sec=timeout_for_attempt,
                        connect_timeout_sec=connect_for_attempt,
                    )
                    tool_calls = _extract_tool_calls_from_chat(raw)
                    response_id = None
                    response_items = None
                dt = time.time() - t0
                logger.log(
                    "model_call",
                    caller=caller,
                    stage=stage,
                    provider=name,
                    model=model,
                    base_url=cfg.base_url,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    output=out_text,
                    raw_response=raw,
                    duration_sec=round(dt, 3),
                    error=None,
                    api=("responses" if use_responses else "chat"),
                    response_id=response_id,
                    retries=attempt,
                    fallback_chain=order,
                )
                return LLMCallResult(
                    provider=name,
                    base_url=cfg.base_url,
                    model=model,
                    raw=raw,
                    attempts=attempt,
                    text=out_text,
                    tool_calls=tool_calls,
                    api=("responses" if use_responses else "chat"),
                    response_id=response_id,
                    response_items=response_items,
                )
            except (ProviderError, requests.exceptions.RequestException) as e:
                attempt += 1
                provider_error = handle_provider_error(e, name, attempt)
                last_err = str(provider_error)
                print(f"[LATTICE] {name} failed (attempt {attempt}): {last_err[:200]}")
                
                transient = True
                if isinstance(provider_error, ProviderError):
                    context = provider_error.context
                    status_code = context.get("status_code")
                    if status_code:
                        transient = status_code in [429, 500, 502, 503, 504]
                    elif "HTTP" in str(provider_error):
                        m = str(provider_error)
                        transient = any(code in m for code in ["429", "500", "502", "503", "504"])
                logger.log(
                    "model_call",
                    caller=caller,
                    stage=stage,
                    provider=name,
                    model=model,
                    base_url=cfg.base_url,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    output=None,
                    raw_response=None,
                    duration_sec=None,
                    error=str(provider_error),
                    error_context=(provider_error.context if isinstance(provider_error, ProviderError) else None),
                    api=("responses" if use_responses else "chat"),
                    retries=attempt,
                    fallback_chain=order,
                )
                if attempt <= retries and transient:
                    base_sleep = min(2 ** attempt, max_delay)
                    time.sleep(base_sleep + random.random())
                else:
                    break
    raise ProviderError(f"All providers failed. Last error: {last_err}")
