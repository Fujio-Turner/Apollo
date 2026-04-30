from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import networkx as nx
import numpy as np


@dataclass
class SpatialCoord:
    x: float
    y: float
    z: float
    face: int

    def to_dict(self) -> dict[str, float | int]:
        return {"x": self.x, "y": self.y, "z": self.z, "face": self.face}


class SpatialMapper:
    """Computes spatial (x, y, z) coordinates and face assignments for graph nodes."""

    def compute_x(
        self,
        graph: nx.DiGraph,
        embeddings_dict: dict[str, list[float]],
    ) -> dict[str, float]:
        if not embeddings_dict:
            return {node: 180.0 for node in graph.nodes}

        node_ids = list(embeddings_dict.keys())
        matrix = np.array([embeddings_dict[nid] for nid in node_ids])

        reduced = self._reduce_to_1d(matrix)

        rmin, rmax = reduced.min(), reduced.max()
        if rmax - rmin > 0:
            scaled = (reduced - rmin) / (rmax - rmin) * 360.0
        else:
            scaled = np.full_like(reduced, 180.0)

        result: dict[str, float] = {}
        for i, nid in enumerate(node_ids):
            result[nid] = float(scaled[i])

        for node in graph.nodes:
            if node not in result:
                result[node] = 180.0

        return result

    def compute_y(self, graph: nx.DiGraph) -> dict[str, float]:
        entry_points: list[str] = []
        for node in graph.nodes:
            incoming_calls = any(
                graph.edges[pred, node].get("type") == "calls"
                for pred in graph.predecessors(node)
            )
            if not incoming_calls:
                entry_points.append(node)

        depth_map: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        for ep in entry_points:
            if ep not in depth_map:
                depth_map[ep] = 0
                queue.append((ep, 0))

        while queue:
            current, d = queue.popleft()
            for succ in graph.successors(current):
                edge_type = graph.edges[current, succ].get("type")
                if edge_type not in ("calls", "defines"):
                    continue
                if succ not in depth_map:
                    depth_map[succ] = d + 1
                    queue.append((succ, d + 1))

        max_depth = max(depth_map.values()) if depth_map else 1

        result: dict[str, float] = {}
        for node in graph.nodes:
            if node in depth_map:
                if max_depth > 0:
                    result[node] = depth_map[node] / max_depth * 360.0
                else:
                    result[node] = 0.0
            else:
                result[node] = 180.0

        return result

    def compute_z(self, graph: nx.DiGraph) -> dict[str, float]:
        if graph.number_of_nodes() == 0:
            return {}

        try:
            pr = nx.pagerank(graph)
        except Exception:
            pr = nx.degree_centrality(graph)

        pr_values = list(pr.values())
        pr_min = min(pr_values)
        pr_max = max(pr_values)
        spread = pr_max - pr_min

        result: dict[str, float] = {}
        for node, score in pr.items():
            if spread > 0:
                result[node] = (score - pr_min) / spread
            else:
                result[node] = 0.0

        return result

    def assign_face(
        self,
        node_id: str,
        node_data: dict,
        graph: nx.DiGraph,
    ) -> int:
        node_type = node_data.get("type", "")
        path = node_data.get("path", "").lower()
        name = node_data.get("name", "")

        incoming_calls = any(
            graph.edges[pred, node_id].get("type") == "calls"
            for pred in graph.predecessors(node_id)
        )
        outgoing_calls = any(
            graph.edges[node_id, succ].get("type") == "calls"
            for succ in graph.successors(node_id)
        )

        if "test_" in path or "_test" in path or "/tests/" in path or path.startswith("tests/"):
            face = 6
        elif any(kw in path for kw in ("config", "settings", "env")):
            face = 5
        elif node_type == "variable":
            face = 5
        elif any(kw in path for kw in ("util", "helper", "format", "common")):
            face = 4
        elif any(kw in path for kw in ("storage", "db", "data", "model", "store")):
            face = 3
        elif not incoming_calls and node_type in ("function", "method"):
            face = 1
        elif incoming_calls and outgoing_calls:
            face = 2
        else:
            if node_type in ("function", "method", "class"):
                face = 2
            elif node_type == "variable":
                face = 5
            else:
                face = 1

        is_private = name.startswith("_") and not name.startswith("__")
        if is_private:
            face = -face

        return face

    def compute_all(self, graph: nx.DiGraph) -> dict[str, dict]:
        embeddings_dict: dict[str, list[float]] = {}
        for node_id, data in graph.nodes(data=True):
            emb = data.get("embedding")
            if emb is not None:
                embeddings_dict[node_id] = emb

        x_map = self.compute_x(graph, embeddings_dict)
        y_map = self.compute_y(graph)
        z_map = self.compute_z(graph)

        result: dict[str, dict] = {}
        for node_id, data in graph.nodes(data=True):
            face = self.assign_face(node_id, data, graph)
            coord = SpatialCoord(
                x=x_map.get(node_id, 180.0),
                y=y_map.get(node_id, 180.0),
                z=z_map.get(node_id, 0.0),
                face=face,
            )
            spatial = coord.to_dict()
            result[node_id] = spatial
            graph.nodes[node_id]["spatial"] = spatial

        return result

    @staticmethod
    def _reduce_to_1d(matrix: np.ndarray) -> np.ndarray:
        try:
            from umap import UMAP

            reducer = UMAP(n_components=1, random_state=42)
            return reducer.fit_transform(matrix).ravel()
        except ImportError:
            pass

        n_samples = matrix.shape[0]
        if n_samples < 2 or matrix.ndim != 2 or matrix.shape[1] == 0:
            return np.zeros(n_samples)

        # Sanitize non-finite values to keep PCA numerically stable.
        if not np.isfinite(matrix).all():
            matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)

        centered = matrix - matrix.mean(axis=0)
        if np.allclose(centered, 0):
            return np.zeros(n_samples)

        # SVD-based PCA is more numerically stable than eigh(cov) and avoids
        # the divide-by-zero / overflow warnings that occur on degenerate inputs.
        try:
            u, s, _ = np.linalg.svd(centered, full_matrices=False)
        except np.linalg.LinAlgError:
            return np.zeros(n_samples)

        return u[:, 0] * s[0]
