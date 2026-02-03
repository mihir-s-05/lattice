def test_default_command_allowlist_allows_echo(tmp_path, tmp_run_dir, mock_logger, mock_run_config, monkeypatch):
    from lattice.artifacts import ArtifactStore
    from lattice.rag import RagIndex
    from lattice.execution_modes import LadderMode

    monkeypatch.chdir(tmp_path)
    artifacts = ArtifactStore(tmp_run_dir)
    rag = RagIndex(tmp_run_dir)
    mode = LadderMode(run_dir=tmp_run_dir, logger=mock_logger, artifacts=artifacts, rag=rag, cfg=mock_run_config)

    assert mode._validate_command("echo hello") is None


def test_command_validation_blocks_dangerous_windows_patterns(tmp_path, tmp_run_dir, mock_logger, mock_run_config, monkeypatch):
    from lattice.artifacts import ArtifactStore
    from lattice.rag import RagIndex
    from lattice.execution_modes import LadderMode

    monkeypatch.chdir(tmp_path)
    artifacts = ArtifactStore(tmp_run_dir)
    rag = RagIndex(tmp_run_dir)
    mode = LadderMode(run_dir=tmp_run_dir, logger=mock_logger, artifacts=artifacts, rag=rag, cfg=mock_run_config)

    assert mode._validate_command("rd /s /q C:\\") is not None
    assert mode._validate_command("Remove-Item -Recurse -Force .\\tmp") is not None
    assert mode._validate_command("cmd /c rd /s /q C:\\tmp") is not None


def test_windows_dangerous_patterns_blocked():
    from lattice.command_validation import command_is_dangerous, validate_command

    assert command_is_dangerous("rd /s /q C:\\") is True
    assert command_is_dangerous("Remove-Item -Recurse -Force C:\\temp") is True
    assert command_is_dangerous("cmd /c rd /s /q C:\\") is True
    assert validate_command("cmd /c del /s C:\\") is not None
