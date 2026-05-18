# SPDX-License-Identifier: BUSL-1.1
"""Phase 5 (PLAN_INDEX_MEMORY_AND_CONCURRENCY) regression tests.

Asserts that ``JsonStore.save`` now streams the document directly to
the file handle instead of building an intermediate ``nodes={...},
edges={...}`` dict and then ``orjson.dumps`` -ing the whole thing.

Round-trip coverage matters most here — the hand-rolled JSON writer
is the kind of thing that goes subtly wrong (missing comma, trailing
brace) and the bug doesn't show up until load time.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import networkx as nx
import numpy as np

from storage.json_store import JsonStore, _stream_save


def _sample_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_node("file::a.py", type="file", name="a.py", path="a.py")
    g.add_node("func::a.py::foo", type="function", name="foo", path="a.py",
               line_start=1, line_end=5,
               embedding=np.array([0.1, 0.2, 0.3], dtype=np.float32),
               embedding_hash="abc")
    g.add_node("func::a.py::bar", type="function", name="bar", path="a.py",
               line_start=10, line_end=15)
    g.add_node("file::b.py", type="file", name="b.py", path="b.py")
    g.add_edge("file::a.py", "func::a.py::foo", type="defines")
    g.add_edge("file::a.py", "func::a.py::bar", type="defines")
    g.add_edge("func::a.py::foo", "func::a.py::bar", type="calls",
               call_line=3)
    g.graph["ml_clusters"] = {1: {"label": "x"}, 2: {"label": "y"}}
    return g


def test_streaming_save_round_trip(tmp_path: Path):
    g = _sample_graph()
    path = tmp_path / "graph.json"
    JsonStore(str(path)).save(g)

    g2 = JsonStore(str(path)).load()
    assert set(g.nodes()) == set(g2.nodes())
    assert set(g.edges()) == set(g2.edges())
    # Embedding survived as a float32 ndarray (Phase 3 invariant).
    emb = g2.nodes["func::a.py::foo"]["embedding"]
    assert isinstance(emb, np.ndarray)
    assert emb.dtype == np.float32
    np.testing.assert_allclose(emb, [0.1, 0.2, 0.3], rtol=1e-6)

    # graph-level attrs survived too.
    assert "ml_clusters" in g2.graph
    assert set(g2.graph["ml_clusters"].keys()) == {"1", "2"}

    # Edge attrs survived.
    assert g2.edges["func::a.py::foo", "func::a.py::bar"]["type"] == "calls"
    assert g2.edges["func::a.py::foo", "func::a.py::bar"]["call_line"] == 3


def test_streaming_save_gzipped(tmp_path: Path):
    g = _sample_graph()
    path = tmp_path / "graph.json.gz"
    JsonStore(str(path)).save(g)
    # Confirm gzip magic and that the result is loadable.
    assert path.read_bytes()[:2] == b"\x1f\x8b"
    g2 = JsonStore(str(path)).load()
    assert set(g.nodes()) == set(g2.nodes())
    assert set(g.edges()) == set(g2.edges())


def test_streaming_save_does_not_mutate_live_graph(tmp_path: Path):
    """Plan §8 step 1: dict(attrs) copy was 'defensive but never needed'
    for orjson, but Phase 3's embedding-encoding step *does* mutate, so
    save MUST keep the per-node copy. This test pins that invariant."""
    g = _sample_graph()
    pre_emb = g.nodes["func::a.py::foo"]["embedding"]
    assert isinstance(pre_emb, np.ndarray)
    JsonStore(str(tmp_path / "g.json")).save(g)
    post_emb = g.nodes["func::a.py::foo"]["embedding"]
    # The live graph's embedding should still be the ndarray we put
    # there — not a base64 string.
    assert isinstance(post_emb, np.ndarray)
    np.testing.assert_array_equal(pre_emb, post_emb)
    # And no encoded sidecars leaked back into the live attrs dict.
    assert "embedding_b64" not in g.nodes["func::a.py::foo"]


def test_streaming_save_handles_empty_graph(tmp_path: Path):
    g = nx.DiGraph()
    path = tmp_path / "empty.json"
    JsonStore(str(path)).save(g)
    g2 = JsonStore(str(path)).load()
    assert g2.number_of_nodes() == 0
    assert g2.number_of_edges() == 0


def test_streaming_save_no_edges(tmp_path: Path):
    g = nx.DiGraph()
    g.add_node("a", type="file")
    g.add_node("b", type="file")
    path = tmp_path / "no_edges.json"
    JsonStore(str(path)).save(g)
    g2 = JsonStore(str(path)).load()
    assert set(g2.nodes()) == {"a", "b"}
    assert g2.number_of_edges() == 0


def test_streaming_writer_emits_valid_json(tmp_path: Path):
    """Hand-rolled JSON is fragile around trailing commas — verify the
    raw bytes round-trip through stdlib ``json`` (not just orjson)."""
    import io
    buf = io.BytesIO()
    g = _sample_graph()
    _stream_save(g, buf)
    raw = buf.getvalue()
    # stdlib json must accept it.
    parsed = json.loads(raw.decode("utf-8"))
    assert parsed["version"] == 2
    assert "func::a.py::foo" in parsed["nodes"]
    assert "func::a.py" in parsed["edges"] or "file::a.py" in parsed["edges"]
    # No trailing comma artifacts.
    assert b",}" not in raw and b",]" not in raw


def test_streaming_save_save_load_equivalent_to_legacy(tmp_path: Path):
    """Build a real graph via GraphBuilder, save with the new streaming
    writer, and re-load — the result must equal the original
    nodes/edges set."""
    from graph.builder import GraphBuilder
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text(
        "def hello():\n    return 'world' * 10\n"
    )
    g = GraphBuilder().build(str(src))
    out = tmp_path / "out.json"
    JsonStore(str(out)).save(g)
    g2 = JsonStore(str(out)).load()
    assert set(g.nodes()) == set(g2.nodes())
    assert set(g.edges()) == set(g2.edges())
