#!/usr/bin/env python3
"""
Benchmark: JSON vs Couchbase Lite storage backends.

Usage:
    python scripts/benchmark_phase5.py [--directory path]
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile
import time

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from apollo.graph import GraphBuilder, GraphQuery
from apollo.storage import open_store


ITERATIONS = 3
SEARCH_QUERY = "build a graph from source files"


def _dir_size(path: str) -> int:
    """Total size in bytes of a file or directory tree."""
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            total += os.path.getsize(os.path.join(dirpath, f))
    return total


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def bench_save(graph, backend: str, location: str) -> float:
    """Save graph, return elapsed seconds."""
    store = open_store(backend, location)
    t0 = time.perf_counter()
    store.save(graph)
    elapsed = time.perf_counter() - t0
    store.close()
    return elapsed


def bench_load(backend: str, location: str) -> float:
    """Load graph, return elapsed seconds."""
    store = open_store(backend, location)
    t0 = time.perf_counter()
    store.load()
    elapsed = time.perf_counter() - t0
    store.close()
    return elapsed


def bench_search(backend: str, location: str, query: str) -> float | None:
    """Run a semantic search, return elapsed seconds or None if unavailable."""
    try:
        from apollo.embeddings import Embedder
        from apollo.search import SemanticSearch
    except ImportError:
        return None

    store = open_store(backend, location)
    graph = store.load()
    has_embeddings = any("embedding" in d for _, d in graph.nodes(data=True))
    if not has_embeddings:
        store.close()
        return None

    embedder = Embedder()
    search = SemanticSearch(graph, embedder)
    t0 = time.perf_counter()
    search.search(query, top_k=5)
    elapsed = time.perf_counter() - t0
    store.close()
    return elapsed


def median_of(fn, *args, n: int = ITERATIONS) -> float:
    """Run fn(*args) n times, return the median result."""
    return statistics.median(fn(*args) for _ in range(n))


def main() -> None:
    default_dir = os.path.join(os.path.dirname(__file__), "..", "apollo")
    parser = argparse.ArgumentParser(description="Benchmark JSON vs CBL backends")
    parser.add_argument(
        "--directory",
        default=os.path.abspath(default_dir),
        help="Directory to index (default: the apollo package)",
    )
    args = parser.parse_args()

    directory = os.path.abspath(args.directory)
    if not os.path.isdir(directory):
        print(f"Error: '{directory}' is not a directory", file=sys.stderr)
        sys.exit(1)

    # --- Build the graph once ------------------------------------------------
    print(f"Indexing: {directory}")
    builder = GraphBuilder()
    graph = builder.build(directory)

    try:
        from apollo.embeddings import Embedder
        print("Generating embeddings...")
        embedder = Embedder()
        embedder.embed_graph(graph)
        has_embeddings = True
    except ImportError:
        has_embeddings = False
        print("Skipping embeddings (sentence-transformers not installed).")

    query = GraphQuery(graph)
    stats = query.stats()
    print(f"  Nodes: {stats['total_nodes']}  Edges: {stats['total_edges']}\n")

    # --- Prepare temp locations ----------------------------------------------
    tmpdir = tempfile.mkdtemp(prefix="gs_bench_")
    json_path = os.path.join(tmpdir, "index.json")
    cblite_path = os.path.join(tmpdir, "graph.cblite2")

    backends = {
        "json": json_path,
        "cblite": cblite_path,
    }

    # Check cblite availability
    try:
        open_store("cblite", cblite_path).close()
    except Exception as exc:
        print(f"⚠  cblite backend unavailable ({exc}); benchmarking JSON only.\n")
        del backends["cblite"]

    results: dict[str, dict[str, str]] = {}

    for backend, loc in backends.items():
        print(f"Benchmarking: {backend}")
        row: dict[str, str] = {}

        # Save
        save_time = median_of(bench_save, graph, backend, loc, n=ITERATIONS)
        row["Save (median)"] = f"{save_time:.4f}s"

        # Load
        load_time = median_of(bench_load, backend, loc, n=ITERATIONS)
        row["Load (median)"] = f"{load_time:.4f}s"

        # Search
        if has_embeddings:
            search_time = bench_search(backend, loc, SEARCH_QUERY)
            row["Search"] = f"{search_time:.4f}s" if search_time is not None else "n/a"
        else:
            row["Search"] = "n/a"

        # Disk size
        size = _dir_size(loc)
        row["Disk size"] = _human_size(size)

        results[backend] = row
        print(f"  done.\n")

    # --- Print comparison table ----------------------------------------------
    metrics = ["Save (median)", "Load (median)", "Search", "Disk size"]
    backend_names = list(results.keys())

    col_w = max(14, *(len(b) for b in backend_names)) + 2
    hdr = f"{'Metric':<20}" + "".join(f"{b:>{col_w}}" for b in backend_names)
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for m in metrics:
        row_str = f"{m:<20}"
        for b in backend_names:
            row_str += f"{results[b].get(m, '-'):>{col_w}}"
        print(row_str)
    print("=" * len(hdr))

    # --- Cleanup -------------------------------------------------------------
    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"\nTemp files cleaned up ({tmpdir}).")


if __name__ == "__main__":
    main()
