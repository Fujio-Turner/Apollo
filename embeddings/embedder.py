from __future__ import annotations

import hashlib
import threading

import networkx as nx

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

    def embed_texts(self, texts: list[str], batch_size: int = 256) -> list[list[float]]:
        if not texts:
            return []
        model = self._load_model()
        embeddings = model.encode(texts, batch_size=batch_size, show_progress_bar=True)
        return [emb.tolist() for emb in embeddings]

    def embed_single(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_graph(
        self,
        graph: nx.DiGraph,
        prev_cache: dict[str, list[float]] | None = None,
    ) -> dict[str, list[float]]:
        """Generate embeddings for eligible nodes, reusing cached vectors.

        ``prev_cache`` maps ``content_hash -> embedding`` from a previous
        run. Nodes whose source already lives in the cache are skipped
        (the cached vector is reused), so a no-op reindex pays only the
        hashing cost rather than the SentenceTransformer encoding cost.
        See ``_extract_cache_from_graph`` for building one from a
        previously-loaded graph.

        Returns the **updated** cache (input cache plus any vectors
        produced this run) so the caller can persist or pass it to the
        next invocation.
        """
        cache: dict[str, list[float]] = dict(prev_cache or {})

        # Bucket nodes by whether their source already has a cached vector.
        cached_nodes: list[tuple[str, str]] = []   # (node_id, hash)
        new_nodes: list[tuple[str, str]] = []      # (node_id, hash)
        new_texts: list[str] = []

        for node_id, data in graph.nodes(data=True):
            if data.get("type") not in _EMBED_TYPES:
                continue
            source = data.get("source")
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

        # Encode the rest in a single batch.
        if new_texts:
            new_embeddings = self.embed_texts(new_texts)
            for (node_id, h), emb in zip(new_nodes, new_embeddings):
                graph.nodes[node_id]["embedding"] = emb
                graph.nodes[node_id]["embedding_hash"] = h
                cache[h] = emb

        return cache


def extract_cache_from_graph(graph: nx.DiGraph) -> dict[str, list[float]]:
    """Build a ``content_hash -> embedding`` dict from an existing graph.

    Used by callers that load the previous index before reindexing so the
    bulk of unchanged nodes don't get re-encoded. Falls back to hashing
    ``source`` when the older graph format didn't store ``embedding_hash``.
    """
    cache: dict[str, list[float]] = {}
    for _, data in graph.nodes(data=True):
        emb = data.get("embedding")
        if emb is None:
            continue
        h = data.get("embedding_hash")
        if not h:
            source = data.get("source")
            if not source:
                continue
            h = _hash_text(source)
        cache[h] = emb
    return cache
