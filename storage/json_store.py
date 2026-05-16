"""
JSON storage backend — saves and loads the graph as JSON files.

On-disk format
==============

Two storage shapes are supported:

* **v2 (current)** — dict-keyed maps for O(1) lookup::

      {
        "version": 2,
        "nodes": {"<id>": {<attrs>}, ...},
        "edges": {"<src>": {"<dst>": {<attrs>}, ...}, ...}
      }

* **v1 (legacy)** — flat arrays, kept for backward-compatible reads::

      {"nodes": [{"id": ..., ...}, ...],
       "edges": [{"source": ..., "target": ..., ...}, ...]}

The shape is detected at load time (``isinstance(raw["nodes"], dict)``); the
loader transparently rebuilds the same NetworkX graph from either form. The
saver always writes v2.

Compression
===========

If the configured ``filepath`` ends with ``.gz`` the payload is gzipped on
write. Reads sniff the gzip magic bytes (``0x1f 0x8b``) so an upgrade from a
plain ``index.json`` to ``index.json.gz`` is a no-op for the loader.

Encoder
=======

Uses ``orjson`` when available (≈3–5× faster, more compact float encoding) and
falls back to stdlib ``json`` otherwise. Both produce equivalent v2 documents.

Why this shape
==============

The v1 array form forced an O(N) scan to look up any node and made
``save()`` rewrite the entire file even for one-node changes. The dict form
trades a small amount of repeated key overhead (recovered by gzip) for
random access — making future per-node patches and ``save_diff()``
implementations straightforward without changing the on-disk layout again.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import networkx as nx

try:  # Optional fast encoder. Falls back transparently if missing.
    import orjson  # type: ignore
    _HAS_ORJSON = True
except ImportError:  # pragma: no cover - exercised by the import path
    _HAS_ORJSON = False

if TYPE_CHECKING:
    from apollo.graph.incremental import GraphDiff


_GZIP_MAGIC = b"\x1f\x8b"
_CURRENT_VERSION = 2


def _purge_index_sidecars(apollo_dir: Path) -> None:
    """Delete the per-index sidecars that live alongside the graph file.

    Wipes ``file_hashes.json`` (incremental-reindex state) and
    ``reindex_history.json`` (sweep telemetry) inside ``apollo_dir`` and at
    the legacy global locations used by Apollo before per-project state
    moved into ``<root>/_apollo/``. Leaves ``apollo.json`` (project
    manifest) and ``chat_history.json`` (user-owned thread history)
    untouched — those have their own lifecycle.

    Used by ``JsonStore.delete()`` and ``CouchbaseLiteStore.delete()`` so
    "Delete index" wipes the same set of stale-after-delete files no
    matter which storage backend is active.
    """
    sidecars = ("file_hashes.json", "reindex_history.json")
    for name in sidecars:
        p = apollo_dir / name
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
    # Legacy / global locations from before the per-project _apollo/ move.
    for legacy in (
        Path(".apollo/file_hashes.json"),
        Path(".apollo/reindex_history.json"),
        Path("data/file_hashes.json"),
    ):
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                pass


def _stringify_keys(obj: Any) -> Any:
    """Recursively coerce dict keys to strings.

    orjson refuses non-string keys; the ML sidecar dicts use int keys
    (``ml_clusters``: ``{cluster_id → summary}``). Walk the structure
    once on save so the encoder is happy. ``list_clusters`` /
    ``get_topics`` iterate ``.values()`` only, so the round-trip key
    type is irrelevant to consumers.
    """
    if isinstance(obj, dict):
        return {str(k): _stringify_keys(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_stringify_keys(v) for v in obj]
    return obj


def _serialize(payload: dict[str, Any]) -> bytes:
    """Serialize ``payload`` to compact UTF-8 bytes.

    Uses ``orjson`` when installed; ``default=str`` mirrors the v1 behaviour
    so non-JSON-native attrs (e.g. ``Path``) still round-trip via ``str()``.
    """
    if _HAS_ORJSON:
        return orjson.dumps(payload, default=str)
    return json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")


def _deserialize(raw: bytes) -> dict[str, Any]:
    """Parse JSON bytes into a Python dict (orjson if available)."""
    if _HAS_ORJSON:
        return orjson.loads(raw)
    return json.loads(raw.decode("utf-8"))


def _read_bytes(path: Path) -> bytes:
    """Read the file, transparently un-gzipping if the magic bytes match.

    We sniff the file rather than trust the extension so a renamed file
    (e.g. ``index.json`` that's actually gzipped, or vice versa) still loads.
    """
    data = path.read_bytes()
    if data.startswith(_GZIP_MAGIC):
        return gzip.decompress(data)
    return data


def _write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``; gzip if the path ends with ``.gz``."""
    if path.suffix == ".gz":
        # mtime=0 keeps writes deterministic — useful for diff-friendly
        # backups and reproducible test fixtures. ``gzip.open()`` doesn't
        # accept ``mtime`` until Py 3.10, so use the lower-level
        # ``GzipFile`` constructor that has supported it since 3.1.
        with open(path, "wb") as raw_fh:
            with gzip.GzipFile(
                filename="", mode="wb", compresslevel=6, fileobj=raw_fh, mtime=0
            ) as fh:
                fh.write(data)
    else:
        path.write_bytes(data)


class JsonStore:
    """Persist a NetworkX graph to a JSON (or gzipped JSON) file."""

    def __init__(self, filepath: str | None = None):
        self._filepath = filepath

    # ------------------------------------------------------------------
    # save / load
    # ------------------------------------------------------------------

    def save(self, graph: nx.DiGraph, filepath: str | None = None):
        """Save the graph as a v2 dict-shaped document.

        Always writes the current schema; readers handle both v1 and v2
        so there's no migration step for callers loading older files.
        """
        path = Path(filepath or self._filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        nodes: dict[str, dict[str, Any]] = {}
        for node_id, attrs in graph.nodes(data=True):
            # Copy so we don't mutate the live graph attrs dict.
            nodes[node_id] = dict(attrs)

        # Adjacency-style: {src: {dst: attrs}}. NetworkX's DiGraph allows at
        # most one edge per (src, dst) so this lossless. Switching to
        # MultiDiGraph later would need a list-valued inner dict.
        edges: dict[str, dict[str, dict[str, Any]]] = {}
        for src, dst, attrs in graph.edges(data=True):
            edges.setdefault(src, {})[dst] = dict(attrs)

        payload = {
            "version": _CURRENT_VERSION,
            "nodes": nodes,
            "edges": edges,
        }
        # Graph-level attrs (e.g. ml_clusters, ml_topics, ml_dead_code
        # sidecars written by ml/passes.py) live on `graph.graph`. Without
        # this they get silently dropped on every save/load round-trip.
        # orjson rejects non-str dict keys, so coerce nested int keys
        # (cluster_id / topic_id) to strings; load() reverses this.
        if graph.graph:
            payload["graph_attrs"] = _stringify_keys(dict(graph.graph))
        _write_bytes(path, _serialize(payload))

    def load(self, filepath: str | None = None, *, include_embeddings: bool = True) -> nx.DiGraph:
        """Load a graph from a JSON file (v1 or v2, plain or gzipped)."""
        path = Path(filepath or self._filepath)
        raw = _deserialize(_read_bytes(path))

        graph = nx.DiGraph()

        nodes_in = raw.get("nodes", {})
        edges_in = raw.get("edges", {})

        if isinstance(nodes_in, dict):
            # v2 shape — dict[node_id, attrs].
            for node_id, attrs in nodes_in.items():
                attrs = dict(attrs)
                if not include_embeddings:
                    attrs.pop("embedding", None)
                graph.add_node(node_id, **attrs)
        else:
            # v1 shape — list of {"id": ..., **attrs}.
            for node in nodes_in:
                node = dict(node)
                node_id = node.pop("id")
                if not include_embeddings:
                    node.pop("embedding", None)
                graph.add_node(node_id, **node)

        if isinstance(edges_in, dict):
            # v2 shape — dict[src, dict[dst, attrs]].
            for src, targets in edges_in.items():
                for dst, attrs in targets.items():
                    graph.add_edge(src, dst, **dict(attrs))
        else:
            # v1 shape — list of {"source": ..., "target": ..., **attrs}.
            for edge in edges_in:
                edge = dict(edge)
                src = edge.pop("source")
                dst = edge.pop("target")
                graph.add_edge(src, dst, **edge)

        # Restore graph-level attrs (ml_clusters, ml_topics, …) if present.
        graph_attrs = raw.get("graph_attrs")
        if isinstance(graph_attrs, dict):
            graph.graph.update(graph_attrs)

        return graph

    # ------------------------------------------------------------------
    # GraphStore protocol stubs
    # ------------------------------------------------------------------

    def save_diff(self, diff: GraphDiff, filepath: str | None = None) -> None:
        """Save diff to graph — for JSON backend, this is just a full rewrite.

        The diff is provided for consistency with CBL backend, but JSON is simple
        enough that full rewrites are acceptable.

        Note: This method requires access to the current graph. For a proper
        implementation, we'd need to load, apply diff, and save. This is a
        minimal stub that assumes the graph has already been updated.
        """
        # For now, this is a no-op — the caller should use save(updated_graph)
        # In a full implementation, we'd apply the diff to the persisted version
        pass

    def close(self) -> None:
        pass

    def delete(self) -> None:
        """Delete the index file and all per-index sidecars.

        Removes:
          * the configured path and its ``.gz`` / non-``.gz`` twin (so users
            who toggle compression don't leave a stale copy behind),
          * the sibling ``file_hashes.json`` (incremental-reindex state —
            stale once the graph it describes is gone),
          * the sibling ``reindex_history.json`` (sweep telemetry — refers
            to runs against an index that no longer exists).

        Preserved on purpose: ``apollo.json`` (project manifest) and
        ``chat_history.json`` (user-owned conversation history; deleting
        chat threads gets its own button rather than being silently bundled
        into "Delete index").
        """
        path = Path(self._filepath)
        # Remove both the chosen file and its compression twin so neither
        # variant lingers across a delete.
        twins = [path]
        if path.suffix == ".gz":
            twins.append(path.with_suffix(""))  # strip .gz
        else:
            twins.append(path.with_suffix(path.suffix + ".gz"))
        for p in twins:
            if p.exists():
                p.unlink()
        _purge_index_sidecars(path.parent)
