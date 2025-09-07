import json
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import ProviderConfig


class ProviderError(Exception):
    pass


def _is_rate_limited(status: int, data: Any) -> bool:
    if status == 429:
        return True
    if isinstance(data, dict):
        msg = json.dumps(data)
        if "rate" in msg.lower():
            return True
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
        body: Dict[str, Any] = {
            "model": model or self.cfg.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice

        resp = requests.post(url, headers=self._headers(), params=self._params(), json=body, timeout=60)
        try:
            data = resp.json()
        except Exception:
            data = {"text": resp.text}

        if not resp.ok:
            raise ProviderError(f"HTTP {resp.status_code}: {data}")
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except Exception as e:
            raise ProviderError(f"Unexpected response format: {e}; data={data}")
        return content, data


def call_with_fallback(
    providers: Dict[str, ProviderConfig],
    order: List[str],
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: Optional[int],
    logger,
    retries: int = 2,
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
                last_err = str(e)
                transient = True
                if isinstance(e, ProviderError) and "HTTP" in str(e):
                    m = str(e)
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
                    error=str(e),
                    retries=attempt,
                    fallback_chain=order,
                )
                if attempt <= retries and transient:
                    time.sleep(min(2 ** attempt, 8))
                else:
                    break
    raise ProviderError(f"All providers failed. Last error: {last_err}")
