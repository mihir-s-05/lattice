import os
import json
import tempfile
import pytest
from unittest.mock import MagicMock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def tmp_run_dir(tmp_path):
    run_dir = tmp_path / "test_run"
    run_dir.mkdir()
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir()
    index_file = artifacts_dir / "index.json"
    index_file.write_text('{"artifacts": []}')
    return str(run_dir)


@pytest.fixture
def mock_logger():
    logger = MagicMock()
    logger.log = MagicMock()
    logger.path = MagicMock(return_value="/fake/path/run.jsonl")
    return logger


@pytest.fixture
def mock_provider_config():
    from lattice.config import ProviderConfig
    return ProviderConfig(
        name="test_provider",
        base_url="http://localhost:1234/v1",
        api_key="test_key",
        model="test-model"
    )


@pytest.fixture
def mock_run_config(mock_provider_config):
    """Create a mock RunConfig."""
    from lattice.config import RunConfig, SystemLimits, RagConfig, ExecutionConfig
    return RunConfig(
        run_id="test-run-123",
        providers={"test": mock_provider_config},
        router_provider_order=["test"],
        agent_provider_order=["test"],
        limits=SystemLimits(),
        rag=RagConfig(),
        execution=ExecutionConfig()
    )
