# Release Notes

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
