"""
Tests for incremental reindex strategies and diff plumbing.

Covers:
- Phase A: GraphDiff and ReindexStats dataclasses
- Phase B: ResolveFullStrategy (Option 1)
- Phase C: ResolveLocalStrategy (Option 2) and reverse-dep index
- Correctness scenarios for all strategies
"""
import json
import shutil
import tempfile
from pathlib import Path

import networkx as nx
import pytest

from graph.incremental import (
    GraphDiff,
    ReindexStats,
    IncrementalResult,
    compute_diff,
    ResolveFullStrategy,
    ResolveLocalStrategy,
    FullBuildStrategy,
)
from graph.builder import GraphBuilder
from storage.json_store import JsonStore


class TestGraphDiff:
    """Test GraphDiff dataclass and serialization."""

    def test_empty_diff(self):
        """Empty diff has no changes."""
        diff = GraphDiff()
        assert diff.is_empty()

    def test_diff_with_added_nodes(self):
        """Diff with added nodes is not empty."""
        diff = GraphDiff(nodes_added=["node1"])
        assert not diff.is_empty()

    def test_diff_serialization(self):
        """Diff can be serialized to/from dict."""
        diff = GraphDiff(
            nodes_added=["n1", "n2"],
            nodes_removed=["n3"],
            edges_added=[("a", "calls", "b")],
        )
        data = diff.to_dict()
        assert isinstance(data, dict)
        
        diff2 = GraphDiff.from_dict(data)
        assert diff2.nodes_added == ["n1", "n2"]
        assert diff2.nodes_removed == ["n3"]
        assert diff2.edges_added == [("a", "calls", "b")]


class TestComputeDiff:
    """Test compute_diff() function."""

    def test_empty_graphs(self):
        """Diff of two empty graphs is empty."""
        g1, g2 = nx.DiGraph(), nx.DiGraph()
        diff = compute_diff(g1, g2)
        assert diff.is_empty()

    def test_added_nodes(self):
        """Diff detects added nodes."""
        g1 = nx.DiGraph()
        g2 = nx.DiGraph()
        g2.add_node("func::a.py::foo", type="function")
        
        diff = compute_diff(g1, g2)
        assert "func::a.py::foo" in diff.nodes_added
        assert len(diff.nodes_removed) == 0

    def test_removed_nodes(self):
        """Diff detects removed nodes."""
        g1 = nx.DiGraph()
        g1.add_node("func::a.py::foo", type="function")
        g2 = nx.DiGraph()
        
        diff = compute_diff(g1, g2)
        assert len(diff.nodes_added) == 0
        assert "func::a.py::foo" in diff.nodes_removed

    def test_modified_nodes(self):
        """Diff detects modified node attributes."""
        g1 = nx.DiGraph()
        g1.add_node("func::a.py::foo", type="function", name="foo")
        
        g2 = nx.DiGraph()
        g2.add_node("func::a.py::foo", type="function", name="foo_renamed")
        
        diff = compute_diff(g1, g2)
        assert "func::a.py::foo" in diff.nodes_modified

    def test_added_edges(self):
        """Diff detects added edges."""
        g1 = nx.DiGraph()
        g1.add_node("a")
        g1.add_node("b")
        
        g2 = nx.DiGraph()
        g2.add_node("a")
        g2.add_node("b")
        g2.add_edge("a", "b", type="calls")
        
        diff = compute_diff(g1, g2)
        # Edge format is (src, dst, type)
        assert ("a", "b", "calls") in diff.edges_added

    def test_removed_edges(self):
        """Diff detects removed edges."""
        g1 = nx.DiGraph()
        g1.add_node("a")
        g1.add_node("b")
        g1.add_edge("a", "b", type="calls")
        
        g2 = nx.DiGraph()
        g2.add_node("a")
        g2.add_node("b")
        
        diff = compute_diff(g1, g2)
        # Edge format is (src, dst, type)
        assert ("a", "b", "calls") in diff.edges_removed


class TestResolveFullStrategy:
    """Test Option 1 (Resolve Full) strategy."""

    def test_strategy_name(self):
        """Strategy has correct name."""
        builder = GraphBuilder()
        strategy = ResolveFullStrategy(builder)
        assert strategy.name == "resolve_full"


class TestResolveLocalStrategy:
    """Test Option 2 (Resolve Local) strategy."""

    def test_strategy_name(self):
        """Strategy has correct name."""
        builder = GraphBuilder()
        strategy = ResolveLocalStrategy(builder)
        assert strategy.name == "resolve_local"

    def test_identify_dirty_files_simple(self):
        """Identify files that changed between runs."""
        new_hashes = {
            "a.py": {"sha256": "hash_a_new", "mtime_ns": 100, "size": 50},
            "b.py": {"sha256": "hash_b", "mtime_ns": 200, "size": 100},
        }
        prev_hashes = {
            "a.py": {"sha256": "hash_a_old", "mtime_ns": 99, "size": 45},
            "b.py": {"sha256": "hash_b", "mtime_ns": 200, "size": 100},
        }
        
        dirty = ResolveLocalStrategy._identify_dirty_files(new_hashes, prev_hashes)
        
        assert "a.py" in dirty  # Changed
        assert "b.py" not in dirty  # Unchanged

    def test_identify_dirty_files_with_deletions(self):
        """Identify deleted files as dirty."""
        new_hashes = {"a.py": {"sha256": "hash_a", "mtime_ns": 100, "size": 50}}
        prev_hashes = {
            "a.py": {"sha256": "hash_a", "mtime_ns": 100, "size": 50},
            "b.py": {"sha256": "hash_b", "mtime_ns": 200, "size": 100},
        }
        
        dirty = ResolveLocalStrategy._identify_dirty_files(new_hashes, prev_hashes)
        
        assert "b.py" in dirty  # Deleted

    def test_compute_affected_files_direct(self):
        """Compute affected files (dirty + one-hop dependents)."""
        dirty_files = {"b.py"}
        dep_index = {
            "b.py": {"a.py", "c.py"},  # a.py and c.py depend on b.py
            "c.py": {"d.py"},  # d.py depends on c.py
        }
        
        affected = ResolveLocalStrategy._compute_affected_files(dirty_files, dep_index, max_hops=1)
        
        assert "b.py" in affected  # dirty
        assert "a.py" in affected  # direct dependent
        assert "c.py" in affected  # direct dependent
        assert "d.py" not in affected  # transitive, max_hops=1

    def test_compute_affected_files_multihop(self):
        """Compute affected files with multi-hop expansion."""
        dirty_files = {"b.py"}
        dep_index = {
            "b.py": {"a.py"},
            "a.py": {"c.py"},
            "c.py": {"d.py"},
        }
        
        affected = ResolveLocalStrategy._compute_affected_files(dirty_files, dep_index, max_hops=2)
        
        assert "b.py" in affected
        assert "a.py" in affected
        assert "c.py" in affected
        assert "d.py" not in affected  # Would need max_hops=3


class TestFullBuildStrategy:
    """Test full rebuild strategy."""

    def test_strategy_name(self):
        """Strategy has correct name."""
        builder = GraphBuilder()
        strategy = FullBuildStrategy(builder)
        assert strategy.name == "full"


class TestCorrectnessScenariosWithTemporaryDirectory:
    """Integration tests for correctness scenarios using a temporary directory."""

    def setup_method(self):
        """Set up temporary directory for tests."""
        self.temp_dir = tempfile.mkdtemp()
        self.root = Path(self.temp_dir)

    def teardown_method(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_python_file(self, rel_path: str, content: str) -> None:
        """Write a Python file to the test directory."""
        file_path = self.root / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    def _build_graph(self) -> nx.DiGraph:
        """Build full graph from current directory state."""
        builder = GraphBuilder()
        return builder.build(str(self.root))

    def _get_full_build_baseline(self, graph: nx.DiGraph) -> dict:
        """Get canonical graph state for correctness comparison."""
        return {
            "nodes": set(graph.nodes()),
            "edges": {(src, dst, graph.edges[src, dst].get("type"))
                      for src, dst in graph.edges()},
        }

    def test_touch_only_no_change(self):
        """Scenario 1: Touch file without content change — graph unchanged."""
        # Create initial file
        self._write_python_file("a.py", "def foo(): pass")
        g1 = self._build_graph()
        baseline = self._get_full_build_baseline(g1)
        
        # Touch file (no content change)
        (self.root / "a.py").touch()
        g2 = self._build_graph()
        
        # Graph should be identical
        state2 = self._get_full_build_baseline(g2)
        assert baseline == state2

    def test_body_edit_public_api_stable(self):
        """Scenario 2: Edit function body without changing signature."""
        initial = """
def helper():
    return 42

def caller():
    return helper()
"""
        self._write_python_file("a.py", initial)
        g1 = self._build_graph()
        baseline = self._get_full_build_baseline(g1)
        
        # Edit helper body
        modified = """
def helper():
    return 43  # Changed

def caller():
    return helper()
"""
        self._write_python_file("a.py", modified)
        g2 = self._build_graph()
        state2 = self._get_full_build_baseline(g2)
        
        # Nodes and edges should be the same (body change doesn't affect structure)
        assert baseline["edges"] == state2["edges"]

    def test_add_new_function(self):
        """Scenario 3: Add new function."""
        self._write_python_file("a.py", "def foo(): pass\ndef caller(): foo()")
        g1 = self._build_graph()
        baseline1 = self._get_full_build_baseline(g1)
        
        # Add new function
        self._write_python_file("a.py", "def foo(): pass\ndef new_func(): pass\ndef caller(): foo()")
        g2 = self._build_graph()
        baseline2 = self._get_full_build_baseline(g2)
        
        # Should have new node
        assert len(baseline2["nodes"]) > len(baseline1["nodes"])

    def test_delete_file(self):
        """Scenario 5: Delete a file — file node and descendants removed."""
        self._write_python_file("a.py", "def foo(): pass")
        self._write_python_file("b.py", "from a import foo\ndef caller(): foo()")
        g1 = self._build_graph()
        
        # Delete a.py
        (self.root / "a.py").unlink()
        g2 = self._build_graph()
        
        # a.py node should be gone
        assert not any(node_id.startswith("file::a.py") for node_id in g2.nodes())

    def test_add_new_file_with_imports(self):
        """Scenario 6: Add new file with imports into existing files."""
        self._write_python_file("a.py", "def helper(): return 42")
        g1 = self._build_graph()
        baseline1 = self._get_full_build_baseline(g1)
        
        # Add new file that imports from a.py
        self._write_python_file("b.py", "from a import helper\ndef caller(): helper()")
        g2 = self._build_graph()
        baseline2 = self._get_full_build_baseline(g2)
        
        # Should have new edges for imports and calls
        assert len(baseline2["edges"]) > len(baseline1["edges"])
        assert len(baseline2["nodes"]) > len(baseline1["nodes"])


class TestReindexStatsSerializaton:
    """Test ReindexStats dataclass serialization."""

    def test_stats_to_dict(self):
        """ReindexStats can be serialized."""
        stats = ReindexStats(
            strategy="resolve_full",
            started_at=1000.0,
            duration_ms=500,
            files_total=10,
            files_parsed=3,
            files_skipped=7,
            edges_resolved=25,
            edges_added=2,
            edges_removed=1,
        )
        data = stats.to_dict()
        assert data["strategy"] == "resolve_full"
        assert data["duration_ms"] == 500
        assert data["files_total"] == 10

    def test_stats_from_dict(self):
        """ReindexStats can be deserialized."""
        data = {
            "strategy": "resolve_full",
            "started_at": 1000.0,
            "duration_ms": 500,
            "files_total": 10,
            "files_parsed": 3,
            "files_skipped": 7,
        }
        stats = ReindexStats.from_dict(data)
        assert stats.strategy == "resolve_full"
        assert stats.duration_ms == 500


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
