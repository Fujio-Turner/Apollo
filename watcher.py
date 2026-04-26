"""
File watcher — monitors a directory for changes and triggers incremental graph updates.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

import networkx as nx

from apollo.graph.builder import GraphBuilder, _SOURCE_EXTENSIONS
from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# How long to wait after the last file event before triggering a rebuild (seconds)
DEBOUNCE_SECONDS = 1.0


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
        # Copy symbol table state from current graph
        self._file_hashes: dict[str, str] = {}
        self._running = False

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

    def _on_file_event(self, filepath: str, deleted: bool = False):
        """Handle a raw filesystem event — debounce before processing."""
        path = Path(filepath)

        # Filter: only source files
        if path.suffix.lower() not in _SOURCE_EXTENSIONS:
            return

        # Skip hidden dirs and __pycache__
        try:
            rel_parts = path.relative_to(self.root).parts
        except ValueError:
            return
        if any(p.startswith(".") or p == "__pycache__" for p in rel_parts):
            return

        rel_path = str(path.relative_to(self.root))

        with self._lock:
            if deleted:
                self._pending_paths.add(f"DELETE:{rel_path}")
            else:
                # Check if content actually changed
                try:
                    content = path.read_bytes()
                    file_hash = hashlib.sha256(content).hexdigest()
                    if file_hash == self._file_hashes.get(rel_path):
                        return  # No real change
                    self._file_hashes[rel_path] = file_hash
                except (OSError, IOError):
                    return
                self._pending_paths.add(rel_path)

            # Reset debounce timer
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
        changed_paths: list[str] = []

        for p in paths:
            if p.startswith("DELETE:"):
                deleted_paths.append(p[7:])
            else:
                changed_paths.append(p)

        updated_nodes: list[str] = []
        removed_nodes: list[str] = []

        # Handle deletions — remove all nodes belonging to deleted files
        for rel_path in deleted_paths:
            file_id = f"file::{rel_path}"
            nodes_to_remove = [file_id]
            # Find all nodes defined in this file
            for nid, data in list(self.graph.nodes(data=True)):
                if data.get("path") == rel_path and nid != file_id:
                    nodes_to_remove.append(nid)
            for nid in nodes_to_remove:
                if nid in self.graph:
                    self.graph.remove_node(nid)
                    removed_nodes.append(nid)
            self._file_hashes.pop(rel_path, None)
            logger.info("Removed %d nodes for deleted file: %s", len(nodes_to_remove), rel_path)

        # Handle changed/new files — re-parse and update graph
        for rel_path in changed_paths:
            abs_path = str(self.root / rel_path)
            parser = self._find_parser(abs_path)
            if parser is None:
                continue

            # Remove old nodes for this file (except the file node itself)
            old_nodes = [
                nid for nid, data in self.graph.nodes(data=True)
                if data.get("path") == rel_path and not nid.startswith("file::")
            ]
            for nid in old_nodes:
                self.graph.remove_node(nid)
                removed_nodes.append(nid)

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
            file_id = f"file::{rel_path}"
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

            temp_builder._build_file_nodes(parsed, rel_path)
            temp_builder._resolve_calls(parsed)

            # Update master symbol table
            self._builder._symbol_table.update(temp_builder._symbol_table)
            self._builder._file_imports.update(temp_builder._file_imports)

            # Collect new node IDs
            new_nodes = [
                nid for nid, data in self.graph.nodes(data=True)
                if data.get("path") == rel_path
            ]
            updated_nodes.extend(new_nodes)

            # Generate embeddings for new nodes
            if self.embedder:
                for nid in new_nodes:
                    source = self.graph.nodes[nid].get("source")
                    if source:
                        try:
                            embedding = self.embedder.embed_single(source)
                            self.graph.nodes[nid]["embedding"] = embedding
                        except Exception as e:
                            logger.warning("Embedding failed for %s: %s", nid, e)

            logger.info("Updated %d nodes for file: %s", len(new_nodes), rel_path)

        # Recompute spatial coordinates for affected nodes
        if updated_nodes:
            try:
                from apollo.spatial import SpatialMapper
                mapper = SpatialMapper()
                mapper.compute_all(self.graph)
            except Exception as e:
                logger.warning("Spatial recompute failed: %s", e)

        # Notify callback
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
