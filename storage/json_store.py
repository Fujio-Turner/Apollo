# SPDX-License-Identifier: BUSL-1.1
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

import base64
import gzip
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import networkx as nx
import numpy as np

try:  # Optional fast encoder. Falls back transparently if missing.
    import orjson  # type: ignore
    _HAS_ORJSON = True
except ImportError:  # pragma: no cover - exercised by the import path
    _HAS_ORJSON = False

if TYPE_CHECKING:
    from apollo.graph.incremental import GraphDiff


_GZIP_MAGIC = b"\x1f\x8b"
_CURRENT_VERSION = 2


# ─────────────────────────────────────────────────────────────────────
# Embedding (de)serialization helpers — Phase 3 of
# PLAN_INDEX_MEMORY_AND_CONCURRENCY.
#
# In-memory embeddings are now ``float32`` numpy arrays (Phase 3 of the
# plan). To keep the on-disk JSON compact we no longer write 384 JSON
# floats per node; instead we write a base64-encoded byte string of the
# raw ``float32`` buffer plus its dtype + dim. For a 384-dim vector this
# is ~520 base64 chars vs. ~7 KB of `[0.123, 0.456, ...]` JSON floats.
#
# Backward compatibility: a v2-shaped file that still uses the legacy
# ``"embedding": [...]`` list form loads cleanly under the new reader —
# the load path detects either shape and reconstructs an ndarray either
# way.
# ─────────────────────────────────────────────────────────────────────
def _encode_embedding_attrs(attrs: dict) -> None:
    """Replace ``attrs["embedding"]`` (ndarray) with base64-encoded
    raw bytes, in-place.

    Idempotent: lists are converted to ndarrays first; entries that are
    already base64-encoded (``embedding_b64`` already present) are
    skipped. Mutates the caller's dict — only safe if the caller passed
    in a *copy* (which :meth:`JsonStore.save` does).
    """
    if "embedding_b64" in attrs:
        # Already encoded (loaded from disk without modification).
        return
    emb = attrs.get("embedding")
    if emb is None:
        return
    if isinstance(emb, list):
        # Legacy in-memory path (watcher-batched) — coerce to ndarray
        # so the on-disk format is uniform.
        arr = np.asarray(emb, dtype=np.float32)
    elif isinstance(emb, np.ndarray):
        arr = emb if emb.dtype == np.float32 else emb.astype(np.float32, copy=False)
    else:
        # Unknown shape — leave it alone, orjson will likely fail (which
        # is a louder, more debuggable error than silently dropping it).
        return
    attrs.pop("embedding", None)
    attrs["embedding_b64"] = base64.b64encode(arr.tobytes()).decode("ascii")
    attrs["embedding_dtype"] = str(arr.dtype)
    attrs["embedding_dim"] = int(arr.shape[-1]) if arr.size else 0


def _decode_embedding_attrs(attrs: dict, *, include_embeddings: bool = True) -> None:
    """Reverse of :func:`_encode_embedding_attrs`.

    Looks for ``embedding_b64`` first (new format); if absent, leaves
    any pre-existing ``embedding`` list (legacy format) untouched
    *unless* it's a list, in which case it's promoted to an ndarray so
    the in-memory invariant matches what ``Embedder.embed_graph`` would
    have written.

    When ``include_embeddings`` is False both forms are stripped — keeps
    parity with the legacy loader's ``attrs.pop("embedding", None)``.
    """
    if not include_embeddings:
        attrs.pop("embedding", None)
        attrs.pop("embedding_b64", None)
        attrs.pop("embedding_dtype", None)
        attrs.pop("embedding_dim", None)
        return

    if "embedding_b64" in attrs:
        b64 = attrs.pop("embedding_b64")
        dtype = attrs.pop("embedding_dtype", "float32")
        # ``embedding_dim`` is informational; ndarray re-derives it from
        # the byte buffer + dtype. Drop the bookkeeping fields.
        attrs.pop("embedding_dim", None)
        try:
            raw = base64.b64decode(b64)
            attrs["embedding"] = np.frombuffer(raw, dtype=np.dtype(dtype)).copy()
        except Exception:
            # Corrupt sidecar — better to drop the embedding than to
            # crash the whole graph load. Search will fall back to
            # ``has_embeddings() == False`` for that node.
            pass
        return

    # Legacy format: ``embedding`` is a list. Promote in place so
    # callers don't see mixed list/ndarray types in the same graph.
    emb = attrs.get("embedding")
    if isinstance(emb, list):
        attrs["embedding"] = np.asarray(emb, dtype=np.float32)


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


def _stream_save(graph: nx.DiGraph, fh) -> None:
    """Stream a v2 JSON document to the binary file handle ``fh``.

    Phase 5 of PLAN_INDEX_MEMORY_AND_CONCURRENCY. Writes the document
    incrementally::

        {"version":2,"nodes":{<id>:<attrs>,...},
         "edges":{<src>:{<dst>:<attrs>,...},...},
         "graph_attrs":{...}}

    Per-node and per-edge ``dict(attrs)`` copies are still made (cheap —
    one dict per node, *not* per attribute), so the embedding-encoding
    step can mutate freely without touching the live graph. What we
    avoid is the previous "build a 2× copy of the entire graph in
    Python dicts, then call ``orjson.dumps`` on the whole thing" peak.

    Works equally with a plain file handle or a ``gzip.GzipFile`` — both
    expose ``.write(bytes)``.
    """
    fh.write(b'{"version":')
    fh.write(_serialize(_CURRENT_VERSION))
    fh.write(b',"nodes":{')

    first = True
    for node_id, attrs in graph.nodes(data=True):
        # Per-node defensive copy (so embedding encoding doesn't mutate
        # the live graph). Tiny — one node's worth of attrs.
        attrs_copy = dict(attrs)
        _encode_embedding_attrs(attrs_copy)
        if not first:
            fh.write(b",")
        fh.write(_serialize(str(node_id)))
        fh.write(b":")
        fh.write(_serialize(attrs_copy))
        first = False

    fh.write(b'},"edges":{')

    # Adjacency-style: {src: {dst: attrs}}. Stream grouped by source
    # node to keep the on-disk layout identical to the legacy writer.
    # ``graph.adj`` is NetworkX's public read-only view of the same
    # adjacency dict ``_adj`` exposes; iterating it avoids building
    # the intermediate (src, dst, attrs) tuples ``edges(data=True)``
    # would yield.
    adj = graph.adj
    first_src = True
    for src in graph.nodes():
        targets = adj.get(src) or {}
        if not targets:
            continue
        if not first_src:
            fh.write(b",")
        fh.write(_serialize(str(src)))
        fh.write(b":{")
        first_dst = True
        for dst, attrs in targets.items():
            if not first_dst:
                fh.write(b",")
            fh.write(_serialize(str(dst)))
            fh.write(b":")
            fh.write(_serialize(dict(attrs)))
            first_dst = False
        fh.write(b"}")
        first_src = False

    fh.write(b"}")

    # Graph-level attrs (ml_clusters, ml_topics, ml_dead_code sidecars
    # written by ml/passes.py). orjson rejects non-str dict keys, so we
    # stringify nested int keys (cluster_id / topic_id); load() ignores
    # the conversion since consumers iterate .values() only.
    if graph.graph:
        fh.write(b',"graph_attrs":')
        fh.write(_serialize(_stringify_keys(dict(graph.graph))))

    fh.write(b"}")


class JsonStore:
    """Persist a NetworkX graph to a JSON (or gzipped JSON) file."""

    def __init__(self, filepath: str | None = None):
        self._filepath = filepath

    # ------------------------------------------------------------------
    # save / load
    # ------------------------------------------------------------------

    def save(self, graph: nx.DiGraph, filepath: str | None = None):
        """Save the graph as a v2 dict-shaped document.

        Phase 5 of PLAN_INDEX_MEMORY_AND_CONCURRENCY: streams the JSON
        document directly to the file handle one node / edge at a time
        instead of building a single ``nodes={...}, edges={...}`` dict
        first and handing it to ``orjson.dumps`` whole. For a 1 GB
        in-memory graph this drops save-time peak RAM from ~2–3 GB
        (graph + per-attr `dict(attrs)` copies + full JSON blob) to
        roughly graph + one-node's-worth of buffer.

        Always writes the current schema; readers handle both v1 and v2
        so there's no migration step for callers loading older files.
        """
        path = Path(filepath or self._filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Pick the right write sink — gzip stream for ``.gz`` paths
        # (already a streaming sink), plain binary file otherwise.
        if path.suffix == ".gz":
            raw_fh = open(path, "wb")
            fh = gzip.GzipFile(
                filename="", mode="wb", compresslevel=6, fileobj=raw_fh, mtime=0,
            )
            close_outer = raw_fh
        else:
            fh = open(path, "wb")
            close_outer = None

        try:
            _stream_save(graph, fh)
        finally:
            fh.close()
            if close_outer is not None:
                close_outer.close()

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
                # Phase 3: decode the base64 sidecar (or promote a
                # legacy ``embedding: [...]`` list) to an ndarray.
                # Strips when ``include_embeddings`` is False.
                _decode_embedding_attrs(attrs, include_embeddings=include_embeddings)
                graph.add_node(node_id, **attrs)
        else:
            # v1 shape — list of {"id": ..., **attrs}.
            for node in nodes_in:
                node = dict(node)
                node_id = node.pop("id")
                _decode_embedding_attrs(node, include_embeddings=include_embeddings)
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
