import json
import time
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import ProviderConfig
from .constants import DEFAULT_HTTP_TIMEOUT, DEFAULT_RETRY_COUNT, DEFAULT_MAX_RETRY_DELAY
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
    try:
        return "gpt-oss" in (model or "").lower()
    except Exception:
        return False


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
    ) -> Tuple[str, Dict[str, Any]]:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        model_to_use = model or self.cfg.model

        if _is_gpt_oss_model(model_to_use):
            try:
                gptoss_temp = float(os.environ.get("LATTICE_GPTOSS_TEMPERATURE", "1"))
            except Exception:
                gptoss_temp = 1.0
            try:
                gptoss_top_k = int(os.environ.get("LATTICE_GPTOSS_TOP_K", "0"))
            except Exception:
                gptoss_top_k = 0
            try:
                gptoss_min_p = float(os.environ.get("LATTICE_GPTOSS_MIN_P", "0.05"))
            except Exception:
                gptoss_min_p = 0.05
            try:
                gptoss_top_p = float(os.environ.get("LATTICE_GPTOSS_TOP_P", "1"))
            except Exception:
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

        resp = requests.post(url, headers=self._headers(), params=self._params(), json=body, timeout=DEFAULT_HTTP_TIMEOUT)
        try:
            data = resp.json()
        except Exception:
            data = {"text": resp.text}

        if not resp.ok:
            raise ProviderError(
                f"HTTP {resp.status_code}: {data}",
                self.cfg.name,
                context={"status_code": resp.status_code, "response_data": data}
            )
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except Exception as e:
            raise ProviderError(
                f"Unexpected response format: {e}; data={data}",
                self.cfg.name,
                context={"response_data": data, "parse_error": str(e)}
            )
        return content, data


def call_with_fallback(
    providers: Dict[str, ProviderConfig],
    order: List[str],
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: Optional[int],
    logger,
    retries: int = DEFAULT_RETRY_COUNT,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = None,
    model_overrides: Optional[Dict[str, str]] = None,
) -> Tuple[str, str, str, Dict[str, Any], int]:
    last_err: Optional[str] = None
    attempt = 0
    for name in order:
        cfg = providers[name]
        prov = OpenAICompatProvider(cfg)
        model = (model_overrides or {}).get(name) or cfg.model
        attempt = 0
        while attempt <= retries:
            try:
                t0 = time.time()
                out_text, raw = prov.chat_completion(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                )
                dt = time.time() - t0
                logger.log(
                    "model_call",
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
                    retries=attempt,
                    fallback_chain=order,
                )
                return name, cfg.base_url, model, raw, attempt
            except Exception as e:
                attempt += 1
                provider_error = handle_provider_error(e, name, attempt)
                last_err = str(provider_error)
                
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
                    retries=attempt,
                    fallback_chain=order,
                )
                if attempt <= retries and transient:
                    time.sleep(min(2 ** attempt, DEFAULT_MAX_RETRY_DELAY))
                else:
                    break
    raise ProviderError(f"All providers failed. Last error: {last_err}")