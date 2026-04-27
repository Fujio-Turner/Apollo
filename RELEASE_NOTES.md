# Release Notes

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
