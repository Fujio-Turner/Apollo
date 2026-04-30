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
import logging
import os
import threading

from starlette.websockets import WebSocket, WebSocketDisconnect

from apollo.graph.query import GraphQuery
from apollo.logging_config import apply_settings as apply_logging_settings, configure_logging
from apollo.projects import ProjectManager, register_project_routes
from apollo.reindex_service import ReindexService, ReindexConfig

logger = logging.getLogger(__name__)

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
        "max_tool_rounds": 5,
        "streaming": True,
    },
    # UI / theming — controls the DaisyUI data-theme attribute.
    "appearance": {
        "theme": "dark",
    },
    # Graph rendering knobs (Phase 10). The slider default position, how
    # many edges per node we send, and when to disable ECharts animation
    # for large graphs.
    "graph": {
        "default_depth": 20,
        "edge_cap_multiplier": 3,
        "animation_threshold": 500,
    },
    # Indexer knobs (Phase 8/9). exclude_globs and extra_skip_dirs
    # supplement the built-in _SKIP_DIRS blocklist.
    "indexing": {
        "exclude_globs": [],
        "extra_skip_dirs": [],
        "embedding_batch_size": 256,
        "embedding_min_text_length": 40,
    },
    # Background re-index service (web/server.py ReindexConfig defaults).
    "reindex": {
        "strategy": "auto",
        "sweep_interval_minutes": 30,
        "sweep_on_session_start": True,
        "local_max_hops": 1,
        "force_full_after_runs": 50,
    },
    # Phase 14 web content capture folder (relative to project root).
    "captures": {
        "folder": "_apollo_web",
    },
    # Diagnostic logging (guides/LOGGING.md). Empty path falls back to
    # APOLLO_LOG_FILE env var or the built-in default
    # (.apollo/logs/apollo.log). Empty level falls back to APOLLO_LOG_LEVEL
    # env var or "INFO". The numeric caps drive the rotating file handler.
    "logging": {
        "path": "",
        "level": "",
        "json_mode": False,
        "max_size_mb": 100,
        "max_age_days": 7,
        "rotated_total_mb": 1024,
    },
    # Plugins detected on disk under ``plugins/``. Populated dynamically
    # in ``_load_settings`` so the file always reflects what's installed.
    "plugins": {},
}


def _load_settings():
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH) as f:
            settings = json_mod.load(f)
    else:
        settings = dict(DEFAULT_SETTINGS)
    # Refresh the plugins section from the live plugins/ directory so
    # settings.json is always an accurate mirror of what's installed.
    # IMPORTANT: the ``config`` slot inside each plugin entry is reserved
    # for **user overrides** written by ``PATCH /api/settings/plugins/
    # <name>/config`` (Phase 2B). We must therefore strip the on-disk
    # ``config.json`` mirror that ``detect_installed_plugins()`` puts
    # into that slot — otherwise the next call to ``_load_settings``
    # would stomp anything the user just patched. The on-disk values
    # are still available live via ``detect_installed_plugins()`` /
    # ``load_plugin_config()``; we just don't *persist* them here.
    try:
        from apollo.projects.settings import detect_installed_plugins
        detected = detect_installed_plugins()
        existing = settings.get("plugins") or {}
        new_plugins: dict = {}
        for name, meta in detected.items():
            entry = {k: v for k, v in meta.items() if k != "config"}
            user_override = (existing.get(name) or {}).get("config")
            if isinstance(user_override, dict) and user_override:
                entry["config"] = user_override
            new_plugins[name] = entry
        if new_plugins != settings.get("plugins"):
            settings["plugins"] = new_plugins
            _save_settings(settings)
    except Exception:
        # Detection is best-effort; never block settings load on it.
        pass
    return settings


def _save_settings(settings):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json_mod.dump(settings, f, indent=2)


def _record_last_project(path):
    """Persist the absolute path of the most recently opened project so we
    can auto-restore it on server restart. Used to keep project-scoped
    features (annotations, etc.) working without forcing the user to
    re-open the folder via the UI after every restart."""
    try:
        settings = _load_settings()
        settings["last_open_project"] = str(path)
        _save_settings(settings)
    except Exception:
        # Best-effort; never block project open on a settings write failure.
        pass


def _get_last_project():
    try:
        return _load_settings().get("last_open_project") or None
    except Exception:
        return None


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


def _build_active_parsers() -> list:
    """Construct the active parser list from plugin discovery.

    Re-runs :func:`apollo.plugins.discover_plugins` (which honours each
    plugin's ``config.json`` ``enabled`` flag and merged user overrides
    from ``data/settings.json``) and appends a ``TextFileParser`` fallback
    so plain text / data files are still indexed when no plugin claims
    them.
    """
    from apollo.plugins import discover_plugins
    from apollo.parser import TextFileParser
    found = list(discover_plugins())
    if not any(isinstance(p, TextFileParser) for p in found):
        found.append(TextFileParser())
    return found


def create_app(store, backend: str = "json", root_dir: str | None = None, parsers: list | None = None, version: str = "0.7.2") -> FastAPI:
    """Create and configure the FastAPI application."""
    # Bring up logging early using whatever the user has saved in
    # ``data/settings.json``. Falls back to env vars / built-in defaults
    # when the section is missing or this is a first-run install.
    try:
        _initial_logging_settings = (_load_settings() or {}).get("logging") or {}
    except Exception:
        _initial_logging_settings = {}
    configure_logging(settings=_initial_logging_settings)

    app = FastAPI(title="Code Knowledge Graph Browser")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Initialize ProjectManager for project lifecycle management
    project_manager = ProjectManager(version=version)

    # Auto-open the project on startup so project-scoped features
    # (annotations, etc.) work without forcing the user to re-open the
    # folder via the UI after every restart. We try, in order:
    #   1) the directory the server was launched against (--watch-dir)
    #   2) the path recorded in settings.json by the previous session
    # Either path needs an `_apollo/apollo.json` manifest to count.
    from pathlib import Path as _Path
    _candidates = [p for p in (root_dir, _get_last_project()) if p]
    for _candidate in _candidates:
        try:
            if (_Path(_candidate) / "_apollo" / "apollo.json").exists():
                project_manager.open(_candidate)
                _record_last_project(_candidate)
                break
        except Exception:
            logger.exception("failed to auto-open project at startup for %s", _candidate)

    # Initialize ReindexService for background graph freshness
    reindex_service: Optional[ReindexService] = None
    if root_dir:
        reindex_config = ReindexConfig(
            strategy="auto",
            sweep_interval_minutes=30,
            sweep_on_session_start=True,
            local_max_hops=1,
            force_full_after_runs=50
        )
        reindex_service = ReindexService(root_dir, store, reindex_config)

    # ── Standardized error responses (Phase 14) ──────────────────
    # Wire format combines the legacy `{status_code, error, detail}` keys
    # (kept for backward compatibility with existing clients) with the new
    # structured `error: {code, message, details?}` object documented in
    # `schema/api-response.schema.json`.
    from apollo.api import ErrorCode, ResponseValidator
    _response_validator = ResponseValidator()

    def _error_body(status_code: int, code: ErrorCode | str, message: str, detail=None) -> dict:
        code_str = code.value if isinstance(code, ErrorCode) else str(code)
        body: dict = {
            "status_code": status_code,
            "error": {"code": code_str, "message": message},
        }
        if detail is not None:
            body["error"]["details"] = detail if isinstance(detail, dict) else {"detail": detail}
        # Validate non-blockingly so schema drift gets logged but never breaks requests.
        problems = _response_validator.validate(body)
        if problems:
            logger.warning(
                "error response failed schema validation: %s", problems
            )
        return body

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_req: Request, exc: HTTPException):
        phrase = HTTPStatus(exc.status_code).phrase if exc.status_code in HTTPStatus._value2member_map_ else "Error"
        message = exc.detail if isinstance(exc.detail, str) else phrase
        details = exc.detail if not isinstance(exc.detail, str) else None
        code = ErrorCode.from_status(exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.status_code, code, message, details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(_req: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_error_body(422, ErrorCode.VALIDATION_ERROR, "Validation Error", {"errors": exc.errors()}),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(_req: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=_error_body(500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred"),
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
            project_manager=project_manager,
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

    # Set up chat history persistence.
    #
    # ``project_manager`` is wired in so that thread storage is scoped to
    # the active project: each folder gets its own _apollo/chat_history.json
    # (JSON backend) or filtered by project_id (CBL backend). Without this
    # the My Hub "Recents" panel would leak chats from one folder to the
    # next when the user switches projects.
    chat_history = None
    try:
        from apollo.chat.history import ChatHistory
        chat_history = ChatHistory(
            cbl_store=store if backend == "cblite" else None,
            project_manager=project_manager,
        )
    except Exception:
        from apollo.chat.history import ChatHistory
        chat_history = ChatHistory(project_manager=project_manager)

    ws_manager = ConnectionManager()
    watcher = None
    _event_loop = None

    # ── Active parser list (Phase 2B) ────────────────────────────
    # `parsers` is the back-compat argument from CLI callers. The
    # internal `_active_parsers` list is what `/api/index` and the
    # PATCH-driven `_reload_parsers()` mutate at runtime so a plugin
    # config flip takes effect with no server restart. We seed it from
    # plugin discovery; a lock guards in-place mutation so a reload
    # cannot race with an in-flight indexing pass.
    _parsers_lock = threading.Lock()
    _active_parsers: list = _build_active_parsers()

    def _reload_parsers() -> int:
        """Re-run plugin discovery and atomically swap ``_active_parsers``.

        Mutates the list in place under ``_parsers_lock`` so any caller
        that captured the reference (e.g. the running file watcher) sees
        the new contents next time it iterates. Returns the new parser
        count.
        """
        new_list = _build_active_parsers()
        with _parsers_lock:
            _active_parsers[:] = new_list
        logger.info("plugin parsers reloaded: %d active", len(new_list))
        return len(new_list)

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
        
        # Start background reindex sweep if service is available
        if reindex_service and reindex_service.config.sweep_on_session_start:
            try:
                await reindex_service.start_background_sweep(delay_seconds=10.0)
            except Exception as e:
                import logging
                logging.warning(f"Failed to start reindex service: {e}")

    # ── Register project management routes ────────────────────────
    register_project_routes(app, project_manager, store, backend, on_project_open=_record_last_project)

    # ------------------------------------------------------------------ API --

    _in_docker = os.path.exists("/.dockerenv")

    @app.get("/api/env")
    def get_env():
        return {"native_picker": not _in_docker}

    @app.get("/api/version")
    def get_version():
        """Get the Apollo version (Python backend)."""
        import main
        return {"version": main.__version__}

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
        nonlocal graph, q, search, chat_service, root_dir
        global _indexing_status
        body = await request.json()
        directory = body.get("directory", "")
        target = os.path.abspath(directory)
        if not os.path.isdir(target):
            raise HTTPException(status_code=400, detail=f"Not a directory: {target}")
        # Remember the user-chosen root so file-inspection endpoints can
        # build relative paths (e.g. "graph/query.py" instead of the full
        # absolute path) regardless of which directory nodes were indexed.
        root_dir = target

        # Ensure a project manifest is bound to this target so project-scoped
        # endpoints (annotations, etc.) work even when /api/index is called
        # without going through /api/projects/open first.
        try:
            already_open = (
                project_manager.manifest is not None
                and project_manager.root_dir is not None
                and os.path.abspath(str(project_manager.root_dir)) == os.path.abspath(target)
            )
            if not already_open:
                project_manager.open(target)
            _record_last_project(target)
        except Exception:
            logger.exception("failed to auto-open project for %s", target)

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

            logger.info("indexing target: %s", target)

            if parsers:
                build_parsers = parsers
            else:
                # Snapshot the live plugin-driven parser list under the
                # lock so a concurrent _reload_parsers() can't mutate it
                # mid-build. Falls back to the legacy hard-coded set
                # only if discovery returned nothing (no plugins).
                with _parsers_lock:
                    build_parsers = list(_active_parsers)
                if not build_parsers:
                    build_parsers = [PythonParser(), TextFileParser()]
                    try:
                        from apollo.parser import TreeSitterParser
                        build_parsers.insert(0, TreeSitterParser())
                    except Exception:
                        pass

            t0 = time.time()
            logger.info("indexing step 1/4: parsing files in %s", target)
            # Pull user-defined filters from the active project manifest
            # (apollo.json) so the bootstrap wizard's choices actually take
            # effect during indexing.
            active_filters = None
            try:
                if (
                    project_manager.manifest is not None
                    and project_manager.root_dir is not None
                    and os.path.abspath(str(project_manager.root_dir)) == os.path.abspath(target)
                    and project_manager.manifest.filters is not None
                ):
                    active_filters = project_manager.manifest.filters.to_dict()
                    logger.info(
                        "applying project filters: mode=%s include_dirs=%s exclude_dirs=%s "
                        "exclude_file_globs=%s include_doc_types=%s",
                        active_filters.get('mode'),
                        active_filters.get('include_dirs'),
                        active_filters.get('exclude_dirs'),
                        active_filters.get('exclude_file_globs'),
                        active_filters.get('include_doc_types'),
                    )
            except Exception:
                logger.exception("could not read project filters; proceeding without filters")
                active_filters = None
            builder = GraphBuilder(parsers=build_parsers, filters=active_filters)
            graph = builder.build(target)
            n_nodes = graph.number_of_nodes()
            n_edges = graph.number_of_edges()
            n_files = sum(1 for _, d in graph.nodes(data=True) if d.get("type") == "file")
            elapsed = time.time() - t0
            logger.info(
                "parsed %d files in %.2fs (%d nodes, %d edges)",
                n_files, elapsed, n_nodes, n_edges,
            )

            _indexing_status.update(step=2, step_label="Generating embeddings",
                                    detail=f"{n_files} files → {n_nodes} nodes, {n_edges} edges")
            t1 = time.time()
            logger.info("indexing step 2/4: generating embeddings for %d nodes", n_nodes)
            try:
                from apollo.embeddings import Embedder
                emb = Embedder()
                emb.embed_graph(graph)
                logger.info("embeddings generated in %.2fs", time.time() - t1)
            except Exception:
                logger.warning("embeddings skipped after %.2fs (sentence-transformers unavailable?)",
                               time.time() - t1)

            _indexing_status.update(step=3, step_label="Saving to store",
                                    detail="Embeddings done")
            t2 = time.time()
            logger.info("indexing step 3/4: saving graph to store")
            store.save(graph)
            logger.info("graph saved in %.2fs", time.time() - t2)

            q = GraphQuery(graph)
            stats = q.stats()

            _indexing_status.update(step=4, step_label="Rebuilding search",
                                    detail="Store saved")
            t3 = time.time()
            logger.info("indexing step 4/4: rebuilding search index (backend=%s)", backend)
            try:
                if backend == "cblite":
                    from apollo.search.cblite_semantic import CouchbaseLiteSemanticSearch
                    from apollo.embeddings.embedder import Embedder as Emb
                    search = CouchbaseLiteSemanticSearch(store, Emb())
                else:
                    from apollo.search.semantic import SemanticSearch
                    from apollo.embeddings.embedder import Embedder as Emb
                    search = SemanticSearch(graph, Emb())
                logger.info("search index ready in %.2fs", time.time() - t3)
            except Exception:
                logger.warning("search index unavailable after %.2fs", time.time() - t3)

            total = time.time() - t0
            logger.info(
                "indexing complete: %d files, %d nodes, %d edges in %.2fs",
                n_files, n_nodes, n_edges, total,
            )

            # Persist final stats to the project manifest so the bootstrap
            # wizard's "Project ready!" page (and /api/projects/current)
            # reports accurate counts instead of the zero defaults.
            try:
                if (
                    project_manager.manifest is not None
                    and project_manager.root_dir is not None
                    and os.path.abspath(str(project_manager.root_dir)) == os.path.abspath(target)
                ):
                    project_manager.mark_index_complete(
                        files_indexed=n_files,
                        nodes=n_nodes,
                        edges=n_edges,
                        elapsed_seconds=total,
                    )
            except Exception:
                logger.exception("failed to persist project index stats")

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
               chat_service = ChatService(
                   graph, search=None, embedder=embedder, root_dir=root_dir,
                   settings_provider=_load_settings,
                   project_manager=project_manager,
               )
           except Exception:
               chat_service = None
       return {"status": "deleted", "total_nodes": 0, "total_edges": 0}

    # ── Phase 9: Reindex Service Endpoints ────────────────────────
    
    @app.get("/api/index/history")
    def get_reindex_history(limit: int = Query(20, ge=1, le=100)):
       """Get reindex history (telemetry for last N runs)."""
       if reindex_service is None:
           raise HTTPException(status_code=503, detail="Reindex service not available")
       history = reindex_service.get_history(limit)
       return {
           "history": [
               {
                   "duration_ms": s.duration_ms,
                   "files_parsed": s.files_parsed,
                   "nodes_added": s.nodes_added,
                   "nodes_removed": s.nodes_removed,
                   "edges_added": s.edges_added,
                   "edges_removed": s.edges_removed,
                   "timestamp": str(s.timestamp) if hasattr(s, 'timestamp') else None,
                   "strategy": s.strategy if hasattr(s, 'strategy') else "unknown",
               }
               for s in history
           ],
           "count": len(history)
       }
    
    @app.get("/api/index/last")
    def get_last_reindex_stats():
       """Get the most recent reindex statistics."""
       if reindex_service is None:
           raise HTTPException(status_code=503, detail="Reindex service not available")
       stats = reindex_service.get_last_stats()
       if stats is None:
           return {
               "status": "never_run",
               "last_stats": None
           }
       return {
           "status": "success",
           "last_stats": {
               "duration_ms": stats.duration_ms,
               "files_parsed": stats.files_parsed,
               "nodes_added": stats.nodes_added,
               "nodes_removed": stats.nodes_removed,
               "edges_added": stats.edges_added,
               "edges_removed": stats.edges_removed,
               "timestamp": str(stats.timestamp) if hasattr(stats, 'timestamp') else None,
               "strategy": stats.strategy if hasattr(stats, 'strategy') else "unknown",
           }
       }
    
    @app.post("/api/index/sweep")
    async def trigger_reindex_sweep():
       """Manually trigger a background reindex sweep."""
       if reindex_service is None:
           raise HTTPException(status_code=503, detail="Reindex service not available")
       
       if reindex_service.is_reindexing():
           return {
               "status": "already_running",
               "message": "A reindex operation is already in progress"
           }
       
       try:
           stats = await reindex_service.run_sweep()
           return {
               "status": "success",
               "message": f"Sweep complete in {stats.duration_ms}ms",
               "stats": {
                   "duration_ms": stats.duration_ms,
                   "files_parsed": stats.files_parsed,
                   "nodes_added": stats.nodes_added,
                   "nodes_removed": stats.nodes_removed,
                   "edges_added": stats.edges_added,
                   "edges_removed": stats.edges_removed,
               }
           }
       except Exception as e:
           raise HTTPException(status_code=500, detail=f"Sweep failed: {str(e)}")

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

        max_deg = max((degree[n] for n in node_ids), default=1) or 1

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

    # ── Path resolution shared by /api/node and /api/node/.../connections ──
    # Resolve a graph-stored relative path against (in order):
    #   1) the file/dir node's own `abs_path` attribute,
    #   2) `root_dir` if the server was launched with one,
    #   3) the indexed project root recorded on the `dir::.` node,
    #   4) the path as-is (which may resolve against CWD).
    def _resolve_indexed_path(p: str) -> Path | None:
        if not p:
            return None
        raw = Path(p).expanduser()
        if raw.is_absolute():
            return raw
        for nid_prefix in (f"file::{p}", f"dir::{p}"):
            n = graph.nodes.get(nid_prefix)
            if n and n.get("abs_path"):
                return Path(n["abs_path"])
        root = graph.nodes.get("dir::.") or {}
        base = root_dir or root.get("abs_path")
        if base:
            return Path(base).expanduser() / raw
        return raw

    def _edge_meta(node_id: str, other_id: str, direction: str, edata: dict, self_path) -> dict:
        """Lightweight per-edge metadata (no source code reads)."""
        n = graph.nodes.get(other_id, {})
        p = n.get("path")
        ls = n.get("line_start")
        le = n.get("line_end")
        prefix = "source" if direction == "in" else "target"
        out = {
            "source": node_id if direction == "out" else other_id,
            "target": other_id if direction == "out" else node_id,
            f"{prefix}_id": other_id,
            f"{prefix}_name": n.get("name"),
            f"{prefix}_type": n.get("type"),
            f"{prefix}_path": p,
            f"{prefix}_line_start": ls,
            f"{prefix}_line_end": le,
            f"{prefix}_lang": n.get("lang"),
            "same_file": bool(self_path) and p == self_path,
        }
        out.update(edata)
        return out

    @app.get("/api/node/{node_id:path}/connections")
    def get_node_connections(node_id: str):
        """Heavy variant of /api/node/{id} that also reads the source file for
        each connected node and includes a small preview snippet. Called
        on-demand when the user opens the Connections tab."""
        # FastAPI's `:path` converter strips trailing /connections off node_id
        # so we receive the bare id here.
        if node_id not in graph:
            raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

        log = logger

        node_data = graph.nodes[node_id]
        self_path = node_data.get("path")
        # Cache stores (lines, error) per requested path. `lines` is None on
        # any failure; `error` is a short, user-facing reason string or None.
        _file_cache: dict[str, tuple[list[str] | None, str | None]] = {}

        def _read_lines(p: str) -> tuple[list[str] | None, str | None]:
            if not p:
                return None, "no path"
            if p in _file_cache:
                return _file_cache[p]
            try:
                fp = _resolve_indexed_path(p)
                if fp is None:
                    result = (None, "could not resolve path")
                elif not fp.exists():
                    result = (None, "file not found")
                elif not fp.is_file():
                    result = (None, "not a regular file")
                else:
                    try:
                        text = fp.read_text(encoding="utf-8", errors="replace")
                    except PermissionError:
                        result = (None, "permission denied")
                    except OSError as ex:
                        result = (None, f"read error: {ex.strerror or ex}")
                    else:
                        result = (text.splitlines(), None)
            except Exception as ex:
                log.warning("read_lines unexpected failure for %s: %s", p, ex)
                result = (None, "unexpected error")
            _file_cache[p] = result
            return result

        def _site_snippet(p, line, context: int = 1):
            """Return a snippet dict on success, or an error stub when the file
            can't be read / the line is out of range. Never raises."""
            if not p:
                return {"error": "no path"}
            if line is None:
                return None
            lines, err = _read_lines(p)
            if lines is None:
                return {"error": err or "unavailable"}
            try:
                line = int(line)
            except (TypeError, ValueError):
                return {"error": "invalid line number"}
            if line < 1 or line > len(lines):
                return {"error": f"line {line} out of range (file has {len(lines)} lines — may have changed)"}
            start = max(1, line - context)
            end = min(len(lines), line + context)
            return {
                "start": start, "end": end,
                "lines": lines[start - 1:end],
                "highlight": line, "truncated": False,
            }

        def _build(other_id: str, direction: str, edata: dict) -> dict:
            try:
                meta = _edge_meta(node_id, other_id, direction, edata, self_path)
                n = graph.nodes.get(other_id, {})
                call_line = edata.get("call_line")
                if call_line is not None:
                    snip = _site_snippet(
                        self_path if direction == "out" else n.get("path"),
                        call_line,
                    )
                else:
                    snip = _site_snippet(n.get("path"), n.get("line_start"))
                prefix = "source" if direction == "in" else "target"
                meta[f"{prefix}_snippet"] = snip
                return meta
            except Exception as ex:
                # One broken edge shouldn't kill the whole connections payload.
                log.exception("connections: failed to build edge %s↔%s", node_id, other_id)
                prefix = "source" if direction == "in" else "target"
                return {
                    "source": node_id if direction == "out" else other_id,
                    "target": other_id if direction == "out" else node_id,
                    f"{prefix}_id": other_id,
                    f"{prefix}_name": graph.nodes.get(other_id, {}).get("name") or other_id,
                    f"{prefix}_snippet": {"error": f"failed to build connection: {ex}"},
                    "rel": edata.get("type") or "related",
                    "error": str(ex),
                }

        edges_in = [
            _build(pred, "in", dict(graph.edges[pred, node_id]))
            for pred in graph.predecessors(node_id)
        ]
        edges_out = [
            _build(succ, "out", dict(graph.edges[node_id, succ]))
            for succ in graph.successors(node_id)
        ]
        return {"id": node_id, "edges_in": edges_in, "edges_out": edges_out}

    @app.get("/api/node/{node_id:path}")
    def get_node(node_id: str):
        """Lightweight node detail. Edges contain metadata only — no source
        snippets. Snippets are loaded on demand from
        `/api/node/{id}/connections`."""
        if node_id not in graph:
            raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

        data = {k: v for k, v in graph.nodes[node_id].items() if k != "embedding"}
        self_path = data.get("path")

        edges_in = [
            _edge_meta(node_id, pred, "in", dict(graph.edges[pred, node_id]), self_path)
            for pred in graph.predecessors(node_id)
        ]
        edges_out = [
            _edge_meta(node_id, succ, "out", dict(graph.edges[node_id, succ]), self_path)
            for succ in graph.successors(node_id)
        ]

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
        direction: str = Query("both", pattern="^(in|out|both)$"),
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

    @app.get("/api/file/content")
    def api_file_content(path: str = Query(...)):
        from apollo import file_inspect
        return _file_inspect_call(file_inspect.file_content, graph, root_dir, path)

    @app.get("/api/file/raw")
    def api_file_raw(path: str = Query(...)):
        """Stream a file as-is — used by the Content div to render images
        (and other binary assets referenced from indexed Markdown/HTML)
        without base64-inlining them in JSON responses."""
        from apollo import file_inspect
        try:
            resolved = file_inspect.safe_path(path, graph, root_dir)
        except file_inspect.FileAccessError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        import mimetypes
        mime, _ = mimetypes.guess_type(str(resolved))
        return FileResponse(str(resolved), media_type=mime or "application/octet-stream")

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
    def wordcloud(
        path: Optional[str] = Query(None),
        mode: str = Query("strong"),
    ):
        """
        Idea Cloud weighted by graph strength (in + out degree), aggregated
        across nodes sharing the same display name. The `mode` parameter
        controls how aggressively the long tail is hidden:

          - strong   : top 30, strength >= 2  (default; readable headline)
          - relevant : top 100, strength >= 2 (compact "Show More" view)
          - all      : everything, capped at 500 (full "show every relationship")
        """
        strengths: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for nid, data in graph.nodes(data=True):
            ntype = data.get("type", "")
            if ntype in EXCLUDE_TYPES_WORDCLOUD:
                continue
            if path and not data.get("path", "").startswith(path):
                continue
            name = data.get("name", "")
            if not name:
                continue
            try:
                deg = graph.degree(nid)
            except Exception:
                deg = 0
            strengths[name] += deg
            counts[name] += 1

        items = [
            {"name": n, "value": float(strengths[n]), "count": counts[n]}
            for n in strengths
        ]
        items.sort(key=lambda x: x["value"], reverse=True)
        total = len(items)

        mode_norm = (mode or "strong").lower()
        if mode_norm == "all":
            items = items[:500]
            min_strength = 0
        elif mode_norm == "relevant":
            items = [i for i in items if i["value"] >= 2][:100]
            min_strength = 2
        else:  # "strong" or anything unknown
            mode_norm = "strong"
            items = [i for i in items if i["value"] >= 2][:30]
            min_strength = 2

        return {
            "items": items,
            "total": total,
            "shown": len(items),
            "mode": mode_norm,
            "min_strength": min_strength,
        }

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

    # -------------------------------------------------------------- Logging --

    @app.get("/api/logging/info")
    def logging_info():
        """Snapshot of Apollo's logging config + on-disk file sizes.

        Powers the Settings → Logging tab so users can see where logs
        are written, how big the active file is, and how many rotated
        files are retained. See guides/LOGGING.md § 9.
        """
        from apollo.logging_config import get_logging_info
        return get_logging_info((_load_settings() or {}).get("logging") or {})

    # ------------------------------------------------------------- Settings --

    @app.get("/api/settings")
    def get_settings():
        from apollo.chat.providers import PROVIDERS, public_registry
        from apollo.projects.settings import (
            detect_installed_plugins,
            load_plugin_config,
        )
        settings = _load_settings()
        # API keys live exclusively in env vars (.env). Surface them masked
        # so the UI can show "set" vs "empty" without leaking secrets.
        api_keys = {
            pid: _mask_key(os.environ.get(p["env"], "") or "")
            for pid, p in PROVIDERS.items()
        }
        # Merge stored values over defaults so newly-added sections
        # always appear even on older settings.json files.
        def _merged(section: str) -> dict:
            base = dict(DEFAULT_SETTINGS.get(section, {}))
            base.update(settings.get(section, {}) or {})
            return base

        # Compose the per-plugin payload the Settings → Plugins UI needs:
        #   * everything _load_settings() persists (installed/version/sha256/
        #     plus any user override under "config"),
        #   * a fresh ``config_schema`` field — the raw on-disk
        #     ``config.json`` including ``_<key>`` description siblings —
        #     so the UI can auto-render labels and tooltips,
        #   * a fresh ``config`` field — the merged effective values
        #     (on-disk defaults ⊕ user overrides, ``_<key>`` stripped) —
        #     so the form controls can be populated without the client
        #     having to do the merge itself.
        plugins_out: dict = {}
        try:
            installed = detect_installed_plugins()
        except Exception:
            installed = {}
        for name, entry in (settings.get("plugins") or {}).items():
            out = dict(entry)
            schema = (installed.get(name) or {}).get("config") or {}
            if schema:
                out["config_schema"] = schema
            try:
                merged = load_plugin_config(name)
            except Exception:
                merged = {}
            if merged or schema:
                out["config"] = merged
            plugins_out[name] = out

        return {
            "providers": public_registry(),
            "api_keys": api_keys,
            "chat": _merged("chat"),
            "appearance": _merged("appearance"),
            "graph": _merged("graph"),
            "indexing": _merged("indexing"),
            "reindex": _merged("reindex"),
            "captures": _merged("captures"),
            "logging": _merged("logging"),
            # Read-only metadata + on-disk schema + merged effective config
            # for every plugin present under ``plugins/``.
            "plugins": plugins_out,
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
            if "max_tool_rounds" in chat_in:
                try:
                    chat_cur["max_tool_rounds"] = max(1, min(20, int(chat_in["max_tool_rounds"])))
                except (TypeError, ValueError):
                    pass
            if "streaming" in chat_in:
                chat_cur["streaming"] = bool(chat_in["streaming"])

        # Free-form sections — validated structurally but values are trusted
        # because everything is sourced from the local settings UI.
        def _patch(section: str, allowed_keys: tuple, coercers: dict | None = None):
            if section not in body or not isinstance(body[section], dict):
                return
            cur = current.setdefault(section, {})
            for k, v in body[section].items():
                if k not in allowed_keys:
                    continue
                if coercers and k in coercers:
                    try:
                        v = coercers[k](v)
                    except (TypeError, ValueError):
                        continue
                cur[k] = v

        _patch("appearance", ("theme",))
        _patch(
            "graph",
            ("default_depth", "edge_cap_multiplier", "animation_threshold"),
            {"default_depth": int, "edge_cap_multiplier": int, "animation_threshold": int},
        )
        _patch(
            "indexing",
            ("exclude_globs", "extra_skip_dirs", "embedding_batch_size", "embedding_min_text_length"),
            {
                "exclude_globs": lambda v: [str(x) for x in v] if isinstance(v, list) else [],
                "extra_skip_dirs": lambda v: [str(x) for x in v] if isinstance(v, list) else [],
                "embedding_batch_size": int,
                "embedding_min_text_length": int,
            },
        )
        _patch(
            "reindex",
            ("strategy", "sweep_interval_minutes", "sweep_on_session_start", "local_max_hops", "force_full_after_runs"),
            {
                "sweep_interval_minutes": int,
                "sweep_on_session_start": bool,
                "local_max_hops": int,
                "force_full_after_runs": int,
            },
        )
        _patch("captures", ("folder",))
        _patch(
            "logging",
            ("path", "level", "json_mode", "max_size_mb", "max_age_days", "rotated_total_mb"),
            {
                "path": str,
                "level": lambda v: str(v).upper() if v else "",
                "json_mode": bool,
                "max_size_mb": int,
                "max_age_days": int,
                "rotated_total_mb": int,
            },
        )

        _save_settings(current)

        # Re-attach logging handlers so a new path / size / level takes
        # effect immediately without requiring a server restart.
        if "logging" in body:
            try:
                apply_logging_settings(current.get("logging") or {})
                logger.info("logging settings reloaded from UI")
            except Exception:
                logger.exception("failed to apply logging settings update")

        # Reset chat client so it picks up any new key / provider switch
        nonlocal chat_service
        if chat_service:
            chat_service.reset_client()

        return {"status": "saved"}

    # -------------------------------------------------- Plugin config (2B) --

    # JSON value types we accept in a per-plugin config override. Anything
    # else (e.g. an arbitrary class instance) is rejected as a 400 so the
    # settings file stays JSON-serializable round-trip.
    _PLUGIN_CONFIG_TYPES = (bool, int, float, str, list, dict, type(None))

    def _validate_plugin_value(key: str, value, expected) -> None:
        """Raise HTTPException(400) when ``value`` is not type-compatible
        with the on-disk default ``expected`` for ``key``.

        ``enabled`` is special-cased as a strict ``bool``. For everything
        else we require the value to be the same broad JSON kind as the
        on-disk default (so a config that ships ``"comment_tags": []``
        accepts any list, even an empty one).
        """
        if key == "enabled":
            if not isinstance(value, bool):
                raise HTTPException(
                    status_code=400,
                    detail=f"`enabled` must be a bool, got {type(value).__name__}",
                )
            return
        if not isinstance(value, _PLUGIN_CONFIG_TYPES):
            raise HTTPException(
                status_code=400,
                detail=f"`{key}` has unsupported value type: {type(value).__name__}",
            )
        # Allow null to clear a key only when the on-disk default is
        # also null (rare); otherwise require a matching kind.
        if expected is None:
            return
        # bool is a subclass of int — disallow that conflation.
        if isinstance(expected, bool):
            if not isinstance(value, bool):
                raise HTTPException(
                    status_code=400,
                    detail=f"`{key}` must be a bool, got {type(value).__name__}",
                )
            return
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise HTTPException(
                    status_code=400,
                    detail=f"`{key}` must be a number, got {type(value).__name__}",
                )
            return
        if isinstance(expected, str):
            if not isinstance(value, str):
                raise HTTPException(
                    status_code=400,
                    detail=f"`{key}` must be a string, got {type(value).__name__}",
                )
            return
        if isinstance(expected, list):
            if not isinstance(value, list):
                raise HTTPException(
                    status_code=400,
                    detail=f"`{key}` must be a list, got {type(value).__name__}",
                )
            return
        if isinstance(expected, dict):
            if not isinstance(value, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"`{key}` must be an object, got {type(value).__name__}",
                )
            return

    @app.patch("/api/settings/plugins/{name}/config")
    async def patch_plugin_config(name: str, request: Request):
        """Apply a partial config override for plugin *name* and reload.

        Body is a partial dict of overrides (a strict subset of keys
        present in the plugin's on-disk ``config.json``). Validates:
        - the plugin exists on disk,
        - every key is known (rejects typos / stale fields),
        - every value's type matches the on-disk default,
        - ``enabled`` is a bool when present.

        On success the override is persisted to ``data/settings.json``
        under ``plugins[<name>].config`` and ``_reload_parsers()`` swaps
        the live parser list so subsequent requests see the new config.
        """
        from apollo.projects.settings import detect_installed_plugins
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Body must be a JSON object")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Body must be a JSON object")

        installed = detect_installed_plugins()
        if name not in installed:
            raise HTTPException(status_code=404, detail=f"Unknown plugin: {name}")

        on_disk = installed[name].get("config") or {}
        if not on_disk:
            # Plugin has no config.json; refuse to invent keys for it.
            raise HTTPException(
                status_code=400,
                detail=f"Plugin {name!r} ships no config.json; nothing to override",
            )

        # Validate keys + types up-front so a bad request never persists.
        for k, v in body.items():
            # Reject `_<key>` description siblings — they are docs for
            # the Settings UI, not runtime knobs, and editing them via
            # the API would just confuse the schema. The on-disk
            # config.json is the source of truth for descriptions.
            if isinstance(k, str) and k.startswith("_"):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Cannot patch description sibling {k!r}: "
                        "keys starting with '_' are read-only docs."
                    ),
                )
            if k not in on_disk:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown config key for plugin {name!r}: {k!r}",
                )
            _validate_plugin_value(k, v, on_disk[k])

        # Persist the merged override under plugins[name].config. We
        # merge over any existing override so a partial patch doesn't
        # erase keys the user previously set.
        current = _load_settings()
        plugins_section = current.setdefault("plugins", {})
        plugin_entry = plugins_section.setdefault(name, {})
        existing_override = plugin_entry.get("config") or {}
        if not isinstance(existing_override, dict):
            existing_override = {}
        existing_override.update(body)
        plugin_entry["config"] = existing_override
        _save_settings(current)

        # Hot-swap the active parser list so the change takes effect
        # immediately without a server restart.
        try:
            n = _reload_parsers()
        except Exception:
            logger.exception("failed to reload parsers after plugin config patch")
            n = -1

        return {
            "status": "saved",
            "plugin": name,
            "config": existing_override,
            "active_parsers": n,
        }

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

        import time as _time
        import uuid as _uuid
        import json as _json
        sse_id = _uuid.uuid4().hex[:8]
        logger.info(
            "sse.open id=%s message_len=%d history=%d ctx=%s",
            sse_id, len(message), len(history or []), context_node,
        )

        def generate():
            t_open = _time.time()
            tokens = 0
            byte_count = 0
            steps = 0
            try:
                for ev in chat_service.chat_stream(
                    message, history=history, context_node_id=context_node, model=model
                ):
                    if isinstance(ev, dict):
                        kind = ev.get("type")
                        if kind == "text":
                            content = ev.get("content", "")
                            tokens += 1
                            byte_count += len(content)
                            # Escape so multi-line tokens survive the SSE wire format
                            safe = content.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
                            yield f"data: {safe}\n\n"
                            continue
                        if kind == "step":
                            steps += 1
                            # JSON one-liner; SSE values cannot contain raw newlines.
                            payload = _json.dumps(ev, default=str)
                            yield f"data: [STEP] {payload}\n\n"
                            continue
                    # Back-compat: treat any plain string as a text token.
                    if isinstance(ev, str):
                        tokens += 1
                        byte_count += len(ev)
                        safe = ev.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
                        yield f"data: {safe}\n\n"
                yield "data: [DONE]\n\n"
                logger.info(
                    "sse.close id=%s reason=done tokens=%d bytes=%d steps=%d dt=%.2fs",
                    sse_id, tokens, byte_count, steps, _time.time() - t_open,
                )
            except Exception as e:
                logger.exception(
                    "sse.close id=%s reason=error tokens=%d bytes=%d steps=%d dt=%.2fs",
                    sse_id, tokens, byte_count, steps, _time.time() - t_open,
                )
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

    # ── Hand-maintained OpenAPI spec + viewer ─────────────────────────
    # FastAPI already exposes its own auto-generated spec at /openapi.json
    # (and Swagger UI at /docs, ReDoc at /redoc). The endpoints below
    # serve the curated docs/openapi.yaml — the human-maintained source
    # of truth referenced by docs/API.md and guides/API_OPENAPI.md — so
    # that external clients and the in-app viewer can consume it without
    # leaving the running server.

    _OPENAPI_SPEC = (Path(__file__).parent.parent / "docs" / "openapi.yaml").resolve()

    @app.get("/openapi.yaml", include_in_schema=False)
    def openapi_yaml():
        """Serve the hand-maintained OpenAPI 3.1 spec verbatim."""
        if not _OPENAPI_SPEC.exists():
            raise HTTPException(status_code=404, detail="openapi.yaml not found")
        return FileResponse(_OPENAPI_SPEC, media_type="application/yaml")

    @app.get("/api-docs", include_in_schema=False)
    def api_docs():
        """Render docs/openapi.yaml with Swagger UI."""
        page = STATIC_DIR / "api-docs.html"
        if not page.exists():
            raise HTTPException(status_code=404, detail="api-docs.html not found")
        return FileResponse(page, media_type="text/html")

    @app.get("/")
    def index():
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="index.html not found")
        return FileResponse(index_file)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
