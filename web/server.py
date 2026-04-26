"""
FastAPI web server for the code knowledge graph browser.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Optional

from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import asyncio
import json as json_mod
import os

from starlette.websockets import WebSocket, WebSocketDisconnect

from apollo.graph.query import GraphQuery

STATIC_DIR = Path(__file__).parent / "static"
EXCLUDE_TYPES_WORDCLOUD = {"directory", "file", "import"}

SETTINGS_PATH = Path("data/settings.json")
ENV_PATH = Path(".env")


def _upsert_env_var(name: str, value: str) -> None:
    """Insert or update a `NAME=value` line in the project's .env file."""
    line = f"{name}={value}\n"
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines(keepends=True)
        prefix = f"{name}="
        for i, existing in enumerate(lines):
            if existing.lstrip().startswith(prefix):
                lines[i] = line
                break
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] = lines[-1] + "\n"
            lines.append(line)
        ENV_PATH.write_text("".join(lines))
    else:
        ENV_PATH.write_text(line)

# ── Indexing progress tracker ─────────────────────────────────────
_indexing_status: dict = {"active": False}

DEFAULT_SETTINGS = {
    # API keys live in .env (one env var per provider) — settings.json only
    # keeps the active provider id and the per-provider model selection.
    "chat": {
        "active_provider": "xai",
        "providers": {
            "xai":       {"model": "grok-4-1-fast-non-reasoning"},
            "openai":    {"model": "gpt-4o-mini"},
            "gemini":    {"model": "gemini-2.5-flash"},
            "anthropic": {"model": "claude-3-5-sonnet-latest"},
            "llama":     {"model": "llama-3.3-70b-versatile"},
        },
    },
}


def _load_settings():
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH) as f:
            return json_mod.load(f)
    return dict(DEFAULT_SETTINGS)


def _save_settings(settings):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json_mod.dump(settings, f, indent=2)


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "***" if key else ""
    return key[:4] + "•" * (len(key) - 8) + key[-4:]


class ConnectionManager:
    """Manages WebSocket connections for live graph updates."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        message = json_mod.dumps(data, default=str)
        stale = []
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


def create_app(store, backend: str = "json", root_dir: str | None = None, parsers: list | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Code Knowledge Graph Browser")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Standardized error responses ─────────────────────────────
    def _error_body(status_code: int, error: str, detail) -> dict:
        return {"status_code": status_code, "error": error, "detail": detail}

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_req: Request, exc: HTTPException):
        phrase = HTTPStatus(exc.status_code).phrase if exc.status_code in HTTPStatus._value2member_map_ else "Error"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.status_code, phrase, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(_req: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_error_body(422, "Validation Error", exc.errors()),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(_req: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=_error_body(500, "Internal Server Error", "An unexpected error occurred"),
        )

    # Load graph — skip embeddings in memory when using cblite (CBL handles vector search)
    include_embeddings = backend != "cblite"
    graph = store.load(include_embeddings=include_embeddings)
    q = GraphQuery(graph)

    search: Optional[object] = None
    embedder = None

    # Set up search backend
    if backend == "cblite":
        try:
            from apollo.embeddings.embedder import Embedder
            from apollo.search.cblite_semantic import CouchbaseLiteSemanticSearch
            embedder = Embedder()
            search = CouchbaseLiteSemanticSearch(store, embedder)
        except Exception:
            search = None
    else:
        try:
            from apollo.embeddings.embedder import Embedder
            from apollo.search.semantic import SemanticSearch
            embedder = Embedder()
            search = SemanticSearch(graph, embedder)
        except Exception:
            search = None

    chat_service = None
    try:
        from apollo.chat.service import ChatService
        chat_service = ChatService(
            graph, search=search, embedder=embedder, root_dir=root_dir,
            settings_provider=_load_settings,
        )
    except Exception:
        chat_service = None

    # Migrate legacy settings (api_keys block, default_model) on startup so
    # users upgrading from the Grok-only era don't lose their selection.
    try:
        startup_settings = _load_settings()
        legacy_keys = startup_settings.get("api_keys", {}) or {}
        # Move any saved legacy api keys into the .env so the new provider
        # registry can find them via os.environ.
        legacy_to_env = {
            "xai_api_key": "XAI_API_KEY",
            "openai_api_key": "OPENAI_API_KEY",
            "gemini_api_key": "GEMINI_API_KEY",
            "anthropic_api_key": "ANTHROPIC_API_KEY",
            "groq_api_key": "GROQ_API_KEY",
        }
        for legacy_name, env_name in legacy_to_env.items():
            v = legacy_keys.get(legacy_name)
            if v and not os.environ.get(env_name):
                os.environ[env_name] = v
                _upsert_env_var(env_name, v)
    except Exception:
        pass

    # Set up chat history persistence
    chat_history = None
    try:
        from apollo.chat.history import ChatHistory
        chat_history = ChatHistory(cbl_store=store if backend == "cblite" else None)
    except Exception:
        from apollo.chat.history import ChatHistory
        chat_history = ChatHistory()

    ws_manager = ConnectionManager()
    watcher = None
    _event_loop = None

    def _ws_on_update(update: dict):
        """Called from watcher thread — schedule async broadcast on event loop."""
        nonlocal _event_loop
        if _event_loop and ws_manager.active:
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast(update), _event_loop
            )

    @app.on_event("startup")
    async def _capture_loop():
        nonlocal _event_loop
        _event_loop = asyncio.get_running_loop()

    # ------------------------------------------------------------------ API --

    _in_docker = os.path.exists("/.dockerenv")

    @app.get("/api/env")
    def get_env():
        return {"native_picker": not _in_docker}

    @app.post("/api/browse-folder")
    async def browse_folder():
        """Open the native OS folder picker (only works on host, not Docker)."""
        if _in_docker:
            raise HTTPException(status_code=501, detail="Not available in Docker")
        import sys, subprocess
        if sys.platform == "darwin":
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["osascript", "-e",
                     'POSIX path of (choose folder with prompt "Select a folder to explore")'],
                    capture_output=True, text=True, timeout=120,
                ),
            )
            path = result.stdout.strip() if result.returncode == 0 else ""
        else:
            try:
                import tkinter as tk
                from tkinter import filedialog
                def _pick():
                    root = tk.Tk()
                    root.withdraw()
                    p = filedialog.askdirectory(title="Select a folder to explore")
                    root.destroy()
                    return p
                path = await asyncio.get_event_loop().run_in_executor(None, _pick)
            except Exception:
                path = ""
        return {"path": path or ""}

    @app.get("/api/browse-dir")
    def browse_dir(path: str = Query("/")):
        """List subdirectories at the given path for the folder browser."""
        target = os.path.abspath(path)
        if not os.path.isdir(target):
            raise HTTPException(status_code=400, detail=f"Not a directory: {target}")
        dirs = []
        try:
            for entry in sorted(os.scandir(target), key=lambda e: e.name.lower()):
                if entry.is_dir() and not entry.name.startswith("."):
                    dirs.append(entry.name)
        except PermissionError:
            pass
        return {"path": target, "dirs": dirs}

    @app.get("/api/indexing-status")
    def get_indexing_status():
        """Return the current indexing progress for UI polling."""
        return _indexing_status

    @app.post("/api/index")
    async def post_index(request: Request):
        """Index a new directory and reload the graph."""
        nonlocal graph, q, search, chat_service
        global _indexing_status
        body = await request.json()
        directory = body.get("directory", "")
        target = os.path.abspath(directory)
        if not os.path.isdir(target):
            raise HTTPException(status_code=400, detail=f"Not a directory: {target}")

        _indexing_status = {
            "active": True,
            "directory": target,
            "step": 1,
            "step_label": "Parsing files",
            "total_steps": 4,
            "detail": "",
        }

        def _do_index():
            """Run the heavy indexing work (called in a thread)."""
            nonlocal graph, q, search, chat_service
            global _indexing_status
            import time

            from apollo.graph import GraphBuilder
            from apollo.parser import PythonParser, TextFileParser

            print(f"\n{'='*60}")
            print(f"📂 Indexing: {target}")
            print(f"{'='*60}")

            if parsers:
                build_parsers = parsers
            else:
                build_parsers = [PythonParser(), TextFileParser()]
                try:
                    from apollo.parser import TreeSitterParser
                    build_parsers.insert(0, TreeSitterParser())
                except Exception:
                    pass

            t0 = time.time()
            print("⏳ [1/4] Parsing files...")
            builder = GraphBuilder(parsers=build_parsers)
            graph = builder.build(target)
            n_nodes = graph.number_of_nodes()
            n_edges = graph.number_of_edges()
            n_files = sum(1 for _, d in graph.nodes(data=True) if d.get("type") == "file")
            elapsed = time.time() - t0
            print(f"   ✅ Parsed {n_files} files — {n_nodes} nodes, {n_edges} edges ({elapsed:.2f}s)")

            _indexing_status.update(step=2, step_label="Generating embeddings",
                                    detail=f"{n_files} files → {n_nodes} nodes, {n_edges} edges")
            t1 = time.time()
            print(f"⏳ [2/4] Generating embeddings for {n_nodes} nodes...")
            try:
                from apollo.embeddings import Embedder
                emb = Embedder()
                emb.embed_graph(graph)
                print(f"   ✅ Embeddings done ({time.time() - t1:.2f}s)")
            except Exception:
                print(f"   ⚠️  Embeddings skipped ({time.time() - t1:.2f}s)")

            _indexing_status.update(step=3, step_label="Saving to store",
                                    detail="Embeddings done")
            t2 = time.time()
            print("⏳ [3/4] Saving to store...")
            store.save(graph)
            print(f"   ✅ Saved ({time.time() - t2:.2f}s)")

            q = GraphQuery(graph)
            stats = q.stats()

            _indexing_status.update(step=4, step_label="Rebuilding search",
                                    detail="Store saved")
            t3 = time.time()
            print("⏳ [4/4] Rebuilding search index...")
            try:
                if backend == "cblite":
                    from apollo.search.cblite_semantic import CouchbaseLiteSemanticSearch
                    from apollo.embeddings.embedder import Embedder as Emb
                    search = CouchbaseLiteSemanticSearch(store, Emb())
                else:
                    from apollo.search.semantic import SemanticSearch
                    from apollo.embeddings.embedder import Embedder as Emb
                    search = SemanticSearch(graph, Emb())
                print(f"   ✅ Search ready ({time.time() - t3:.2f}s)")
            except Exception:
                print(f"   ⚠️  Search unavailable ({time.time() - t3:.2f}s)")

            total = time.time() - t0
            print(f"{'='*60}")
            print(f"🎉 Indexing complete — {n_files} files, {n_nodes} nodes, {n_edges} edges in {total:.2f}s")
            print(f"{'='*60}\n")

            _indexing_status.update(
                active=False, step=4, step_label="Complete",
                detail=f"{n_files} files, {n_nodes} nodes, {n_edges} edges in {total:.1f}s",
            )
            return stats

        loop = asyncio.get_event_loop()
        stats = await loop.run_in_executor(None, _do_index)
        return stats

    @app.delete("/api/index")
    def delete_index():
        """Delete the current index and reset to an empty graph."""
        nonlocal graph, q, search, chat_service
        try:
            store.delete()
        except Exception:
            pass
        import networkx as nx
        graph = nx.DiGraph()
        q = GraphQuery(graph)
        search = None
        if chat_service is not None:
            try:
                from apollo.chat.service import ChatService
                chat_service = ChatService(graph, search=None, embedder=embedder, root_dir=root_dir)
            except Exception:
                chat_service = None
        return {"status": "deleted", "total_nodes": 0, "total_edges": 0}

    @app.get("/api/graph")
    def get_graph(
        path: Optional[str] = Query(None),
        types: Optional[str] = Query(None),
        edges: Optional[str] = Query(None),
        limit: int = Query(2000, ge=0, description="Max nodes (0=unlimited). Keeps highest-degree nodes."),
        max_edges: int = Query(0, ge=0, description="Max edges (0=auto: 3× node count). Cap for browser performance."),
    ):
        type_filter = {t.strip() for t in types.split(",")} if types else None
        edge_filter = {e.strip() for e in edges.split(",")} if edges else None

        candidate_ids: list[str] = []
        for nid, data in graph.nodes(data=True):
            if path and not data.get("path", "").startswith(path):
                continue
            if type_filter and data.get("type") not in type_filter:
                continue
            candidate_ids.append(nid)

        total_matching = len(candidate_ids)

        degree: dict[str, int] = {}
        for nid in candidate_ids:
            degree[nid] = graph.in_degree(nid) + graph.out_degree(nid)

        # When the graph is large, keep only the highest-degree nodes
        if limit and len(candidate_ids) > limit:
            candidate_ids.sort(key=lambda n: degree[n], reverse=True)
            candidate_ids = candidate_ids[:limit]

        node_ids: set[str] = set(candidate_ids)

        category_set: list[str] = sorted(
            {graph.nodes[n].get("type", "unknown") for n in node_ids}
        )
        cat_index = {name: idx for idx, name in enumerate(category_set)}

        max_deg = max(degree[n] for n in node_ids) if node_ids else 1

        nodes_out = []
        for nid in node_ids:
            data = graph.nodes[nid]
            size = int(10 + (degree[nid] / max_deg) * 40)
            attrs = {k: v for k, v in data.items() if k != "embedding"}
            nodes_out.append({
                "id": nid,
                "name": data.get("name", nid),
                "category": cat_index.get(data.get("type", "unknown"), 0),
                "value": data.get("type", "unknown"),
                "symbolSize": min(max(size, 10), 50),
                "attributes": attrs,
            })

        # Cap edges to avoid freezing the browser.
        # Use client-provided cap or default to ~3× the node count.
        if max_edges <= 0:
            max_edges = max(len(node_ids) * 3, 500)

        raw_edges = []
        for src, dst, edata in graph.edges(data=True):
            if src not in node_ids or dst not in node_ids:
                continue
            etype = edata.get("type", "")
            if edge_filter and etype not in edge_filter:
                continue
            raw_edges.append((src, dst, etype))

        total_edges = len(raw_edges)
        if len(raw_edges) > max_edges:
            # Score by sum of endpoint degrees — keep most connected edges
            raw_edges.sort(
                key=lambda e: degree.get(e[0], 0) + degree.get(e[1], 0),
                reverse=True,
            )
            raw_edges = raw_edges[:max_edges]

        edges_out = [{"source": s, "target": t, "type": tp} for s, t, tp in raw_edges]

        categories = [{"name": name} for name in category_set]

        return {
            "nodes": nodes_out,
            "edges": edges_out,
            "categories": categories,
            "total_nodes": total_matching,
            "total_edges": total_edges,
            "truncated": total_matching > len(nodes_out),
            "edges_truncated": total_edges > len(edges_out),
        }

    @app.get("/api/search")
    def search_nodes(
        q_text: str = Query(..., alias="q"),
        top: int = Query(10),
        type_filter: Optional[str] = Query(None, alias="type"),
    ):
        if search is not None and hasattr(search, "has_embeddings") and search.has_embeddings():
            results = search.search(q_text, top_k=top, node_type=type_filter)
            return {
                "results": [
                    {
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "type": r.get("type"),
                        "path": r.get("path"),
                        "line_start": r.get("line_start"),
                        "score": r.get("score"),
                    }
                    for r in results
                ]
            }

        found = q.find(q_text, node_type=type_filter)
        return {
            "results": [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "type": r.get("type"),
                    "path": r.get("path"),
                    "line_start": r.get("line_start"),
                    "score": None,
                }
                for r in found[:top]
            ]
        }

    @app.get("/api/node/{node_id:path}")
    def get_node(node_id: str):
        if node_id not in graph:
            raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

        data = {k: v for k, v in graph.nodes[node_id].items() if k != "embedding"}

        edges_in = []
        for pred in graph.predecessors(node_id):
            edata = graph.edges[pred, node_id]
            edges_in.append({"source": pred, "target": node_id, **dict(edata)})

        edges_out = []
        for succ in graph.successors(node_id):
            edata = graph.edges[node_id, succ]
            edges_out.append({"source": node_id, "target": succ, **dict(edata)})

        return {"id": node_id, **data, "edges_in": edges_in, "edges_out": edges_out}

    @app.post("/api/search/multi")
    async def search_multi(request: Request):
        """Run multiple graph searches in parallel and return a single deduped,
        score-merged result list. Mirrors the AI's `search_graph_multi` tool.

        Body: { "queries": ["a","b"], "top": 10, "type": "function" }
        """
        body = await request.json()
        queries = body.get("queries") or []
        top = int(body.get("top", 10) or 10)
        type_filter = body.get("type")
        if not isinstance(queries, list) or not queries:
            raise HTTPException(status_code=400, detail="`queries` must be a non-empty list")

        merged: dict[str, dict] = {}
        scores: dict[str, float] = {}
        has_sem = bool(search and hasattr(search, "has_embeddings") and search.has_embeddings())
        for qstr in queries:
            if not qstr:
                continue
            if has_sem:
                rows = search.search(qstr, top_k=top, node_type=type_filter)
            else:
                rows = q.find(qstr, node_type=type_filter)[:top]
            for r in rows:
                rid = r.get("id")
                if not rid:
                    continue
                s = float(r.get("score", 0.0) or 0.0)
                if rid not in merged:
                    merged[rid] = {
                        "id": rid,
                        "name": r.get("name"),
                        "type": r.get("type"),
                        "path": r.get("path"),
                        "line_start": r.get("line_start"),
                        "matched_queries": [qstr],
                    }
                    scores[rid] = s
                else:
                    merged[rid]["matched_queries"].append(qstr)
                    scores[rid] = max(scores[rid], s)
        ordered = sorted(
            merged.values(),
            key=lambda r: (scores.get(r["id"], 0.0), len(r["matched_queries"])),
            reverse=True,
        )
        return {"queries": queries, "results": ordered}

    @app.get("/api/neighbors/{node_id:path}")
    def neighbors(
        node_id: str,
        depth: int = Query(1, ge=1, le=5),
        edge_types: Optional[str] = Query(None, description="Comma-separated edge types"),
        direction: str = Query("both", regex="^(in|out|both)$"),
    ):
        """BFS-walk the graph from `node_id`. Mirrors the AI's `get_neighbors` tool.

        Query: ?depth=2&edge_types=calls,imports&direction=out
        """
        if node_id not in graph:
            raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
        types_list = [t.strip() for t in edge_types.split(",")] if edge_types else None
        rows = q.neighbors(node_id, depth=depth, edge_types=types_list, direction=direction)
        return {
            "node_id": node_id,
            "depth": depth,
            "direction": direction,
            "edge_types": types_list,
            "neighbors": [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "type": r.get("type"),
                    "path": r.get("path"),
                    "line_start": r.get("line_start"),
                    "depth": r.get("depth"),
                }
                for r in rows
            ],
        }

    # ── File inspection (Phase 12.3a) — read-only ──────────────────────
    def _file_inspect_call(fn, *a, **kw):
        from apollo import file_inspect
        try:
            return fn(*a, **kw)
        except file_inspect.FileChangedError as e:
            raise HTTPException(status_code=409, detail={
                "message": str(e), "expected_md5": e.expected, "actual_md5": e.actual,
            })
        except file_inspect.FileAccessError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))

    @app.get("/api/file/stats")
    def api_file_stats(path: str = Query(...)):
        from apollo import file_inspect
        return _file_inspect_call(file_inspect.file_stats, graph, root_dir, path)

    @app.get("/api/file/section")
    def api_file_section(
        path: str = Query(...),
        start: int = Query(..., ge=1),
        end: int = Query(..., ge=1),
        md5: Optional[str] = Query(None),
        ):
        from apollo import file_inspect
        return _file_inspect_call(
            file_inspect.get_file_section, graph, root_dir, path, start, end,
            expected_md5=md5,
        )

    @app.get("/api/file/function")
    def api_file_function(
        path: str = Query(...),
        name: str = Query(...),
        md5: Optional[str] = Query(None),
        ):
        from apollo import file_inspect
        return _file_inspect_call(
            file_inspect.get_function_source, graph, root_dir, path, name,
            expected_md5=md5,
        )

    @app.post("/api/file/search")
    async def api_file_search(request: Request):
        from apollo import file_inspect
        body = await request.json()
        if "path" not in body or "pattern" not in body:
            raise HTTPException(status_code=400, detail="`path` and `pattern` are required")
        return _file_inspect_call(
            file_inspect.file_search, graph, root_dir,
            body["path"], body["pattern"],
            context=int(body.get("context", 5) or 5),
            regex=bool(body.get("regex", True)),
            expected_md5=body.get("expected_md5"),
        )

    @app.post("/api/project/search")
    async def api_project_search(request: Request):
        from apollo import file_inspect
        body = await request.json()
        if "pattern" not in body:
            raise HTTPException(status_code=400, detail="`pattern` is required")
        return _file_inspect_call(
            file_inspect.project_search, graph, root_dir,
            body["pattern"],
            root=body.get("root"),
            context=int(body.get("context", 5) or 5),
            file_glob=body.get("file_glob", "*.py") or "*.py",
            regex=bool(body.get("regex", True)),
        )

    @app.get("/api/wordcloud")
    def wordcloud(path: Optional[str] = Query(None)):
        counts: dict[str, int] = defaultdict(int)
        for _, data in graph.nodes(data=True):
            ntype = data.get("type", "")
            if ntype in EXCLUDE_TYPES_WORDCLOUD:
                continue
            if path and not data.get("path", "").startswith(path):
                continue
            name = data.get("name", "")
            if name:
                counts[name] += 1

        return [{"name": name, "value": count} for name, count in counts.items()]

    @app.get("/api/tree")
    def tree():
        dir_nodes: dict[str, dict] = {}
        file_nodes: dict[str, dict] = {}

        for nid, data in graph.nodes(data=True):
            ntype = data.get("type")
            if ntype == "directory":
                dir_nodes[nid] = {
                    "id": nid,
                    "name": data.get("name", nid),
                    "path": data.get("path", ""),
                    "type": "directory",
                    "children": [],
                }
            elif ntype == "file":
                file_nodes[nid] = {
                    "id": nid,
                    "name": data.get("name", nid),
                    "path": data.get("path", ""),
                    "type": "file",
                    "children": [],
                }

        all_nodes = {**dir_nodes, **file_nodes}
        roots: list[dict] = []

        for src, dst, edata in graph.edges(data=True):
            if edata.get("type") != "contains":
                continue
            parent = all_nodes.get(src)
            child = all_nodes.get(dst)
            if parent is not None and child is not None:
                parent["children"].append(child)

        child_ids: set[str] = set()
        for src, dst, edata in graph.edges(data=True):
            if edata.get("type") == "contains" and dst in all_nodes:
                child_ids.add(dst)

        for nid, node in all_nodes.items():
            if nid not in child_ids:
                roots.append(node)

        if len(roots) == 1:
            return roots[0]
        return {"name": "root", "path": "", "type": "directory", "children": roots}

    @app.get("/api/stats")
    def stats():
        return q.stats()

    # ------------------------------------------------------------- Settings --

    @app.get("/api/settings")
    def get_settings():
        from apollo.chat.providers import PROVIDERS, public_registry
        settings = _load_settings()
        # API keys live exclusively in env vars (.env). Surface them masked
        # so the UI can show "set" vs "empty" without leaking secrets.
        api_keys = {
            pid: _mask_key(os.environ.get(p["env"], "") or "")
            for pid, p in PROVIDERS.items()
        }
        return {
            "providers": public_registry(),
            "api_keys": api_keys,
            "chat": settings.get("chat", {}),
        }

    @app.put("/api/settings")
    async def update_settings(request: Request):
        from apollo.chat.providers import PROVIDERS

        body = await request.json()
        current = _load_settings()

        # Update API keys per-provider. Body shape: {api_keys: {<provider_id>: "..."}}.
        # Empty or masked (contains •) values are ignored. Persisted to .env only.
        if "api_keys" in body:
            for pid, value in (body["api_keys"] or {}).items():
                if not value or "•" in value or pid not in PROVIDERS:
                    continue
                env_name = PROVIDERS[pid]["env"]
                _upsert_env_var(env_name, value)
                os.environ[env_name] = value

        # Update chat settings (active_provider + per-provider model selection).
        if "chat" in body:
            chat_in = body["chat"] or {}
            chat_cur = current.setdefault("chat", {})
            if "active_provider" in chat_in and chat_in["active_provider"] in PROVIDERS:
                chat_cur["active_provider"] = chat_in["active_provider"]
            if "providers" in chat_in and isinstance(chat_in["providers"], dict):
                providers_cur = chat_cur.setdefault("providers", {})
                for pid, cfg in chat_in["providers"].items():
                    if pid in PROVIDERS and isinstance(cfg, dict):
                        providers_cur.setdefault(pid, {}).update(
                            {k: v for k, v in cfg.items() if k in ("model",)}
                        )

        _save_settings(current)

        # Reset chat client so it picks up any new key / provider switch
        nonlocal chat_service
        if chat_service:
            chat_service.reset_client()

        return {"status": "saved"}

    # ---------------------------------------------------------------- Chat --

    @app.get("/api/chat/status")
    def chat_status():
        from apollo.chat.providers import get_provider
        if chat_service is None:
            return {"available": False, "provider": None, "model": None}
        pid = chat_service.active_provider
        return {
            "available": chat_service.available,
            "provider": pid,
            "provider_label": get_provider(pid)["label"],
            "model": chat_service.active_model,
        }

    @app.post("/api/chat")
    async def chat(request: Request):
        from apollo.chat.providers import get_provider, env_key
        if chat_service is None or not chat_service.available:
            pid = chat_service.active_provider if chat_service else "xai"
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Chat not available. Set the {env_key(pid)} environment variable "
                    f"for the active provider ({get_provider(pid)['label']})."
                ),
            )
        body = await request.json()
        message = body.get("message", "")
        history = body.get("history", [])
        context_node = body.get("context_node")
        model = body.get("model")

        if not message.strip():
            raise HTTPException(status_code=400, detail="Empty message")

        def generate():
            try:
                for token in chat_service.chat_stream(
                    message, history=history, context_node_id=context_node, model=model
                ):
                    # Escape so multi-line tokens survive the SSE wire format
                    safe = token.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
                    yield f"data: {safe}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                yield f"data: [ERROR] {e}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    # ------------------------------------------------------------- Chat History --

    @app.get("/api/chat/threads")
    def list_chat_threads():
        return chat_history.list_threads()

    @app.post("/api/chat/threads")
    async def create_chat_thread(request: Request):
        body = await request.json()
        title = body.get("title", "New Chat")
        model = body.get("model", "")
        thread = chat_history.create_thread(title=title, model=model)
        return thread

    @app.get("/api/chat/threads/{thread_id}")
    def get_chat_thread(thread_id: str):
        thread = chat_history.get_thread(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        return thread

    @app.delete("/api/chat/threads/{thread_id}")
    def delete_chat_thread(thread_id: str):
        if chat_history.delete_thread(thread_id):
            return {"status": "deleted"}
        raise HTTPException(status_code=404, detail="Thread not found")

    @app.post("/api/chat/threads/{thread_id}/messages")
    async def add_chat_message(thread_id: str, request: Request):
        body = await request.json()
        role = body.get("role", "user")
        content = body.get("content", "")
        thread = chat_history.add_message(thread_id, role, content)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        return thread

    @app.put("/api/chat/threads/{thread_id}/messages/last")
    async def replace_last_chat_message(thread_id: str, request: Request):
        body = await request.json()
        role = body.get("role", "assistant")
        content = body.get("content", "")
        thread = chat_history.replace_last_message(thread_id, role, content)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread or matching last message not found")
        return thread

    @app.post("/api/image/generate")
    async def generate_image(request: Request):
        if chat_service is None or not chat_service.available:
            raise HTTPException(
                status_code=503,
                detail="Image generation not available. Set the XAI_API_KEY.",
            )
        body = await request.json()
        prompt = body.get("prompt", "")
        model = body.get("model", "grok-imagine-image")
        if not prompt.strip():
            raise HTTPException(status_code=400, detail="Empty prompt")

        try:
            images = chat_service.generate_image(prompt=prompt, model=model)
            return {"images": images, "model": model}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # -------------------------------------------------------------- Watch --

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)

    @app.get("/api/watch/status")
    def watch_status():
        return {
            "active": watcher is not None and watcher.running,
            "root_dir": str(watcher.root) if watcher else None,
            "connections": len(ws_manager.active),
        }

    @app.post("/api/watch/start")
    def watch_start(request: Request):
        nonlocal watcher
        if watcher and watcher.running:
            return {"status": "already_running"}

        watch_dir = root_dir
        if watch_dir is None:
            raise HTTPException(status_code=400, detail="No root directory configured")

        # Set up embedder for live re-embedding
        live_embedder = None
        try:
            from apollo.embeddings.embedder import Embedder
            live_embedder = Embedder()
        except Exception:
            pass

        from apollo.watcher import FileWatcher
        watcher = FileWatcher(
            root_dir=watch_dir,
            graph=graph,
            parsers=parsers,
            on_update=_ws_on_update,
            embedder=live_embedder,
        )
        watcher.start()

        # Persist updated graph on each change
        original_on_update = watcher.on_update
        def _on_update_and_save(update):
            try:
                store.save(graph)
            except Exception:
                pass
            if original_on_update:
                original_on_update(update)
        watcher.on_update = _on_update_and_save

        return {"status": "started", "root_dir": watch_dir}

    @app.post("/api/watch/stop")
    def watch_stop():
        nonlocal watcher
        if watcher and watcher.running:
            watcher.stop()
            return {"status": "stopped"}
        return {"status": "not_running"}

    # ---------------------------------------------------------- Static files --

    @app.get("/favicon.ico")
    def favicon():
        favicon_file = STATIC_DIR / "favicon.svg"
        if not favicon_file.exists():
            raise HTTPException(status_code=404, detail="favicon not found")
        return FileResponse(favicon_file, media_type="image/svg+xml")

    @app.get("/")
    def index():
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="index.html not found")
        return FileResponse(index_file)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
