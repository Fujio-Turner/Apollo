# SPDX-License-Identifier: BUSL-1.1
"""CLI integration tests for the new `search --expand` flags (Phase B).

Drives :func:`main.cmd_search` directly rather than via subprocess so
the test stays fast and avoids the model-download cost of the real
embedder.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import networkx as nx
import pytest

# Ensure project root is on sys.path so `import main` works even when the
# tests are run from a working directory other than the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeEmbedder:
    def embed_single(self, text):
        if "alpha" in text:
            return [1.0, 0.0]
        return [1.0, 1.0]


@pytest.fixture
def indexed_project(tmp_path, monkeypatch):
    """Save a JSON-store index with embedded nodes + caller edges.

    Returns the (index_path, expected_seed_id, expected_caller_id) tuple.
    """
    from apollo.storage.json_store import JsonStore

    g = nx.DiGraph()
    g.add_node(
        "func::a.py::seed_fn", name="seed_fn", type="function",
        path="a.py", line_start=10, line_end=20, embedding=[1.0, 0.0],
    )
    g.add_node(
        "func::a.py::caller_fn", name="caller_fn", type="function",
        path="a.py", line_start=1, line_end=5, embedding=[0.1, 0.1],
    )
    g.add_edge("func::a.py::caller_fn", "func::a.py::seed_fn", type="calls")

    index_path = tmp_path / "index.json"
    JsonStore(str(index_path)).save(g)

    # Patch the embedder *before* main imports it lazily inside cmd_search.
    import apollo.embeddings as emb_module
    monkeypatch.setattr(emb_module, "get_shared_embedder", lambda: _FakeEmbedder())

    return str(index_path), "func::a.py::seed_fn", "func::a.py::caller_fn"


def _ns(**overrides):
    """Build a Namespace mirroring the search subparser defaults."""
    defaults = dict(
        text="alpha", top=3, type=None, expand="none", depth=1,
        per_seed_cap=10, index=None, backend="json",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestCliFlatUnchanged:
    def test_flat_search_has_no_expansion_markers(self, indexed_project, capsys):
        index_path, seed_id, caller_id = indexed_project
        from main import cmd_search
        cmd_search(_ns(text="alpha", index=index_path, expand="none"))
        out = capsys.readouterr().out
        assert "seed_fn" in out
        # No expansion header / indented tree markers in flat mode.
        assert "--expand" not in out
        assert "←" not in out
        assert "→" not in out


class TestCliExpandedCallers:
    def test_expanded_callers_renders_indented_tree(self, indexed_project, capsys):
        index_path, seed_id, caller_id = indexed_project
        from main import cmd_search
        cmd_search(_ns(
            text="alpha", index=index_path,
            expand="callers", depth=1, top=3,
        ))
        out = capsys.readouterr().out

        # Seed appears.
        assert "seed_fn" in out
        # Expansion header recorded.
        assert "--expand=callers" in out
        # Caller appears indented under it with the in-arrow.
        assert "caller_fn" in out
        assert "←" in out
