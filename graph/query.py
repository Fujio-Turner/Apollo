# SPDX-License-Identifier: BUSL-1.1
"""
Graph query engine — structural queries over the knowledge graph.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import networkx as nx


# ─────────────────────────────────────────────────────────────────────
# Phase 4 of PLAN_INDEX_MEMORY_AND_CONCURRENCY — source-text helper.
#
# Before Phase 4, every function / method / class / document / section
# / code_block node carried its own ``source`` string attribute.
# For a typical code file that means the same characters are duplicated
# 3–5× across nodes (class source contains all method sources, which
# contain func sources, etc.) — the single biggest memory hog on a
# medium-sized project.
#
# Phase 4 stores one copy of every indexed file's full source text in
# the graph-level sidecar ``graph.graph["_file_text"]`` and drops the
# per-node ``source`` attr. Readers call :func:`get_source` to slice
# the file text on demand using the node's ``line_start`` / ``line_end``
# (which the parsers were already populating).
#
# Backward compat: a legacy graph loaded from disk that still has
# per-node ``source`` attrs falls through the legacy branch — the
# helper returns ``data["source"]`` unchanged. So this change is safe
# to deploy against existing ``graph.json`` files.
# ─────────────────────────────────────────────────────────────────────
def get_source(graph: nx.DiGraph, node_id: str) -> str:
    """Return the source text for ``node_id`` (Phase 4).

    Resolution order:

    1. If ``graph.nodes[node_id]`` carries a legacy ``source`` string
       attribute, return it verbatim (back-compat for graphs persisted
       before Phase 4 added the file-text sidecar).
    2. Otherwise look up ``graph.graph["_file_text"][data["path"]]``
       and slice it by ``data["line_start"]`` / ``data["line_end"]``
       (1-indexed, inclusive — matches the parser output).
    3. If neither is available, return an empty string. Callers should
       treat the result the way they treated the old empty/missing
       ``source`` attribute (skip embedding, skip keyword extraction,
       …) — no exception is raised.

    The function never mutates the graph or the node attrs; it only
    reads. It is safe to call concurrently.
    """
    if node_id not in graph.nodes:
        return ""
    data = graph.nodes[node_id]
    legacy = data.get("source")
    if isinstance(legacy, str):
        return legacy
    path = data.get("path")
    if not path:
        return ""
    file_text_map = graph.graph.get("_file_text") or {}
    text = file_text_map.get(path)
    if not isinstance(text, str) or not text:
        return ""
    ls = data.get("line_start")
    le = data.get("line_end")
    if ls is None or le is None:
        # Caller asked for a sub-range that doesn't exist — return the
        # whole file rather than nothing so the worst case is "too much
        # context" instead of "no context".
        return text
    try:
        ls_i = max(1, int(ls))
        le_i = int(le)
    except (TypeError, ValueError):
        return text
    if le_i < ls_i:
        return ""
    # ``splitlines(keepends=False)`` would lose the trailing newline
    # discrimination some callers (e.g. embedding text comparison)
    # care about. ``str.split("\n")`` is faithful to the source bytes.
    lines = text.split("\n")
    # Clamp end to file size — the parser may have reported a
    # line_end one past EOF for files without a trailing newline.
    le_i = min(len(lines), le_i)
    return "\n".join(lines[ls_i - 1:le_i])


def _normalize_node_types(
    node_type: str | list[str] | tuple[str, ...] | None,
) -> frozenset[str] | None:
    """Normalize a ``node_type`` argument to a frozenset of types or None.

    Accepts ``None``, a single type string, a comma- or whitespace-separated
    string (``"function,class"``), or any iterable of strings. Empty or
    whitespace-only input returns ``None`` so callers treat it as
    "no filter".
    """
    if node_type is None:
        return None
    if isinstance(node_type, str):
        # Split on comma; trim whitespace on each piece.
        parts = [p.strip() for p in node_type.split(",")]
        parts = [p for p in parts if p]
        if not parts:
            return None
        return frozenset(parts)
    # Treat as iterable.
    parts = [str(p).strip() for p in node_type if str(p).strip()]
    if not parts:
        return None
    return frozenset(parts)


class GraphQuery:
    """Query interface for the code knowledge graph."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph

    def find(
        self,
        name: str,
        node_type: str | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict]:
        """Find nodes by name (substring match) and optional type filter.

        ``node_type`` accepts a single type string (``"function"``), a
        comma- or whitespace-separated string (``"function,class,method"``),
        or a list/tuple of strings. When multiple types are supplied, a
        node matches if its type is in the set.
        """
        results = []
        name_lower = name.lower()
        type_set = _normalize_node_types(node_type)
        for node_id, data in self.graph.nodes(data=True):
            node_name = data.get("name", "")
            if name_lower not in node_name.lower():
                continue
            if type_set is not None and data.get("type") not in type_set:
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

    def children(
        self,
        node_id: str,
        node_type: str | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict]:
        """Find nodes that this node defines/contains.

        ``node_type`` accepts the same forms as :meth:`find`.
        """
        results = []
        type_set = _normalize_node_types(node_type)
        for succ in self.graph.successors(node_id):
            edge_data = self.graph.edges[node_id, succ]
            if edge_data.get("type") not in ("defines", "contains"):
                continue
            data = self.graph.nodes[succ]
            if type_set is not None and data.get("type") not in type_set:
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
