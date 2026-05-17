# Release Notes

## Unreleased

### New Features

- **Branch-Keyed Stores + Git-Aware Watcher (#17)** — Apollo now maintains a separate index per git branch and swaps to the right one automatically on `git checkout`. New [`apollo/git/branch.py`](apollo/git/branch.py) (path helpers: `current_branch`, `safe_branch_suffix`, `branched_path`, `head_watch_path`) and [`apollo/git/watcher.py`](apollo/git/watcher.py) (`BranchWatcher` — watchdog-based `.git/HEAD` subscriber with 0.25s debounce and same-branch suppression). On-disk layout becomes `_apollo/graph__<branch>.json` and `_apollo/cblite/apollo_<md5>__<branch>.cblite2`; branch names like `issue/17` are sanitised to `issue__17`, detached HEAD picks a per-SHA suffix, and git worktrees are supported via the `.git`-file gitdir indirection. Wired into `web/server.py` `_resolve_project_store_location` and `_swap_to_project_store` (re-binds the watcher per project, stops it on shutdown). `ProjectManager._resolve_cbl_path` and the JSON branch of `ProjectManager.reprocess` route through the same helper, so "full reprocess" only nukes the active branch's store. New `GET /api/git/branch` diagnostic endpoint exposes the active branch + watcher status. Non-git folders pass through unchanged for backward compatibility. Covered by 18 new tests in [`tests/test_git_branch.py`](tests/test_git_branch.py) including a real watchdog round-trip; see [`docs/DESIGN.md §4.2.3`](docs/DESIGN.md#423-branch-keyed-stores-git-checkouts) for the invariants.
- **Combined Semantic + Graph-Expansion Search (#16)** — Vector top-k plus graph traversal in one round: the new [`search/expand.py`](search/expand.py) helper and `SemanticSearch.search_expanded` / `CouchbaseLiteSemanticSearch.search_expanded` methods return clustered `{seed, neighbors[]}` results ranked by `seed_score / (1 + depth)`. Surfaced via `python main.py search <text> --expand {callers|callees|neighbors|references} [--depth N] [--per-seed-cap N]`, the additive `GET /api/search?expand=…&depth=…&per_seed_cap=…` query params, and a new `search_graph_expanded` chat tool that replaces the `search_graph` + N parallel `get_neighbors` fan-out with a single round. Flat callers (`expand=none`, default) are byte-identical to v1.3.0. See [`docs/work/PLAN_COMBINED_SEMANTIC_GRAPH_SEARCH.md`](docs/work/PLAN_COMBINED_SEMANTIC_GRAPH_SEARCH.md) for the design and [`docs/DESIGN.md §4.5`](docs/DESIGN.md#combined-queries) for the response shape.

## v1.3.0 — 2026-05-16

### New Features

- **Python ML Libraries Integration (#8)** — New top-level `ml/` package (`ml/__init__.py`, `ml/passes.py`, `ml/tools.py`) introducing a full ML-pass pipeline for indexing, query, and UI. Adds ML-powered search/ranking tools surfaced through the chat agent. See `docs/work/PLAN_ML_LIBS_IMPLEMENTATION_REPORT.md` for the implementation report and `docs/openapi.yaml` for the new endpoints.
- **Couchbase Lite Enterprise Edition Support** — `storage/cblite_installer.py` provides an automated installer for the Couchbase Lite EE binary; `storage/cblite/ctypes_api.py` and `storage/cblite/store.py` gained EE-only hooks. New `cblite_config.json` keys and `Dockerfile.cblite` updates wire it all together.
- **Couchbase Lite Storage Tab** — New browser tab in `web/static/index.html` / `web/static/app.js` surfacing live Couchbase Lite storage state, with backing routes in `web/server.py` and `main.py`.
- **Trace Visualizer (#10)** — New chat trace visualization in `web/static/app.js` / `app.css` showing tool-call flow per chat turn. Backend support in `chat/service.py`; design doc at `docs/work/TRACE_VISUALIZER.md`.
- **Faster Incremental Indexing (#9)** — `graph/builder.py` and `graph/incremental.py` reworked for materially faster reindex passes on changed files.
- **Optimized Chat Request Schema (#12)** — `ai/chat_request.json` heavily restructured to reduce tokens / tool rounds. Versioned snapshots committed at `ai/chat_request_v5.json` through `ai/chat_request_v9.json` for reproducibility; `ai/CHANGELOG.md` documents the evolution.
- **README Refresh** — Expanded `README.md` (+130 lines) with new feature walkthroughs, Couchbase Lite + ML sections, and updated screenshots/badges.

### Bug Fixes

- **Random Graph Movement (#7)** — Fixed graph layout jitter in `web/static/app.js` so nodes no longer randomly reposition between renders.
- **File Tree Showing `/root` (#11)** — `graph/incremental.py` no longer leaks `/root`-style placeholder paths into the right-side file folder; real indexed file paths are surfaced instead.
- **Recent Files Panel** — Fixes in `web/static/app.js` / `app.css` so the "recent" file panel renders correctly after the storage-tab refactor.
- **Couchbase Lite Fixes** — `main.py`, `web/server.py`, and `web/static/app.js` patched to handle missing/partial EE installs gracefully; `.gitignore` updated to exclude per-machine `storage_binary/` artifacts (with a `.gitkeep` placeholder).
- **ML Search & CBL EE** — Fixes in `chat/service.py` and `ai/chat_request.json` so the ML search tools and Couchbase Lite EE backend interoperate correctly under chat-tool calls.

### Changes

- **`main.py`** — Significant expansion (+193 lines) for CBL EE bootstrap, storage-tab routes, and ML pipeline wiring.
- **`web/server.py`** — +335 lines: new endpoints for the storage tab, CBL installer status, ML tools, and trace export.
- **`web/static/app.js`** — +1247 lines covering the storage tab, trace visualizer, graph-stability fix, and ML-result rendering.
- **`apollo/`** — `apollo/__init__.py`, `apollo/projects/manifest.py`, and `apollo/reindex_service.py` updated for the new reindex lifecycle and manifest fields used by ML passes.
- **`storage/json_store.py`** — Reworked (+96 lines) to support ML-pass state and CBL EE coexistence.
- **`docs/`** — New / expanded docs: `docs/DESIGN.md`, `docs/openapi.yaml`, `docs/work/PLAN_ML_LIBS_IMPLEMENTATION_REPORT.md`, `docs/work/TRACE_VISUALIZER.md`.
- **`requirements.txt`** — Added dependencies required by the new ML pipeline.
- **Ignore Rules** — `.gitignore` now excludes `storage_binary/` contents and additional per-machine CBL artifacts.
- **Version bump** — All version references updated from v1.2.0 to v1.3.0 (`main.py`, `README.md` badge, `web/static/index.html` sidebar).

## v1.2.0 — 2026-05-13

### New Features

- **Expanded Local AI Tooling** — New `chat/local_tools.py` module with a large suite of local-first tool functions the chat agent can call directly against the graph, search index, and file store, dramatically reducing reliance on the remote LLM for common queries.
- **Round-Reduction Chat Pipeline** — Optimized `chat/service.py` and request schemas (`ai/chat_request.json`, `ai/chat_request_v2.json`, `ai/chat_request_v3.json`, `ai/chat_request_v4.json`) to minimize tool-calling rounds. See `docs/work/BENCHMARK_ROUND_REDUCTION.md` for measurements.
- **Tree Route Parameters** — New `/tree`-style API endpoints accept richer query parameters for filtering and traversing the file tree (`tests/test_tree_route_params.py`).
- **File Inspection Utility** — New top-level `file_inspect.py` script for ad‑hoc inspection of indexed files.
- **Project Manager Improvements** — `apollo/projects/manager.py` and `apollo/reindex_service.py` extended with new lifecycle and reindex hooks.
- **Graph Indices Module** — New `graph/indices.py` providing additional index structures used by the optimized search and traversal paths.
- **Couchbase Lite Storage Hooks** — Extended `storage/cblite/ctypes_api.py` and `storage/cblite/store.py` for richer semantic-search integration.
- **Embedder Enhancements** — `embeddings/embedder.py` upgraded with new model/option handling used by the chat tools.

### Bug Fixes

- **Graph Query Fixes (#2)** — Corrected behavior in `graph/query.py`, `graph/incremental.py`, `search/semantic.py`, and `search/cblite_semantic.py`; covered by new tests in `tests/test_graph_query.py` and `tests/test_search_semantic.py`. UI updates in `web/static/app.js` / `app.css` reflect the corrected results.
- **Settings & Project State Cleanup (#3)** — Stopped tracking machine-local `data/_apollo/apollo.json` and `data/settings.json`; expanded `.gitignore` and added project-manager safeguards.
- **Stop Tracking Demo Runtime State** — Removed `demo/_apollo/apollo.json` from version control (per-machine runtime artifact).
- **Chat Tool Routing (#5)** — Fixes in `web/server.py` for chat tool dispatch and tree-route handling, with new coverage in `tests/test_chat_local_tools.py`.

### Changes

- **Documentation** — New / expanded docs: `docs/AI_BEST_PRACTICES.md`, `docs/AI_MORE_LOCAL_FUNCTIONS.md`, `docs/work/PLAN_LLM_ROUND_REDUCTION.md`, `docs/work/PLAN_MORE_LOCAL_AI_FUNCTIONS.md`, `docs/work/BENCHMARK_ROUND_REDUCTION.md`, `docs/work/PLAN_ML_LIBS.md`, plus updates to `docs/DESIGN.md`, `docs/API.md`, and `docs/openapi.yaml`.
- **Web Server** — `web/server.py` significantly expanded to expose the new local tool endpoints, tree-route parameters, and chat trace plumbing.
- **Storage** — `storage/json_store.py` reworked to support the new project/state model.
- **Watcher** — `watcher.py` overhauled for more robust incremental indexing.
- **Tests** — Added/expanded test suites: `tests/test_chat_local_tools.py`, `tests/test_file_routes.py`, `tests/test_project_manager.py`, `tests/test_tree_route_params.py`, `tests/test_graph_query.py`, `tests/test_search_semantic.py`.
- **Dependencies** — `requirements.txt` updated with packages required by the new local AI tooling.
- **Ignore Rules** — `.gitignore` now excludes `data/index.json` and per-project runtime state.
- **Version bump** — All version references updated from v1.1.0 to v1.2.0.

## v1.1.0 — 2026-04-30

### New Features

- **Massive Plugin Expansion** — Apollo now ships with **49 language and format plugins** (up from 8), covering virtually any project type. New plugins include:
  - **Programming Languages:** TypeScript, C, C++17, C# 12, Dart, Elixir, Java 17, JavaScript, Kotlin, Lua, Node.js 20, PHP 8, PowerShell 7, R, Ruby 3, Rust, Scala 3, Shell, Swift 5
  - **Structured Data:** JSON, YAML, TOML, XML, OpenAPI 3, JSON Schema, CSV
  - **Document Formats:** AsciiDoc, Org Mode, reStructuredText
  - **Notebooks:** Jupyter, R Markdown
  - **Build/Ops:** Dockerfile, docker-compose, Makefile, CMake, Maven (pom.xml), Gradle, Terraform, Kubernetes YAML
  - **CI/CD & Config:** GitHub Actions, EditorConfig, .gitignore, .env / .properties
  - **Database:** SQL
- **API Docs Endpoint** — New `/api-docs` static page rendering the OpenAPI 3.1 spec, plus expanded REST API reference (`docs/API.md`).
- **Optimized Chat Pipeline** — Refactored `chat/service.py` with externalized request/response schemas (`ai/chat_request.json`, `ai/chat_request_v1.json`) and a leaner tool-calling loop.
- **Chat Tracing** — End-to-end trace capture for chat interactions (timing, tool calls, rounds) surfaced in both the API and browser UI.
- **Improved Idea Cloud** — Better word cloud generation, layout, and styling for the unified browser dashboard.
- **Notes & Bookmarks Search** — Annotations, highlights, and bookmarks are now fully searchable alongside graph nodes.
- **HTML5 Plugin** — Built-in HTML parsing with element/attribute extraction and link/import edge detection.
- **Plugin Configuration UI** — Per-plugin configuration loader, ignore-dirs settings, and admin API for plugin management.
- **Expanded Settings** — `data/settings.json` now exposes plugin-level configuration, ignore-dir overrides, and additional chat provider options.

### Changes

- **Web UI Overhaul** — `web/static/app.js` rewritten with new graph rendering, chat trace panel, and plugin/version display in the sidebar. `app.css` redesigned for plugin badges and trace UI. `index.html` updated with new entry points.
- **Server Expansion** — `web/server.py` adds plugin config endpoints, indexing-status improvements, chat trace propagation, and the `/api-docs` route.
- **Documentation** — Added `docs/work/PLUGINS_CREATED.md`, `docs/work/PLUGINS_CHECKLIST.md`, `docs/work/PLUGIN_BIG_BUILD_IMPLEMENTATION_SUMMARY.md`, `docs/AI_MORE_LOCAL_FUNCTIONS.md`, expanded `docs/DESIGN.md` and `guides/making_plugins.md`.
- **Tests** — New test suites: `tests/test_plugin_config_api.py`, `tests/test_plugin_config_loader.py`, `tests/test_plugin_ignore_dirs.py`, plus per-plugin test files (one `test_parser.py` per plugin).
- **Dependencies** — Updated `requirements.txt` with new packages required for chat tracing and additional parsers.
- **Version bump** — All version references updated from v1.0.0 to v1.1.0.

---

## v1.0.0 — 2026-04-27

### 🎉 Initial Release

Apollo v1.0.0 marks the first stable release of the code knowledge graph browser.

### New Features

- **Code Knowledge Graph** — Parse Python via AST with rich extraction (params, defaults, type annotations, decorators, docstrings, complexity metrics, async/nested/test detection, dataclass support). Tree-sitter backend for JS/TS/Go/Rust.
- **Markdown Indexing** — Full AST-based parsing with frontmatter, hierarchical sections (h1–h6), code blocks, links, images, tables, and task items.
- **Non-Code Files** — JSON, YAML, CSV, TOML, and plain text files indexed as searchable documents.
- **Semantic Search** — Vector embeddings with `all-MiniLM-L6-v2` (384-dim) and cosine-similarity search across functions, classes, documents, and sections.
- **Spatial Coordinates** — Every node positioned in 3D space (X: conceptual domain via UMAP, Y: structural depth via BFS, Z: importance via PageRank). Enables face queries, range queries, and spatial walks.
- **Interactive Browser UI** — Force-directed graph rendering (ECharts), word cloud, depth slider, source preview panel, sidebar filters, and unified chat input.
- **AI Chat with Tool-Calling** — Grok API integration with 10+ tools for graph search, node inspection, stats, file inspection, and multi-round reasoning (up to 5 rounds).
- **Live File Watching** — Incremental re-indexing with stat-based prefilter (~261× speedup on no-change runs). WebSocket push updates to browser.
- **Annotations** — Highlights, Markdown notes, and bookmarks anchored to nodes. Soft-delete with trash recovery.
- **Web Content Capture** — Pull URLs (HTML/PDF) into the graph, auto-convert to Markdown via readability + markdownify (HTML) or Grok summarization (PDF), with version history.
- **Dual Storage Backends** — JSON (zero dependencies) or Couchbase Lite with SQL++ queries and native vector search.
- **Plugin Architecture** — Drop-in language plugins under `plugins/`. Python3 and Markdown GFM built-in; easily extend for other languages.
- **Release Guide** — Comprehensive release checklist and semantic versioning standards (`guides/RELEASE.md`).

### Performance

- Single filesystem walk with lazy directory creation.
- Auto-skip 20+ dependency directories (`node_modules/`, `venv/`, `__pycache__/`, etc.).
- Stat-based incremental prefilter delivers **261× speedup** on unchanged files.
- Compact JSON serialization with configurable embedding batch sizes.
- Non-blocking indexing via `asyncio` executor.
- Edge cap (3× node count) prevents browser freeze on large graphs.

### Configuration & Environment

- **XAI_API_KEY** — Optional Grok API key for AI chat (can be set via `.env` or web Settings panel).
- CLI flags for parser selection, incremental indexing, embedding/spatial coordinate toggling.
- Configurable settings JSON for chat providers, appearance, graph rendering, and indexing behavior.
- Docker Compose support for containerized deployments.

### Documentation

- Full design document (`docs/DESIGN.md`) with 14 architectural phases.
- REST API reference (`docs/API.md`) and OpenAPI 3.1 specification (`docs/openapi.yaml`).
- Plugin development guide (`guides/making_plugins.md`).
- Schema design guidelines (`guides/SCHEMA_DESIGN.md`).
- HTML/CSS standards (`guides/STYLE_HTML_CSS.md`).
- API maintenance guide (`guides/API_OPENAPI.md`).
- **NEW** Release process guide (`guides/RELEASE.md`).

### Version Display

The Apollo backend and browser UI automatically log and display their version (`v1.0.0`) on startup and via `/api/version` endpoint.

### Testing & Quality

- Full unit test suite with pytest.
- Type hints throughout codebase.
- Linting with ruff (E9, F63, F7, F82 checks).
- CI pipeline with Docker build verification.

### Breaking Changes

None — this is the initial release.

### Known Limitations

- Folder picker uses host OS dialog; Docker containers must index via mount to `./target/`.
- Spatial coordinate computation can be disabled for very large codebases (>100k nodes) if performance is a concern.
- Embedding generation is optional and requires `sentence-transformers` (not installed by default).

### License

Source code is licensed under various licenses (Business Source License 1.1 and others). See `licenses/` directory for details.

---

**[Release Process Guide](guides/RELEASE.md)** — How to cut future releases following semver.
