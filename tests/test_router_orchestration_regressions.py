import json


def _run_cfg(tmp_path, *, provider_order, base_url="https://api.openai.com/v1"):
    from lattice.config import RunConfig, ProviderConfig, SystemLimits, RagConfig, ExecutionConfig, CommandPolicy

    provs = {
        "openai": ProviderConfig(name="openai", base_url=base_url, api_key="test", model="gpt-5-mini"),
        "groq": ProviderConfig(name="groq", base_url="https://api.groq.com/openai/v1", api_key="test", model="openai/gpt-oss-120b"),
        "local": ProviderConfig(name="local", base_url="http://localhost:1234/v1", api_key="local", model="gpt-oss-20b"),
    }
    return RunConfig(
        run_id="test-run",
        providers=provs,
        router_provider_order=list(provider_order),
        agent_provider_order=["openai"],
        router_model_default="gpt-5-mini",
        agent_model_default="gpt-5-mini",
        limits=SystemLimits(),
        rag=RagConfig(),
        execution=ExecutionConfig(huddle_mode="dialog", web_search_enabled=True),
        command_policy=CommandPolicy(),
    )


def test_dialog_huddle_executes_tool_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("LATTICE_RUNS_DIR", str(tmp_path / "runs"))

    from lattice.router_main import RouterRunner
    from lattice.transcript import RunningTranscript
    from lattice.router_llm import RouterLLM

    rr = RouterRunner(cwd=str(tmp_path), mode="ladder")
    rr.cfg = _run_cfg(tmp_path, provider_order=["openai"])

    calls = {"web": 0, "llm": 0}

    def _fake_web_search_exec(self, query, top_k, time_range, engines, language, pageno):
        calls["web"] += 1
        return {"query": query, "source": "test", "results": [{"title": "t", "url": "https://example.com", "snippet": ""}], "extracts": []}

    def _fake_call_with_tools(self, messages, tools, phase, tool_choice="auto"):
        calls["llm"] += 1
        if calls["llm"] == 1:
            return {
                "provider": "openai",
                "model": "gpt-5-mini",
                "api": "responses",
                "text": "",
                "prev_response_id_enabled": True,
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": "{\"query\":\"x\",\"top_k\":3}"}}
                ],
            }
        decision = {
            "id": "ds_1",
            "topic": "T",
            "options": ["A"],
            "decision": "A",
            "rationale": "R",
            "risks": [],
            "actions": [],
            "contracts": [],
            "links": [],
            "sources": [{"type": "external", "url": "https://example.com", "title": "Example"}],
        }
        return {"provider": "openai", "model": "gpt-5-mini", "api": "responses", "prev_response_id_enabled": True, "text": json.dumps(decision), "tool_calls": []}

    monkeypatch.setattr(RouterRunner, "_web_search_exec", _fake_web_search_exec, raising=True)
    monkeypatch.setattr(RouterLLM, "_call_with_tools", _fake_call_with_tools, raising=True)

    transcript = RunningTranscript(rr.run_id)
    hud = rr._execute_huddle(
        topic="Test",
        questions=["Q"],
        proposed_contract=None,
        transcript=transcript,
        agents={},
        decisions_so_far=[],
    )

    assert calls["llm"] == 2
    assert calls["web"] == 1
    assert hud["decisions"]
