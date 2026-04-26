"""
Couchbase Lite semantic search — searches embeddings stored in CBL.

Uses APPROX_VECTOR_DISTANCE when available (Enterprise Edition), otherwise
falls back to fetching all embeddings and doing brute-force cosine similarity.
"""
from __future__ import annotations

import json

import numpy as np


class CouchbaseLiteSemanticSearch:
    """Semantic search using embeddings stored in Couchbase Lite."""

    def __init__(self, store, embedder) -> None:
        self._store = store
        self._embedder = embedder

    def has_embeddings(self) -> bool:
        cbl = self._store.cbl
        rows = cbl.execute_query(
            "SELECT COUNT(*) AS cnt FROM nodes WHERE embedding IS NOT NULL"
        )
        return bool(rows and rows[0].get("cnt", 0) > 0)

    def search(
        self,
        query: str,
        top_k: int = 10,
        node_type: str | None = None,
    ) -> list[dict]:
        query_embedding = self._embedder.embed_single(query)
        cbl = self._store.cbl

        if cbl.has_vector_index:
            return self._search_vector_index(cbl, query_embedding, top_k, node_type)
        return self._search_brute_force(cbl, query_embedding, top_k, node_type)

    def _search_vector_index(
        self, cbl, query_vec: list[float], top_k: int, node_type: str | None
    ) -> list[dict]:
        """Use APPROX_VECTOR_DISTANCE (Enterprise Edition)."""
        where = ""
        if node_type:
            where = f'WHERE n.type = "{node_type}"'

        sql = (
            f"SELECT META(n).id AS _id, n.name, n.type, n.path, "
            f"n.line_start, n.line_end, "
            f"APPROX_VECTOR_DISTANCE(n.embedding, $vec) AS distance "
            f"FROM nodes AS n {where} "
            f"ORDER BY distance "
            f"LIMIT {top_k}"
        )
        params = json.dumps({"vec": query_vec})
        rows = cbl.execute_query(sql, params_json=params)

        results = []
        for row in rows:
            dist = row.get("distance")
            score = 1.0 - dist if dist is not None else 0.0
            results.append({
                "id": row.get("_id"),
                "name": row.get("name"),
                "type": row.get("type"),
                "path": row.get("path"),
                "line_start": row.get("line_start"),
                "line_end": row.get("line_end"),
                "score": score,
            })
        return results

    def _search_brute_force(
        self, cbl, query_vec: list[float], top_k: int, node_type: str | None
    ) -> list[dict]:
        """Fetch all embeddings from CBL and compute cosine similarity."""
        where = "WHERE embedding IS NOT NULL"
        if node_type:
            where += f' AND type = "{node_type}"'

        sql = (
            f"SELECT META().id AS _id, name, type, path, "
            f"line_start, line_end, embedding "
            f"FROM nodes {where}"
        )
        rows = cbl.execute_query(sql)

        q = np.asarray(query_vec)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []

        scored = []
        for row in rows:
            emb = row.get("embedding")
            if not emb:
                continue
            v = np.asarray(emb)
            v_norm = np.linalg.norm(v)
            if v_norm == 0:
                continue
            score = float(np.dot(q, v) / (q_norm * v_norm))
            scored.append({
                "id": row.get("_id"),
                "name": row.get("name"),
                "type": row.get("type"),
                "path": row.get("path"),
                "line_start": row.get("line_start"),
                "line_end": row.get("line_end"),
                "score": score,
            })

        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:top_k]
