"""
Storage backend factory — creates the right store based on backend name.
"""
from __future__ import annotations

from .json_store import JsonStore


def open_store(backend: str, location: str) -> JsonStore:
    """Create a storage backend instance.

    Args:
        backend: "json" or "cblite"
        location: file path (json) or database directory (cblite)
    """
    if backend == "json":
        return JsonStore(location)

    if backend == "cblite":
        from .cblite.store import CouchbaseLiteStore
        return CouchbaseLiteStore(location)

    raise ValueError(f"Unknown storage backend: {backend!r}. Use 'json' or 'cblite'.")
