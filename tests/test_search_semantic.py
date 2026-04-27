"""Tests for search.semantic.SemanticSearch."""
import networkx as nx
import pytest

from search.semantic import SemanticSearch


class FakeEmbedder:
    """Minimal embedder stub returning fixed vectors based on the text."""

    def embed_single(self, text):
        # Map known queries to specific vectors
        if "alpha" in text:
            return [1.0, 0.0]
        if "beta" in text:
            return [0.0, 1.0]
        return [1.0, 1.0]


@pytest.fixture
def graph_with_embeddings():
    g = nx.DiGraph()
    g.add_node(
        "a", name="add", type="function", path="x.py", line_start=1, line_end=2,
        embedding=[1.0, 0.0],
    )
    g.add_node(
        "b", name="Bee", type="class", path="y.py", line_start=10, line_end=20,
        embedding=[0.0, 1.0],
    )
    g.add_node(
        "c", name="cee", type="function", path="z.py", line_start=5, line_end=6,
        embedding=[1.0, 1.0],
    )
    g.add_node("noemb", name="no", type="function", path="q.py", line_start=1, line_end=1)
    return g


class TestSemanticSearchHasEmbeddings:
    def test_true_when_embeddings_present(self, graph_with_embeddings):
        s = SemanticSearch(graph_with_embeddings, FakeEmbedder())
        assert s.has_embeddings() is True

    def test_false_when_no_embeddings(self):
        g = nx.DiGraph()
        g.add_node("a", name="a", type="function")
        s = SemanticSearch(g, FakeEmbedder())
        assert s.has_embeddings() is False


class TestSemanticSearchSearch:
    def test_returns_top_match(self, graph_with_embeddings):
        s = SemanticSearch(graph_with_embeddings, FakeEmbedder())
        results = s.search("alpha", top_k=3)
        assert results[0]["id"] == "a"
        assert results[0]["score"] == pytest.approx(1.0)

    def test_skips_nodes_without_embedding(self, graph_with_embeddings):
        s = SemanticSearch(graph_with_embeddings, FakeEmbedder())
        ids = [r["id"] for r in s.search("alpha", top_k=10)]
        assert "noemb" not in ids

    def test_filter_by_node_type(self, graph_with_embeddings):
        s = SemanticSearch(graph_with_embeddings, FakeEmbedder())
        results = s.search("anything", top_k=10, node_type="class")
        assert all(r["type"] == "class" for r in results)
        assert {r["id"] for r in results} == {"b"}

    def test_results_sorted_by_score_desc(self, graph_with_embeddings):
        s = SemanticSearch(graph_with_embeddings, FakeEmbedder())
        results = s.search("alpha", top_k=10)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_top_k_limits_results(self, graph_with_embeddings):
        s = SemanticSearch(graph_with_embeddings, FakeEmbedder())
        results = s.search("alpha", top_k=1)
        assert len(results) == 1

    def test_zero_norm_returns_zero(self, graph_with_embeddings):
        """Cosine similarity: if either vector is all zeros, score is 0."""
        graph_with_embeddings.add_node("z", name="z", type="function", embedding=[0.0, 0.0])
        s = SemanticSearch(graph_with_embeddings, FakeEmbedder())
        results = s.search("alpha", top_k=10)
        z_result = next(r for r in results if r["id"] == "z")
        assert z_result["score"] == 0.0
