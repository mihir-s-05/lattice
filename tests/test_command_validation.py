def test_default_command_allowlist_allows_echo(tmp_path, tmp_run_dir, mock_logger, mock_run_config, monkeypatch):
    from lattice.artifacts import ArtifactStore
    from lattice.rag import RagIndex
    from lattice.execution_modes import LadderMode

    monkeypatch.chdir(tmp_path)
    artifacts = ArtifactStore(tmp_run_dir)
    rag = RagIndex(tmp_run_dir)
    mode = LadderMode(run_dir=tmp_run_dir, logger=mock_logger, artifacts=artifacts, rag=rag, cfg=mock_run_config)

    assert mode._validate_command("echo hello") is None
