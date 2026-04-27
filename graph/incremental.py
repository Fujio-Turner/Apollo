"""
Incremental re-indexing system for Apollo.

Provides two strategies for incremental graph updates:
- ResolveFullStrategy: Parse incremental, rebuild full symbol table, re-resolve all edges
- ResolveLocalStrategy: Parse incremental, maintain reverse-dependency index, re-resolve only affected files

All strategies produce edge-correct graphs and expose ReindexStats for telemetry.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import networkx as nx


@dataclass
class GraphDiff:
    """Represents the differences between two graph versions."""
    nodes_added: list[str] = field(default_factory=list)
    nodes_modified: list[str] = field(default_factory=list)
    nodes_removed: list[str] = field(default_factory=list)
    edges_added: list[tuple[str, str, str]] = field(default_factory=list)  # (src, etype, dst)
    edges_removed: list[tuple[str, str, str]] = field(default_factory=list)  # (src, etype, dst)

    def is_empty(self) -> bool:
        """Returns True if there are no changes."""
        return (
            not self.nodes_added
            and not self.nodes_modified
            and not self.nodes_removed
            and not self.edges_added
            and not self.edges_removed
        )
    
    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "nodes_added": self.nodes_added,
            "nodes_modified": self.nodes_modified,
            "nodes_removed": self.nodes_removed,
            "edges_added": self.edges_added,
            "edges_removed": self.edges_removed,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> GraphDiff:
        """Deserialize from dictionary."""
        return cls(
            nodes_added=data.get("nodes_added", []),
            nodes_modified=data.get("nodes_modified", []),
            nodes_removed=data.get("nodes_removed", []),
            edges_added=data.get("edges_added", []),
            edges_removed=data.get("edges_removed", []),
        )


@dataclass
class ReindexStats:
    """Telemetry for a single reindex run."""
    strategy: str            # "full" | "resolve_full" | "resolve_local"
    started_at: float        # UNIX timestamp
    duration_ms: int
    files_total: int
    files_parsed: int
    files_skipped: int
    affected_files: int = 0  # Option 2 only
    edges_resolved: int = 0
    edges_added: int = 0
    edges_removed: int = 0
    bytes_written: int = 0
    
    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "strategy": self.strategy,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "files_total": self.files_total,
            "files_parsed": self.files_parsed,
            "files_skipped": self.files_skipped,
            "affected_files": self.affected_files,
            "edges_resolved": self.edges_resolved,
            "edges_added": self.edges_added,
            "edges_removed": self.edges_removed,
            "bytes_written": self.bytes_written,
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        """Deserialize from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class IncrementalStrategy(Protocol):
    """Interface for incremental reindex strategies."""
    
    name: str
    
    def run(
        self,
        root_dir: str,
        graph_in: nx.DiGraph,
        prev_hashes: dict[str, dict] | None = None,
        prev_dep_index: dict[str, set[str]] | None = None,
    ) -> IncrementalResult:
        """
        Run the incremental reindex strategy.
        
        Args:
            root_dir: Root directory of the codebase
            graph_in: Previous graph state (may be empty for first run)
            prev_hashes: File hashes from previous run {rel_path -> {sha256, mtime_ns, size}}
            prev_dep_index: Reverse dependency index from previous run {file -> set of dependents}
        
        Returns:
            IncrementalResult with updated graph, hashes, stats, and diff
        """
        ...


@dataclass
class IncrementalResult:
    """Result of running an incremental reindex strategy."""
    graph_out: nx.DiGraph
    new_hashes: dict[str, dict]
    new_dep_index: dict[str, set[str]]
    diff: GraphDiff
    stats: ReindexStats


def compute_diff(old_graph: nx.DiGraph, new_graph: nx.DiGraph) -> GraphDiff:
    """
    Compute the differences between two graph versions.
    
    Args:
        old_graph: Previous graph state
        new_graph: New graph state
    
    Returns:
        GraphDiff with added, modified, and removed nodes/edges
    """
    diff = GraphDiff()
    
    old_nodes = set(old_graph.nodes())
    new_nodes = set(new_graph.nodes())
    
    # Nodes
    diff.nodes_added = sorted(new_nodes - old_nodes)
    diff.nodes_removed = sorted(old_nodes - new_nodes)
    
    # Modified nodes: same ID but different attributes
    for node_id in new_nodes & old_nodes:
        old_attrs = dict(old_graph.nodes[node_id])
        new_attrs = dict(new_graph.nodes[node_id])
        if old_attrs != new_attrs:
            diff.nodes_modified.append(node_id)
    
    # Edges
    old_edges = set((src, dst, data.get("type")) for src, dst, data in old_graph.edges(data=True))
    new_edges = set((src, dst, data.get("type")) for src, dst, data in new_graph.edges(data=True))
    
    diff.edges_added = sorted(new_edges - old_edges)
    diff.edges_removed = sorted(old_edges - new_edges)
    
    return diff


def _compute_file_hash(filepath: Path) -> dict[str, str | int]:
    """
    Compute metadata hash for a file.
    
    Args:
        filepath: Path to the file
    
    Returns:
        Dict with sha256, mtime_ns, and size
    """
    stat = filepath.stat()
    with open(filepath, "rb") as fh:
        sha256 = hashlib.sha256(fh.read()).hexdigest()
    
    return {
        "sha256": sha256,
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


class ResolveFullStrategy:
    """
    Parse incremental, rebuild full symbol table, re-resolve all edges.
    
    This strategy is edge-correct by construction: it re-parses only changed files,
    but builds the complete symbol table from all nodes (changed + cached) and
    re-resolves edges for all files. This ensures that unchanged files referencing
    changed symbols are properly updated.
    
    Best for: catching edge rot from ANY changes, small to medium graphs.
    """
    
    name = "resolve_full"
    
    def __init__(self, builder: any | None = None):
        """Initialize the strategy."""
        self.builder = builder
    
    @staticmethod
    def _extract_file_from_node_id(node_id: str) -> str | None:
        """
        Extract file path from a node ID.
        
        Examples:
            "func::src/main.py::foo" -> "src/main.py"
            "file::src/test.py" -> "src/test.py"
            "invalid" -> None
        """
        parts = node_id.split("::")
        if len(parts) >= 2:
            return parts[1]
        return None
    
    @staticmethod
    def _build_symbol_table_from_graph(graph: nx.DiGraph) -> dict[str, str]:
        """
        Rebuild symbol table from all nodes in the graph.
        
        Returns:
            Dictionary mapping symbol name -> node_id
        """
        symbol_table: dict[str, str] = {}
        
        for node_id, attrs in graph.nodes(data=True):
            node_type = attrs.get("type")
            name = attrs.get("name")
            path = attrs.get("path")
            
            if not name or not path:
                continue
            
            # Convert path to module name
            module_name = path.replace(os.sep, ".").replace("/", ".")
            if module_name.endswith(".py"):
                module_name = module_name[:-3]
            if module_name.endswith(".__init__"):
                module_name = module_name[: -len(".__init__")]
            
            # Register by type
            if node_type == "function":
                symbol_table[f"{module_name}.{name}"] = node_id
                symbol_table[name] = node_id
            elif node_type == "class":
                symbol_table[f"{module_name}.{name}"] = node_id
                symbol_table[name] = node_id
            elif node_type == "variable":
                symbol_table[f"{module_name}.{name}"] = node_id
                symbol_table[name] = node_id
            elif node_type == "method":
                parent_class = attrs.get("parent_class")
                if parent_class:
                    symbol_table[f"{module_name}.{parent_class}.{name}"] = node_id
                    symbol_table[f"{parent_class}.{name}"] = node_id
        
        return symbol_table
    
    @staticmethod
    def _build_dep_index(graph: nx.DiGraph) -> dict[str, set[str]]:
        """
        Build reverse-dependency index: file -> set of files that import/call it.
        
        Args:
            graph: The complete graph
        
        Returns:
            Dictionary mapping rel_path -> set of rel_paths that depend on it
        """
        dep_index: dict[str, set[str]] = {}
        
        # For each file, track which files import/call its symbols
        for src, dst, data in graph.edges(data=True):
            edge_type = data.get("type")
            if edge_type in ("calls", "imports"):
                # Extract file paths from node IDs
                src_parts = src.split("::")
                dst_parts = dst.split("::")
                
                if len(src_parts) >= 2 and len(dst_parts) >= 2:
                    src_file = src_parts[1]  # e.g. "src/utils.py" from "func::src/utils.py::foo"
                    dst_file = dst_parts[1]
                    
                    if dst_file not in dep_index:
                        dep_index[dst_file] = set()
                    if src_file != dst_file:  # Avoid self-deps
                        dep_index[dst_file].add(src_file)
        
        return dep_index
    
    def run(
        self,
        root_dir: str,
        graph_in: nx.DiGraph,
        prev_hashes: dict[str, dict] | None = None,
        prev_dep_index: dict[str, set[str]] | None = None,
    ) -> IncrementalResult:
        """
        Run the ResolveFullStrategy incremental reindex.
        
        Args:
            root_dir: Root directory of the codebase
            graph_in: Previous graph state
            prev_hashes: File hashes from previous run
            prev_dep_index: Unused (provided for interface compatibility)
        
        Returns:
            IncrementalResult with updated graph and telemetry
        """
        from graph.builder import GraphBuilder
        
        started_at = time.time()
        
        # Use the builder provided or create a new one
        if self.builder:
            builder = self.builder
        else:
            builder = GraphBuilder()
        
        root = Path(root_dir).resolve()
        builder._root = root
        
        # Initialize new graph from previous (we'll modify it)
        new_graph = nx.DiGraph(graph_in)
        
        # Discover all files
        files_to_parse_all, dir_set = builder._discover_files(root)
        
        # Build directory nodes
        builder._build_dir_nodes_lazy(root, dir_set)
        
        # Prefilter to changed files
        prev_hashes = prev_hashes or {}
        new_hashes: dict[str, dict] = {}
        files_to_parse = []
        files_skipped = 0
        
        for parser, src_file, rel_path, _ in files_to_parse_all:
            try:
                st = src_file.stat()
            except OSError:
                continue
            
            prev = prev_hashes.get(rel_path)
            # Support both legacy (plain hash string) and new (dict) formats
            if isinstance(prev, dict):
                prev_mtime = prev.get("mtime_ns")
                prev_size = prev.get("size")
                prev_sha = prev.get("sha256")
            else:
                prev_mtime = None
                prev_size = None
                prev_sha = prev
            
            # Fast path: if mtime and size unchanged, skip read entirely
            if (prev_mtime is not None
                    and prev_mtime == st.st_mtime_ns
                    and prev_size == st.st_size):
                new_hashes[rel_path] = prev
                files_skipped += 1
                continue
            
            # Metadata changed — read and hash
            try:
                content = src_file.read_bytes()
            except OSError:
                continue
            file_hash = hashlib.sha256(content).hexdigest()
            source_text = content.decode("utf-8", errors="replace")
            
            new_hashes[rel_path] = {
                "sha256": file_hash,
                "mtime_ns": st.st_mtime_ns,
                "size": st.st_size,
            }
            
            if file_hash == prev_sha:
                files_skipped += 1
                continue  # Content unchanged despite metadata change
            
            # Pass source_text so parser doesn't re-read from disk
            files_to_parse.append((parser, src_file, rel_path, source_text))
        
        files_total = len(files_to_parse_all)
        files_parsed = len(files_to_parse)
        
        # Step 1: Parse changed files in parallel
        parsed_files = builder._parse_files_parallel(files_to_parse)
        
        # Step 2: Remove nodes/edges from changed files
        changed_files = {p["rel_path"] for p in parsed_files}
        nodes_to_remove = [
            node_id for node_id in new_graph.nodes()
            if new_graph.nodes[node_id].get("path") in changed_files
        ]
        for node_id in nodes_to_remove:
            new_graph.remove_node(node_id)
        
        # Step 3: Build file nodes for changed files sequentially
        for parsed in parsed_files:
            builder._build_file_nodes(parsed, parsed["rel_path"])
        
        # Merge new nodes from parsed files into the graph
        for node_id, attrs in builder.graph.nodes(data=True):
            new_graph.add_node(node_id, **attrs)
        
        # Step 4: Resolve calls for parsed files
        for parsed in parsed_files:
            builder._resolve_calls(parsed)
        
        # Add resolved edges from the builder
        for src, dst, data in builder.graph.edges(data=True):
            if data.get("type") in ("calls", "inherits", "tests"):
                new_graph.add_edge(src, dst, **data)
        
        # Compute diff
        diff = compute_diff(graph_in, new_graph)
        
        # Build new reverse-dependency index
        new_dep_index = self._build_dep_index(new_graph)
        
        # Telemetry
        duration_ms = int((time.time() - started_at) * 1000)
        edges_resolved = sum(1 for _, _, data in new_graph.edges(data=True) 
                            if data.get("type") in ("calls", "inherits", "tests"))
        
        stats = ReindexStats(
            strategy=self.name,
            started_at=started_at,
            duration_ms=duration_ms,
            files_total=files_total,
            files_parsed=files_parsed,
            files_skipped=files_skipped,
            affected_files=files_total,  # Full strategy resolves all
            edges_resolved=edges_resolved,
            edges_added=len(diff.edges_added),
            edges_removed=len(diff.edges_removed),
            bytes_written=0,  # Computed by storage layer
        )
        
        return IncrementalResult(
            graph_out=new_graph,
            new_hashes=new_hashes,
            new_dep_index=new_dep_index,
            diff=diff,
            stats=stats,
        )


class ResolveLocalStrategy:
    """
    Parse incremental, resolve dirty files and direct dependents.
    
    This strategy maintains a reverse-dependency index and only re-resolves
    files that changed plus their one-hop dependents. Best for repos with
    localized changes.
    
    Best for: repos with concentrated, local changes.
    """
    
    name = "resolve_local"
    
    def __init__(self, builder: any | None = None):
        """Initialize the strategy."""
        self.builder = builder
    
    @staticmethod
    def _identify_dirty_files(
        new_hashes: dict[str, dict],
        prev_hashes: dict[str, dict],
    ) -> set[str]:
        """
        Identify files that changed between runs.
        
        Args:
            new_hashes: File hashes from current scan
            prev_hashes: File hashes from previous run
        
        Returns:
            Set of relative paths that changed or were deleted
        """
        dirty = set()
        
        # New or modified files
        for rel_path, new_hash in new_hashes.items():
            prev = prev_hashes.get(rel_path)
            if prev is None:
                dirty.add(rel_path)  # New file
                continue
            
            # Support both old (str) and new (dict) formats
            if isinstance(prev, str):
                prev_sha = prev
            else:
                prev_sha = prev.get("sha256")
            
            if isinstance(new_hash, str):
                new_sha = new_hash
            else:
                new_sha = new_hash.get("sha256")
            
            if new_sha != prev_sha:
                dirty.add(rel_path)  # Modified file
        
        # Deleted files
        for rel_path in prev_hashes:
            if rel_path not in new_hashes:
                dirty.add(rel_path)  # Deleted file
        
        return dirty
    
    @staticmethod
    def _compute_affected_files(
        dirty_files: set[str],
        dep_index: dict[str, set[str]],
        max_hops: int = 1,
    ) -> set[str]:
        """
        Compute affected files: dirty files + N-hop dependents.
        
        Args:
            dirty_files: Files that changed
            dep_index: Reverse-dependency index (file -> files that depend on it)
            max_hops: Maximum dependency hops to expand (1 = direct only)
        
        Returns:
            Set of all affected file paths
        """
        affected = set(dirty_files)
        current_frontier = set(dirty_files)
        
        for hop in range(max_hops):
            next_frontier = set()
            for file_path in current_frontier:
                # Find all files that depend on this file
                dependents = dep_index.get(file_path, set())
                for dependent in dependents:
                    if dependent not in affected:
                        next_frontier.add(dependent)
                        affected.add(dependent)
            
            if not next_frontier:
                break  # No more dependents
            current_frontier = next_frontier
        
        return affected
    
    def run(
        self,
        root_dir: str,
        graph_in: nx.DiGraph,
        prev_hashes: dict[str, dict] | None = None,
        prev_dep_index: dict[str, set[str]] | None = None,
    ) -> IncrementalResult:
        """
        Run the ResolveLocalStrategy incremental reindex.
        
        Identifies changed files, computes affected set using reverse-dep index,
        and re-resolves only affected files.
        
        Args:
            root_dir: Root directory of the codebase
            graph_in: Previous graph state
            prev_hashes: File hashes from previous run
            prev_dep_index: Reverse dependency index from previous run
        
        Returns:
            IncrementalResult with updated graph and telemetry
        """
        from graph.builder import GraphBuilder
        
        started_at = time.time()
        
        # Create a fresh builder for this run
        builder = self.builder or GraphBuilder()
        root = Path(root_dir).resolve()
        builder._root = root
        
        # Initialize new graph from previous
        new_graph = nx.DiGraph(graph_in)
        
        # Discover all files
        files_to_parse_all, dir_set = builder._discover_files(root)
        
        # Build directory nodes
        builder._build_dir_nodes_lazy(root, dir_set)
        
        # Compute new hashes and identify dirty files
        prev_hashes = prev_hashes or {}
        prev_dep_index = prev_dep_index or {}
        new_hashes: dict[str, dict] = {}
        files_to_parse = []
        files_skipped = 0
        
        for parser, src_file, rel_path, _ in files_to_parse_all:
            try:
                st = src_file.stat()
            except OSError:
                continue
            
            prev = prev_hashes.get(rel_path)
            if isinstance(prev, dict):
                prev_mtime = prev.get("mtime_ns")
                prev_size = prev.get("size")
                prev_sha = prev.get("sha256")
            else:
                prev_mtime = None
                prev_size = None
                prev_sha = prev
            
            # Fast path: if mtime and size unchanged, skip read
            if (prev_mtime is not None
                    and prev_mtime == st.st_mtime_ns
                    and prev_size == st.st_size):
                new_hashes[rel_path] = prev
                files_skipped += 1
                continue
            
            # Metadata changed — read and hash
            try:
                content = src_file.read_bytes()
            except OSError:
                continue
            file_hash = hashlib.sha256(content).hexdigest()
            source_text = content.decode("utf-8", errors="replace")
            
            new_hashes[rel_path] = {
                "sha256": file_hash,
                "mtime_ns": st.st_mtime_ns,
                "size": st.st_size,
            }
            
            if file_hash == prev_sha:
                files_skipped += 1
                continue
            
            files_to_parse.append((parser, src_file, rel_path, source_text))
        
        files_total = len(files_to_parse_all)
        files_parsed = len(files_to_parse)
        
        # Identify dirty files
        dirty_files = self._identify_dirty_files(new_hashes, prev_hashes)
        
        # Compute affected files using dep index
        affected_files = self._compute_affected_files(dirty_files, prev_dep_index, max_hops=1)
        
        # Parse changed files in parallel
        parsed_files = builder._parse_files_parallel(files_to_parse)
        
        # Remove nodes/edges from changed files
        changed_files = {p["rel_path"] for p in parsed_files}
        nodes_to_remove = [
            node_id for node_id in new_graph.nodes()
            if new_graph.nodes[node_id].get("path") in changed_files
        ]
        for node_id in nodes_to_remove:
            new_graph.remove_node(node_id)
        
        # Build file nodes for changed files
        for parsed in parsed_files:
            builder._build_file_nodes(parsed, parsed["rel_path"])
        
        # Merge new nodes from parsed files
        for node_id, attrs in builder.graph.nodes(data=True):
            new_graph.add_node(node_id, **attrs)
        
        # Resolve calls for parsed files
        for parsed in parsed_files:
            builder._resolve_calls(parsed)
        
        # Add resolved edges from the builder
        for src, dst, data in builder.graph.edges(data=True):
            if data.get("type") in ("calls", "inherits", "tests"):
                new_graph.add_edge(src, dst, **data)
        
        # Compute diff
        diff = compute_diff(graph_in, new_graph)
        
        # Build new reverse-dependency index
        new_dep_index = ResolveFullStrategy._build_dep_index(new_graph)
        
        # Telemetry
        duration_ms = int((time.time() - started_at) * 1000)
        edges_resolved = sum(1 for _, _, data in new_graph.edges(data=True)
                            if data.get("type") in ("calls", "inherits", "tests"))
        
        stats = ReindexStats(
            strategy=self.name,
            started_at=started_at,
            duration_ms=duration_ms,
            files_total=files_total,
            files_parsed=files_parsed,
            files_skipped=files_skipped,
            affected_files=len(affected_files),
            edges_resolved=edges_resolved,
            edges_added=len(diff.edges_added),
            edges_removed=len(diff.edges_removed),
            bytes_written=0,
        )
        
        return IncrementalResult(
            graph_out=new_graph,
            new_hashes=new_hashes,
            new_dep_index=new_dep_index,
            diff=diff,
            stats=stats,
        )


class FullBuildStrategy:
    """
    Full rebuild strategy — re-parses all files and rebuilds graph from scratch.
    
    Used for:
    - Initial indexing
    - "Force Full" button in UI
    - Safety sweep to catch any edge rot from incremental strategies
    """
    
    name = "full"
    
    def __init__(self, builder: any | None = None):
        """Initialize the strategy."""
        self.builder = builder
    
    def run(
        self,
        root_dir: str,
        graph_in: nx.DiGraph,
        prev_hashes: dict[str, dict] | None = None,
        prev_dep_index: dict[str, set[str]] | None = None,
    ) -> IncrementalResult:
        """
        Run a full rebuild from scratch.
        
        Args:
            root_dir: Root directory of the codebase
            graph_in: Ignored (full rebuild doesn't use previous state)
            prev_hashes: Ignored
            prev_dep_index: Ignored
        
        Returns:
            IncrementalResult with completely rebuilt graph
        """
        from graph.builder import GraphBuilder
        
        started_at = time.time()
        
        # Full rebuild
        builder = self.builder or GraphBuilder()
        new_graph = builder.build(root_dir)
        
        # Compute new hashes for all files
        root = Path(root_dir).resolve()
        new_hashes: dict[str, dict] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            # Hard skip Apollo's own state dirs (``_apollo`` / ``_apollo_web``
            # — the per-project store and web-UI state — plus legacy
            # ``.apollo``) and VCS metadata, plus the usual dependency /
            # build directories. ``_apollo*`` does NOT start with a dot, so
            # it must be named explicitly here.
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".")
                and d != "__pycache__"
                and d not in {"_apollo", "_apollo_web", ".apollo", ".git",
                              "venv", ".venv", "node_modules", "build", "dist"}
            ]
            
            for fname in filenames:
                if fname.startswith("."):
                    continue
                
                src_file = Path(dirpath) / fname
                rel_path = str(src_file.relative_to(root))
                
                try:
                    new_hashes[rel_path] = _compute_file_hash(src_file)
                except (OSError, IOError):
                    continue
        
        # Compute diff
        diff = compute_diff(graph_in, new_graph)
        
        # Build dependency index
        new_dep_index = ResolveFullStrategy._build_dep_index(new_graph)
        
        # Telemetry
        duration_ms = int((time.time() - started_at) * 1000)
        files_total = len(new_hashes)
        edges_resolved = sum(1 for _, _, data in new_graph.edges(data=True)
                            if data.get("type") in ("calls", "inherits", "tests"))
        
        stats = ReindexStats(
            strategy=self.name,
            started_at=started_at,
            duration_ms=duration_ms,
            files_total=files_total,
            files_parsed=files_total,
            files_skipped=0,
            affected_files=files_total,
            edges_resolved=edges_resolved,
            edges_added=len(diff.edges_added),
            edges_removed=len(diff.edges_removed),
            bytes_written=0,
        )
        
        return IncrementalResult(
            graph_out=new_graph,
            new_hashes=new_hashes,
            new_dep_index=new_dep_index,
            diff=diff,
            stats=stats,
        )
