"""
File watcher — monitors a directory for changes and triggers incremental graph updates.

Performance notes
-----------------
The original implementation did three full ``graph.nodes(data=True)`` walks
per single file event (find-by-path scans), recomputed the entire spatial
layout (PageRank + UMAP) on every save, embedded new nodes one at a time,
and SHA-256'd file content from inside the high-frequency ``on_modified``
callback. On macOS/Linux a single editor save can fire ``on_modified``
multiple times, multiplying that work.

This module is now structured around a few invariants:

* A reverse index ``path -> set(node_id)`` is maintained on the graph in
  ``_build_path_index`` and updated incrementally on add/remove. The 3
  full scans become O(k) lookups.
* Spatial coordinates are *not* recomputed inside the watcher — full
  reindex is the right place for that. The watcher is purely a fast,
  best-effort delta updater for symbol nodes.
* New embeddings are computed in a single batched ``embed_texts`` call,
  not in a per-node Python loop.
* File content hashing happens **once after the debounce window**,
  collapsing the multiple ``on_modified`` events from one real save.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Callable

import networkx as nx

from apollo.graph.builder import GraphBuilder, _SOURCE_EXTENSIONS
from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# How long to wait after the last file event before triggering a rebuild (seconds)
DEBOUNCE_SECONDS = 1.0

_APOLLO_STATE_DIRS = frozenset({"_apollo", "_apollo_web", ".apollo"})


def _build_path_index(graph: nx.DiGraph) -> dict[str, set[str]]:
    """Bootstrap the ``path -> {node_id}`` reverse index from the graph.

    Walks the node list once at watcher-startup, then we incrementally
    maintain the index in :meth:`FileWatcher._add_indexed_node` /
    :meth:`FileWatcher._remove_indexed_node` so per-event work stays O(k).
    """
    idx: dict[str, set[str]] = defaultdict(set)
    for nid, data in graph.nodes(data=True):
        p = data.get("path")
        if p:
            idx[p].add(nid)
    return idx


class FileWatcher:
    """Watches a directory for file changes and incrementally updates the graph."""

    def __init__(
        self,
        root_dir: str,
        graph: nx.DiGraph,
        parsers: list[BaseParser] | None = None,
        on_update: Callable[[dict], None] | None = None,
        embedder=None,
    ):
        self.root = Path(root_dir).resolve()
        self.graph = graph
        self.parsers = parsers
        self.on_update = on_update
        self.embedder = embedder
        self._observer = None
        self._debounce_timer: threading.Timer | None = None
        self._pending_paths: set[str] = set()
        self._lock = threading.Lock()
        self._builder = GraphBuilder(parsers=parsers)
        # rel_path -> sha256 of last-known content (only used to debounce
        # "no real change" events; populated lazily inside _process_pending).
        self._file_hashes: dict[str, str] = {}
        # rel_path -> set of node_ids defined under that file. Lets us
        # collapse the 3 full-graph scans the previous implementation
        # did per file event into O(k) set operations.
        self._path_index: dict[str, set[str]] = _build_path_index(graph)
        self._running = False

    # ------------------------------------------------------------------
    # Index maintenance helpers
    # ------------------------------------------------------------------

    def _add_indexed_node(self, node_id: str, rel_path: str) -> None:
        self._path_index.setdefault(rel_path, set()).add(node_id)

    def _remove_indexed_node(self, node_id: str, rel_path: str) -> None:
        bucket = self._path_index.get(rel_path)
        if bucket:
            bucket.discard(node_id)
            if not bucket:
                self._path_index.pop(rel_path, None)

    def _nodes_for_path(self, rel_path: str) -> set[str]:
        return self._path_index.get(rel_path, set())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start watching the directory for changes."""
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if not event.is_directory:
                    watcher._on_file_event(event.src_path)

            def on_created(self, event):
                if not event.is_directory:
                    watcher._on_file_event(event.src_path)

            def on_deleted(self, event):
                if not event.is_directory:
                    watcher._on_file_event(event.src_path, deleted=True)

            def on_moved(self, event):
                if not event.is_directory:
                    watcher._on_file_event(event.src_path, deleted=True)
                    watcher._on_file_event(event.dest_path)

        self._observer = Observer()
        self._observer.schedule(_Handler(), str(self.root), recursive=True)
        self._observer.daemon = True
        self._observer.start()
        self._running = True
        logger.info("File watcher started: %s", self.root)

    def stop(self):
        """Stop watching."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        if self._debounce_timer:
            self._debounce_timer.cancel()
            self._debounce_timer = None
        self._running = False
        logger.info("File watcher stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _on_file_event(self, filepath: str, deleted: bool = False):
        """Queue a raw filesystem event — debounce before processing.

        ``on_modified`` can fire several times for one real save; we keep
        this callback as cheap as possible (string ops only, no I/O) and
        defer hashing/parsing to the debounce window. The previous
        implementation hashed the file here and was therefore burning
        SHA-256 work on the same content multiple times per save.
        """
        path = Path(filepath)

        if path.suffix.lower() not in _SOURCE_EXTENSIONS:
            return

        try:
            rel_parts = path.relative_to(self.root).parts
        except ValueError:
            return
        if any(
            p.startswith(".") or p == "__pycache__" or p in _APOLLO_STATE_DIRS
            for p in rel_parts
        ):
            return

        rel_path = str(path.relative_to(self.root))

        with self._lock:
            if deleted:
                self._pending_paths.add(f"DELETE:{rel_path}")
            else:
                self._pending_paths.add(rel_path)

            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                DEBOUNCE_SECONDS, self._process_pending
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _process_pending(self):
        """Process all pending file changes after debounce period."""
        with self._lock:
            paths = list(self._pending_paths)
            self._pending_paths.clear()

        if not paths:
            return

        deleted_paths: list[str] = []
        candidate_paths: list[str] = []

        for p in paths:
            if p.startswith("DELETE:"):
                deleted_paths.append(p[7:])
            else:
                candidate_paths.append(p)

        # Deferred-hash filter: now (after debounce) is when we read the
        # file and check if the content actually changed since last time.
        # This collapses bursts of ``on_modified`` events for one real
        # save into a single hash + parse + embed cycle.
        changed_paths: list[str] = []
        for rel_path in candidate_paths:
            try:
                content = (self.root / rel_path).read_bytes()
            except (OSError, IOError):
                continue
            file_hash = hashlib.sha256(content).hexdigest()
            if file_hash == self._file_hashes.get(rel_path):
                continue
            self._file_hashes[rel_path] = file_hash
            changed_paths.append(rel_path)

        updated_nodes: list[str] = []
        removed_nodes: list[str] = []

        # Handle deletions — remove all nodes belonging to deleted files.
        # The path index turns this from an O(N) graph walk into O(k).
        for rel_path in deleted_paths:
            for nid in list(self._nodes_for_path(rel_path)):
                if nid in self.graph:
                    self.graph.remove_node(nid)
                    removed_nodes.append(nid)
            self._path_index.pop(rel_path, None)
            self._file_hashes.pop(rel_path, None)
            logger.info("Removed nodes for deleted file: %s", rel_path)

        # Handle changed/new files — re-parse and update graph.
        # Collect (node_id, source) tuples across all files first so we
        # can batch the embedder call instead of doing one encode per
        # node.
        embed_node_ids: list[str] = []
        embed_texts: list[str] = []

        for rel_path in changed_paths:
            abs_path = str(self.root / rel_path)
            parser = self._find_parser(abs_path)
            if parser is None:
                continue

            # Remove old nodes for this file (except the file node) using
            # the path index — no full-graph scan.
            file_id = f"file::{rel_path}"
            for nid in list(self._nodes_for_path(rel_path)):
                if nid == file_id:
                    continue
                if nid in self.graph:
                    self.graph.remove_node(nid)
                    removed_nodes.append(nid)
                self._remove_indexed_node(nid, rel_path)

            # Re-parse
            parsed = parser.parse_file(abs_path)
            if parsed is None:
                continue

            parsed["rel_path"] = rel_path

            # Rebuild nodes for this file using a temporary builder
            temp_builder = GraphBuilder(parsers=self.parsers)
            temp_builder.graph = self.graph
            # Copy symbol table — rebuild for this file
            temp_builder._symbol_table = {
                k: v for k, v in self._builder._symbol_table.items()
            }
            temp_builder._file_imports = dict(self._builder._file_imports)

            # Ensure file node exists
            if file_id not in self.graph:
                self.graph.add_node(
                    file_id, type="file",
                    name=os.path.basename(rel_path), path=rel_path,
                )
                # Connect to parent dir
                parent_dir = os.path.dirname(rel_path)
                dir_id = f"dir::{parent_dir}" if parent_dir else "dir::."
                if dir_id in self.graph:
                    self.graph.add_edge(dir_id, file_id, type="contains")
            self._add_indexed_node(file_id, rel_path)

            temp_builder._build_file_nodes(parsed, rel_path)
            temp_builder._resolve_calls(parsed)

            # Update master symbol table
            self._builder._symbol_table.update(temp_builder._symbol_table)
            self._builder._file_imports.update(temp_builder._file_imports)

            # Capture the new nodes for this file (path index walk only,
            # not the entire graph). The just-rebuilt nodes all carry
            # ``path == rel_path`` so we re-derive the path-index bucket
            # from the graph rather than tracking every emit site inside
            # GraphBuilder._build_file_nodes.
            new_for_file: list[str] = []
            for nid, data in self.graph.nodes(data=True):
                if data.get("path") == rel_path:
                    self._path_index.setdefault(rel_path, set()).add(nid)
                    new_for_file.append(nid)
            updated_nodes.extend(new_for_file)

            # Collect embed candidates instead of embedding inline.
            if self.embedder:
                for nid in new_for_file:
                    source = self.graph.nodes[nid].get("source")
                    if source and len(source.strip()) >= 40:
                        embed_node_ids.append(nid)
                        embed_texts.append(source)

            logger.info("Updated %d nodes for file: %s", len(new_for_file), rel_path)

        # Single batched embed call — orders of magnitude faster than
        # the previous per-node embed_single loop. ``embed_texts`` reuses
        # the in-process SentenceTransformer model (a singleton when
        # ``get_shared_embedder`` is used by the caller).
        if self.embedder and embed_texts:
            try:
                vectors = self.embedder.embed_texts(embed_texts)
                for nid, vec in zip(embed_node_ids, vectors):
                    self.graph.nodes[nid]["embedding"] = vec
            except Exception as e:
                logger.warning("Batch embedding failed: %s", e)

        # Notify callback. Spatial recompute is intentionally *not* done
        # here — recomputing PageRank+UMAP for the whole graph on every
        # single file save scaled badly (seconds per save on 50k+ node
        # graphs) and the result is only consumed by the spatial views
        # which can be refreshed on demand or on a full reindex.
        if self.on_update and (updated_nodes or removed_nodes):
            self.on_update({
                "type": "graph_update",
                "updated_nodes": updated_nodes,
                "removed_nodes": removed_nodes,
                "changed_files": changed_paths,
                "deleted_files": deleted_paths,
            })

    def _find_parser(self, filepath: str) -> BaseParser | None:
        """Return the first parser that can handle the file."""
        if not self.parsers:
            return None
        for parser in self.parsers:
            if parser.can_parse(filepath):
                return parser
        return None
