#!/usr/bin/env python3
"""
Benchmark harness for incremental reindex strategies.

Tests all three strategies (full, resolve_full, resolve_local) on a known
test graph with random mutations to understand performance characteristics.

Usage:
    python3 scripts/bench_reindex.py [--mutations 5] [--iterations 3]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

import networkx as nx

from graph.builder import GraphBuilder
from graph.incremental import (
    FullBuildStrategy,
    ResolveFullStrategy,
    ResolveLocalStrategy,
    ReindexStats,
)


class BenchmarkSuite:
    """Benchmark suite for reindex strategies."""
    
    def __init__(self, test_root: Optional[str] = None, mutations_per_run: int = 5):
        """
        Initialize the benchmark suite.
        
        Args:
            test_root: Root directory of a real project to benchmark
                      If None, uses a synthetic test project
            mutations_per_run: Number of files to mutate per reindex run
        """
        self.test_root = test_root or self._create_synthetic_project()
        self.mutations_per_run = mutations_per_run
        self.python_files = self._discover_python_files()
        self.results: dict[str, list[ReindexStats]] = {
            "full": [],
            "resolve_full": [],
            "resolve_local": [],
        }
    
    def _create_synthetic_project(self) -> str:
        """Create a synthetic test project with interdependent modules."""
        tmpdir = tempfile.mkdtemp(prefix="apollo_bench_")
        root = Path(tmpdir)
        
        # Create a simple project structure
        files = {
            "utils.py": """
def format_string(s: str) -> str:
    return s.strip().lower()

def validate_input(x):
    return x is not None
""",
            "api.py": """
from utils import format_string, validate_input

def handle_request(data):
    if not validate_input(data):
        return {"error": "Invalid"}
    return {"result": format_string(data.get("query", ""))}
""",
            "db.py": """
from api import handle_request

class Database:
    def query(self, q):
        return handle_request({"query": q})
""",
            "main.py": """
from api import handle_request
from db import Database

db = Database()

def process(request):
    return handle_request(request)
""",
            "models/user.py": """
class User:
    def __init__(self, name):
        self.name = name
""",
            "models/post.py": """
from user import User

class Post:
    def __init__(self, author: User):
        self.author = author
""",
        }
        
        # Create directories and files
        (root / "models").mkdir(exist_ok=True)
        (root / "models" / "__init__.py").touch()
        
        for rel_path, content in files.items():
            path = root / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        
        return str(root)
    
    def _discover_python_files(self) -> list[str]:
        """Discover all Python files in the test root."""
        files = []
        for dirpath, dirnames, filenames in os.walk(self.test_root):
            for fname in filenames:
                if fname.endswith(".py") and not fname.startswith("test_"):
                    rel_path = os.path.relpath(os.path.join(dirpath, fname), self.test_root)
                    files.append(rel_path)
        return files
    
    def _mutate_random_files(self, count: int) -> None:
        """Mutate K random files in the test project."""
        to_mutate = random.sample(self.python_files, min(count, len(self.python_files)))
        
        for rel_path in to_mutate:
            file_path = Path(self.test_root) / rel_path
            current = file_path.read_text()
            
            # Simple mutation: add a comment and touch mtime
            mutated = current + f"\n# Mutated at {time.time()}\n"
            file_path.write_text(mutated)
    
    def _reset_project(self) -> None:
        """Reset project to initial state."""
        shutil.rmtree(self.test_root)
        self.test_root = self._create_synthetic_project()
        self.python_files = self._discover_python_files()
    
    def benchmark_full_build(self) -> ReindexStats:
        """Benchmark a full rebuild."""
        builder = GraphBuilder()
        start = time.time()
        graph = builder.build(self.test_root)
        duration_ms = int((time.time() - start) * 1000)
        
        stats = ReindexStats(
            strategy="full",
            started_at=start,
            duration_ms=duration_ms,
            files_total=len(self.python_files),
            files_parsed=len(self.python_files),
            files_skipped=0,
            affected_files=len(self.python_files),
            edges_resolved=sum(1 for _, _, data in graph.edges(data=True)
                              if data.get("type") in ("calls", "inherits", "tests")),
            edges_added=0,  # N/A for initial build
            edges_removed=0,
            bytes_written=0,
        )
        return stats
    
    def benchmark_resolve_full(self, prev_graph: nx.DiGraph, prev_hashes: dict) -> ReindexStats:
        """Benchmark ResolveFullStrategy."""
        strategy = ResolveFullStrategy()
        result = strategy.run(
            root_dir=self.test_root,
            graph_in=prev_graph,
            prev_hashes=prev_hashes,
        )
        return result.stats
    
    def benchmark_resolve_local(
        self,
        prev_graph: nx.DiGraph,
        prev_hashes: dict,
        prev_dep_index: dict,
    ) -> ReindexStats:
        """Benchmark ResolveLocalStrategy."""
        strategy = ResolveLocalStrategy()
        result = strategy.run(
            root_dir=self.test_root,
            graph_in=prev_graph,
            prev_hashes=prev_hashes,
            prev_dep_index=prev_dep_index,
        )
        return result.stats
    
    def run_benchmark(self, iterations: int = 3) -> dict:
        """
        Run the full benchmark suite.
        
        For each iteration:
        1. Do a full build (baseline)
        2. Mutate K files
        3. Run each incremental strategy and compare
        
        Args:
            iterations: Number of mutation/reindex cycles
        
        Returns:
            Dictionary with results and summary statistics
        """
        print(f"Benchmark: {len(self.python_files)} files, {iterations} iterations, {self.mutations_per_run} mutations/run")
        print()
        
        results = {}
        
        # Initial full build
        print("=" * 70)
        print("INITIAL FULL BUILD")
        print("=" * 70)
        
        start = time.time()
        builder = GraphBuilder()
        prev_graph = builder.build(self.test_root)
        prev_hashes, prev_dep_index = self._compute_hashes_and_deps(prev_graph)
        
        full_build_ms = int((time.time() - start) * 1000)
        print(f"Full build: {full_build_ms:6d}ms | {len(prev_graph.nodes()):5d} nodes | {len(prev_graph.edges()):5d} edges")
        print()
        
        # Iterations
        for iteration in range(1, iterations + 1):
            print(f"Iteration {iteration}/{iterations}")
            print("-" * 70)
            
            # Mutate files
            self._mutate_random_files(self.mutations_per_run)
            
            # Benchmark each strategy
            strategies = [
                ("full", lambda: self.benchmark_full_build()),
                ("resolve_full", lambda: self.benchmark_resolve_full(prev_graph, prev_hashes)),
                ("resolve_local", lambda: self.benchmark_resolve_local(prev_graph, prev_hashes, prev_dep_index)),
            ]
            
            for name, bench_fn in strategies:
                stats = bench_fn()
                self.results[name].append(stats)
                
                # Update graph for next iteration
                if name == "full":
                    prev_graph = GraphBuilder().build(self.test_root)
                # (resolve_* strategies return updated graph in result, but we skip for simplicity)
                
                print(f"{name:15s}: {stats.duration_ms:6d}ms | "
                      f"{stats.files_parsed:3d} parsed | "
                      f"+{stats.edges_added:3d}/-{stats.edges_removed:3d} edges")
            
            # Update hashes for next iteration
            prev_hashes, prev_dep_index = self._compute_hashes_and_deps(prev_graph)
            print()
        
        # Summary statistics
        print("=" * 70)
        print("SUMMARY STATISTICS")
        print("=" * 70)
        
        for strategy_name in ["full", "resolve_full", "resolve_local"]:
            runs = self.results[strategy_name]
            if runs:
                times = [r.duration_ms for r in runs]
                avg = sum(times) / len(times)
                min_t = min(times)
                max_t = max(times)
                print(f"{strategy_name:15s}: avg={avg:7.1f}ms min={min_t:6d}ms max={max_t:6d}ms (n={len(times)})")
        
        return self.results
    
    def _compute_hashes_and_deps(self, graph: nx.DiGraph) -> tuple[dict, dict]:
        """Compute file hashes and dependency index from graph."""
        hashes = {}
        for dirpath, dirnames, filenames in os.walk(self.test_root):
            for fname in filenames:
                if fname.endswith(".py"):
                    file_path = Path(dirpath) / fname
                    rel_path = str(file_path.relative_to(self.test_root))
                    try:
                        import hashlib
                        stat = file_path.stat()
                        content = file_path.read_bytes()
                        sha256 = hashlib.sha256(content).hexdigest()
                        hashes[rel_path] = {
                            "sha256": sha256,
                            "mtime_ns": stat.st_mtime_ns,
                            "size": stat.st_size,
                        }
                    except (OSError, IOError):
                        pass
        
        # Build dep index
        dep_index: dict[str, set[str]] = {}
        for src, dst, data in graph.edges(data=True):
            if data.get("type") in ("calls", "imports"):
                src_parts = src.split("::")
                dst_parts = dst.split("::")
                if len(src_parts) >= 2 and len(dst_parts) >= 2:
                    src_file = src_parts[1].replace("/", os.sep)
                    dst_file = dst_parts[1].replace("/", os.sep)
                    if dst_file not in dep_index:
                        dep_index[dst_file] = set()
                    if src_file != dst_file:
                        dep_index[dst_file].add(src_file)
        
        return hashes, dep_index


def main():
    parser = argparse.ArgumentParser(description="Benchmark incremental reindex strategies")
    parser.add_argument(
        "--test-root",
        help="Path to a test project (uses synthetic project if not provided)",
    )
    parser.add_argument(
        "--mutations",
        type=int,
        default=5,
        help="Number of files to mutate per iteration (default: 5)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Number of benchmark iterations (default: 3)",
    )
    
    args = parser.parse_args()
    
    suite = BenchmarkSuite(test_root=args.test_root, mutations_per_run=args.mutations)
    results = suite.run_benchmark(iterations=args.iterations)
    
    # Save results to file
    output_file = "docs/work/REINDEX_BENCHMARKS.md"
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, "w") as f:
        f.write("# Reindex Strategy Benchmarks\n\n")
        f.write(f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n\n")
        f.write(f"Test Project: {suite.test_root}\n")
        f.write(f"Files: {len(suite.python_files)} Python files\n")
        f.write(f"Mutations per run: {suite.mutations_per_run}\n")
        f.write(f"Iterations: {len(results['full'])}\n\n")
        
        f.write("## Results (milliseconds)\n\n")
        for strategy in ["full", "resolve_full", "resolve_local"]:
            runs = results[strategy]
            if runs:
                times = [r.duration_ms for r in runs]
                avg = sum(times) / len(times)
                f.write(f"- **{strategy}**: {times} (avg: {avg:.1f}ms)\n")
        
        f.write("\n## Strategy Comparison\n\n")
        f.write("- **full**: Baseline — re-parses all files\n")
        f.write("- **resolve_full**: Incremental parse, full edge re-resolution\n")
        f.write("- **resolve_local**: Incremental parse, selective edge re-resolution\n")
    
    print(f"\nResults saved to {output_file}")


if __name__ == "__main__":
    main()
