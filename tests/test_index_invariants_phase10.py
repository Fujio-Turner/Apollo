# SPDX-License-Identifier: BUSL-1.1
"""Phase 10 of PLAN_INDEX_MEMORY_AND_CONCURRENCY — invariant guards.

Each phase 1-9 of the index-memory-and-concurrency plan introduced a
structural invariant the codebase now relies on for its low-memory /
high-concurrency behaviour. If any of those silently regresses (a
refactor adds back the per-node ``source`` attr, a "defensive copy"
re-materialises the parser output list, an embedding dtype slips from
``float32`` to ``list[float]``, …) memory blows back up but tests stay
green because no individual feature broke.

These checks live as a single thin test module so a future developer
who breaks one gets a precise failure pointing at the phase that
introduced the invariant. They are deliberately structural — they
don't measure wall-clock or RSS (that's what ``scripts/bench_check.py``
is for). They pin the *shape* of the pipeline.
"""
from __future__ import annotations

import gc
import weakref
from pathlib import Path

import networkx as nx
import numpy as np
import pytest

from embeddings import embedder as embedder_mod
from graph import builder as builder_mod
from graph.builder import GraphBuilder
from graph.query import get_source


# ─────────────────────────────────────────────────────────────────────
# Tiny synthetic project — one Python file with a class + two funcs
# plus a markdown doc. Cheap enough to materialise per test (≪ 1 ms)
# and exercises every node type Phase 4 touches.
# ─────────────────────────────────────────────────────────────────────
_PY_SAMPLE = '''\
"""Module docstring."""
import os


def alpha(x):
    """Alpha."""
    return beta(x) + os.getpid()


def beta(x):
    return x * 2


class Service:
    """A service class."""

    def run(self):
        return alpha(7)
'''

_MD_SAMPLE = """\
# Title

Some prose.

```python
print("hi")
```

## Section

More prose.
"""


def _write_sample(root: Path) -> None:
    (root / "code.py").write_text(_PY_SAMPLE)
    (root / "doc.md").write_text(_MD_SAMPLE)


# ─────────────────────────────────────────────────────────────────────
# Phase 1 invariant — parser-pool output is streamed, parsed dicts are
# reclaimable mid-build (no long-lived ``parsed_files`` list).
# ─────────────────────────────────────────────────────────────────────
class _WeakableDict(dict):
    __slots__ = ("__weakref__",)


def test_phase1_invariant_parsed_dicts_are_reclaimed(tmp_path, monkeypatch):
    _write_sample(tmp_path)

    # The monkeypatch on ``_parse_one`` below only takes effect when
    # the streaming build uses the in-process thread pool. Pin the
    # parser-pool mode explicitly so this assertion is meaningful even
    # when the project-default mode changes (e.g. Phase 2 process pool).
    monkeypatch.setenv("APOLLO_PARSER_POOL", "thread")

    weak_refs: list[weakref.ref] = []
    real = builder_mod._parse_one

    def wrapped(*a, **kw):
        out = real(*a, **kw)
        if isinstance(out, dict):
            wrapped_dict = _WeakableDict(out)
            weak_refs.append(weakref.ref(wrapped_dict))
            return wrapped_dict
        return out

    monkeypatch.setattr(builder_mod, "_parse_one", wrapped)

    GraphBuilder().build(str(tmp_path))
    gc.collect()

    assert weak_refs, "parser didn't run on the sample project"
    dead = [r for r in weak_refs if r() is None]
    assert dead, (
        "Phase 1 regression: at least one parsed dict survived past "
        "_build_file_nodes — the streaming-build path is leaking the "
        "per-file payload again."
    )


# ─────────────────────────────────────────────────────────────────────
# Phase 3 invariant — embeddings on the graph are ``float32`` ndarrays
# (not Python ``list[float]``). The dtype switch was the single biggest
# on-disk + in-memory size win and a regression here would silently
# double both.
# ─────────────────────────────────────────────────────────────────────
class _FakeST:
    """Stand-in SentenceTransformer that returns deterministic vectors."""

    def __init__(self, *_a, **_k):
        pass

    def encode(self, texts, **_kw):
        return np.asarray(
            [[float(len(t)), float(sum(map(ord, t[:4])))] for t in texts],
            dtype=np.float32,
        )

    def get_sentence_embedding_dimension(self):
        return 2


def test_phase3_invariant_embeddings_are_float32_ndarray(tmp_path):
    _write_sample(tmp_path)

    g = GraphBuilder().build(str(tmp_path))
    emb = embedder_mod.Embedder(model_name="fake")
    # SentenceTransformer is loaded lazily inside ``_load_model`` —
    # pre-seed it so the test never hits the network / model cache.
    emb._model = _FakeST()
    emb.embed_graph(g)

    embedded = [
        (nid, data["embedding"])
        for nid, data in g.nodes(data=True)
        if "embedding" in data
    ]
    assert embedded, "no embeddings written — fixture too small?"
    for nid, vec in embedded:
        assert isinstance(vec, np.ndarray), (
            f"Phase 3 regression: node {nid} embedding is "
            f"{type(vec).__name__}, expected np.ndarray."
        )
        assert vec.dtype == np.float32, (
            f"Phase 3 regression: node {nid} embedding dtype is "
            f"{vec.dtype}, expected float32."
        )


# ─────────────────────────────────────────────────────────────────────
# Phase 4 invariant — no per-node ``source`` attr on the six node types
# Phase 4 migrated to the ``_file_text`` sidecar. A regression here means
# we're double-storing source text 3-5× per file again.
# ─────────────────────────────────────────────────────────────────────
_PHASE4_NODE_TYPES = frozenset({
    "function", "method", "class", "document", "section", "code_block",
})


def test_phase4_invariant_no_per_node_source_attr(tmp_path):
    _write_sample(tmp_path)
    g = GraphBuilder().build(str(tmp_path))

    file_text = g.graph.get("_file_text")
    assert isinstance(file_text, dict) and file_text, (
        "Phase 4 regression: _file_text sidecar is missing — "
        "_build_file_nodes is no longer populating it."
    )
    # Every indexed file should land in the sidecar.
    file_paths = {
        data["path"] for _nid, data in g.nodes(data=True)
        if data.get("type") == "file"
    }
    assert file_paths.issubset(file_text.keys()), (
        f"Phase 4 regression: files {file_paths - file_text.keys()} "
        f"are indexed but missing from _file_text."
    )

    offenders = []
    for nid, data in g.nodes(data=True):
        if data.get("type") not in _PHASE4_NODE_TYPES:
            continue
        if "source" in data:
            offenders.append((nid, data["type"]))
    assert not offenders, (
        f"Phase 4 regression: per-node `source` attr is back on "
        f"{offenders[:5]} (and possibly more) — file text is being "
        f"duplicated again."
    )

    # Spot-check: get_source still returns the function body.
    func_ids = [
        nid for nid, data in g.nodes(data=True)
        if data.get("type") == "function" and data.get("name") == "alpha"
    ]
    assert func_ids, "fixture has no `alpha` function — wrong shape?"
    src = get_source(g, func_ids[0])
    assert "def alpha" in src and "beta(x)" in src


# ─────────────────────────────────────────────────────────────────────
# Phase 5 invariant — JsonStore.save does not mutate the live graph.
# Phase 5 had to retain a per-node ``dict(attrs)`` copy precisely to
# keep this invariant; pinning it here prevents a future "optimise
# away that copy" PR from silently corrupting the in-memory graph
# during save.
# ─────────────────────────────────────────────────────────────────────
def test_phase5_invariant_save_does_not_mutate_live_graph(tmp_path):
    _write_sample(tmp_path)

    g = GraphBuilder().build(str(tmp_path))
    emb = embedder_mod.Embedder(model_name="fake")
    emb._model = _FakeST()
    emb.embed_graph(g)

    from storage.json_store import JsonStore

    # Snapshot the embedding ndarray identities so we can prove save()
    # didn't swap them out (or mutate them in place).
    snap = {
        nid: id(data["embedding"]) for nid, data in g.nodes(data=True)
        if "embedding" in data
    }
    assert snap, "fixture produced no embeddings"

    out = tmp_path / "graph.json"
    JsonStore(filepath=str(out)).save(g)
    assert out.exists()

    after = {
        nid: id(data["embedding"]) for nid, data in g.nodes(data=True)
        if "embedding" in data
    }
    assert snap == after, (
        "Phase 5 regression: JsonStore.save swapped out at least one "
        "live-graph embedding (the per-node dict(attrs) copy is no "
        "longer being made)."
    )


# ─────────────────────────────────────────────────────────────────────
# Phase 6 invariant — EmbedQueue exists and exposes the contract
# ``GraphBuilder.build(embed_queue=…)`` relies on. We don't run the
# queue end-to-end here (test_embed_queue_phase6.py already does that)
# — this is a structural pin so an accidental rename / deletion of
# the public API surfaces as a clear test failure.
# ─────────────────────────────────────────────────────────────────────
def test_phase6_invariant_embed_queue_public_api():
    from embeddings.embed_queue import EmbedQueue

    for name in ("enqueue", "close_and_join", "stats"):
        assert hasattr(EmbedQueue, name), (
            f"Phase 6 regression: EmbedQueue.{name} is gone — "
            f"GraphBuilder.build(embed_queue=…) relies on it."
        )


# ─────────────────────────────────────────────────────────────────────
# Phase 7 invariant — ``run_all_passes`` accepts the ``parallel`` /
# ``max_workers`` kwargs added by Phase 7. The web server's
# ``_do_index`` calls it with ``parallel=True``; if those kwargs vanish
# we silently fall back to sequential ML passes.
# ─────────────────────────────────────────────────────────────────────
def test_phase7_invariant_run_all_passes_signature():
    import inspect

    from ml.passes import run_all_passes

    sig = inspect.signature(run_all_passes)
    for required in ("parallel", "max_workers"):
        assert required in sig.parameters, (
            f"Phase 7 regression: run_all_passes lost the "
            f"`{required}` kwarg — parallel ML passes are silently "
            f"disabled."
        )


# ─────────────────────────────────────────────────────────────────────
# Phase 8 invariant — ResolveFullStrategy.run mutates the input graph
# in place (``graph_out is graph_in``) instead of doing the legacy
# ``nx.DiGraph(graph_in)`` deep copy. A regression here is the single
# biggest sweep-time memory blowup.
# ─────────────────────────────────────────────────────────────────────
def test_phase8_invariant_resolve_full_mutates_in_place(tmp_path):
    _write_sample(tmp_path)

    builder = GraphBuilder()
    graph_in, hashes_in = builder.build_incremental(str(tmp_path))

    from graph.incremental import ResolveFullStrategy

    strategy = ResolveFullStrategy(GraphBuilder())
    result = strategy.run(
        root_dir=str(tmp_path),
        graph_in=graph_in,
        prev_hashes=hashes_in,
        prev_dep_index={},
    )
    assert result.graph_out is graph_in, (
        "Phase 8 regression: ResolveFullStrategy is back to "
        "deep-copying graph_in instead of mutating in place."
    )


# ─────────────────────────────────────────────────────────────────────
# Phase 9 invariant — the parallel rehash helper exists in
# ``graph.builder``. A direct import keeps the contract pinned even
# if no caller passes a non-default ``max_workers``.
# ─────────────────────────────────────────────────────────────────────
def test_phase9_invariant_parallel_rehash_helper_exists():
    from graph.builder import _parallel_rehash, _rehash_file_for_incremental

    assert callable(_parallel_rehash)
    assert callable(_rehash_file_for_incremental)
