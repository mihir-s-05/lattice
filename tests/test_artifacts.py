"""
Tests for lattice.artifacts module.
"""
import pytest
import os
import json


class TestSha256Bytes:
    """Tests for sha256_bytes function."""
    
    def test_returns_hex_string(self):
        from lattice.artifacts import sha256_bytes
        result = sha256_bytes(b"hello")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex is 64 chars
    
    def test_consistent_hash(self):
        from lattice.artifacts import sha256_bytes
        result1 = sha256_bytes(b"test data")
        result2 = sha256_bytes(b"test data")
        assert result1 == result2
    
    def test_different_data_different_hash(self):
        from lattice.artifacts import sha256_bytes
        result1 = sha256_bytes(b"data1")
        result2 = sha256_bytes(b"data2")
        assert result1 != result2
    
    def test_empty_bytes(self):
        from lattice.artifacts import sha256_bytes
        result = sha256_bytes(b"")
        assert isinstance(result, str)
        assert len(result) == 64


class TestEnsureDir:
    """Tests for ensure_dir function."""
    
    def test_creates_directory(self, tmp_path):
        from lattice.artifacts import ensure_dir
        new_dir = str(tmp_path / "new_dir")
        ensure_dir(new_dir)
        assert os.path.isdir(new_dir)
    
    def test_nested_directories(self, tmp_path):
        from lattice.artifacts import ensure_dir
        nested = str(tmp_path / "a" / "b" / "c")
        ensure_dir(nested)
        assert os.path.isdir(nested)
    
    def test_existing_directory_ok(self, tmp_path):
        from lattice.artifacts import ensure_dir
        existing = str(tmp_path / "existing")
        os.makedirs(existing)
        ensure_dir(existing)
        assert os.path.isdir(existing)


class TestArtifactStore:
    """Tests for ArtifactStore class."""
    
    def test_init_creates_artifacts_dir(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        assert os.path.isdir(store.art_dir)
    
    def test_init_creates_index_file(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        assert os.path.exists(store.index_path)
    
    def test_add_text_creates_file(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        artifact = store.add_text("test.txt", "Hello World")
        
        file_path = os.path.join(tmp_run_dir, artifact.path)
        assert os.path.exists(file_path)
        with open(file_path) as f:
            assert f.read() == "Hello World"
    
    def test_add_text_returns_artifact(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore, Artifact
        store = ArtifactStore(tmp_run_dir)
        artifact = store.add_text("test.txt", "content")
        
        assert isinstance(artifact, Artifact)
        assert artifact.path.replace("\\", "/") == "artifacts/test.txt"
        assert artifact.mime == "text/plain"
        assert len(artifact.sha256) == 64
    
    def test_add_text_with_tags(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        artifact = store.add_text("test.txt", "content", tags=["tag1", "tag2"])
        
        assert artifact.tags == ["tag1", "tag2"]
    
    def test_add_text_with_meta(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        artifact = store.add_text("test.txt", "content", meta={"key": "value"})
        
        assert artifact.meta == {"key": "value"}
    
    def test_add_text_updates_index(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        store.add_text("test.txt", "content")
        
        with open(store.index_path) as f:
            index = json.load(f)
        
        assert len(index["artifacts"]) == 1
        assert index["artifacts"][0]["path"].replace("\\", "/") == "artifacts/test.txt"
    
    def test_add_text_replaces_same_path(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        store.add_text("test.txt", "first content")
        store.add_text("test.txt", "second content")
        
        with open(store.index_path) as f:
            index = json.load(f)
        
        assert len(index["artifacts"]) == 1
    
    def test_add_text_nested_path(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        artifact = store.add_text("subdir/nested/test.txt", "content")
        
        file_path = os.path.join(tmp_run_dir, artifact.path)
        assert os.path.exists(file_path)

    def test_add_text_rejects_traversal(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        with pytest.raises(ValueError):
            store.add_text("../escape.txt", "nope")
        with pytest.raises(ValueError):
            store.add_text("artifacts/../../escape.txt", "nope")
    
    def test_list_returns_all_artifacts(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        store.add_text("file1.txt", "content1")
        store.add_text("file2.txt", "content2")
        
        artifacts = store.list()
        assert len(artifacts) == 2
    
    def test_list_empty_store(self, tmp_run_dir):
        from lattice.artifacts import ArtifactStore
        store = ArtifactStore(tmp_run_dir)
        artifacts = store.list()
        assert len(artifacts) == 0
