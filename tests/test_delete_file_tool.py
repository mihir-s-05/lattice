import os


def test_agent_delete_file_tool_deletes_file(tmp_path, tmp_run_dir, mock_logger, mock_run_config):
    from lattice.rag import RagIndex
    from lattice.agent_tools import AgentToolExecutor

    ws = tmp_path / "ws"
    ws.mkdir()
    fp = ws / "hello.txt"
    fp.write_text("hi", encoding="utf-8")

    rag = RagIndex(tmp_run_dir)
    ex = AgentToolExecutor(
        agent_name="frontend",
        cfg=mock_run_config,
        logger=mock_logger,
        rag=rag,
        workspace_root=str(ws),
        allow_write_globs=None,
        deny_write_globs=[],
        request_huddle_cb=lambda **kw: None,
    )
    out = ex.execute("delete_file", {"path": "hello.txt", "missing_ok": False})
    assert out.get("deleted") is True
    assert not os.path.exists(str(fp))


def test_agent_delete_file_tool_respects_write_policy(tmp_path, tmp_run_dir, mock_logger, mock_run_config):
    from lattice.rag import RagIndex
    from lattice.agent_tools import AgentToolExecutor

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "blocked.txt").write_text("hi", encoding="utf-8")

    rag = RagIndex(tmp_run_dir)
    ex = AgentToolExecutor(
        agent_name="frontend",
        cfg=mock_run_config,
        logger=mock_logger,
        rag=rag,
        workspace_root=str(ws),
        allow_write_globs=["allowed/**"],
        deny_write_globs=[],
        request_huddle_cb=lambda **kw: None,
    )
    out = ex.execute("delete_file", {"path": "blocked.txt", "missing_ok": True})
    assert out.get("error") == "delete_blocked"
    assert os.path.exists(str(ws / "blocked.txt"))

