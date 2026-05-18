"""Phase 6 (PLAN_INDEX_MEMORY_AND_CONCURRENCY) regression tests.

Pins the contract of :class:`apollo.embeddings.embed_queue.EmbedQueue`
and its integration with :meth:`GraphBuilder.build`:

* Background-thread enqueue → encode → write-back works end-to-end.
* Cache hits never invoke the model (the key Phase 6 done-when
  invariant).
* Exceptions raised inside the worker thread propagate to the caller
  via ``close_and_join``.
* ``GraphBuilder.build(embed_queue=eq)`` enqueues function / method /
  class / document / section nodes during the streaming-build loop,
  and the queue can be drained to produce identical embedding
  coverage as the post-build ``embed_graph`` path.
"""
from __future__ import annotations

import threading
from pathlib import Path

import networkx as nx
import numpy as np
import pytest

from embeddings.embed_queue import EmbedQueue
from embeddings.embedder import _hash_text
from graph.builder import GraphBuilder


# ─────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────
class _FakeST:
    """SentenceTransformer stand-in — returns one vector per input.

    Counts ``encode`` invocations and total batch size so tests can
    assert on the cache-hit invariant without mocking the embedder
    wholesale.
    """

    def __init__(self):
        self.encode_calls = 0
        self.encoded_texts: list[str] = []

    def encode(self, texts, batch_size=256, show_progress_bar=False):
        self.encode_calls += 1
        self.encoded_texts.extend(texts)
        out = []
        for t in texts:
            v = np.zeros(4, dtype=np.float32)
            v[0] = float(len(t) % 17)
            v[1] = float(sum(ord(c) for c in t[:16]) % 251)
            out.append(v)
        return np.stack(out) if out else np.zeros((0, 4), dtype=np.float32)


def _make_embedder():
    from embeddings.embedder import Embedder
    e = Embedder()
    e._model = _FakeST()
    return e


_LONG_TEXT_A = "def alpha():\n    return 1\n" * 5
_LONG_TEXT_B = "def beta():\n    return 2\n" * 5


# ─────────────────────────────────────────────────────────────────────
# Standalone EmbedQueue behaviour
# ─────────────────────────────────────────────────────────────────────
def test_queue_encodes_new_text_and_writes_embedding():
    g = nx.DiGraph()
    g.add_node("func::a.py::alpha", type="function", path="a.py",
               line_start=1, line_end=5)

    embedder = _make_embedder()
    eq = EmbedQueue(embedder, g)
    eq.enqueue("func::a.py::alpha", _LONG_TEXT_A)
    eq.close_and_join()

    emb = g.nodes["func::a.py::alpha"]["embedding"]
    assert isinstance(emb, np.ndarray)
    assert emb.dtype == np.float32
    # Hash recorded so the next reindex can cache-hit.
    assert g.nodes["func::a.py::alpha"]["embedding_hash"] == _hash_text(_LONG_TEXT_A)


def test_queue_short_text_is_skipped():
    g = nx.DiGraph()
    g.add_node("func::a.py::tiny", type="function", path="a.py")
    embedder = _make_embedder()
    eq = EmbedQueue(embedder, g)
    eq.enqueue("func::a.py::tiny", "x")   # below _MIN_TEXT_LENGTH
    eq.close_and_join()
    assert "embedding" not in g.nodes["func::a.py::tiny"]
    assert embedder._model.encode_calls == 0


def test_queue_cache_hit_skips_model():
    """Phase 6 done-when: 'Embeddings on existing-cache nodes happen
    with zero model invocations.'"""
    h = _hash_text(_LONG_TEXT_A)
    cached_vec = np.full(4, 0.5, dtype=np.float32)
    prev_cache = {h: cached_vec}

    g = nx.DiGraph()
    g.add_node("func::a.py::alpha", type="function", path="a.py")

    embedder = _make_embedder()
    eq = EmbedQueue(embedder, g, prev_cache=prev_cache)
    eq.enqueue("func::a.py::alpha", _LONG_TEXT_A)
    eq.close_and_join()

    # No model invocation at all.
    assert embedder._model.encode_calls == 0, (
        "Cache-hit enqueue should not invoke the encoder."
    )
    # Node received the cached vector verbatim.
    np.testing.assert_array_equal(
        g.nodes["func::a.py::alpha"]["embedding"], cached_vec,
    )
    # Telemetry reflects the cache hit.
    assert eq.stats["cache_hits"] == 1
    assert eq.stats["enqueued"] == 0
    assert eq.stats["encoded"] == 0


def test_queue_batches_multiple_enqueues():
    g = nx.DiGraph()
    g.add_node("func::a.py::alpha", type="function", path="a.py")
    g.add_node("func::a.py::beta", type="function", path="a.py")

    embedder = _make_embedder()
    eq = EmbedQueue(embedder, g, batch_size=256)
    eq.enqueue("func::a.py::alpha", _LONG_TEXT_A)
    eq.enqueue("func::a.py::beta", _LONG_TEXT_B)
    eq.close_and_join()

    assert "embedding" in g.nodes["func::a.py::alpha"]
    assert "embedding" in g.nodes["func::a.py::beta"]
    # Two distinct texts → encoded at least once. Often a single batch.
    assert embedder._model.encode_calls >= 1
    assert eq.stats["encoded"] == 2


def test_queue_worker_exception_propagates():
    """A worker-thread exception must surface in close_and_join."""

    class _BrokenST:
        def encode(self, texts, batch_size=256, show_progress_bar=False):
            raise RuntimeError("model exploded")

    from embeddings.embedder import Embedder
    embedder = Embedder()
    embedder._model = _BrokenST()
    g = nx.DiGraph()
    g.add_node("func::a.py::alpha", type="function", path="a.py")

    eq = EmbedQueue(embedder, g)
    eq.enqueue("func::a.py::alpha", _LONG_TEXT_A)
    with pytest.raises(RuntimeError, match="model exploded"):
        eq.close_and_join()


def test_queue_returns_merged_cache():
    g = nx.DiGraph()
    g.add_node("func::a.py::alpha", type="function", path="a.py")
    g.add_node("func::a.py::beta", type="function", path="a.py")

    prev_cache = {"old-hash": np.ones(4, dtype=np.float32)}
    embedder = _make_embedder()
    eq = EmbedQueue(embedder, g, prev_cache=prev_cache)
    eq.enqueue("func::a.py::alpha", _LONG_TEXT_A)
    eq.enqueue("func::a.py::beta", _LONG_TEXT_B)
    merged = eq.close_and_join()
    # Merged cache includes the original entry + two new hashes.
    assert "old-hash" in merged
    assert _hash_text(_LONG_TEXT_A) in merged
    assert _hash_text(_LONG_TEXT_B) in merged


# ─────────────────────────────────────────────────────────────────────
# Builder integration
# ─────────────────────────────────────────────────────────────────────
_SAMPLE_PY = '''\
"""Module docstring."""


def alpha():
    """Alpha docstring covering enough text for embedding."""
    return 1 + 1 + 1 + 1 + 1


class Beta:
    """Beta docstring covering enough text for embedding."""

    def gamma(self, x):
        return x * 2 + x + 1 + 1 + 1 + 1
'''


def test_builder_streams_into_embed_queue(tmp_path: Path):
    (tmp_path / "a.py").write_text(_SAMPLE_PY)

    embedder = _make_embedder()
    builder = GraphBuilder()
    eq = EmbedQueue(embedder, builder.graph)
    builder.build(str(tmp_path), embed_queue=eq)
    eq.close_and_join()

    graph = builder.graph
    # The function, the class, and the method all received embeddings.
    for nid in ("func::a.py::alpha", "class::a.py::Beta",
                "method::a.py::Beta::gamma"):
        assert nid in graph.nodes, f"missing node {nid}"
        assert "embedding" in graph.nodes[nid], (
            f"{nid} did not receive an embedding via the queue"
        )
        assert isinstance(graph.nodes[nid]["embedding"], np.ndarray)


def test_builder_with_queue_matches_post_build_embedding(tmp_path: Path):
    """The streaming-queue path and the legacy post-build path must
    embed the same nodes with the same hashes."""
    (tmp_path / "a.py").write_text(_SAMPLE_PY)

    # Path A — streaming queue.
    embedder_a = _make_embedder()
    builder_a = GraphBuilder()
    eq = EmbedQueue(embedder_a, builder_a.graph)
    builder_a.build(str(tmp_path), embed_queue=eq)
    eq.close_and_join()

    # Path B — legacy post-build embed_graph.
    embedder_b = _make_embedder()
    builder_b = GraphBuilder()
    builder_b.build(str(tmp_path))
    embedder_b.embed_graph(builder_b.graph)

    hashes_a = {
        nid: data["embedding_hash"]
        for nid, data in builder_a.graph.nodes(data=True)
        if "embedding_hash" in data
    }
    hashes_b = {
        nid: data["embedding_hash"]
        for nid, data in builder_b.graph.nodes(data=True)
        if "embedding_hash" in data
    }
    assert hashes_a == hashes_b, (
        "Queue path and post-build path disagree on which nodes got "
        "embedded (or on the content-hash key)."
    )


def test_queue_close_is_idempotent():
    """close_and_join can be called twice without error (defensive
    against split error-handling paths in _do_index)."""
    g = nx.DiGraph()
    g.add_node("func::a.py::alpha", type="function", path="a.py")
    embedder = _make_embedder()
    eq = EmbedQueue(embedder, g)
    eq.enqueue("func::a.py::alpha", _LONG_TEXT_A)
    cache1 = eq.close_and_join()
    cache2 = eq.close_and_join()
    assert cache1.keys() == cache2.keys()
