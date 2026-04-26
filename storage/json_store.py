"""
JSON storage backend — saves and loads the graph as simple JSON files.
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx


class JsonStore:
    """Persist a NetworkX graph to a JSON file."""

    def __init__(self, filepath: str | None = None):
        self._filepath = filepath

    def save(self, graph: nx.DiGraph, filepath: str | None = None):
        """Save the graph to a JSON file."""
        path = Path(filepath or self._filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "nodes": [],
            "edges": [],
        }

        for node_id, attrs in graph.nodes(data=True):
            node = {"id": node_id}
            node.update(attrs)
            data["nodes"].append(node)

        for src, dst, attrs in graph.edges(data=True):
            edge = {"source": src, "target": dst}
            edge.update(attrs)
            data["edges"].append(edge)

        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"), default=str)

    def load(self, filepath: str | None = None, *, include_embeddings: bool = True) -> nx.DiGraph:
        """Load a graph from a JSON file."""
        path = Path(filepath or self._filepath)
        raw = json.loads(path.read_text(encoding="utf-8"))

        graph = nx.DiGraph()

        for node in raw["nodes"]:
            node = dict(node)
            node_id = node.pop("id")
            if not include_embeddings:
                node.pop("embedding", None)
            graph.add_node(node_id, **node)

        for edge in raw["edges"]:
            edge = dict(edge)
            src = edge.pop("source")
            dst = edge.pop("target")
            graph.add_edge(src, dst, **edge)

        return graph

    def close(self) -> None:
        pass

    def delete(self) -> None:
        """Delete the index file and associated data."""
        path = Path(self._filepath)
        if path.exists():
            path.unlink()
        # Also remove file hashes
        hashes = Path(".apollo/file_hashes.json")
        if hashes.exists():
            hashes.unlink()
