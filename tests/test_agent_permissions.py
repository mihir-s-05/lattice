import os
import pytest


def test_agent_write_policy_blocks_cross_scope(tmp_path, mock_run_config, mock_logger):
    from lattice.artifacts import ArtifactStore
    from lattice.rag import RagIndex
    from lattice.agents import BaseAgent

    class DummyAgent(BaseAgent):
        def plan(self, step_or_goal, context):
            raise NotImplementedError

        def act(self, inputs):
            return []

    run_dir = tmp_path / "run"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "artifacts" / "index.json").write_text('{"artifacts": []}', encoding="utf-8")
    store = ArtifactStore(str(run_dir))
    rag = RagIndex(str(run_dir))

    ws = tmp_path / "ws"
    ws.mkdir()
    ag = DummyAgent("backend", mock_run_config, mock_logger, store, rag, workspace_root=str(ws))

    ag.set_write_policy(allow_globs=["backend/**"], deny_globs=[])

    with pytest.raises(ValueError):
        ag._write_artifact("contracts/openapi.yaml", "openapi: 3.0.0\npaths: {}\n")

    ref = ag._write_artifact("backend/README.md", "ok")
    assert os.path.exists(ref.path)
