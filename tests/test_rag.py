"""
Tests for lattice.rag module.
"""
import pytest
import os


class TestTokenize:
    """Tests for tokenize function."""
    
    def test_basic_tokenization(self):
        from lattice.rag import tokenize
        result = tokenize("Hello World")
        assert result == ["hello", "world"]
    
    def test_handles_punctuation(self):
        from lattice.rag import tokenize
        result = tokenize("Hello, World! How are you?")
        assert result == ["hello", "world", "how", "are", "you"]
    
    def test_handles_numbers(self):
        from lattice.rag import tokenize
        result = tokenize("Python 3.9 and version 2.0")
        assert result == ["python", "3", "9", "and", "version", "2", "0"]
    
    def test_handles_underscores(self):
        from lattice.rag import tokenize
        result = tokenize("hello_world function_name")
        assert result == ["hello_world", "function_name"]
    
    def test_empty_string(self):
        from lattice.rag import tokenize
        result = tokenize("")
        assert result == []


class TestRagIndex:
    """Tests for RagIndex class."""
    
    def test_init_creates_empty_index(self, tmp_run_dir):
        from lattice.rag import RagIndex
        rag = RagIndex(tmp_run_dir)
        assert rag.docs == {}
        assert rag.vocab == {}
    
    def test_ingest_text_adds_document(self, tmp_run_dir):
        from lattice.rag import RagIndex
        rag = RagIndex(tmp_run_dir)
        rag.ingest_text("doc1", "Hello world this is a test document", "/path/to/doc")
        assert "doc1" in rag.docs
        assert rag.docs["doc1"]["path"] == "/path/to/doc"
    
    def test_ingest_text_creates_tokens(self, tmp_run_dir):
        from lattice.rag import RagIndex
        rag = RagIndex(tmp_run_dir)
        rag.ingest_text("doc1", "Hello world", "/path/to/doc")
        assert "hello" in rag.docs["doc1"]["tokens"]
        assert "world" in rag.docs["doc1"]["tokens"]
    
    def test_ingest_text_saves_snippet(self, tmp_run_dir):
        from lattice.rag import RagIndex
        rag = RagIndex(tmp_run_dir)
        text = "Hello world this is a test"
        rag.ingest_text("doc1", text, "/path/to/doc")
        assert rag.docs["doc1"]["snippet"] == text
    
    def test_search_returns_matching_docs(self, tmp_run_dir):
        from lattice.rag import RagIndex
        rag = RagIndex(tmp_run_dir)
        rag.ingest_text("doc1", "Python programming language", "/doc1")
        rag.ingest_text("doc2", "JavaScript web development", "/doc2")
        results = rag.search("Python programming")
        assert len(results) > 0
        assert results[0]["doc_id"] == "doc1"
    
    def test_search_returns_empty_for_no_match(self, tmp_run_dir):
        from lattice.rag import RagIndex
        rag = RagIndex(tmp_run_dir)
        rag.ingest_text("doc1", "Python programming", "/doc1")
        results = rag.search("xyznonexistent")
        assert len(results) == 0
    
    def test_search_respects_top_k(self, tmp_run_dir):
        from lattice.rag import RagIndex
        rag = RagIndex(tmp_run_dir)
        for i in range(10):
            rag.ingest_text(f"doc{i}", f"test document number {i}", f"/doc{i}")
        results = rag.search("document", top_k=3)
        assert len(results) <= 3
    
    def test_ingest_file_reads_content(self, tmp_run_dir):
        from lattice.rag import RagIndex
        test_file = os.path.join(tmp_run_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("This is test file content for RAG indexing")
        
        rag = RagIndex(tmp_run_dir)
        rag.ingest_file(test_file, "testdoc")
        
        assert "testdoc" in rag.docs
        assert "test" in rag.docs["testdoc"]["tokens"]

    def test_search_where_filters_by_tags_and_kind(self, tmp_run_dir):
        from lattice.rag import RagIndex

        rag = RagIndex(tmp_run_dir)
        rag.ingest_text("hud1", "frobnicate alpha", "/huddle.md", tags=["huddle", "transcript"], meta={"kind": "HuddleTranscript"})
        rag.ingest_text("doc2", "frobnicate beta", "/readme.md", tags=["docs"], meta={"kind": "Readme"})

        hits = rag.search("frobnicate", top_k=10, where={"tags_any": ["huddle"]})
        assert any(h.get("doc_id") == "hud1" for h in hits)
        assert not any(h.get("doc_id") == "doc2" for h in hits)

        sem_hits = rag.search_semantic("frobnicate", top_k=10, where={"kind": "HuddleTranscript"})
        assert any(h.get("doc_id") == "hud1" for h in sem_hits)


class TestCosine:
    """Tests for cosine similarity calculation."""
    
    def test_identical_vectors(self, tmp_run_dir):
        from lattice.rag import RagIndex
        rag = RagIndex(tmp_run_dir)
        vec = {0: 1.0, 1: 2.0, 2: 3.0}
        result = rag._cosine(vec, vec)
        assert abs(result - 1.0) < 0.0001
    
    def test_orthogonal_vectors(self, tmp_run_dir):
        from lattice.rag import RagIndex
        rag = RagIndex(tmp_run_dir)
        vec_a = {0: 1.0}
        vec_b = {1: 1.0}
        result = rag._cosine(vec_a, vec_b)
        assert result == 0.0
    
    def test_empty_vectors(self, tmp_run_dir):
        from lattice.rag import RagIndex
        rag = RagIndex(tmp_run_dir)
        result = rag._cosine({}, {})
        assert result == 0.0
