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
        assert "hello" in result
        assert "world" in result
    
    def test_handles_punctuation(self):
        from lattice.rag import tokenize
        result = tokenize("Hello, World! How are you?")
        assert "hello" in result
        assert "world" in result
        assert "how" in result
    
    def test_handles_numbers(self):
        from lattice.rag import tokenize
        result = tokenize("Python 3.9 and version 2.0")
        assert "python" in result
        assert "3.9" in result
        assert "version" in result
        assert "2.0" in result
    
    def test_handles_underscores(self):
        from lattice.rag import tokenize
        result = tokenize("hello_world function_name")
        assert "hello_world" in result
        assert "function_name" in result
    
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
        assert rag.df == {}
    
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


class TestCodeAwareTokenize:
    def test_splits_dotted_identifiers(self):
        from lattice.rag import tokenize

        toks = tokenize("self.cfg.base_url app.route")
        assert "cfg" in toks
        assert "base_url" in toks
        assert "app.route" in toks
        assert "route" in toks

    def test_splits_camel_and_snake(self):
        from lattice.rag import tokenize

        toks = tokenize("handleRequest handle_request")
        assert "handlerequest" in toks
        assert "handle" in toks
        assert "request" in toks
        assert "handle_request" in toks

    def test_filters_common_code_stopwords(self):
        from lattice.rag import tokenize

        toks = tokenize("def handler(self): return self.value")
        assert "handler" in toks
        assert "self" not in toks
        assert "return" not in toks
