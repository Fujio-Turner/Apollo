# SPDX-License-Identifier: BUSL-1.1
"""Phase 3 (PLAN_INDEX_MEMORY_AND_CONCURRENCY) regression tests.

Covers:
- ``Embedder.embed_texts_array`` returns a ``float32`` ndarray.
- ``embed_graph`` writes ``float32`` ndarrays into node attrs.
- ``JsonStore`` round-trips ndarray embeddings via base64 sidecars
  (and writes a much smaller file than the legacy list-of-floats form).
- ``JsonStore`` still loads a legacy ``embedding: [...]`` list file
  produced by today's code (promoted to ndarray on read).
- ``extract_cache_from_graph`` returns ndarray entries regardless of
  whether the source graph stored lists or ndarrays.
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import numpy as np
import pytest

from embeddings.embedder import (
    Embedder,
    _as_float32_array,
    extract_cache_from_graph,
)
from storage.json_store import (
    JsonStore,
    _decode_embedding_attrs,
    _encode_embedding_attrs,
)


# ─────────────────────────────────────────────────────────────────────
# Fake SentenceTransformer — emits deterministic vectors so we can
# verify the float32-ndarray invariant without paying the model
# download / encode cost.
# ─────────────────────────────────────────────────────────────────────
class _FakeST:
    def __init__(self, *args, **kwargs):
        pass

    def encode(self, texts, batch_size=256, show_progress_bar=False):
        return np.asarray(
            [[float(len(t)), float(i), 0.5] for i, t in enumerate(texts)],
            dtype=np.float32,
        )


@pytest.fixture
def fake_embedder(monkeypatch):
    import embeddings.embedder as mod
    e = Embedder("fake")
    e._model = _FakeST()
    return e


def test_embed_texts_array_returns_float32_ndarray(fake_embedder):
    arr = fake_embedder.embed_texts_array(["hello world", "another text here"])
    assert isinstance(arr, np.ndarray)
    assert arr.dtype == np.float32
    assert arr.shape == (2, 3)


def test_embed_texts_legacy_wrapper_still_returns_list(fake_embedder):
    out = fake_embedder.embed_texts(["hello world", "another text here"])
    assert isinstance(out, list)
    assert isinstance(out[0], list)
    assert all(isinstance(x, float) for x in out[0])


def test_embed_graph_writes_float32_ndarrays(fake_embedder):
    g = nx.DiGraph()
    g.add_node("a", type="function",
               source="def f():\n    return 'x' * 100  # padding to clear min length")
    g.add_node("b", type="class",
               source="class B:\n    " + "pass\n    " * 20)
    fake_embedder.embed_graph(g)
    for nid in ("a", "b"):
        emb = g.nodes[nid]["embedding"]
        assert isinstance(emb, np.ndarray), f"node {nid} embedding is {type(emb)}"
        assert emb.dtype == np.float32


def test_embed_graph_prev_cache_accepts_lists(fake_embedder):
    """prev_cache passed in by legacy callers may still use lists —
    embed_graph must normalize internally and skip re-encoding."""
    g = nx.DiGraph()
    text = "def f():\n    return 1\n# extra padding for the minimum text length"
    g.add_node("a", type="function", source=text)

    # First pass — generate a real vector + hash.
    cache = fake_embedder.embed_graph(g)
    assert len(cache) == 1
    h = next(iter(cache))

    # Build a *list*-valued cache (simulating an old saved graph or the
    # watcher path) and re-run on a fresh graph.
    list_cache = {h: cache[h].tolist()}
    g2 = nx.DiGraph()
    g2.add_node("a", type="function", source=text)
    new_cache = fake_embedder.embed_graph(g2, prev_cache=list_cache)

    # The cache hit path should have applied the cached vector as an
    # ndarray, not the original list.
    assert isinstance(g2.nodes["a"]["embedding"], np.ndarray)
    np.testing.assert_array_equal(g2.nodes["a"]["embedding"], cache[h])
    # And the returned cache is ndarray-only.
    assert isinstance(new_cache[h], np.ndarray)


def test_encode_decode_round_trip():
    vec = np.array([0.1, -0.2, 0.3, 0.4], dtype=np.float32)
    attrs = {"name": "f", "embedding": vec}
    _encode_embedding_attrs(attrs)
    assert "embedding" not in attrs
    assert "embedding_b64" in attrs
    assert attrs["embedding_dtype"] == "float32"
    assert attrs["embedding_dim"] == 4

    _decode_embedding_attrs(attrs)
    assert "embedding_b64" not in attrs
    np.testing.assert_array_equal(attrs["embedding"], vec)
    assert attrs["embedding"].dtype == np.float32


def test_decode_promotes_legacy_list_to_ndarray():
    """Old files used ``embedding: [...]`` — readers must transparently
    promote to ndarray so callers see one consistent type."""
    attrs = {"name": "f", "embedding": [0.1, 0.2, 0.3]}
    _decode_embedding_attrs(attrs)
    assert isinstance(attrs["embedding"], np.ndarray)
    assert attrs["embedding"].dtype == np.float32


def test_jsonstore_round_trip_with_ndarray_embedding(tmp_path: Path):
    g = nx.DiGraph()
    g.add_node("a", type="function", name="f",
               embedding=np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32),
               embedding_hash="abc")
    g.add_node("b", type="file", name="b.py")  # no embedding
    g.add_edge("b", "a", type="defines")

    path = tmp_path / "graph.json"
    JsonStore(str(path)).save(g)

    # On-disk: should be base64, not list-of-floats.
    raw = json.loads(path.read_bytes())
    a_doc = raw["nodes"]["a"]
    assert "embedding" not in a_doc
    assert "embedding_b64" in a_doc
    assert a_doc["embedding_dtype"] == "float32"
    assert a_doc["embedding_dim"] == 4

    # Re-load round-trips back to ndarray.
    g2 = JsonStore(str(path)).load()
    np.testing.assert_array_equal(g2.nodes["a"]["embedding"],
                                  np.asarray([0.1, 0.2, 0.3, 0.4], dtype=np.float32))
    assert g2.nodes["a"]["embedding"].dtype == np.float32


def test_jsonstore_load_legacy_list_embedding(tmp_path: Path):
    """A v2-shaped graph.json that still stores embeddings as lists
    (the pre-Phase-3 format) must load cleanly."""
    path = tmp_path / "legacy_graph.json"
    payload = {
        "version": 2,
        "nodes": {
            "a": {
                "type": "function",
                "name": "f",
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
        },
        "edges": {},
    }
    path.write_bytes(json.dumps(payload).encode("utf-8"))

    g = JsonStore(str(path)).load()
    assert isinstance(g.nodes["a"]["embedding"], np.ndarray)
    assert g.nodes["a"]["embedding"].dtype == np.float32


def test_jsonstore_include_embeddings_false_strips_both_forms(tmp_path: Path):
    # New format
    g = nx.DiGraph()
    g.add_node("a", type="function",
               embedding=np.array([0.1, 0.2], dtype=np.float32))
    path = tmp_path / "g.json"
    JsonStore(str(path)).save(g)
    g_no = JsonStore(str(path)).load(include_embeddings=False)
    assert "embedding" not in g_no.nodes["a"]
    assert "embedding_b64" not in g_no.nodes["a"]

    # Legacy list form
    legacy = {
        "version": 2,
        "nodes": {"a": {"type": "function", "embedding": [0.1, 0.2]}},
        "edges": {},
    }
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_bytes(json.dumps(legacy).encode("utf-8"))
    g_no2 = JsonStore(str(legacy_path)).load(include_embeddings=False)
    assert "embedding" not in g_no2.nodes["a"]


def test_extract_cache_from_graph_handles_both_types():
    g = nx.DiGraph()
    g.add_node("a", type="function",
               embedding=np.array([1, 2, 3], dtype=np.float32),
               embedding_hash="ha")
    g.add_node("b", type="function",
               embedding=[4.0, 5.0, 6.0],
               embedding_hash="hb")
    cache = extract_cache_from_graph(g)
    assert set(cache.keys()) == {"ha", "hb"}
    for h, v in cache.items():
        assert isinstance(v, np.ndarray)
        assert v.dtype == np.float32


def test_on_disk_size_smaller_than_legacy(tmp_path: Path):
    """384-dim vectors should be much smaller as base64 bytes than as
    JSON-encoded list-of-floats. We use a 384-dim vector to match the
    MiniLM-L6-v2 size assumed by ``embed_graph``."""
    vec = np.random.RandomState(0).rand(384).astype(np.float32)

    # New (base64) format
    g_new = nx.DiGraph()
    g_new.add_node("a", type="function", embedding=vec.copy())
    new_path = tmp_path / "new.json"
    JsonStore(str(new_path)).save(g_new)
    new_size = new_path.stat().st_size

    # Simulate legacy on-disk format by hand-rolling the JSON.
    legacy = {
        "version": 2,
        "nodes": {"a": {"type": "function", "embedding": vec.tolist()}},
        "edges": {},
    }
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_bytes(json.dumps(legacy).encode("utf-8"))
    legacy_size = legacy_path.stat().st_size

    # Plan target is "≥ 60% smaller". Tolerate noise from other attrs.
    assert new_size < legacy_size * 0.5, (
        f"new={new_size}B legacy={legacy_size}B "
        f"ratio={new_size/legacy_size:.2f}"
    )
