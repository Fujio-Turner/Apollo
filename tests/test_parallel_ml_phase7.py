"""Phase 7 (PLAN_INDEX_MEMORY_AND_CONCURRENCY) regression tests.

Pins:

* ``run_all_passes(parallel=True)`` returns the same per-pass summary
  shape as ``parallel=False`` on the same input.
* A single failing pass does not poison the rest of the summary —
  the failed pass is recorded as ``{ml_available: False, reason: …}``
  exactly like the sequential per-pass try/except path.
* Empty / no-eligible-passes inputs produce ``{}`` (don't spin up a
  thread pool with zero work).
* The parallel orchestrator actually uses multiple threads (worker
  thread names contain the ``apollo-ml-pass`` prefix).
"""
from __future__ import annotations

import threading
from typing import Set

import networkx as nx
import numpy as np

import ml.passes as passes
from ml.passes import run_all_passes


def _tiny_graph_with_embeddings(n_nodes: int = 12) -> nx.DiGraph:
    """A small graph with synthetic embeddings — enough for the
    pass functions to exercise their happy path without needing
    UMAP / HDBSCAN to converge meaningfully."""
    g = nx.DiGraph()
    for i in range(n_nodes):
        nid = f"func::a.py::f{i}"
        g.add_node(
            nid,
            type="function",
            name=f"f{i}",
            path="a.py",
            line_start=i,
            line_end=i + 1,
            docstring=f"Function {i} docstring",
            embedding=np.random.RandomState(i).rand(4).astype(np.float32),
            embedding_hash=f"h{i}",
        )
    # Some edges so centrality / communities have something to do.
    for i in range(n_nodes - 1):
        g.add_edge(f"func::a.py::f{i}", f"func::a.py::f{i+1}", type="calls")
    return g


# ─────────────────────────────────────────────────────────────────────
# Equivalence: parallel vs sequential
# ─────────────────────────────────────────────────────────────────────
def test_parallel_summary_shape_matches_sequential():
    g_seq = _tiny_graph_with_embeddings()
    g_par = _tiny_graph_with_embeddings()  # fresh copy

    # Restrict to passes that don't need external libs to keep the test
    # deterministic across CI environments (centrality + communities +
    # outliers all use stdlib + networkx).
    wanted = {"centrality", "communities", "outliers"}

    seq = run_all_passes(g_seq, include=wanted, parallel=False)
    par = run_all_passes(g_par, include=wanted, parallel=True)

    assert set(seq.keys()) == set(par.keys()) == wanted
    # Per-pass: both should have ml_available True (these passes only
    # depend on the in-graph structure we provide).
    for name in wanted:
        assert seq[name].get("ml_available") == par[name].get("ml_available")


def test_parallel_propagates_per_node_writes_for_all_passes():
    """Per-node attrs written by each pass must land identically on
    every node when run via the parallel orchestrator."""
    g_par = _tiny_graph_with_embeddings()
    run_all_passes(
        g_par,
        include={"centrality", "communities", "outliers"},
        parallel=True,
    )
    # Every node should have all three new attrs.
    for nid, data in g_par.nodes(data=True):
        assert "pagerank" in data, f"missing pagerank on {nid}"
        assert "community_id" in data, f"missing community_id on {nid}"
        assert "outlier_score" in data, f"missing outlier_score on {nid}"


# ─────────────────────────────────────────────────────────────────────
# Failure isolation
# ─────────────────────────────────────────────────────────────────────
def test_failed_pass_is_recorded_not_raised(monkeypatch):
    g = _tiny_graph_with_embeddings()

    def _broken_centrality(graph):
        raise RuntimeError("centrality exploded")

    monkeypatch.setattr(passes, "pass_centrality", _broken_centrality)
    summary = run_all_passes(
        g,
        include={"centrality", "communities"},
        parallel=True,
    )
    assert summary["centrality"] == {
        "ml_available": False, "reason": "centrality exploded",
    }
    # The other pass still ran cleanly.
    assert summary["communities"].get("ml_available") is True


# ─────────────────────────────────────────────────────────────────────
# Boundary cases
# ─────────────────────────────────────────────────────────────────────
def test_include_with_no_known_passes_returns_empty_summary():
    """``run_all_passes`` treats an empty / falsy ``include`` as
    "run everything" (legacy behavior pinned by the sequential
    path). When the caller passes a non-empty include set that
    matches *no* known pass name, the orchestrator should produce
    an empty summary and skip thread-pool setup entirely."""
    g = _tiny_graph_with_embeddings()
    out = run_all_passes(g, include={"nonexistent_pass"}, parallel=True)
    assert out == {}


# ─────────────────────────────────────────────────────────────────────
# Concurrency proof
# ─────────────────────────────────────────────────────────────────────
def test_parallel_actually_uses_multiple_threads(monkeypatch):
    """Confirm the orchestrator submits work to threads rather than
    running everything on the calling thread. We instrument every
    pass to record its current thread name; the parallel orchestrator
    should produce names like 'apollo-ml-pass_*' (the executor's
    prefix)."""
    g = _tiny_graph_with_embeddings()
    seen_thread_names: Set[str] = set()
    lock = threading.Lock()

    def _record(orig):
        def wrapper(*args, **kwargs):
            with lock:
                seen_thread_names.add(threading.current_thread().name)
            return orig(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(passes, "pass_centrality",
                        _record(passes.pass_centrality))
    monkeypatch.setattr(passes, "pass_communities",
                        _record(passes.pass_communities))
    monkeypatch.setattr(passes, "pass_outliers",
                        _record(passes.pass_outliers))

    run_all_passes(
        g,
        include={"centrality", "communities", "outliers"},
        parallel=True,
    )
    # At least one of the threads must have been a pool worker
    # (named via ``thread_name_prefix='apollo-ml-pass'``).
    assert any(name.startswith("apollo-ml-pass") for name in seen_thread_names), (
        f"expected at least one pool-worker thread, saw: {seen_thread_names}"
    )
