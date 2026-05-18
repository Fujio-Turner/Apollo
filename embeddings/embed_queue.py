# SPDX-License-Identifier: BUSL-1.1
"""Background embedding worker — Phase 6 of
PLAN_INDEX_MEMORY_AND_CONCURRENCY.

The pre-Phase-6 pipeline ran parsing → graph build to completion
before the embedder ever started. Embedding is the slowest stage on
most projects, so total wall-clock was strictly ``parse + embed``.

This module introduces :class:`EmbedQueue`, a daemon thread fed by the
streaming-build loop in :class:`apollo.graph.GraphBuilder`. As each
file's nodes land in the graph, the builder calls
:meth:`EmbedQueue.enqueue` with ``(node_id, source_text)`` for every
embedding-eligible node. The worker batches enqueued items (up to
``_BATCH_SIZE``) and calls ``Embedder.embed_texts_array`` in the
background, writing the resulting vectors back onto the graph as
``float32`` ndarrays.

Cache reuse mirrors :func:`apollo.embeddings.embedder.embed_graph`:
``enqueue`` hashes the text against a ``prev_cache`` (hash → vector)
and short-circuits without touching the queue when a cached vector
already exists, so a no-op reindex stays free.

Exception handling: the worker thread catches every ``BaseException``
and stashes it on ``self._exc``; :meth:`close_and_join` re-raises it
in the caller's thread. A corrupt model file or an OOM in the encoder
fails loudly instead of silently producing a half-embedded graph (one
of the Phase 6 risk-notes).

Thread-safety on the graph: ``GraphBuilder._build_file_nodes`` finishes
adding *all* of a file's nodes before the streaming loop's enqueue
step runs, so the worker never indexes a node the main thread is
still constructing. Within each per-node attrs dict, only the worker
writes ``embedding`` / ``embedding_hash`` — the main thread already
emitted the dict via ``add_node``. CPython's GIL makes single
attribute assignment + lookup atomic, so no explicit lock is needed
for the per-node writes themselves.
"""
from __future__ import annotations

import queue
import threading
from typing import Optional

import networkx as nx
import numpy as np

from .embedder import (
    _MIN_TEXT_LENGTH,
    _as_float32_array,
    _hash_text,
)


_SENTINEL = object()
_DEFAULT_BATCH_SIZE = 256


class EmbedQueue:
    """Batched, background-thread embedding pump.

    Lifecycle::

        eq = EmbedQueue(embedder, graph, prev_cache=cache)
        # ... builder pushes nodes via eq.enqueue(nid, text) ...
        merged_cache = eq.close_and_join()  # blocks until drained
    """

    def __init__(
        self,
        embedder,
        graph: nx.DiGraph,
        prev_cache: Optional[dict] = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ):
        self.embedder = embedder
        self.graph = graph
        # Normalize the cache up-front so the hot path (``enqueue``)
        # doesn't have to re-check value shapes per call.
        self._cache: dict[str, np.ndarray] = {
            h: _as_float32_array(v) for h, v in (prev_cache or {}).items()
        }
        self._batch_size = max(1, int(batch_size))
        self._q: queue.Queue = queue.Queue()
        self._exc: Optional[BaseException] = None
        self._closed = False
        # Telemetry — useful for the cache-hit invariant test (Phase 6
        # done-when bullet #2).
        self._cache_hits = 0
        self._enqueued = 0
        self._encoded = 0
        self._thread = threading.Thread(
            target=self._run, name="apollo-embed-queue", daemon=True,
        )
        self._thread.start()

    # ------------------------------------------------------------------
    # producer side
    # ------------------------------------------------------------------

    def enqueue(self, node_id: str, text: str) -> None:
        """Push ``(node_id, text)`` for background embedding.

        Cheap fast-paths:

        * ``text`` shorter than ``_MIN_TEXT_LENGTH`` → skipped (matches
          ``embed_graph``).
        * Hash already present in ``prev_cache`` → vector is attached
          directly and the queue is not touched. This keeps the
          done-when invariant "Embeddings on existing-cache nodes
          happen with zero model invocations" testable.
        * Worker thread already crashed → enqueue is a silent no-op;
          ``close_and_join`` re-raises in the caller's thread.
        """
        if self._closed:
            # Calling enqueue after close is a programmer error, but
            # silently dropping the event is friendlier than raising
            # in the middle of a build-tear-down. Telemetry counter
            # makes it visible in unit tests.
            return
        if self._exc is not None:
            return
        if not text or len(text.strip()) < _MIN_TEXT_LENGTH:
            return
        h = _hash_text(text)
        cached = self._cache.get(h)
        if cached is not None:
            # Direct attach — no model invocation, no queue traffic.
            if node_id in self.graph.nodes:
                self.graph.nodes[node_id]["embedding"] = cached
                self.graph.nodes[node_id]["embedding_hash"] = h
            self._cache_hits += 1
            return
        self._enqueued += 1
        self._q.put((node_id, text, h))

    # ------------------------------------------------------------------
    # consumer side
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            while True:
                first = self._q.get()
                if first is _SENTINEL:
                    return
                batch = [first]
                # Coalesce — drain whatever is sitting in the queue
                # up to ``_batch_size`` to keep the SentenceTransformer
                # batched-encode efficient (its overhead per call is
                # significant on CPU, far worse with batch-of-1).
                got_sentinel = False
                while len(batch) < self._batch_size:
                    try:
                        item = self._q.get_nowait()
                    except queue.Empty:
                        break
                    if item is _SENTINEL:
                        got_sentinel = True
                        break
                    batch.append(item)
                self._encode_batch(batch)
                if got_sentinel:
                    return
        except BaseException as e:  # noqa: BLE001 — surface in main thread
            self._exc = e

    def _encode_batch(self, batch: list[tuple[str, str, str]]) -> None:
        if not batch:
            return
        texts = [item[1] for item in batch]
        vectors = self.embedder.embed_texts_array(texts)
        for (nid, _text, h), vec in zip(batch, vectors):
            v = np.ascontiguousarray(vec, dtype=np.float32)
            self._cache[h] = v
            if nid in self.graph.nodes:
                self.graph.nodes[nid]["embedding"] = v
                self.graph.nodes[nid]["embedding_hash"] = h
        self._encoded += len(batch)

    # ------------------------------------------------------------------
    # teardown
    # ------------------------------------------------------------------

    def close_and_join(self, timeout: float | None = None) -> dict[str, np.ndarray]:
        """Block until every queued item has been encoded.

        Returns the *merged* cache (``prev_cache`` + everything the
        queue just produced) so the caller can persist it / hand it to
        the next reindex without re-extracting from the graph.

        Re-raises any exception caught inside the worker — callers see
        a normal Python exception in their own thread rather than a
        silently-half-embedded graph.
        """
        if self._closed:
            return dict(self._cache)
        self._closed = True
        self._q.put(_SENTINEL)
        self._thread.join(timeout=timeout)
        if self._exc is not None:
            raise self._exc
        return dict(self._cache)

    # ------------------------------------------------------------------
    # introspection — for tests / telemetry
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict:
        return {
            "cache_hits": self._cache_hits,
            "enqueued": self._enqueued,
            "encoded": self._encoded,
        }
