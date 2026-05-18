# SPDX-License-Identifier: BUSL-1.1
"""Apollo ML module — index-time precomputation + query-time chat tools.

See `docs/work/PLAN_ML_LIBS.md` for the design and per-phase rationale.

Index-time work lives in :mod:`ml.passes` (UMAP layout, HDBSCAN clusters,
PageRank/betweenness, KeyBERT keyphrases, Louvain communities,
IsolationForest outliers, BERTopic topics, vulture dead-code candidates).

Query-time chat tools live in :mod:`ml.tools` and are pure reads off the
graph payload populated by the passes.

Every dependency is **optional**. If a library isn't installed, the
corresponding pass becomes a no-op and the matching tool returns
``{"ml_available": false, "reason": "<lib> not installed"}`` so the
agent can degrade gracefully instead of erroring.
"""
from .passes import (
    run_all_passes,
    pass_layout_clusters,
    pass_centrality,
    pass_keyphrases,
    pass_communities,
    pass_outliers,
    pass_topics,
    pass_dead_code,
)
from .tools import (
    list_clusters,
    get_cluster_members,
    get_node_importance,
    search_graph_by_keyphrase,
    get_community,
    find_outliers,
    find_dead_code,
    get_topics,
)

__all__ = [
    "run_all_passes",
    "pass_layout_clusters",
    "pass_centrality",
    "pass_keyphrases",
    "pass_communities",
    "pass_outliers",
    "pass_topics",
    "pass_dead_code",
    "list_clusters",
    "get_cluster_members",
    "get_node_importance",
    "search_graph_by_keyphrase",
    "get_community",
    "find_outliers",
    "find_dead_code",
    "get_topics",
]
