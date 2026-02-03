import pytest
from unittest.mock import MagicMock, patch


class TestIsRateLimited:
    def test_429_status_returns_true(self):
        from lattice.providers import _is_rate_limited
        assert _is_rate_limited(429, {}) is True

    def test_200_status_returns_false(self):
        from lattice.providers import _is_rate_limited
        assert _is_rate_limited(200, {}) is False

    def test_rate_in_response_body_returns_true(self):
        from lattice.providers import _is_rate_limited
        data = {"error": "rate limit exceeded"}
        assert _is_rate_limited(400, data) is True

    def test_normal_error_returns_false(self):
        from lattice.providers import _is_rate_limited
        data = {"error": "invalid request"}
        assert _is_rate_limited(400, data) is False


class TestIsGptOssModel:
    def test_gpt_oss_model_returns_true(self):
        from lattice.providers import _is_gpt_oss_model
        assert _is_gpt_oss_model("openai/gpt-oss-120b") is True

    def test_gpt_oss_lowercase_returns_true(self):
        from lattice.providers import _is_gpt_oss_model
        assert _is_gpt_oss_model("GPT-OSS-20B") is True

    def test_regular_model_returns_false(self):
        from lattice.providers import _is_gpt_oss_model
        assert _is_gpt_oss_model("gpt-4") is False

    def test_none_returns_false(self):
        from lattice.providers import _is_gpt_oss_model
        assert _is_gpt_oss_model(None) is False

    def test_empty_string_returns_false(self):
        from lattice.providers import _is_gpt_oss_model
        assert _is_gpt_oss_model("") is False


class TestOpenAICompatProvider:
    def test_headers_include_content_type(self, mock_provider_config):
        from lattice.providers import OpenAICompatProvider
        provider = OpenAICompatProvider(mock_provider_config)
        headers = provider._headers()
        assert headers["Content-Type"] == "application/json"

    def test_headers_include_authorization(self, mock_provider_config):
        from lattice.providers import OpenAICompatProvider
        provider = OpenAICompatProvider(mock_provider_config)
        headers = provider._headers()
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test_key"

    def test_headers_no_auth_when_no_key(self, mock_provider_config):
        from lattice.providers import OpenAICompatProvider
        mock_provider_config.api_key = None
        provider = OpenAICompatProvider(mock_provider_config)
        headers = provider._headers()
        assert "Authorization" not in headers

    def test_params_empty_for_normal_provider(self, mock_provider_config):
        from lattice.providers import OpenAICompatProvider
        provider = OpenAICompatProvider(mock_provider_config)
        params = provider._params()
        assert params == {}

    def test_params_includes_key_for_google(self, mock_provider_config):
        from lattice.providers import OpenAICompatProvider
        mock_provider_config.base_url = "https://generativelanguage.googleapis.com/v1"
        mock_provider_config.api_key = "google_key"
        provider = OpenAICompatProvider(mock_provider_config)
        params = provider._params()
        assert params["key"] == "google_key"


class TestResponsesHelpers:
    def test_convert_tools_for_responses_flattens_function(self):
        from lattice.providers import _convert_tools_for_responses
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "do_thing",
                    "description": "Do a thing",
                    "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
                },
            }
        ]
        converted = _convert_tools_for_responses(tools)
        assert converted[0]["type"] == "function"
        assert converted[0]["name"] == "do_thing"
        assert "function" not in converted[0]

    def test_convert_messages_to_responses_input_handles_tools(self):
        from lattice.providers import _convert_messages_to_responses_input
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "do_thing", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        items = _convert_messages_to_responses_input(messages)
        assert {"role": "user", "content": "hi"} in items
        assert any(i.get("type") == "function_call" and i.get("name") == "do_thing" for i in items)
        assert any(i.get("type") == "function_call_output" and i.get("call_id") == "call_1" for i in items)

    def test_extract_tool_calls_from_responses(self):
        from lattice.providers import _extract_tool_calls_from_responses
        raw = {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "hi"}]},
                {"type": "function_call", "name": "do_thing", "arguments": "{\"x\": 1}", "call_id": "call_1"},
            ]
        }
        calls = _extract_tool_calls_from_responses(raw)
        assert calls[0]["function"]["name"] == "do_thing"
        assert calls[0]["function"]["arguments"] == "{\"x\": 1}"
