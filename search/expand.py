"""
Combined semantic + graph-expansion helper.

Given a list of seed nodes from a vector search, walks the graph outward
along callers/callees/neighbors/references edges and returns a clustered
list of ``{seed_fields..., "neighbors": [...]}`` rows so the AI / CLI /
HTTP layer can serve a combined answer in one round.

See ``docs/work/PLAN_COMBINED_SEMANTIC_GRAPH_SEARCH.md §3.1`` for the
design rationale.
"""
from __future__ import annotations

from typing import Literal

import networkx as nx

from graph.query import GraphQuery


ExpandKind = Literal["none", "callers", "callees", "neighbors", "references"]

_VALID_EXPAND = ("none", "callers", "callees", "neighbors", "references")


def _direction_for(expand: ExpandKind) -> str:
    """Return the human-readable edge direction tag for a neighbor row."""
    if expand == "callers":
        return "in"
    if expand == "callees":
        return "out"
    # neighbors / references walk both directions.
    return "both"


def _edge_label(expand: ExpandKind) -> str:
    """Default ``edge`` field for the neighbor row.

    For callers/callees the edge type is always ``calls`` by definition
    of :class:`GraphQuery`. For ``neighbors`` / ``references`` we report
    the actual edge type discovered during traversal — but the BFS in
    :class:`GraphQuery` discards that information, so v1 records the
    expansion kind itself. This is documented as a known limitation in
    the plan §3.6 (future refinement).
    """
    if expand in ("callers", "callees"):
        return "calls"
    return expand  # "neighbors" / "references"


def expand_hits(
    graph: nx.DiGraph,
    seeds: list[dict],
    expand: ExpandKind,
    depth: int = 1,
    per_seed_cap: int = 10,
) -> list[dict]:
    """Cluster vector-search seeds with their structural neighborhood.

    Parameters
    ----------
    graph:
        The in-memory ``nx.DiGraph`` used by :class:`GraphQuery`.
    seeds:
        Rows from ``SemanticSearch.search`` — each must carry at least
        ``id`` and ``score``.
    expand:
        One of ``none / callers / callees / neighbors / references``.
        ``none`` returns the seeds with an empty ``neighbors`` list.
    depth:
        BFS depth for the expansion. Must be ``>= 1`` when ``expand``
        is not ``none``.
    per_seed_cap:
        Maximum neighbors per seed; the rest are summarized via a
        ``truncated`` field on the seed.

    Returns
    -------
    A list of clustered dicts. Each cluster carries the original seed
    fields plus a ``neighbors`` list of
    ``{id, name, type, path, line_start, line_end, edge, direction,
       depth, score}`` rows. When the cap kicks in, the seed gains a
    ``truncated`` field with the count of dropped neighbors.

    Raises
    ------
    ValueError:
        If ``expand`` is unknown, or ``depth <= 0`` for a non-``none``
        expansion.
    """
    if expand not in _VALID_EXPAND:
        raise ValueError(
            f"expand must be one of {_VALID_EXPAND}, got {expand!r}"
        )

    if expand == "none":
        return [{**s, "neighbors": []} for s in seeds]

    if depth <= 0:
        raise ValueError(f"depth must be >= 1 when expanding, got {depth}")

    query = GraphQuery(graph)
    direction = _direction_for(expand)
    edge_label = _edge_label(expand)

    clustered: list[dict] = []
    for seed in seeds:
        seed_id = seed.get("id")
        seed_score = float(seed.get("score", 0.0))

        if seed_id is None or seed_id not in graph:
            clustered.append({**seed, "neighbors": []})
            continue

        if expand == "callers":
            raw = query.callers(seed_id, depth=depth)
        elif expand == "callees":
            raw = query.callees(seed_id, depth=depth)
        elif expand == "neighbors":
            raw = query.neighbors(seed_id, depth=depth)
        else:  # references
            raw = query.references(seed_id, depth=depth)

        # Sort neighbors by decayed score (i.e. by depth ascending) so the
        # cap keeps the closest blast radius first.
        raw.sort(key=lambda r: r.get("depth", 1))

        total = len(raw)
        capped = raw[:per_seed_cap]

        neighbors: list[dict] = []
        for n in capped:
            d = int(n.get("depth", 1))
            neighbors.append({
                "id": n.get("id"),
                "name": n.get("name"),
                "type": n.get("type"),
                "path": n.get("path"),
                "line_start": n.get("line_start"),
                "line_end": n.get("line_end"),
                "edge": edge_label,
                "direction": direction,
                "depth": d,
                # TODO(phase-2.5): re-score against the query embedding
                # once neighbor embeddings are guaranteed. For v1 we use
                # the simple seed_score / (1+d) decay documented in
                # PLAN §3.6.
                "score": seed_score / (1 + d),
            })

        cluster = {**seed, "neighbors": neighbors}
        if total > per_seed_cap:
            cluster["truncated"] = total - per_seed_cap
        clustered.append(cluster)

    return clustered
