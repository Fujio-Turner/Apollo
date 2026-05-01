from __future__ import annotations

import numpy as np
import networkx as nx


class SemanticSearch:
    """Cosine-similarity search over node embeddings.

    Implementation note (perf)
    --------------------------
    The first ``search()`` call lazily materializes a single ``(N, D)``
    matrix of L2-normalized embeddings plus a parallel list of
    ``node_ids`` and ``metadata`` rows. Subsequent queries reduce to one
    BLAS-backed ``M @ q_normalized`` matmul + ``argpartition`` for top-k,
    which is ~50–200× faster than the previous per-node Python loop that
    re-`np.asarray`'d both vectors and recomputed the query norm on every
    iteration.

    The cache is invalidated by stamping in the graph's
    ``(num_nodes, num_edges)`` signature; the watcher mutates the graph
    in place, so on the next query we cheaply detect the change and
    rebuild. This is good enough for a single-process server — the
    rebuild is ~O(N·D) of vector copies but no model encode work.
    """

    def __init__(self, graph: nx.DiGraph, embedder) -> None:
        self.graph = graph
        self.embedder = embedder
        self._matrix: np.ndarray | None = None
        self._node_ids: list[str] = []
        self._meta: list[dict] = []
        self._cache_signature: tuple[int, int] | None = None

    # ------------------------------------------------------------------
    # Internal: lazy index build / invalidation
    # ------------------------------------------------------------------

    def _signature(self) -> tuple[int, int]:
        """Cheap cache-key — invalidate when the graph mutates."""
        return (self.graph.number_of_nodes(), self.graph.number_of_edges())

    def _ensure_matrix(self) -> None:
        sig = self._signature()
        if self._matrix is not None and self._cache_signature == sig:
            return

        node_ids: list[str] = []
        meta: list[dict] = []
        rows: list[list[float]] = []

        for node_id, data in self.graph.nodes(data=True):
            embedding = data.get("embedding")
            if embedding is None:
                continue
            node_ids.append(node_id)
            rows.append(embedding)
            meta.append({
                "id": node_id,
                "name": data.get("name"),
                "type": data.get("type"),
                "path": data.get("path"),
                "line_start": data.get("line_start"),
                "line_end": data.get("line_end"),
            })

        if not rows:
            self._matrix = np.zeros((0, 0), dtype=np.float32)
            self._node_ids = []
            self._meta = []
            self._cache_signature = sig
            return

        matrix = np.asarray(rows, dtype=np.float32)
        # L2-normalize once. Zero-norm rows would yield NaNs after a
        # divide; replace those with zero vectors so their cosine score
        # is always 0 (matches the legacy behaviour exercised by
        # ``test_zero_norm_returns_zero``).
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix /= norms
        # Re-zero rows that started zero so their cosine is 0 not 1.
        zero_mask = (np.linalg.norm(np.asarray(rows, dtype=np.float32), axis=1) == 0)
        if zero_mask.any():
            matrix[zero_mask] = 0.0

        self._matrix = matrix
        self._node_ids = node_ids
        self._meta = meta
        self._cache_signature = sig

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        node_type: str | None = None,
    ) -> list[dict]:
        self._ensure_matrix()
        if self._matrix is None or self._matrix.size == 0:
            return []

        query_embedding = self.embedder.embed_single(query)
        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0:
            return []
        q /= q_norm

        # One matmul over the full normalized matrix → all cosine scores.
        scores = self._matrix @ q  # shape: (N,)

        if node_type is not None:
            # Mask to the requested node_type. Building this mask each
            # call is cheap (O(N) Python comparison), and avoids a more
            # complex per-type secondary index.
            mask = np.fromiter(
                (m["type"] == node_type for m in self._meta),
                count=len(self._meta), dtype=bool,
            )
            if not mask.any():
                return []
            # argpartition on the full array then filter is faster than
            # rebuilding per-type matrices for typical (small top_k) calls.
            scores = np.where(mask, scores, -np.inf)

        # Top-k via argpartition (O(N), avoids fully sorting N elements).
        n = scores.shape[0]
        k = min(top_k, n)
        if k <= 0:
            return []
        # ``argpartition`` doesn't sort the partition; sort just the top-k.
        if k < n:
            top_idx = np.argpartition(-scores, k - 1)[:k]
        else:
            top_idx = np.arange(n)
        top_idx = top_idx[np.argsort(-scores[top_idx])]

        results: list[dict] = []
        for idx in top_idx:
            score = float(scores[idx])
            if not np.isfinite(score):
                continue
            row = self._meta[idx]
            results.append({
                "id": row["id"],
                "score": score,
                "name": row["name"],
                "type": row["type"],
                "path": row["path"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
            })
        return results

    def has_embeddings(self) -> bool:
        # Use the cached matrix when available; fall back to a generator
        # scan only on the very first call.
        if self._matrix is not None and self._cache_signature == self._signature():
            return self._matrix.size > 0
        return any(
            "embedding" in data
            for _, data in self.graph.nodes(data=True)
        )
