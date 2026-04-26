"""
Graph query engine — structural queries over the knowledge graph.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import networkx as nx


class GraphQuery:
    """Query interface for the code knowledge graph."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph

    def find(self, name: str, node_type: str | None = None) -> list[dict]:
        """Find nodes by name (substring match) and optional type filter."""
        results = []
        name_lower = name.lower()
        for node_id, data in self.graph.nodes(data=True):
            node_name = data.get("name", "")
            if name_lower not in node_name.lower():
                continue
            if node_type and data.get("type") != node_type:
                continue
            results.append({"id": node_id, **data})
        return results

    def callers(self, node_id: str, depth: int = 1) -> list[dict]:
        """Find nodes that call the given node (incoming 'calls' edges).

        With depth > 1, finds transitive callers.
        """
        return self._traverse_edges(node_id, direction="in", edge_type="calls", depth=depth)

    def callees(self, node_id: str, depth: int = 1) -> list[dict]:
        """Find nodes that the given node calls (outgoing 'calls' edges).

        With depth > 1, finds transitive callees.
        """
        return self._traverse_edges(node_id, direction="out", edge_type="calls", depth=depth)

    def references(self, node_id: str, depth: int = 1) -> list[dict]:
        """Find all nodes connected to the given node, any edge type."""
        return self._traverse_edges(node_id, direction="both", edge_type=None, depth=depth)

    def neighbors(
        self,
        node_id: str,
        depth: int = 1,
        edge_types: list[str] | None = None,
        direction: str = "both",
    ) -> list[dict]:
        """Walk the graph from `node_id`, optionally restricting to specific edge types.

        - direction: 'in' (predecessors), 'out' (successors), or 'both'.
        - edge_types: if provided, only follow edges whose `type` is in this list.
        - depth: BFS depth (>=1).
        """
        if node_id not in self.graph:
            return []

        types_set = set(edge_types) if edge_types else None
        visited: set[str] = set()
        results: list[dict] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])

        while queue:
            current, current_depth = queue.popleft()
            if current_depth > 0 and current not in visited:
                visited.add(current)
                node_data = self.graph.nodes[current]
                results.append({"id": current, "depth": current_depth, **node_data})

            if current_depth >= depth:
                continue

            if direction in ("in", "both"):
                for pred in self.graph.predecessors(current):
                    edata = self.graph.edges[pred, current]
                    et = edata.get("type", "")
                    if types_set is None or et in types_set:
                        if pred not in visited:
                            queue.append((pred, current_depth + 1))

            if direction in ("out", "both"):
                for succ in self.graph.successors(current):
                    edata = self.graph.edges[current, succ]
                    et = edata.get("type", "")
                    if types_set is None or et in types_set:
                        if succ not in visited:
                            queue.append((succ, current_depth + 1))

        return results

    def defined_in(self, node_id: str) -> dict | None:
        """Find the file that defines a given node."""
        for pred in self.graph.predecessors(node_id):
            edge_data = self.graph.edges[pred, node_id]
            if edge_data.get("type") == "defines":
                return {"id": pred, **self.graph.nodes[pred]}
        return None

    def children(self, node_id: str, node_type: str | None = None) -> list[dict]:
        """Find nodes that this node defines/contains."""
        results = []
        for succ in self.graph.successors(node_id):
            edge_data = self.graph.edges[node_id, succ]
            if edge_data.get("type") not in ("defines", "contains"):
                continue
            data = self.graph.nodes[succ]
            if node_type and data.get("type") != node_type:
                continue
            results.append({"id": succ, **data})
        return results

    def stats(self) -> dict:
        """Return summary statistics about the graph."""
        type_counts: dict[str, int] = {}
        edge_type_counts: dict[str, int] = {}

        for _, data in self.graph.nodes(data=True):
            t = data.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        for _, _, data in self.graph.edges(data=True):
            t = data.get("type", "unknown")
            edge_type_counts[t] = edge_type_counts.get(t, 0) + 1

        return {
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "node_types": type_counts,
            "edge_types": edge_type_counts,
        }

    def _traverse_edges(
        self,
        start_id: str,
        direction: str,
        edge_type: str | None,
        depth: int,
    ) -> list[dict]:
        """BFS traversal from a node following edges of a given type/direction."""
        if start_id not in self.graph:
            return []

        visited: set[str] = set()
        results: list[dict] = []
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])

        while queue:
            current, current_depth = queue.popleft()
            if current_depth > 0 and current not in visited:
                visited.add(current)
                node_data = self.graph.nodes[current]
                results.append({
                    "id": current,
                    "depth": current_depth,
                    **node_data,
                })

            if current_depth >= depth:
                continue

            neighbors = self._get_neighbors(current, direction, edge_type)
            for neighbor in neighbors:
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))

        return results

    def _get_neighbors(
        self, node_id: str, direction: str, edge_type: str | None
    ) -> list[str]:
        """Get neighbors of a node filtered by direction and edge type."""
        neighbors = []

        if direction in ("in", "both"):
            for pred in self.graph.predecessors(node_id):
                edge_data = self.graph.edges[pred, node_id]
                if edge_type is None or edge_data.get("type") == edge_type:
                    neighbors.append(pred)

        if direction in ("out", "both"):
            for succ in self.graph.successors(node_id):
                edge_data = self.graph.edges[node_id, succ]
                if edge_type is None or edge_data.get("type") == edge_type:
                    neighbors.append(succ)

        return neighbors
