"""
Storage backend protocol — defines the interface all storage backends must implement.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import networkx as nx


@runtime_checkable
class GraphStore(Protocol):
    """Protocol for graph persistence backends."""

    def save(self, graph: nx.DiGraph) -> None: ...

    def load(self, *, include_embeddings: bool = True) -> nx.DiGraph: ...

    def close(self) -> None: ...

    def delete(self) -> None: ...
