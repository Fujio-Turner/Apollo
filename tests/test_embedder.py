"""Tests for embeddings.embedder.Embedder.

Mocks SentenceTransformer to avoid the heavy dependency at test time.
"""
import sys
import types

import networkx as nx
import numpy as np
import pytest

from embeddings.embedder import Embedder


class FakeST:
    """Minimal SentenceTransformer stub used by tests."""

    def __init__(self, model_name):
        self.model_name = model_name

    def encode(self, texts, batch_size=256, show_progress_bar=False):
        # Return a deterministic vector per text
        return np.array([[float(len(t)), 1.0, 0.0] for t in texts])


@pytest.fixture
def fake_st_module(monkeypatch):
    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
    yield


class TestEmbedTexts:
    def test_returns_list_of_lists(self, fake_st_module):
        e = Embedder()
        out = e.embed_texts(["hello", "world!"])
        assert isinstance(out, list)
        assert len(out) == 2
        assert all(isinstance(v, list) for v in out)
        assert out[0][0] == 5.0  # len("hello")
        assert out[1][0] == 6.0  # len("world!")

    def test_caches_model_between_calls(self, fake_st_module):
        e = Embedder()
        e.embed_texts(["a"])
        first = e._model
        e.embed_texts(["b"])
        assert e._model is first


class TestEmbedSingle:
    def test_returns_single_vector(self, fake_st_module):
        e = Embedder()
        vec = e.embed_single("hi")
        assert isinstance(vec, list)
        assert vec[0] == 2.0


class TestEmbedGraph:
    def test_embeds_eligible_nodes(self, fake_st_module):
        g = nx.DiGraph()
        long_src = "x" * 50
        g.add_node("f", type="function", source=long_src)
        g.add_node("c", type="class", source=long_src)
        g.add_node("doc", type="document", source=long_src)

        Embedder().embed_graph(g)
        for nid in ("f", "c", "doc"):
            assert "embedding" in g.nodes[nid]

    def test_skips_short_source(self, fake_st_module):
        g = nx.DiGraph()
        g.add_node("short", type="function", source="too short")
        Embedder().embed_graph(g)
        assert "embedding" not in g.nodes["short"]

    def test_skips_unknown_type(self, fake_st_module):
        g = nx.DiGraph()
        g.add_node("module", type="module", source="x" * 100)
        Embedder().embed_graph(g)
        assert "embedding" not in g.nodes["module"]

    def test_skips_node_without_source(self, fake_st_module):
        g = nx.DiGraph()
        g.add_node("nofield", type="function")
        Embedder().embed_graph(g)
        assert "embedding" not in g.nodes["nofield"]

    def test_no_op_when_nothing_eligible(self, fake_st_module):
        g = nx.DiGraph()
        g.add_node("a", type="module", source="x" * 100)
        # Should not load model or raise
        Embedder().embed_graph(g)


class TestImportError:
    def test_missing_dependency_raises(self, monkeypatch):
        # Force import to fail
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        e = Embedder()
        with pytest.raises(ImportError):
            e._load_model()
