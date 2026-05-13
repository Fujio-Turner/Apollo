"""
Cached, lazy reverse-indices over a NetworkX graph.

Why this exists
===============
Several chat tools and web endpoints repeatedly walk the entire graph
(``graph.nodes(data=True)``) per call to bucket nodes by ``type``,
``path``, or to compute aggregate statistics. With even a moderately
sized project (10k–50k nodes) a single chat tool round-trip was running
that O(N) walk five or more times. The work is identical between calls
because the graph is mostly static during a session — and when the
watcher *does* mutate it, it changes (node_count, edge_count) which we
use as a cheap cache-invalidation key.

Usage
-----
``get_indices(graph)`` returns a ``GraphIndices`` instance whose data
members are computed lazily on first access. The cache lives on the
graph object itself in ``graph.graph['_apollo_indices']``; if the graph
mutates (signature changes) we transparently rebuild only the parts
that get accessed again.

This module deliberately stays small — it's a perf shim, not a
replacement for ``GraphQuery``. New callers that need richer queries
should still go through ``apollo.graph.query.GraphQuery``.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import networkx as nx


class GraphIndices:
    """Lazily computed reverse indices over a graph.

    The instance holds a snapshot signature ``(num_nodes, num_edges)``
    captured at construction time. Callers should re-fetch via
    :func:`get_indices` rather than holding onto an instance long-term:
    the watcher mutates the graph in place and a stale instance would
    return stale buckets.
    """

    __slots__ = ("_graph", "signature", "_by_type", "_by_path", "_wordcloud")

    def __init__(self, graph: "nx.DiGraph") -> None:
        self._graph = graph
        self.signature = (graph.number_of_nodes(), graph.number_of_edges())
        self._by_type: dict[str, list[str]] | None = None
        self._by_path: dict[str, list[str]] | None = None
        self._wordcloud: tuple[
            dict[str, float], dict[str, int]
        ] | None = None  # (strengths, counts) keyed by node name

    def by_type(self) -> dict[str, list[str]]:
        """Return ``{node_type: [node_id, ...]}`` over all nodes."""
        if self._by_type is None:
            buckets: dict[str, list[str]] = defaultdict(list)
            for nid, data in self._graph.nodes(data=True):
                t = data.get("type") or ""
                buckets[t].append(nid)
            self._by_type = dict(buckets)
        return self._by_type

    def by_path(self) -> dict[str, list[str]]:
        """Return ``{path: [node_id, ...]}`` over all nodes with a path."""
        if self._by_path is None:
            buckets: dict[str, list[str]] = defaultdict(list)
            for nid, data in self._graph.nodes(data=True):
                p = data.get("path")
                if p:
                    buckets[p].append(nid)
            self._by_path = dict(buckets)
        return self._by_path

    def wordcloud(self, exclude_types: frozenset[str]) -> tuple[
        dict[str, float], dict[str, int]
    ]:
        """Return ``(strengths_by_name, counts_by_name)`` for the wordcloud.

        Keys are node ``name`` values; ``strength[n]`` is the sum of
        node degrees, ``count[n]`` is how many nodes share that name.
        Cached so multiple wordcloud requests within a session don't
        re-walk the entire graph.
        """
        if self._wordcloud is None:
            strengths: dict[str, float] = defaultdict(float)
            counts: dict[str, int] = defaultdict(int)
            graph = self._graph
            for nid, data in graph.nodes(data=True):
                if data.get("type", "") in exclude_types:
                    continue
                n = data.get("name", "")
                if not n:
                    continue
                # ``graph.degree(nid)`` is O(1) on NetworkX DiGraphs.
                strengths[n] += graph.degree(nid)
                counts[n] += 1
            self._wordcloud = (dict(strengths), dict(counts))
        return self._wordcloud


def get_indices(graph: "nx.DiGraph") -> GraphIndices:
    """Return a (cached) :class:`GraphIndices` for ``graph``.

    The cache key is the graph's ``(num_nodes, num_edges)`` signature.
    If those numbers haven't changed since the last call we return the
    same instance — most chat tool round-trips therefore reuse the
    same bucket maps without ever re-walking the graph.
    """
    cache_holder = graph.graph
    cached = cache_holder.get("_apollo_indices")
    sig = (graph.number_of_nodes(), graph.number_of_edges())
    if isinstance(cached, GraphIndices) and cached.signature == sig:
        return cached
    inst = GraphIndices(graph)
    cache_holder["_apollo_indices"] = inst
    return inst


def invalidate(graph: "nx.DiGraph") -> None:
    """Drop any cached indices for ``graph``.

    Callers should invoke this after a known-mutating operation
    (e.g. a watcher batch finishing a delta) when they want the next
    ``get_indices`` call to start fresh even if the size signature
    happens to match.
    """
    graph.graph.pop("_apollo_indices", None)
