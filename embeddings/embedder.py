from __future__ import annotations

import hashlib
import threading

import networkx as nx
import numpy as np

_EMBED_TYPES = {"function", "method", "class", "document", "section"}
_MIN_TEXT_LENGTH = 40

# Module-level cache of constructed embedder instances keyed by model name.
# A SentenceTransformer load is hundreds of MB of weights and >1s of CPU on
# a cold cache, and ``web/server.py`` constructs a fresh ``Embedder()`` on
# every project swap (see ``_swap_to_project_store``). The singleton path
# means the second-and-subsequent swaps reuse the already-loaded model.
_INSTANCE_CACHE: dict[str, "Embedder"] = {}
_INSTANCE_LOCK = threading.Lock()


def get_shared_embedder(model_name: str = "all-MiniLM-L6-v2") -> "Embedder":
    """Return a process-wide :class:`Embedder` for ``model_name``.

    Multiple callers asking for the same model share a single instance,
    so the underlying SentenceTransformer model is loaded once per
    process instead of once per project open / per request handler.
    """
    with _INSTANCE_LOCK:
        inst = _INSTANCE_CACHE.get(model_name)
        if inst is None:
            inst = Embedder(model_name)
            _INSTANCE_CACHE[model_name] = inst
        return inst


def _hash_text(text: str) -> str:
    """Stable content hash used as the embedding-cache key.

    Uses MD5 — fast, plenty of collision-resistance for cache keying, and
    matches the MD5 the graph builder already stores in ``source_md5``.
    """
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _as_float32_array(emb) -> np.ndarray:
    """Coerce an embedding payload to a 1-D ``float32`` :class:`numpy.ndarray`.

    Phase 3 of PLAN_INDEX_MEMORY_AND_CONCURRENCY made the in-memory
    storage canonical, but callers (older saved graphs, watcher-batched
    re-embeds, JSON sidecars) may still hand us plain Python lists. This
    helper centralizes the conversion so the embedder + cache stay
    ndarray-only without breaking any of those paths.
    """
    if isinstance(emb, np.ndarray):
        return emb if emb.dtype == np.float32 else emb.astype(np.float32, copy=False)
    return np.asarray(emb, dtype=np.float32)


class Embedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for embedding. "
                    "Install it with: pip install sentence-transformers"
                )
            # Newer transformers releases initialize weights on the "meta" device
            # when low_cpu_mem_usage is on; passing device="cpu" then triggers a
            # .to("cpu") on meta tensors which raises NotImplementedError.
            # Load without forcing a device — SentenceTransformer picks CPU when
            # no CUDA/MPS is available.
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_texts_array(
        self, texts: list[str], batch_size: int = 256
    ) -> np.ndarray:
        """Encode ``texts`` and return a 2-D ``float32`` ndarray.

        Phase 3 of PLAN_INDEX_MEMORY_AND_CONCURRENCY: this is the new
        canonical encoding API. The legacy :meth:`embed_texts` is now a
        thin wrapper that materializes the array as a ``list[list[float]]``
        for callers that still want the list shape — every kept-in-RAM
        embedding now lives as a small ``float32`` ndarray (~1.5 KB for
        a 384-dim vector) instead of a 384-entry Python ``list`` of
        Python ``float`` objects (~10–12 KB each on CPython).
        """
        if not texts:
            # ``(0, 0)`` keeps shape arithmetic consistent for callers
            # that immediately index into the result.
            return np.zeros((0, 0), dtype=np.float32)
        model = self._load_model()
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=True,
        )
        # SentenceTransformer returns numpy by default; some fakes / older
        # backends return lists. Coerce to a contiguous ``float32``
        # ndarray either way.
        if not isinstance(embeddings, np.ndarray):
            embeddings = np.asarray(embeddings, dtype=np.float32)
        elif embeddings.dtype != np.float32:
            embeddings = embeddings.astype(np.float32, copy=False)
        return embeddings

    def embed_texts(self, texts: list[str], batch_size: int = 256) -> list[list[float]]:
        """Encode ``texts`` and return them as Python lists of floats.

        .. deprecated:: Phase 3 (PLAN_INDEX_MEMORY_AND_CONCURRENCY)
           Prefer :meth:`embed_texts_array` — this wrapper materializes
           the ndarray output into a much larger list-of-lists for
           backward compatibility with the watcher and CLI search
           paths that haven't been migrated yet.
        """
        arr = self.embed_texts_array(texts, batch_size=batch_size)
        if arr.size == 0:
            return []
        return arr.tolist()

    def embed_single(self, text: str) -> list[float]:
        """Encode a single text — kept as a list-returning helper for
        the search query path (callers immediately ``np.asarray`` it)."""
        arr = self.embed_texts_array([text])
        if arr.size == 0:
            return []
        return arr[0].tolist()

    def embed_graph(
        self,
        graph: nx.DiGraph,
        prev_cache: dict[str, "np.ndarray | list[float]"] | None = None,
    ) -> dict[str, np.ndarray]:
        """Generate embeddings for eligible nodes, reusing cached vectors.

        ``prev_cache`` maps ``content_hash -> embedding`` from a previous
        run. Nodes whose source already lives in the cache are skipped
        (the cached vector is reused), so a no-op reindex pays only the
        hashing cost rather than the SentenceTransformer encoding cost.
        See ``_extract_cache_from_graph`` for building one from a
        previously-loaded graph.

        Phase 3 of PLAN_INDEX_MEMORY_AND_CONCURRENCY: the values written
        to ``graph.nodes[nid]["embedding"]`` are now ``float32`` 1-D
        numpy arrays (≈ 5–7× smaller in RAM than the legacy
        ``list[float]``). ``prev_cache`` accepts either form and is
        normalized internally; the returned cache is ndarray-only.

        The dual storage (cache *and* node attr) is also dropped — the
        node attr is now the single source of truth. The returned cache
        is still populated so the immediate caller can hand it to the
        next ``embed_graph`` call, but ``extract_cache_from_graph`` can
        rebuild it from any loaded graph if needed.
        """
        # Normalize the incoming cache to ndarray entries up-front so
        # the rest of this method is dtype-uniform.
        cache: dict[str, np.ndarray] = {
            h: _as_float32_array(v) for h, v in (prev_cache or {}).items()
        }

        # Bucket nodes by whether their source already has a cached vector.
        cached_nodes: list[tuple[str, str]] = []   # (node_id, hash)
        new_nodes: list[tuple[str, str]] = []      # (node_id, hash)
        new_texts: list[str] = []

        # Phase 4 of PLAN_INDEX_MEMORY_AND_CONCURRENCY: the per-node
        # ``source`` attr is gone; we resolve the text on demand via
        # ``graph.query.get_source`` which slices the
        # ``graph.graph["_file_text"]`` sidecar by line range. The
        # import is local so this module stays cheap to import for
        # callers that never embed (e.g. CLI ``apollo search`` without
        # the model installed).
        from apollo.graph.query import get_source

        for node_id, data in graph.nodes(data=True):
            if data.get("type") not in _EMBED_TYPES:
                continue
            source = get_source(graph, node_id)
            if not source or len(source.strip()) < _MIN_TEXT_LENGTH:
                continue
            h = _hash_text(source)
            if h in cache:
                cached_nodes.append((node_id, h))
            else:
                new_nodes.append((node_id, h))
                new_texts.append(source)

        # Apply cached embeddings without touching the model.
        for node_id, h in cached_nodes:
            graph.nodes[node_id]["embedding"] = cache[h]
            graph.nodes[node_id]["embedding_hash"] = h

        # Encode the rest in a single batch — ndarray throughout.
        if new_texts:
            new_embeddings = self.embed_texts_array(new_texts)
            for (node_id, h), emb in zip(new_nodes, new_embeddings):
                # ``emb`` is a 1-D view into the batched array; copy
                # so dropping ``new_embeddings`` doesn't leave each node
                # holding a strided slice of a much larger buffer.
                vec = np.ascontiguousarray(emb, dtype=np.float32)
                graph.nodes[node_id]["embedding"] = vec
                graph.nodes[node_id]["embedding_hash"] = h
                cache[h] = vec

        return cache


def extract_cache_from_graph(graph: nx.DiGraph) -> dict[str, np.ndarray]:
    """Build a ``content_hash -> embedding`` dict from an existing graph.

    Used by callers that load the previous index before reindexing so the
    bulk of unchanged nodes don't get re-encoded. Falls back to hashing
    ``source`` when the older graph format didn't store ``embedding_hash``.

    Returns ndarray-valued entries regardless of whether the loaded graph
    stored embeddings as ndarrays (Phase 3+) or lists (legacy /
    watcher-batched / older JSON files).
    """
    # Phase 4 of PLAN_INDEX_MEMORY_AND_CONCURRENCY: legacy graphs that
    # didn't store ``embedding_hash`` need the text to re-hash; that
    # text is now sliced from the ``_file_text`` sidecar via
    # ``get_source`` (or, on truly old graphs, the still-present
    # ``source`` attr — ``get_source`` handles both).
    from apollo.graph.query import get_source

    cache: dict[str, np.ndarray] = {}
    for node_id, data in graph.nodes(data=True):
        emb = data.get("embedding")
        if emb is None:
            continue
        h = data.get("embedding_hash")
        if not h:
            source = get_source(graph, node_id)
            if not source:
                continue
            h = _hash_text(source)
        cache[h] = _as_float32_array(emb)
    return cache
