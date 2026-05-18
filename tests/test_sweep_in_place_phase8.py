# SPDX-License-Identifier: BUSL-1.1
"""Phase 8 (PLAN_INDEX_MEMORY_AND_CONCURRENCY) regression tests.

Pins:

* ``ResolveFullStrategy.run`` mutates ``graph_in`` in place — the
  returned ``graph_out`` is the **same object** as ``graph_in``
  (proves the ``nx.DiGraph(graph_in)`` deep-copy is gone).
* ML attrs that lived on nodes about to be re-parsed (embeddings,
  pagerank, cluster_id, umap_xy, …) survive the re-add via the
  strategy's snapshot-and-restore (no longer the
  ``_PRESERVED_NODE_ATTRS`` carry-over in ``reindex_service``).
* Per-graph sidecars (``ml_clusters`` / ``ml_topics`` /
  ``ml_dead_code``) survive the sweep automatically because the
  strategy never wipes ``graph.graph``.
* The reconstructed diff still reports correct ``edges_added`` /
  ``edges_removed`` counts despite the in-place mutation.
* Nodes belonging to *unchanged* files (i.e. the strategy didn't
  re-parse them) keep all their original attrs untouched.
"""
from __future__ import annotations

from pathlib import Path

import networkx as nx
import numpy as np

from graph.builder import GraphBuilder
from graph.incremental import ResolveFullStrategy


_SAMPLE_PY = '''\
def alpha():
    return 1


def beta():
    return alpha() + 1
'''


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text(_SAMPLE_PY)
    return tmp_path


def _build_with_ml_attrs(tmp_path: Path) -> nx.DiGraph:
    """Build a graph and stamp synthetic ML attrs on every node so we
    can detect whether they survive the sweep."""
    g = GraphBuilder().build(str(tmp_path))
    for nid, data in g.nodes(data=True):
        data["embedding"] = np.array([0.1, 0.2], dtype=np.float32)
        data["embedding_hash"] = f"hash:{nid}"
        data["pagerank"] = 0.5
        data["cluster_id"] = 42
        data["umap_xy"] = [1.0, 2.0]
        data["community_id"] = 7
    g.graph["ml_clusters"] = {1: {"label": "x"}}
    g.graph["ml_topics"] = {0: {"label": "topic-zero"}}
    g.graph["ml_dead_code"] = {"summary": "nothing dead"}
    return g


# ─────────────────────────────────────────────────────────────────────
# In-place mutation invariant
# ─────────────────────────────────────────────────────────────────────
def test_resolve_full_mutates_graph_in_place(tmp_path: Path):
    _make_project(tmp_path)
    g_in = _build_with_ml_attrs(tmp_path)

    result = ResolveFullStrategy().run(
        root_dir=str(tmp_path),
        graph_in=g_in,
        prev_hashes={},  # forces re-parse
    )

    # The exact same object — no deep copy.
    assert result.graph_out is g_in, (
        "ResolveFullStrategy.run should mutate graph_in in place "
        "(Phase 8 — nx.DiGraph(graph_in) deep-copy is removed)"
    )


# ─────────────────────────────────────────────────────────────────────
# ML-attr preservation — handled now inside the strategy
# ─────────────────────────────────────────────────────────────────────
def test_resolve_full_preserves_node_ml_attrs(tmp_path: Path):
    _make_project(tmp_path)
    g_in = _build_with_ml_attrs(tmp_path)
    func_id = "func::a.py::alpha"
    assert func_id in g_in.nodes
    original_embedding = g_in.nodes[func_id]["embedding"]

    result = ResolveFullStrategy().run(
        root_dir=str(tmp_path),
        graph_in=g_in,
        prev_hashes={},
    )
    g_out = result.graph_out

    # The re-parsed alpha function should still exist...
    assert func_id in g_out.nodes
    # ...and still carry its ML attrs (snapshot/restore inside the
    # strategy, no carry-over loop in reindex_service required).
    data = g_out.nodes[func_id]
    np.testing.assert_array_equal(data["embedding"], original_embedding)
    assert data["embedding_hash"] == f"hash:{func_id}"
    assert data["pagerank"] == 0.5
    assert data["cluster_id"] == 42
    assert data["umap_xy"] == [1.0, 2.0]
    assert data["community_id"] == 7


def test_resolve_full_preserves_graph_level_ml_sidecars(tmp_path: Path):
    _make_project(tmp_path)
    g_in = _build_with_ml_attrs(tmp_path)

    result = ResolveFullStrategy().run(
        root_dir=str(tmp_path),
        graph_in=g_in,
        prev_hashes={},
    )
    g_out = result.graph_out

    # In-place mutation never wipes graph.graph, so the ML sidecars
    # are still here. (The carry-over loop in reindex_service that
    # used to do this is gone.)
    assert g_out.graph["ml_clusters"] == {1: {"label": "x"}}
    assert g_out.graph["ml_topics"] == {0: {"label": "topic-zero"}}
    assert g_out.graph["ml_dead_code"] == {"summary": "nothing dead"}


# ─────────────────────────────────────────────────────────────────────
# Diff still accurate
# ─────────────────────────────────────────────────────────────────────
def test_resolve_full_diff_reflects_added_function(tmp_path: Path):
    """If we add a brand-new function to a file, the diff should
    report it under nodes_added."""
    _make_project(tmp_path)
    g_in = _build_with_ml_attrs(tmp_path)

    # Mutate the source file to add a third function.
    new_py = _SAMPLE_PY + "\n\ndef gamma():\n    return 99\n"
    (tmp_path / "a.py").write_text(new_py)

    result = ResolveFullStrategy().run(
        root_dir=str(tmp_path),
        graph_in=g_in,
        prev_hashes={},  # force re-parse
    )

    gamma_id = "func::a.py::gamma"
    assert gamma_id in result.diff.nodes_added, (
        f"diff.nodes_added = {result.diff.nodes_added!r}, "
        "expected gamma in the added list"
    )
    assert gamma_id in result.graph_out.nodes


def test_resolve_full_diff_reflects_removed_function(tmp_path: Path):
    """If we delete a function from a file, the diff should report
    it under nodes_removed."""
    _make_project(tmp_path)
    g_in = _build_with_ml_attrs(tmp_path)
    assert "func::a.py::beta" in g_in.nodes

    # Mutate the source file to drop beta.
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")

    result = ResolveFullStrategy().run(
        root_dir=str(tmp_path),
        graph_in=g_in,
        prev_hashes={},  # force re-parse
    )

    assert "func::a.py::beta" in result.diff.nodes_removed
    assert "func::a.py::beta" not in result.graph_out.nodes


# ─────────────────────────────────────────────────────────────────────
# Unchanged-file nodes are untouched
# ─────────────────────────────────────────────────────────────────────
def test_unchanged_file_nodes_keep_attrs(tmp_path: Path):
    """If a file's content hash matches prev_hashes, its nodes should
    not be re-parsed and their ML attrs should be byte-identical."""
    import hashlib

    _make_project(tmp_path)
    g_in = _build_with_ml_attrs(tmp_path)
    func_id = "func::a.py::alpha"
    original_attrs = dict(g_in.nodes[func_id])

    # Build a prev_hashes entry that matches the file on disk so the
    # strategy skips re-parsing entirely.
    content = (tmp_path / "a.py").read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    st = (tmp_path / "a.py").stat()
    prev_hashes = {"a.py": {"sha256": sha, "mtime_ns": st.st_mtime_ns,
                            "size": st.st_size}}

    result = ResolveFullStrategy().run(
        root_dir=str(tmp_path),
        graph_in=g_in,
        prev_hashes=prev_hashes,
    )
    # No files re-parsed — the file's nodes should be byte-identical.
    assert result.stats.files_parsed == 0
    data = result.graph_out.nodes[func_id]
    np.testing.assert_array_equal(
        data["embedding"], original_attrs["embedding"],
    )
    assert data["pagerank"] == original_attrs["pagerank"]
    assert data["cluster_id"] == original_attrs["cluster_id"]
