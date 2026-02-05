import json


def test_agent_registry_merges_dynamic_tool_modules(tmp_path, monkeypatch):
    from lattice.subagents.registry import AgentRegistry, codebase_key
    from lattice.subagents.tools.dynamic_loader import load_dynamic_tools

    codebase_root = tmp_path / "repo"
    codebase_root.mkdir()
    global_tool = tmp_path / "global_tool.py"
    codebase_tool = tmp_path / "codebase_tool.py"
    global_tool.write_text(
        "TOOL_SPEC={'name':'globaltool','description':'g','parameters':{'type':'object','properties':{},'required':[]}}\n"
        "def run(ctx,args):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    codebase_tool.write_text(
        "TOOL_SPEC={'name':'cbstool','description':'c','parameters':{'type':'object','properties':{},'required':[]}}\n"
        "def run(ctx,args):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    cfg_path = tmp_path / "config.json"
    k = codebase_key(str(codebase_root))
    cfg = {
        "agent_library": {
            "dynamic_tools": {
                "enabled": True,
                "modules": {"globaltool": {"path": str(global_tool), "description": "g"}},
            },
            "codebases": {
                k: {
                    "root": str(codebase_root),
                    "dynamic_tools": {"modules": {"cbstool": {"path": str(codebase_tool), "description": "c"}}},
                }
            },
        }
    }
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("LATTICE_USER_CONFIG", str(cfg_path))

    reg = AgentRegistry(codebase_root=str(codebase_root))
    lib = reg.list_library()
    mods = lib.get("dynamic_tools", {}).get("modules", {})
    assert "globaltool" in mods
    assert "cbstool" in mods

    loaded = load_dynamic_tools(codebase_root=str(codebase_root))
    assert "globaltool" in loaded
    assert "cbstool" in loaded


def test_dynamic_tools_disabled(tmp_path, monkeypatch):
    from lattice.subagents.tools.dynamic_loader import load_dynamic_tools

    codebase_root = tmp_path / "repo"
    codebase_root.mkdir()
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"agent_library": {"dynamic_tools": {"enabled": False, "modules": {}}}}), encoding="utf-8")
    monkeypatch.setenv("LATTICE_USER_CONFIG", str(cfg_path))

    loaded = load_dynamic_tools(codebase_root=str(codebase_root))
    assert loaded == {}


def test_toolbox_agent_artifact_refs_from_tool_writes(tmp_path, mock_run_config, mock_logger):
    from lattice.artifacts import ArtifactStore
    from lattice.rag import RagIndex
    from lattice.subagents.registry import ToolboxVariantSpec, WritePolicySpec
    from lattice.subagents.toolbox import ToolboxAgent

    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "artifacts" / "index.json").write_text('{"artifacts": []}', encoding="utf-8")
    store = ArtifactStore(str(run_dir))
    rag = RagIndex(str(run_dir))

    ws = tmp_path / "ws"
    ws.mkdir()
    p = ws / "out.txt"
    p.write_text("hello", encoding="utf-8")

    spec = ToolboxVariantSpec(
        id="toolbox/t",
        description="t",
        tool_names=["write_file"],
        featuresets=[],
        prompt_prelude="",
        write_policy=WritePolicySpec(allow_globs=None, deny_globs=[]),
    )
    ag = ToolboxAgent("toolbox/t", mock_run_config, mock_logger, store, rag, workspace_root=str(ws), spec=spec)
    msgs = [
        {"role": "system", "content": "x"},
        {"role": "tool", "name": "write_file", "content": json.dumps({"path": str(p)})},
    ]
    refs = ag._artifact_refs_from_tool_writes(msgs)
    assert len(refs) == 1
    assert refs[0].path == str(p)


def test_featureset_write_policy_applied_to_toolbox_variant(tmp_path, monkeypatch):
    from lattice.subagents.registry import AgentRegistry, codebase_key

    codebase_root = tmp_path / "repo"
    codebase_root.mkdir()
    cfg_path = tmp_path / "config.json"
    k = codebase_key(str(codebase_root))
    cfg = {
        "agent_library": {
            "featuresets": {
                "restricted": {
                    "tool_names": ["write_file"],
                    "prompt_prelude": "",
                    "write_policy": {"allow_globs": ["safe/**"], "deny_globs": ["**/*.secret"]},
                }
            },
            "toolbox_variants": {
                "toolbox/restricted": {
                    "description": "x",
                    "featuresets": ["restricted"],
                    "tool_names": [],
                    "prompt_prelude": "",
                    "write_policy": {"allow_globs": None, "deny_globs": []},
                    "max_tool_iters": 2,
                    "tool_choice": "auto",
                }
            },
            "codebases": {k: {"root": str(codebase_root)}},
        }
    }
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("LATTICE_USER_CONFIG", str(cfg_path))

    reg = AgentRegistry(codebase_root=str(codebase_root))
    spec = reg.materialize_toolbox_variant("toolbox/restricted")
    assert spec is not None
    assert spec.write_policy.allow_globs == ["safe/**"]
    assert "**/*.secret" in (spec.write_policy.deny_globs or [])
