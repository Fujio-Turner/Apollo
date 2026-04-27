"""
Storage backend protocol — defines the interface all storage backends must implement.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable, TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from apollo.graph.incremental import GraphDiff


@runtime_checkable
class GraphStore(Protocol):
    """Protocol for graph persistence backends."""

    def save(self, graph: nx.DiGraph) -> None: ...

    def load(self, *, include_embeddings: bool = True) -> nx.DiGraph: ...

    def save_diff(self, diff: GraphDiff) -> None:
        """Save only the changes (diff) to the graph.
        
        For JSON backend, this may just call save(). For CBL, this performs
        targeted document upserts/purges within a transaction.
        """
        ...

    def close(self) -> None: ...

    def delete(self) -> None: ...
