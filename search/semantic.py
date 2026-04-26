from __future__ import annotations

import numpy as np
import networkx as nx


class SemanticSearch:
    def __init__(self, graph: nx.DiGraph, embedder) -> None:
        self.graph = graph
        self.embedder = embedder

    def search(
        self,
        query: str,
        top_k: int = 10,
        node_type: str | None = None,
    ) -> list[dict]:
        query_embedding = self.embedder.embed_single(query)

        results = []
        for node_id, data in self.graph.nodes(data=True):
            embedding = data.get("embedding")
            if embedding is None:
                continue
            if node_type is not None and data.get("type") != node_type:
                continue

            score = self._cosine_similarity(query_embedding, embedding)
            results.append({
                "id": node_id,
                "score": score,
                "name": data.get("name"),
                "type": data.get("type"),
                "path": data.get("path"),
                "line_start": data.get("line_start"),
                "line_end": data.get("line_end"),
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def _cosine_similarity(self, vec_a, vec_b) -> float:
        a = np.asarray(vec_a)
        b = np.asarray(vec_b)
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom == 0:
            return 0.0
        return float(np.dot(a, b) / denom)

    def has_embeddings(self) -> bool:
        return any(
            "embedding" in data
            for _, data in self.graph.nodes(data=True)
        )
