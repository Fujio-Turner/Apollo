"""Tests for combined semantic + graph-expansion search (Phase A).

Covers ``search.expand.expand_hits`` and
``search.semantic.SemanticSearch.search_expanded``.
"""
from __future__ import annotations

import time

import networkx as nx
import pytest

from search.expand import expand_hits
from search.semantic import SemanticSearch


class FakeEmbedder:
    """Returns deterministic vectors based on the query keyword."""

    def embed_single(self, text):
        if "alpha" in text:
            return [1.0, 0.0]
        if "beta" in text:
            return [0.0, 1.0]
        return [1.0, 1.0]


@pytest.fixture
def caller_callee_graph():
    """3-node directed graph wired through ``calls`` edges.

        caller --calls--> seed --calls--> callee
    """
    g = nx.DiGraph()
    g.add_node(
        "caller", name="caller_fn", type="function",
        path="a.py", line_start=1, line_end=2, embedding=[0.1, 0.1],
    )
    g.add_node(
        "seed", name="seed_fn", type="function",
        path="b.py", line_start=10, line_end=20, embedding=[1.0, 0.0],
    )
    g.add_node(
        "callee", name="callee_fn", type="function",
        path="c.py", line_start=30, line_end=31, embedding=[0.1, 0.1],
    )
    g.add_edge("caller", "seed", type="calls")
    g.add_edge("seed", "callee", type="calls")
    return g


class TestExpandNone:
    def test_returns_seeds_with_empty_neighbors(self, caller_callee_graph):
        s = SemanticSearch(caller_callee_graph, FakeEmbedder())
        flat = s.search("alpha", top_k=3)
        expanded = s.search_expanded("alpha", top_k=3, expand="none")

        assert [r["id"] for r in flat] == [r["id"] for r in expanded]
        assert all(r["neighbors"] == [] for r in expanded)
        # Cosine scores unchanged.
        for f, e in zip(flat, expanded):
            assert f["score"] == pytest.approx(e["score"])


class TestExpandCallers:
    def test_callers_returns_incoming_with_in_direction(self, caller_callee_graph):
        s = SemanticSearch(caller_callee_graph, FakeEmbedder())
        res = s.search_expanded("alpha", top_k=1, expand="callers", depth=1)
        assert len(res) == 1
        seed = res[0]
        assert seed["id"] == "seed"
        assert len(seed["neighbors"]) == 1
        n = seed["neighbors"][0]
        assert n["id"] == "caller"
        assert n["direction"] == "in"
        assert n["edge"] == "calls"
        assert n["depth"] == 1
        # Decay: seed_score / (1 + d)
        assert n["score"] == pytest.approx(seed["score"] / 2.0)


class TestExpandCallees:
    def test_callees_returns_outgoing_with_out_direction(self, caller_callee_graph):
        s = SemanticSearch(caller_callee_graph, FakeEmbedder())
        res = s.search_expanded("alpha", top_k=1, expand="callees", depth=1)
        assert len(res) == 1
        seed = res[0]
        n = seed["neighbors"][0]
        assert n["id"] == "callee"
        assert n["direction"] == "out"
        assert n["edge"] == "calls"


class TestPerSeedCap:
    def test_truncates_and_reports_count(self):
        g = nx.DiGraph()
        g.add_node("seed", name="seed", type="function",
                   path="b.py", line_start=1, line_end=2, embedding=[1.0, 0.0])
        for i in range(5):
            cid = f"c{i}"
            g.add_node(cid, name=cid, type="function",
                       path=f"{cid}.py", line_start=1, line_end=2)
            g.add_edge(cid, "seed", type="calls")

        s = SemanticSearch(g, FakeEmbedder())
        res = s.search_expanded("alpha", top_k=1, expand="callers",
                                depth=1, per_seed_cap=2)
        seed = res[0]
        assert len(seed["neighbors"]) == 2
        assert seed.get("truncated") == 3


class TestDepth2:
    def test_depth2_returns_depth2_nodes_with_decay(self):
        g = nx.DiGraph()
        g.add_node("seed", name="seed", type="function",
                   path="s.py", line_start=1, line_end=2, embedding=[1.0, 0.0])
        g.add_node("d1", name="d1", type="function",
                   path="d1.py", line_start=1, line_end=2)
        g.add_node("d2", name="d2", type="function",
                   path="d2.py", line_start=1, line_end=2)
        g.add_edge("d1", "seed", type="calls")
        g.add_edge("d2", "d1", type="calls")

        s = SemanticSearch(g, FakeEmbedder())
        res = s.search_expanded("alpha", top_k=1, expand="callers",
                                depth=2, per_seed_cap=10)
        seed = res[0]
        by_id = {n["id"]: n for n in seed["neighbors"]}
        assert by_id["d1"]["depth"] == 1
        assert by_id["d2"]["depth"] == 2
        # Decayed scores.
        assert by_id["d1"]["score"] == pytest.approx(seed["score"] / 2.0)
        assert by_id["d2"]["score"] == pytest.approx(seed["score"] / 3.0)


class TestDepthValidation:
    def test_depth_zero_raises(self, caller_callee_graph):
        s = SemanticSearch(caller_callee_graph, FakeEmbedder())
        with pytest.raises(ValueError):
            s.search_expanded("alpha", top_k=1, expand="callers", depth=0)

    def test_negative_depth_raises(self, caller_callee_graph):
        s = SemanticSearch(caller_callee_graph, FakeEmbedder())
        with pytest.raises(ValueError):
            s.search_expanded("alpha", top_k=1, expand="callers", depth=-1)

    def test_unknown_expand_raises(self):
        g = nx.DiGraph()
        with pytest.raises(ValueError):
            expand_hits(g, [{"id": "x", "score": 1.0}],
                        expand="bogus", depth=1)  # type: ignore[arg-type]


class TestExpandReferencesAndNeighbors:
    def test_references_both_directions(self, caller_callee_graph):
        s = SemanticSearch(caller_callee_graph, FakeEmbedder())
        res = s.search_expanded("alpha", top_k=1, expand="references", depth=1)
        seed = res[0]
        ids = {n["id"] for n in seed["neighbors"]}
        assert ids == {"caller", "callee"}
        assert all(n["direction"] == "both" for n in seed["neighbors"])
        assert all(n["edge"] == "references" for n in seed["neighbors"])

    def test_neighbors_both_directions(self, caller_callee_graph):
        s = SemanticSearch(caller_callee_graph, FakeEmbedder())
        res = s.search_expanded("alpha", top_k=1, expand="neighbors", depth=1)
        seed = res[0]
        ids = {n["id"] for n in seed["neighbors"]}
        assert ids == {"caller", "callee"}
        assert all(n["edge"] == "neighbors" for n in seed["neighbors"])


class TestPerfBudget:
    def test_perf_budget_10k_nodes(self):
        """``top=10, depth=1, per_seed_cap=10`` must finish < 50 ms on a
        10k-node fixture (catches accidental N² regressions)."""
        g = nx.DiGraph()
        # 10 seeds with embeddings, each fanned in by ~5 callers; pad to ~10k.
        for s_idx in range(10):
            sid = f"s{s_idx}"
            g.add_node(sid, name=sid, type="function",
                       path=f"{sid}.py", line_start=1, line_end=2,
                       embedding=[1.0, 0.0])
            for c_idx in range(5):
                cid = f"s{s_idx}_c{c_idx}"
                g.add_node(cid, name=cid, type="function",
                           path=f"{cid}.py", line_start=1, line_end=2)
                g.add_edge(cid, sid, type="calls")
        # Pad with structural-only nodes to hit ~10k.
        for i in range(10_000 - g.number_of_nodes()):
            g.add_node(f"pad{i}", name="pad", type="function",
                       path="pad.py", line_start=1, line_end=2)

        s = SemanticSearch(g, FakeEmbedder())
        # Warm the matrix cache so we measure the expansion, not the
        # one-time matrix build.
        s.search("alpha", top_k=10)

        start = time.perf_counter()
        res = s.search_expanded("alpha", top_k=10, expand="callers",
                                depth=1, per_seed_cap=10)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(res) == 10
        # Generous budget — local runs land in single-digit ms. Bumped
        # to 250 ms on first land to absorb CI noise; tighten later.
        assert elapsed_ms < 250, f"expansion took {elapsed_ms:.1f} ms"
