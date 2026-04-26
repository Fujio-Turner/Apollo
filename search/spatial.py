from __future__ import annotations

from collections import deque

import networkx as nx


class SpatialSearch:
    def __init__(self, graph: nx.DiGraph) -> None:
        self.graph = graph

    def _node_to_result(self, node_id: str) -> dict | None:
        data = self.graph.nodes.get(node_id)
        if data is None:
            return None
        spatial = data.get("spatial")
        if spatial is None:
            return None
        return {
            "id": node_id,
            "name": data.get("name"),
            "type": data.get("type"),
            "path": data.get("path"),
            "line_start": data.get("line_start"),
            "spatial": spatial,
        }

    def range_query(
        self,
        cx: float,
        cy: float,
        range_deg: float,
        z_min: float = 0.0,
        top: int = 20,
        face: int | None = None,
    ) -> list[dict]:
        results: list[dict] = []
        for node_id, data in self.graph.nodes(data=True):
            spatial = data.get("spatial")
            if spatial is None:
                continue
            x, y, z = spatial["x"], spatial["y"], spatial["z"]
            if abs(x - cx) > range_deg or abs(y - cy) > range_deg:
                continue
            if z < z_min:
                continue
            if face is not None and spatial["face"] != face:
                continue
            entry = self._node_to_result(node_id)
            if entry is None:
                continue
            entry["distance"] = abs(x - cx) + abs(y - cy)
            results.append(entry)

        results.sort(key=lambda r: r["distance"])
        return results[:top] if top > 0 else results

    def face_query(self, face: int) -> list[dict]:
        results: list[dict] = []
        for node_id, data in self.graph.nodes(data=True):
            spatial = data.get("spatial")
            if spatial is None:
                continue
            if spatial["face"] != face:
                continue
            entry = self._node_to_result(node_id)
            if entry is not None:
                results.append(entry)

        results.sort(key=lambda r: r["spatial"]["z"], reverse=True)
        return results

    def near_node(
        self,
        node_id: str,
        range_deg: float = 30.0,
        top: int = 20,
    ) -> list[dict]:
        data = self.graph.nodes.get(node_id)
        if data is None:
            return []
        spatial = data.get("spatial")
        if spatial is None:
            return []

        results = self.range_query(
            cx=spatial["x"],
            cy=spatial["y"],
            range_deg=range_deg,
            top=top + 1,
        )
        return [r for r in results if r["id"] != node_id][:top]

    def spatial_walk(
        self,
        node_id: str,
        step: float = 15.0,
        max_rings: int = 5,
    ) -> list[dict[str, any]]:
        data = self.graph.nodes.get(node_id)
        if data is None:
            return []
        spatial = data.get("spatial")
        if spatial is None:
            return []

        cx, cy = spatial["x"], spatial["y"]
        seen: set[str] = set()

        rings: list[dict[str, any]] = []
        for ring_idx in range(max_rings + 1):
            current_range = ring_idx * step
            if ring_idx == 0:
                entry = self._node_to_result(node_id)
                nodes = [entry] if entry is not None else []
                seen.add(node_id)
            else:
                candidates = self.range_query(
                    cx=cx,
                    cy=cy,
                    range_deg=current_range,
                    top=0,
                )
                nodes = []
                for c in candidates:
                    if c["id"] not in seen:
                        seen.add(c["id"])
                        nodes.append(c)

            rings.append({
                "ring": ring_idx,
                "range": current_range,
                "nodes": nodes,
            })

        return rings

    def combined_spatial_structural(
        self,
        cx: float,
        cy: float,
        range_deg: float,
        direction: str = "out",
        edge_type: str = "calls",
        depth: int = 1,
        z_min: float = 0.0,
    ) -> dict:
        spatial_matches = self.range_query(cx=cx, cy=cy, range_deg=range_deg, z_min=z_min)

        traversal_results: list[dict] = []
        for match in spatial_matches:
            mid = match["id"]
            visited: set[str] = {mid}
            frontier: deque[tuple[str, int]] = deque([(mid, 0)])
            neighbors: list[dict] = []

            while frontier:
                current, d = frontier.popleft()
                if d >= depth:
                    continue
                if direction == "out":
                    adjacent = self.graph.successors(current)
                else:
                    adjacent = self.graph.predecessors(current)
                for adj in adjacent:
                    if adj in visited:
                        continue
                    edge = self.graph.edges[current, adj] if direction == "out" else self.graph.edges[adj, current]
                    if edge.get("type") != edge_type:
                        continue
                    visited.add(adj)
                    entry = self._node_to_result(adj)
                    if entry is not None:
                        neighbors.append(entry)
                    frontier.append((adj, d + 1))

            traversal_results.append({
                "source": mid,
                "neighbors": neighbors,
            })

        return {
            "spatial_matches": spatial_matches,
            "traversal_results": traversal_results,
        }
