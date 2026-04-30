#!/usr/bin/env python3
"""
Apollo CLI — index a directory and query the code knowledge graph.

Usage:
    python main.py index <directory>
    python main.py query <name> [--type TYPE] [--callers] [--callees] [--depth N]
    python main.py search <text> [--top N]
    python main.py spatial --near <node_id> [--range N]
    python main.py spatial --at X,Y [--range N] [--top N]
    python main.py spatial --face <N>
    python main.py spatial-walk <node_id> [--step N] [--rings N]
    python main.py serve [--port PORT] [--watch-dir DIR]
    python main.py watch <directory>
    python main.py status
"""

__version__ = "1.1.0"

import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv

# Load environment variables from .env (e.g. XAI_API_KEY) before anything
# else imports modules that may read them.
load_dotenv()

from apollo.graph import GraphBuilder, GraphQuery
from apollo.logging_config import configure_logging
from apollo.parser import MarkdownParser, PythonParser, TextFileParser, TreeSitterParser
from apollo.storage import open_store

logger = logging.getLogger(__name__)

DEFAULT_INDEX_PATH = "data/index.json"
DEFAULT_CBLITE_PATH = "data/graph.cblite2"
HASHES_PATH = "data/file_hashes.json"


def _default_index_path(backend: str) -> str:
    if backend == "cblite":
        return DEFAULT_CBLITE_PATH
    return DEFAULT_INDEX_PATH


def _open_store(args):
    backend = getattr(args, "backend", "json")
    location = args.index or _default_index_path(backend)
    return open_store(backend, location), location


def _build_parsers(parser_name: str) -> list:
    """Build the parser list based on the --parser flag.

    MarkdownParser handles .md/.markdown with rich AST-based extraction.
    TextFileParser is always appended so non-code files (JSON, YAML, CSV,
    plain text) are indexed regardless of the code-parser choice.
    """
    md_parser = MarkdownParser()
    text_parser = TextFileParser()
    if parser_name == "tree-sitter":
        ts = TreeSitterParser()
        # Fallback to AST parser for Python if tree-sitter-python isn't installed
        return [ts, PythonParser(), md_parser, text_parser]
    elif parser_name == "ast":
        return [PythonParser(), md_parser, text_parser]
    else:
        # auto: prefer tree-sitter if available, fallback to ast
        ts = TreeSitterParser()
        return [ts, PythonParser(), md_parser, text_parser]


def cmd_index(args):
    """Index a directory and save the graph."""
    target_dir = os.path.abspath(args.directory)
    if not os.path.isdir(target_dir):
        print(f"Error: '{target_dir}' is not a directory", file=sys.stderr)
        sys.exit(1)

    parser_name = getattr(args, "parser", "auto")
    parsers = _build_parsers(parser_name)
    print(f"Indexing: {target_dir} (parser: {parser_name})")
    builder = GraphBuilder(parsers=parsers)

    if args.incremental:
        prev_hashes = {}
        if os.path.exists(HASHES_PATH):
            with open(HASHES_PATH) as f:
                prev_hashes = json.load(f)
        graph, new_hashes = builder.build_incremental(target_dir, prev_hashes)
        os.makedirs(os.path.dirname(HASHES_PATH), exist_ok=True)
        with open(HASHES_PATH, "w") as f:
            json.dump(new_hashes, f, separators=(",", ":"))
        changed = sum(1 for k in new_hashes if new_hashes[k] != prev_hashes.get(k))
        print(f"  Incremental: {changed} file(s) changed, {len(new_hashes)} total")
    else:
        graph = builder.build(target_dir)

    # Generate embeddings if sentence-transformers is available
    if not args.no_embeddings:
        try:
            from apollo.embeddings import Embedder
            print("Generating embeddings...")
            embedder = Embedder()
            embedder.embed_graph(graph)
            print("  Embeddings generated.")
        except ImportError:
            print("  Skipping embeddings (sentence-transformers not installed).")

    # Compute spatial coordinates
    if not args.no_spatial:
        from apollo.spatial import SpatialMapper
        print("Computing spatial coordinates...")
        mapper = SpatialMapper()
        coords = mapper.compute_all(graph)
        print(f"  Spatial coordinates assigned to {len(coords)} nodes.")

    store, out_path = _open_store(args)
    store.save(graph)

    query = GraphQuery(graph)
    stats = query.stats()

    print(f"Done! Graph saved to {out_path}")
    print(f"  Nodes: {stats['total_nodes']}")
    print(f"  Edges: {stats['total_edges']}")
    for ntype, count in sorted(stats["node_types"].items()):
        print(f"    {ntype}: {count}")

    store.close()


def cmd_query(args):
    """Query the graph for a symbol."""
    store, index_path = _open_store(args)
    backend = getattr(args, "backend", "json")

    if backend == "json" and not os.path.exists(index_path):
        print(f"Error: No index found at '{index_path}'. Run 'index' first.", file=sys.stderr)
        sys.exit(1)

    graph = store.load()
    q = GraphQuery(graph)

    results = q.find(args.name, node_type=args.type)
    if not results:
        print(f"No results found for '{args.name}'")
        store.close()
        return

    for result in results:
        node_id = result["id"]
        print(f"\n{'='*60}")
        print(f"  {result.get('type', '?'):>10}  {result.get('name', '?')}")
        print(f"  {'path':>10}  {result.get('path', '?')}:{result.get('line_start', '?')}")
        print(f"  {'id':>10}  {node_id}")

        if args.callers:
            callers = q.callers(node_id, depth=args.depth)
            if callers:
                print(f"\n  Callers (depth={args.depth}):")
                for c in callers:
                    indent = "    " * c.get("depth", 1)
                    print(f"  {indent}← {c.get('type', '?')} {c.get('name', '?')}  ({c.get('path', '?')}:{c.get('line_start', '?')})")
            else:
                print("\n  No callers found.")

        if args.callees:
            callees = q.callees(node_id, depth=args.depth)
            if callees:
                print(f"\n  Callees (depth={args.depth}):")
                for c in callees:
                    indent = "    " * c.get("depth", 1)
                    print(f"  {indent}→ {c.get('type', '?')} {c.get('name', '?')}  ({c.get('path', '?')}:{c.get('line_start', '?')})")
            else:
                print("\n  No callees found.")

    print()
    store.close()


def cmd_status(args):
    """Show graph status and statistics."""
    store, index_path = _open_store(args)
    backend = getattr(args, "backend", "json")

    if backend == "json" and not os.path.exists(index_path):
        print(f"No index found at '{index_path}'. Run 'index' first.")
        return

    graph = store.load(include_embeddings=False)
    q = GraphQuery(graph)
    stats = q.stats()

    print(f"Index: {index_path}")
    print(f"Backend: {backend}")
    print(f"Total nodes: {stats['total_nodes']}")
    print(f"Total edges: {stats['total_edges']}")
    print("\nNode types:")
    for ntype, count in sorted(stats["node_types"].items()):
        print(f"  {ntype}: {count}")
    print("\nEdge types:")
    for etype, count in sorted(stats["edge_types"].items()):
        print(f"  {etype}: {count}")

    store.close()


def cmd_search(args):
    """Semantic search across the graph."""
    store, index_path = _open_store(args)
    backend = getattr(args, "backend", "json")

    if backend == "json" and not os.path.exists(index_path):
        print(f"Error: No index found at '{index_path}'. Run 'index' first.", file=sys.stderr)
        sys.exit(1)

    # For cblite backend, try CBL-native vector search first
    if backend == "cblite":
        try:
            from apollo.search.cblite_semantic import CouchbaseLiteSemanticSearch
            from apollo.embeddings import Embedder
            embedder = Embedder()
            cbl_search = CouchbaseLiteSemanticSearch(store, embedder)
            if cbl_search.has_embeddings():
                results = cbl_search.search(args.text, top_k=args.top, node_type=args.type)
                print(f"\nSemantic search (cblite): \"{args.text}\" (top {args.top})\n")
                for r in results:
                    score = f"{r['score']:.4f}" if r.get('score') is not None else "?"
                    print(f"  [{score}]  {r.get('type', '?'):>10}  {r.get('name', '?')}")
                    print(f"           {r.get('path', '?')}:{r.get('line_start', '?')}")
                if not results:
                    print("  No results found.")
                print()
                store.close()
                return
        except (ImportError, Exception):
            pass

    graph = store.load()

    # Check if we have embeddings
    has_embeddings = any("embedding" in data for _, data in graph.nodes(data=True))

    if has_embeddings:
        try:
            from apollo.embeddings import Embedder
            from apollo.search import SemanticSearch
            embedder = Embedder()
            search = SemanticSearch(graph, embedder)
            results = search.search(args.text, top_k=args.top, node_type=args.type)
            print(f"\nSemantic search: \"{args.text}\" (top {args.top})\n")
            for r in results:
                score = f"{r['score']:.4f}" if r.get('score') is not None else "?"
                print(f"  [{score}]  {r.get('type', '?'):>10}  {r.get('name', '?')}")
                print(f"           {r.get('path', '?')}:{r.get('line_start', '?')}")
            if not results:
                print("  No results found.")
            print()
            store.close()
            return
        except ImportError:
            pass

    # Fall back to string matching
    q = GraphQuery(graph)
    results = q.find(args.text, node_type=args.type)
    print(f"\nText search: \"{args.text}\" (top {args.top})\n")
    for r in results[:args.top]:
        print(f"  {r.get('type', '?'):>10}  {r.get('name', '?')}")
        print(f"             {r.get('path', '?')}:{r.get('line_start', '?')}")
    if not results:
        print("  No results found.")
    print()
    store.close()


def cmd_serve(args):
    """Start the web UI server."""
    import uvicorn
    from apollo.web.server import create_app

    store, index_path = _open_store(args)
    backend = getattr(args, "backend", "json")
    parser_name = getattr(args, "parser", "auto")

    # Auto-index the bundled demo/ folder on first run so the UI has
    # something to display without requiring the user to run
    # `python main.py index` separately.
    demo_dir = os.path.abspath("demo")
    if (
        backend == "json"
        and not os.path.exists(index_path)
        and os.path.isdir(demo_dir)
    ):
        print(f"No index found at '{index_path}'. Auto-indexing demo/ folder...")
        parsers_for_demo = _build_parsers(parser_name)
        builder = GraphBuilder(parsers=parsers_for_demo)
        graph = builder.build(demo_dir)

        # Embeddings (best-effort)
        try:
            from apollo.embeddings import Embedder
            print("  Generating embeddings...")
            Embedder().embed_graph(graph)
        except ImportError:
            print("  Skipping embeddings (sentence-transformers not installed).")

        # Spatial coordinates
        try:
            from apollo.spatial import SpatialMapper
            print("  Computing spatial coordinates...")
            SpatialMapper().compute_all(graph)
        except Exception as e:
            print(f"  Skipping spatial layout: {e}")

        os.makedirs(os.path.dirname(index_path) or ".", exist_ok=True)
        store.save(graph)
        print(f"  Demo index saved to {index_path}")

        # Default the live watcher to demo/ as well, unless the user
        # already pointed it somewhere else.
        if not getattr(args, "watch_dir", None):
            args.watch_dir = demo_dir

    if backend == "json" and not os.path.exists(index_path):
        print(f"Error: No index found at '{index_path}'. Run 'index' first.", file=sys.stderr)
        sys.exit(1)

    root_dir = getattr(args, "watch_dir", None)
    parsers = _build_parsers(parser_name) if root_dir else None

    app = create_app(store, backend=backend, root_dir=root_dir, parsers=parsers)
    watch_msg = f", watching: {root_dir}" if root_dir else ""
    print(f"Starting Apollo UI at http://0.0.0.0:{args.port} (backend: {backend}{watch_msg})")
    uvicorn.run(app, host="0.0.0.0", port=args.port)


def cmd_watch(args):
    """Watch a directory for changes and re-index incrementally."""
    target_dir = os.path.abspath(args.directory)
    if not os.path.isdir(target_dir):
        print(f"Error: '{target_dir}' is not a directory", file=sys.stderr)
        sys.exit(1)

    store, index_path = _open_store(args)
    backend = getattr(args, "backend", "json")

    if backend == "json" and not os.path.exists(index_path):
        print(f"Error: No index found at '{index_path}'. Run 'index' first.", file=sys.stderr)
        sys.exit(1)

    graph = store.load()

    parser_name = getattr(args, "parser", "auto")
    parsers = _build_parsers(parser_name)

    embedder = None
    if not args.no_embeddings:
        try:
            from apollo.embeddings import Embedder
            embedder = Embedder()
        except ImportError:
            pass

    def on_update(update):
        changed = update.get("changed_files", [])
        deleted = update.get("deleted_files", [])
        updated = update.get("updated_nodes", [])
        removed = update.get("removed_nodes", [])
        if changed:
            print(f"  Changed: {', '.join(changed)}")
        if deleted:
            print(f"  Deleted: {', '.join(deleted)}")
        print(f"  Nodes updated: {len(updated)}, removed: {len(removed)}")
        # Persist
        store.save(graph)
        print(f"  Graph saved to {index_path}")

    from apollo.watcher import FileWatcher
    watcher = FileWatcher(
        root_dir=target_dir,
        graph=graph,
        parsers=parsers,
        on_update=on_update,
        embedder=embedder,
    )
    watcher.start()
    print(f"Watching: {target_dir} (press Ctrl+C to stop)")

    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping watcher...")
        watcher.stop()
        store.close()


def cmd_spatial(args):
    """Run spatial queries: range, face, or near-node."""
    store, index_path = _open_store(args)
    backend = getattr(args, "backend", "json")

    if backend == "json" and not os.path.exists(index_path):
        print(f"Error: No index found at '{index_path}'. Run 'index' first.", file=sys.stderr)
        sys.exit(1)

    graph = store.load(include_embeddings=False)

    from apollo.search.spatial import SpatialSearch
    ss = SpatialSearch(graph)

    if args.face is not None:
        results = ss.face_query(args.face)
        print(f"\nFace {args.face} — {len(results)} nodes\n")
        for r in results:
            z = f"{r['spatial']['z']:.3f}"
            print(f"  [z={z}]  {r.get('type', '?'):>10}  {r.get('name', '?')}")
            print(f"           {r.get('path', '?')}:{r.get('line_start', '?')}")
    elif args.near:
        results = ss.near_node(args.near, range_deg=args.range, top=args.top)
        print(f"\nNear '{args.near}' (±{args.range}°) — {len(results)} results\n")
        for r in results:
            dist = f"{r.get('distance', 0):.1f}"
            print(f"  [dist={dist}]  {r.get('type', '?'):>10}  {r.get('name', '?')}")
            print(f"              {r.get('path', '?')}:{r.get('line_start', '?')}")
    elif args.at:
        parts = args.at.split(",")
        cx, cy = float(parts[0]), float(parts[1])
        results = ss.range_query(cx, cy, range_deg=args.range, top=args.top)
        print(f"\nSpatial range ({cx},{cy}) ±{args.range}° — {len(results)} results\n")
        for r in results:
            dist = f"{r.get('distance', 0):.1f}"
            print(f"  [dist={dist}]  {r.get('type', '?'):>10}  {r.get('name', '?')}")
            print(f"              {r.get('path', '?')}:{r.get('line_start', '?')}")
    else:
        print("Error: specify --face, --near, or --at", file=sys.stderr)
        sys.exit(1)

    print()
    store.close()


def cmd_spatial_walk(args):
    """Spatial walk: concentric ring expansion from a node."""
    store, index_path = _open_store(args)
    backend = getattr(args, "backend", "json")

    if backend == "json" and not os.path.exists(index_path):
        print(f"Error: No index found at '{index_path}'. Run 'index' first.", file=sys.stderr)
        sys.exit(1)

    graph = store.load(include_embeddings=False)

    from apollo.search.spatial import SpatialSearch
    ss = SpatialSearch(graph)

    rings = ss.spatial_walk(args.node_id, step=args.step, max_rings=args.rings)
    for ring in rings:
        count = len(ring["nodes"])
        print(f"\n  Ring {ring['ring']} (±{ring['range']:.0f}°) — {count} node(s)")
        for r in ring["nodes"]:
            print(f"    {r.get('type', '?'):>10}  {r.get('name', '?')}  ({r.get('path', '?')}:{r.get('line_start', '?')})")

    print()
    store.close()


def cmd_inspect(args):
    """Inspect a single node by ID."""
    store, index_path = _open_store(args)
    backend = getattr(args, "backend", "json")

    if backend == "json" and not os.path.exists(index_path):
        print(f"Error: No index found at '{index_path}'.", file=sys.stderr)
        sys.exit(1)

    graph = store.load(include_embeddings=False)
    if args.node_id not in graph:
        print(f"Node '{args.node_id}' not found.", file=sys.stderr)
        sys.exit(1)

    data = dict(graph.nodes[args.node_id])
    data["id"] = args.node_id

    # Gather edges
    incoming = []
    for pred in graph.predecessors(args.node_id):
        edge = dict(graph.edges[pred, args.node_id])
        incoming.append({"from": pred, **edge})
    outgoing = []
    for succ in graph.successors(args.node_id):
        edge = dict(graph.edges[args.node_id, succ])
        outgoing.append({"to": succ, **edge})

    data["edges_in"] = incoming
    data["edges_out"] = outgoing

    print(json.dumps(data, indent=2, default=str))
    store.close()


def _add_common_args(parser):
    """Add --index and --backend to a subparser."""
    parser.add_argument("--index", help="Index/database path")
    parser.add_argument(
        "--backend",
        choices=["json", "cblite"],
        default="json",
        help="Storage backend (default: json)",
    )


def main():
    configure_logging()
    logger.info("Apollo v%s starting", __version__)
    parser = argparse.ArgumentParser(
        prog="apollo",
        description="Code knowledge graph — index, query, and explore your codebase.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # index
    p_index = subparsers.add_parser("index", help="Index a directory of source files")
    p_index.add_argument("directory", help="Directory to scan")
    p_index.add_argument("-o", "--output", dest="index", help="Output path")
    p_index.add_argument("--no-embeddings", action="store_true", help="Skip embedding generation")
    p_index.add_argument("--no-spatial", action="store_true", help="Skip spatial coordinate computation")
    p_index.add_argument(
        "--parser",
        choices=["auto", "ast", "tree-sitter"],
        default="auto",
        help="Parser backend (default: auto — prefer tree-sitter, fallback to ast)",
    )
    p_index.add_argument(
        "--incremental",
        action="store_true",
        help="Only re-parse files whose content has changed since the last index",
    )
    _add_common_args(p_index)

    # query
    p_query = subparsers.add_parser("query", help="Query the graph for a symbol")
    p_query.add_argument("name", help="Symbol name to search for")
    p_query.add_argument("-t", "--type", help="Filter by node type (function, class, variable, ...)")
    p_query.add_argument("--callers", action="store_true", help="Show callers")
    p_query.add_argument("--callees", action="store_true", help="Show callees")
    p_query.add_argument("-d", "--depth", type=int, default=1, help="Traversal depth (default: 1)")
    _add_common_args(p_query)

    # search
    p_search = subparsers.add_parser("search", help="Semantic search across the graph")
    p_search.add_argument("text", help="Search query text")
    p_search.add_argument("--top", type=int, default=10, help="Number of results (default: 10)")
    p_search.add_argument("-t", "--type", help="Filter by node type")
    _add_common_args(p_search)

    # serve
    p_serve = subparsers.add_parser("serve", help="Start the browser UI")
    p_serve.add_argument("-p", "--port", type=int, default=8080, help="Port (default: 8080)")
    p_serve.add_argument(
        "--watch-dir", dest="watch_dir",
        help="Directory to watch for live file changes (enables file watcher)",
    )
    p_serve.add_argument(
        "--parser",
        choices=["auto", "ast", "tree-sitter"],
        default="auto",
        help="Parser backend for file watcher (default: auto)",
    )
    _add_common_args(p_serve)

    # watch
    p_watch = subparsers.add_parser("watch", help="Watch a directory for changes and re-index")
    p_watch.add_argument("directory", help="Directory to watch")
    p_watch.add_argument("--no-embeddings", action="store_true", help="Skip embedding generation")
    p_watch.add_argument(
        "--parser",
        choices=["auto", "ast", "tree-sitter"],
        default="auto",
        help="Parser backend (default: auto)",
    )
    _add_common_args(p_watch)

    # status
    p_status = subparsers.add_parser("status", help="Show graph statistics")
    _add_common_args(p_status)

    # spatial
    p_spatial = subparsers.add_parser("spatial", help="Spatial queries (range, face, near-node)")
    p_spatial.add_argument("--near", help="Node ID to search near")
    p_spatial.add_argument("--at", help="Center coordinates as X,Y (e.g., 90,180)")
    p_spatial.add_argument("--face", type=int, help="Face number (1-6 or negative)")
    p_spatial.add_argument("--range", type=float, default=30.0, help="Range in degrees (default: 30)")
    p_spatial.add_argument("--top", type=int, default=20, help="Max results (default: 20)")
    _add_common_args(p_spatial)

    # spatial-walk
    p_swalk = subparsers.add_parser("spatial-walk", help="Spatial walk: concentric ring expansion")
    p_swalk.add_argument("node_id", help="Start node ID")
    p_swalk.add_argument("--step", type=float, default=15.0, help="Degrees per ring (default: 15)")
    p_swalk.add_argument("--rings", type=int, default=4, help="Number of rings (default: 4)")
    _add_common_args(p_swalk)

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect a node by ID")
    p_inspect.add_argument("node_id", help="Full node ID (e.g., func::src/main.py::my_func)")
    _add_common_args(p_inspect)

    args = parser.parse_args()

    commands = {
        "index": cmd_index,
        "query": cmd_query,
        "search": cmd_search,
        "serve": cmd_serve,
        "watch": cmd_watch,
        "status": cmd_status,
        "spatial": cmd_spatial,
        "spatial-walk": cmd_spatial_walk,
        "inspect": cmd_inspect,
    }
    logger.info("CLI: %s", args.command)
    commands[args.command](args)


if __name__ == "__main__":
    main()
