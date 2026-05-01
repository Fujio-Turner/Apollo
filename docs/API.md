# Graph Search — API Reference

Quick reference for the Graph Search REST API.
For the full machine-readable spec see [`openapi.yaml`](openapi.yaml).

The running server exposes three interactive views:

- [`/api-docs`](http://localhost:8080/api-docs) — Swagger UI rendering of the **hand-maintained** [`docs/openapi.yaml`](openapi.yaml) (curated descriptions, examples, schemas).
- [`/openapi.yaml`](http://localhost:8080/openapi.yaml) — the raw YAML spec, served verbatim for external clients and codegen.
- [`/docs`](http://localhost:8080/docs) and [`/redoc`](http://localhost:8080/redoc) — FastAPI's auto-generated views (derived from the route signatures in `server.py`).

---

## System

### `GET /api/env`

Returns runtime environment capabilities.

**Response**
```json
{ "native_picker": true }
```

### `GET /api/version`

Returns the Apollo backend version (sourced from `main.__version__`).

**Response**
```json
{ "version": "1.4.2" }
```

---

## Filesystem

### `POST /api/browse-folder`

Opens the native OS folder-picker dialog and returns the selected path.

**Response**
```json
{ "path": "/Users/me/projects" }
```

### `GET /api/browse-dir`

Lists immediate subdirectories of a given path.

| Param  | Type   | Default | Description          |
|--------|--------|---------|----------------------|
| `path` | string | `/`     | Directory to inspect |

**Response**
```json
{ "path": "/Users/me", "dirs": ["projects", "Documents"] }
```

### `GET /api/tree`

Returns a recursive directory/file tree of the indexed project.

---

## Indexing

### `GET /api/indexing-status`

Returns the current indexing progress.

**Response**
```json
{ "active": true, "step": 2, "step_label": "Parsing files", "total_steps": 5, "detail": "src/main.py" }
```

### `POST /api/index`

Indexes a directory and builds the code graph.

**Request**
```json
{ "directory": "/Users/me/projects/myapp" }
```

**Response** — graph statistics after indexing completes.

### `DELETE /api/index`

Deletes the current index.

**Response**
```json
{ "status": "deleted", "total_nodes": 0, "total_edges": 0 }
```

---

## Reindex

Phase 9 incremental-reindex telemetry and configuration. All endpoints
return `503 Service Unavailable` when the reindex service is not running.

### `POST /api/index/sweep`

Manually trigger a single background reindex sweep.

**Response**
```json
{
  "status": "success",
  "message": "Sweep complete in 1234ms",
  "stats": { "duration_ms": 1234, "files_parsed": 7, "nodes_added": 12,
             "nodes_removed": 0, "edges_added": 18, "edges_removed": 0 }
}
```
Returns `{ "status": "already_running" }` when a sweep is in progress.

### `GET /api/index/history`

Recent reindex runs.

| Param   | Type | Default | Description                |
|---------|------|---------|----------------------------|
| `limit` | int  | `20`    | 1–100, most recent runs    |

### `GET /api/index/last`

Most recent reindex statistics.

**Response**
```json
{ "has_run": true, "stats": { "duration_ms": 1234, "files_parsed": 7, "...": "..." } }
```

### `GET /api/index/summary`

Aggregate activity summary plus the active configuration.

### `GET /api/index/config`

Current reindex configuration plus the effective foreground/background
strategy.

### `POST /api/index/config`

Update one or more reindex configuration fields. All parameters are
**query-string** values (not body).

| Param                    | Type    | Description                                |
|--------------------------|---------|--------------------------------------------|
| `strategy`               | string  | `incremental_local` or `full`              |
| `sweep_interval_minutes` | int     | Background sweep cadence                   |
| `sweep_on_session_start` | bool    | Run a sweep on each new session            |
| `local_max_hops`         | int     | BFS hops for `incremental_local`           |
| `force_full_after_runs`  | int     | Force a full reindex every N runs          |

---

## Graph

### `GET /api/graph`

Returns nodes, edges, and categories for visualization.

| Param       | Type   | Default | Description                          |
|-------------|--------|---------|--------------------------------------|
| `path`      | string | —       | Filter to a file/directory path      |
| `types`     | string | —       | Comma-separated node types to include|
| `edges`     | string | —       | Comma-separated edge types to include|
| `limit`     | int    | `2000`  | Max nodes returned                   |
| `max_edges` | int    | `0`     | Max edges (0 = unlimited)            |

**Response** includes `nodes`, `edges`, `categories`, and truncation info.

### `GET /api/node/{node_id}`

Returns full detail for a single node, including incoming and outgoing edges.

| Param     | Type   | Description |
|-----------|--------|-------------|
| `node_id` | string | Node ID     |

### `GET /api/node/{node_id}/connections`

Heavier variant of `/api/node/{node_id}` that also reads each connected
node's source file and includes a small surrounding-line preview snippet
for the call site or definition. Called on-demand by the Connections tab.

**Response**
```json
{
  "id": "func::main.py::main",
  "edges_in":  [ { "source_id": "...", "source_snippet": { "start": 9, "end": 11, "lines": ["..."], "highlight": 10 }, "...": "..." } ],
  "edges_out": [ { "target_id": "...", "target_snippet": { "...": "..." }, "...": "..." } ]
}
```

### `GET /api/neighbors/{node_id}`

BFS-walks the graph from `node_id`, optionally restricted to specific edge types and a direction. Mirrors the AI's `get_neighbors` tool.

| Param        | Type   | Default | Description                                                  |
|--------------|--------|---------|--------------------------------------------------------------|
| `node_id`    | string | —       | Starting node ID *(path)*                                    |
| `depth`      | int    | `1`     | BFS depth (1–5)                                              |
| `edge_types` | string | —       | Comma-separated edge types (e.g. `calls,imports,defines`)    |
| `direction`  | string | `both`  | `in` (predecessors), `out` (successors), or `both`           |

**Response**
```json
{
  "node_id": "func::main.py::main",
  "depth": 2,
  "direction": "out",
  "edge_types": ["calls"],
  "neighbors": [
    { "id": "func::utils.py::parse", "name": "parse", "type": "function", "path": "utils.py", "line_start": 7, "depth": 1 },
    { "id": "func::utils.py::tokenize", "name": "tokenize", "type": "function", "path": "utils.py", "line_start": 22, "depth": 2 }
  ]
}
```

### `GET /api/wordcloud`

Idea Cloud — symbols ranked by **graph strength** (sum of in+out degree across
nodes sharing the same display name), aggregated per name. The `value` field
is the strength itself, not a raw frequency, so high-value items are the
project's true hubs and the most useful candidates for impact analysis.

| Param   | Type   | Description                                                                       |
|---------|--------|-----------------------------------------------------------------------------------|
| `path`  | string | Optional path-prefix filter (e.g. `apollo/`).                                     |
| `mode`  | string | `strong` (default, top 30, strength ≥ 2), `relevant` (top 100, strength ≥ 2), or `all` (everything, capped at 500). |

**Response**
```json
{
  "items": [
    { "name": "GraphQuery", "value": 84.0, "count": 1 },
    { "name": "search",     "value": 42.0, "count": 3 }
  ],
  "total": 412,
  "shown": 30,
  "mode": "strong",
  "min_strength": 2
}
```

`value` = sum of in+out degree across every node with that display name.
`count` = number of distinct nodes that share the name. `total` is the total
unique names before tier filtering; `shown` is what made it into `items`.

**Recommended workflow (impact analysis):**

1. Fetch `mode=strong` to surface the hub symbols.
2. For a hub name, call [`/api/search`](#post-apisearch) → grab the top node IDs.
3. For each ID, call [`/api/graph`](#get-apigraph) with a small depth and/or
   [`/api/node/{id}`](#get-apinodeid) to enumerate callers/callees.
4. The breadth and `value` together estimate **blast radius** — how much of
   the project a change to that hub is likely to touch.

Use `mode=all` only when you explicitly need the long tail (low-strength
helpers, single-use utilities) — it returns dense, low-signal data.

### `GET /api/stats`

Returns high-level graph statistics.

**Response**
```json
{ "total_nodes": 350, "total_edges": 1200, "node_types": {}, "edge_types": {} }
```

---

## Files

Read-only file inspection (Phase 12.3a). Every endpoint validates `path` against the indexed sandbox — paths outside the index return `403 Forbidden`. The `expected_md5` / `md5` query parameter is optional; when provided and stale the server returns `409 Conflict` with the new md5.

### `GET /api/file/stats`

Cheap structural summary of a file. Mirrors the AI's `file_stats` tool.

| Param  | Type   | Description                |
|--------|--------|----------------------------|
| `path` | string | File path *(required)*     |

**Response**
```json
{
  "path": "/abs/project/parser.py",
  "size_bytes": 18234,
  "line_count": 612,
  "md5": "020d9cb368ceca4b90105c050173d334",
  "language": "python",
  "function_count": 23,
  "class_count": 4,
  "top_level_imports": ["import os", "from ast import parse"]
}
```

### `GET /api/file/content`

Returns the full UTF-8 source of a sandboxed file. Read-only.

| Param  | Type   | Description            |
|--------|--------|------------------------|
| `path` | string | File path *(required)* |

### `GET /api/file/raw`

Streams a sandboxed file as-is using its detected MIME type. Used by the
Content view to render images and other binary assets referenced from
indexed Markdown / HTML without base64-inlining them in JSON responses.

| Param  | Type   | Description            |
|--------|--------|------------------------|
| `path` | string | File path *(required)* |

### `GET /api/file/section`

Return an inclusive 1-indexed line range. Cap: 800 lines per call.

| Param   | Type   | Description                                       |
|---------|--------|---------------------------------------------------|
| `path`  | string | File path *(required)*                            |
| `start` | int    | Start line (1-indexed) *(required)*               |
| `end`   | int    | End line (inclusive) *(required)*                 |
| `md5`   | string | Optional version check. 409 on mismatch.          |

**Response**
```json
{
  "path": "...", "start_line": 100, "end_line": 137, "md5": "...",
  "lines": [{ "n": 100, "text": "def main():" }],
  "truncated": false
}
```

### `GET /api/file/function`

AST-extract a function, method, or class by name. `name` may be `foo`, `MyClass.foo`, or `MyClass`.

| Param  | Type   | Description                                       |
|--------|--------|---------------------------------------------------|
| `path` | string | File path *(required)*                            |
| `name` | string | Symbol name *(required)*                          |
| `md5`  | string | Optional version check. 409 on mismatch.          |

### `GET /api/files/declarations`

List every top-level declaration in a file (functions, classes, methods, and `const/let/var/def` bindings — including `new Map()` / `WeakMap()` / `Set()` / `WeakSet()`). Reads from the parser's `defines` edges and falls back to a single regex pass for files indexed by `text_parser`. Mirrors the AI's `list_declarations` tool.

| Param   | Type   | Default | Description                                                                                              |
|---------|--------|---------|----------------------------------------------------------------------------------------------------------|
| `path`  | string | —       | File path *(required)*                                                                                   |
| `kinds` | string | —       | Comma-separated kind filter: `function,class,method,const,let,var,def,map_decl,weakmap_decl,set_decl,weakset_decl` |
| `limit` | int    | `200`   | Max declarations to return (capped at 500)                                                               |

**Response**
```json
{
  "path": "/abs/project/cache.js",
  "accuracy": "ast",
  "count": 5,
  "truncated": false,
  "declarations": [
    { "name": "parseTimeCache", "kind": "map_decl", "line_start": 2, "line_end": 2, "is_exported": false, "parent": "" },
    { "name": "clearCaches",    "kind": "function", "line_start": 5, "line_end": 8, "is_exported": true,  "parent": "" }
  ]
}
```

`accuracy` is one of `ast` (parser produced the rows), `regex` (regex fallback used), or `graph_only` (file unreadable; only graph rows returned).

### `GET /api/files/usages`

Every line in a file that references one or more symbols, classified as `declaration` / `read` / `write` / `call` / `comment` / `string`. Returns line numbers + a single trimmed line per hit, no surrounding context (~10× less data than `/api/file/search`). Mirrors the AI's `find_symbol_usages` tool.

**Two input modes (pass exactly one):**

- `symbol=foo` — single-symbol mode. Returns the legacy flat shape.
- `symbols=foo,bar,baz` — **batch mode** (max 20). Reads the file ONCE
  and classifies every line against every requested symbol. Strongly
  preferred when checking ≥2 symbols in the same file: folds N
  round-trips into 1. See [`PLAN_MORE_LOCAL_AI_FUNCTIONS.md` §8.13](./work/PLAN_MORE_LOCAL_AI_FUNCTIONS.md)
  for the benchmark trace that motivated this addition.

| Param     | Type   | Description                                                                                  |
|-----------|--------|----------------------------------------------------------------------------------------------|
| `path`    | string | File path *(required)*                                                                       |
| `symbol`  | string | Single symbol name. Use this **OR** `symbols` (one of the two is required)                   |
| `symbols` | string | Comma-separated list (max 20). Triggers batch mode and returns the `results[]` shape         |
| `kinds`   | string | Comma-separated kind filter: `declaration,read,write,call,comment,string` (applies to all)   |

**Response — single-symbol mode (`symbol=…`)**
```json
{
  "path": "/abs/project/cache.js",
  "symbol": "parseTimeCache",
  "md5": "...",
  "accuracy": "text",
  "count": 4,
  "usages": [
    { "line_no": 2,  "kind": "declaration", "text": "const parseTimeCache = new Map();" },
    { "line_no": 6,  "kind": "write",       "text": "parseTimeCache.clear();" },
    { "line_no": 10, "kind": "read",        "text": "if (parseTimeCache.has(s)) return parseTimeCache.get(s);" },
    { "line_no": 11, "kind": "write",       "text": "parseTimeCache.set(s, 0);" }
  ]
}
```

**Response — batch mode (`symbols=parseTimeCache,clearCaches`)**
```json
{
  "path": "/abs/project/cache.js",
  "md5": "...",
  "accuracy": "text",
  "total": 5,
  "results": [
    {
      "symbol": "parseTimeCache",
      "count": 4,
      "usages": [
        { "line_no": 2,  "kind": "declaration", "text": "const parseTimeCache = new Map();" },
        { "line_no": 6,  "kind": "write",       "text": "parseTimeCache.clear();" }
      ]
    },
    {
      "symbol": "clearCaches",
      "count": 1,
      "usages": [
        { "line_no": 5, "kind": "declaration", "text": "function clearCaches() {" }
      ]
    }
  ]
}
```

### `GET /api/files/outline`

Sub-second outline of a file. Source files: top-level declaration tree (kinds + line ranges, `accuracy: "ast"`). HTML: regex-derived tag tree of landmark elements (`head`/`body`/`script`/`style`/`<h1..h6>`/`<section>`/`<main>`/…) plus the JS function/class/const declarations found inside each `<script>` block (`accuracy: "regex"`). For other files outside the parser graph, returns `outline: []` and `accuracy: "none"` rather than guessing. Mirrors the AI's `outline_file` tool.

| Param   | Type | Default | Description                                |
|---------|------|---------|--------------------------------------------|
| `path`  | string | — | File path *(required)*                       |
| `depth` | int    | `2` | Max nesting depth to descend (max 6). For HTML, the depth caps tag nesting; JS decls inside `<script>` are emitted as content (one level deeper than the script row) whenever `depth ≥ 2`. |

**Response — source file (`accuracy: "ast"`)**
```json
{
  "path": "cache.js",
  "accuracy": "ast",
  "count": 2,
  "depth": 2,
  "outline": [
    { "kind": "function", "name": "clearCaches",  "line_start": 5, "line_end": 8,  "depth": 1 },
    { "kind": "function", "name": "getOperators", "line_start": 9, "line_end": 13, "depth": 1 }
  ]
}
```

**Response — HTML file (`accuracy: "regex"`)**

Replaces `file_search` for "what's in this HTML?" — the [§8.13 benchmark trace](./work/PLAN_MORE_LOCAL_AI_FUNCTIONS.md) showed the model burning a round on `get_function_source` (which can't parse HTML) before this fallback existed.

```json
{
  "path": "en/index.html",
  "accuracy": "regex",
  "count": 6,
  "depth": 3,
  "outline": [
    { "kind": "tag",      "name": "<head>",            "line_start": 3,  "line_end": 5,  "depth": 2 },
    { "kind": "tag",      "name": "<body>",            "line_start": 6,  "line_end": 18, "depth": 2 },
    { "kind": "heading",  "name": "<h1>",              "line_start": 7,  "line_end": 7,  "depth": 1 },
    { "kind": "tag",      "name": "<section #main>",   "line_start": 8,  "line_end": 10, "depth": 3 },
    { "kind": "script",   "name": "<script>",          "line_start": 11, "line_end": 17, "depth": 3 },
    { "kind": "function", "name": "clearCaches",       "line_start": 12, "line_end": 12, "depth": 4 },
    { "kind": "const",    "name": "operatorsCache",    "line_start": 15, "line_end": 15, "depth": 4 },
    { "kind": "class",    "name": "Helper",            "line_start": 16, "line_end": 16, "depth": 4 }
  ]
}
```

---

## Search

### `GET /api/search`

Full-text search over indexed symbols.

| Param  | Type   | Default | Description                    |
|--------|--------|---------|--------------------------------|
| `q`    | string | —       | Search query *(required)*      |
| `top`  | int    | `10`    | Max results                    |
| `type` | string | —       | Filter by node type (e.g. `function`) |

**Response**
```json
{ "results": [{ "id": "abc", "name": "parse_file", "type": "function", "path": "src/parser.py", "line_start": 12, "score": 0.95 }] }
```

### `POST /api/project/search` ⚠️ Deprecated for AI/LLM file-named queries

> **Deprecated.** Prefer the Phase 8 endpoints when the user already
> knows the target file or symbol:
> - [`GET /api/files/outline`](#get-apifilesoutline) for "what's in file X"
> - [`GET /api/files/declarations`](#get-apifilesdeclarations) for
>   "list everything declared in file X" (replaces regexes like
>   `function|class|def`)
> - [`GET /api/files/usages`](#get-apifilesusages) for "where is symbol Y
>   used in file X"
>
> The chat service now strips `project_search` and `file_search` from
> the LLM tool catalog whenever the user names a specific file. See
> [`PLAN_MORE_LOCAL_AI_FUNCTIONS.md` §8.13](./work/PLAN_MORE_LOCAL_AI_FUNCTIONS.md)
> for the trace that motivated the change. The HTTP route is still
> served unchanged for non-LLM callers; there is no scheduled removal
> date.

Grep across the indexed project. Returns matches with file/line/context. Read-only. Mirrors the AI's `project_search` tool. Hard caps: 500 matches or 200 KB of snippet bytes (whichever first).

**Request Body**

| Field       | Type   | Default | Description                                       |
|-------------|--------|---------|---------------------------------------------------|
| `pattern`   | string | —       | Regex (or literal if `regex=false`) *(required)*  |
| `root`      | string | indexed root | Sub-directory to scope the search             |
| `context`   | int    | `5`     | Lines of context before AND after each match      |
| `file_glob` | string | `*.py`  | Comma-separated globs, e.g. `*.py,*.md`           |
| `regex`     | bool   | `true`  | Treat pattern as regex                            |

**Response**
```json
{
  "root": "/abs/project",
  "pattern": "requests\\.put",
  "match_count": 2,
  "truncated": false,
  "matches": [
    { "path": "/abs/project/api.py", "line_no": 42, "text": "    requests.put(url, json=data)", "context_before": ["..."], "context_after": ["..."] }
  ]
}
```

### `POST /api/file/search` ⚠️ Deprecated for AI/LLM file-named queries

> **Deprecated.** Use one of the structured Phase 8 endpoints instead:
> - [`GET /api/files/outline`](#get-apifilesoutline) — sub-second
>   declaration tree of a file. Use this on first contact.
> - [`GET /api/files/declarations`](#get-apifilesdeclarations) — exact
>   list of every top-level declaration with line ranges.
> - [`GET /api/files/usages`](#get-apifilesusages) — every line in a
>   file that references a given symbol, classified as
>   `declaration` / `read` / `write` / `call` / `comment` / `string`.
>
> The chat service now strips `file_search` and `project_search` from
> the LLM tool catalog whenever the user names a specific file. See
> [`PLAN_MORE_LOCAL_AI_FUNCTIONS.md` §8.13](./work/PLAN_MORE_LOCAL_AI_FUNCTIONS.md)
> for the rationale and the benchmark trace. The HTTP route is still
> served unchanged for non-LLM callers; there is no scheduled removal
> date.

Grep within a single file. Read-only. Mirrors the AI's `file_search` tool. Cap: 200 matches.

**Request Body**

| Field          | Type   | Default | Description                                  |
|----------------|--------|---------|----------------------------------------------|
| `path`         | string | —       | File path *(required)*                       |
| `pattern`      | string | —       | Pattern *(required)*                         |
| `context`      | int    | `5`     | Lines of context before/after                |
| `regex`        | bool   | `true`  | Treat pattern as regex                       |
| `expected_md5` | string | —       | Optional. 409 if the file changed.           |

### `POST /api/search/multi`

Run multiple graph searches in parallel and merge them into a single deduped, score-merged result list. Mirrors the AI's `search_graph_multi` tool — useful for casting a wide net with synonyms in a single round-trip.

**Request Body**

| Field     | Type            | Default | Description                                       |
|-----------|-----------------|---------|---------------------------------------------------|
| `queries` | array of string | —       | Sub-queries to run in parallel *(required, ≥1)*   |
| `top`     | int             | `10`    | Max results per sub-query                         |
| `type`    | string          | —       | Optional node-type filter applied to every query  |

**Response**
```json
{
  "queries": ["couchbase", "cblite", "lite"],
  "results": [
    {
      "id": "file::cblite/store.py",
      "name": "store.py",
      "type": "file",
      "path": "cblite/store.py",
      "line_start": 1,
      "matched_queries": ["couchbase", "cblite"]
    }
  ]
}
```

---

## Annotations (Notes & Bookmarks)

User-authored **highlights**, **notes**, **bookmarks**, and **tags**, stored
in `<project>/_apollo/annotations.json`. Each annotation is anchored to either
a file path **or** a graph node ID, so an AI assistant can pull the user's own
notes alongside graph context — e.g. fetch every note attached to a node, then
follow up with `/api/node/{node_id}` or `/api/neighbors/{node_id}` to see how
the noted entity relates to the rest of the codebase.

Powers the **Notes & Bookmarks** tab in the *My Hub Content* panel.

### `GET /api/annotations`

List all annotations in the active project, newest first.

| Param  | Type   | Description                                              |
|--------|--------|----------------------------------------------------------|
| `type` | string | Filter: `highlight`, `bookmark`, `note`, or `tag`        |

**Response**
```json
{
  "annotations": [
    {
      "id": "an::a1b2c3d4e5f60718",
      "type": "note",
      "target": { "type": "node", "node_id": "func::src/main.py::main" },
      "created_at": "2026-04-28T22:11:03.482Z",
      "content": "Entry point — refactor candidate, see issue #42.",
      "tags": ["refactor", "entrypoint"],
      "color": "yellow",
      "stale": false,
      "last_modified_at": null
    }
  ]
}
```

### `POST /api/annotations/create`

Create a new annotation.

**Request Body**

| Field             | Type   | Required | Description                                                       |
|-------------------|--------|----------|-------------------------------------------------------------------|
| `type`            | string | yes      | `highlight` \| `bookmark` \| `note` \| `tag`                      |
| `target`          | object | yes      | `{type:"file", file_path}` or `{type:"node", node_id}`            |
| `content`         | string | no       | Free-form text (markdown allowed)                                 |
| `tags`            | array  | no       | Tag strings                                                       |
| `color`           | string | no       | `red`, `yellow`, `green`, `blue`, `purple`, `gray`                |
| `highlight_range` | object | no       | `{start_line, end_line, start_col?, end_col?}` (file targets)     |

**Response** — the created [`Annotation`](#get-apiannotations).

### `GET /api/annotations/by-target`

Find every annotation attached to a single file or graph node. Provide
exactly one of `file` or `node`.

| Param  | Type   | Description                                |
|--------|--------|--------------------------------------------|
| `file` | string | Project-relative file path                 |
| `node` | string | Graph node ID, e.g. `func::main.py::main`  |

### `GET /api/annotations/by-tag`

Find every annotation carrying a given tag.

| Param | Type   | Required | Description |
|-------|--------|----------|-------------|
| `tag` | string | yes      | Tag value   |

### `GET /api/annotations/{annotation_id}`

Return a single annotation by ID.

### `PUT /api/annotations/{annotation_id}`

Update any subset of an annotation's fields. Body fields match
`POST /api/annotations/create`; omit fields you don't want to change.
You may also set `stale: true|false`.

### `DELETE /api/annotations/{annotation_id}`

Delete an annotation. Also drops it from any collection that referenced it.

**Response**
```json
{ "deleted": "an::a1b2c3d4e5f60718" }
```

### `GET /api/annotations/collections`

List all annotation collections.

**Response**
```json
{
  "collections": [
    {
      "id": "coll::3344aabbccdd0011",
      "name": "Refactor backlog",
      "description": null,
      "annotation_ids": ["an::a1b2c3d4e5f60718"],
      "created_at": "2026-04-28T22:00:00Z"
    }
  ]
}
```

### `POST /api/annotations/collections`

Create a collection grouping related annotations.

**Request Body**

| Field            | Type   | Required | Description                  |
|------------------|--------|----------|------------------------------|
| `name`           | string | yes      | Display name                 |
| `description`    | string | no       | Optional description         |
| `annotation_ids` | array  | no       | Annotation IDs to include    |

### `DELETE /api/annotations/collections/{collection_id}`

Delete a collection. Annotations inside the collection are **not** deleted.

---

## Settings

### `GET /api/logging/info`

Snapshot of Apollo's logging config plus on-disk file sizes. Powers the
Settings → Logging tab. See [`guides/LOGGING.md`](../guides/LOGGING.md) § 9.

### `GET /api/settings`

Returns current settings. API keys are masked.

### `PUT /api/settings`

Updates settings.

**Request**
```json
{ "api_keys": { "openai": "sk-..." }, "chat": { "model": "gpt-4o" } }
```

**Response**
```json
{ "status": "saved" }
```

### `PATCH /api/settings/plugins/{name}/config`

Apply a partial config override for plugin `name` and hot-reload the
active parser list. Persists the merged override into
`data/settings.json` under `plugins[<name>].config` and re-runs
`discover_plugins()` so the change takes effect without a server
restart.

**Path Parameters**

| Field  | Type   | Required | Description                                                |
|--------|--------|----------|------------------------------------------------------------|
| `name` | string | yes      | Plugin folder name under `plugins/` (e.g. `markdown_gfm`). |

**Request Body**

A partial dict of overrides. Each key MUST exist in the plugin's
on-disk `config.json`; each value MUST type-match the on-disk default;
`enabled` is a strict `bool` when present. Unknown keys → `400`,
unknown plugin → `404`, type mismatch → `400`.

```json
{ "enabled": false }
```

**Response**

```json
{
  "status": "saved",
  "plugin": "markdown_gfm",
  "config": { "enabled": false },
  "active_parsers": 4
}
```

---

## Chat

### `GET /api/chat/status`

Returns chat availability and the active model.

**Response**
```json
{ "available": true, "model": "gpt-4o" }
```

### `POST /api/chat`

Sends a message and streams the response via **SSE** (`text/event-stream`).

**Request**
```json
{ "message": "Explain the parse module", "history": [], "context_node": "node_abc", "model": "gpt-4o" }
```

### `GET /api/chat/threads`

Lists all chat thread summaries.

### `POST /api/chat/threads`

Creates a new chat thread.

**Request**
```json
{ "title": "Architecture review", "model": "gpt-4o" }
```

### `GET /api/chat/threads/{thread_id}`

Returns a full thread including all messages.

### `DELETE /api/chat/threads/{thread_id}`

Deletes a thread.

**Response**
```json
{ "status": "deleted" }
```

### `POST /api/chat/threads/{thread_id}/messages`

Appends a message to an existing thread.

**Request**
```json
{ "role": "user", "content": "What does this function do?" }
```

### `PUT /api/chat/threads/{thread_id}/messages/last`

Replaces the last message in a thread. Used to commit the assistant's
streamed reply at the end of an SSE stream so the saved thread reflects
the final content (instead of any placeholder appended when streaming
started).

**Request**
```json
{ "role": "assistant", "content": "Final assembled reply" }
```

---

## Images

### `POST /api/image/generate`

Generates images from a text prompt.

**Request**
```json
{ "prompt": "architecture diagram of the parser", "model": "dall-e-3" }
```

**Response**
```json
{ "images": ["base64..."], "model": "dall-e-3" }
```

---

## Watch

### `GET /api/watch/status`

Returns file-watcher status.

**Response**
```json
{ "active": true, "root_dir": "/Users/me/projects/myapp", "connections": 2 }
```

### `POST /api/watch/start`

Starts the file watcher. Returns `"started"` or `"already_running"`.

### `POST /api/watch/stop`

Stops the file watcher. Returns `"stopped"` or `"not_running"`.

---

## Projects

Manage Apollo projects (`apollo.json` manifests). The bootstrap wizard
and the Projects sidebar are built on these endpoints.

### `POST /api/projects/open`

Open or switch to a project directory.

**Request**
```json
{ "path": "/Users/me/projects/myapp" }
```

**Response** — project info; the `needs_bootstrap` flag tells the UI
whether to show the wizard. Refuses `400` when opening a directory
nested inside another initialized project.

### `POST /api/projects/init`

Initialize a project with custom inclusion / exclusion filters. Triggered
when the bootstrap wizard submits its filter selections.

**Request**
```json
{ "path": "/Users/me/projects/myapp", "filters": { "...": "..." } }
```

### `PUT /api/projects/filters`

Update the active project's filters.

**Request**
```json
{ "filters": { "include_dirs": ["src"], "exclude_dirs": ["dist"] } }
```

### `POST /api/projects/reprocess`

Re-index the current project.

**Request**
```json
{ "mode": "incremental" }
```

| Field  | Type   | Default        | Description                             |
|--------|--------|----------------|-----------------------------------------|
| `mode` | string | `incremental`  | `incremental` re-parses changed files; `full` rebuilds from scratch |

### `POST /api/projects/leave`

Remove the current project. Deletes `_apollo/` and `_apollo_web/`.

**Request**
```json
{ "confirm": true }
```

**Response**
```json
{ "status": "removed", "deleted": ["_apollo/", "_apollo_web/"] }
```

### `GET /api/projects/current`

Returns project info for the currently open project, or `null` when no
project is open.

### `GET /api/projects/tree`

Folder tree of the current project, used by the bootstrap wizard.

| Param   | Type | Default | Description                |
|---------|------|---------|----------------------------|
| `depth` | int  | `3`     | Maximum recursion depth    |

---

## Local AI Tools

These endpoints mirror the AI agent's extended tool catalog (see
`PLAN_MORE_LOCAL_AI_FUNCTIONS`). Each one is a single-call replacement for
a multi-grep dance the model would otherwise perform. All read-only.

### `POST /api/nodes/batch`

Get up to 20 node payloads in one call. Replaces sequential `GET /api/node/{id}`.

**Request Body**

| Field            | Type      | Required | Description                          |
|------------------|-----------|----------|--------------------------------------|
| `ids`            | string[]  | yes      | Up to 20 node IDs                    |
| `include_source` | boolean   | no       | Default `true`                       |
| `include_edges`  | boolean   | no       | Default `true`                       |

Returns `{ nodes[], missing[], requested }`. Unknown IDs land in `missing[]`.

### `POST /api/files/sections`

Read up to 10 line ranges across files in one call. Per-range cap is 400
lines. Errors on individual ranges are inlined without failing the batch.

**Request Body**

```json
{ "ranges": [ { "path": "x.py", "start": 1, "end": 40 } ] }
```

### `GET /api/stats/detailed`

Deeper aggregation over the existing graph — group counts, top-N largest
files, top-N most-connected nodes.

| Param   | Type   | Default | Description                                  |
|---------|--------|---------|----------------------------------------------|
| `top_n` | int    | 20      | 1..50                                        |
| `group` | string | dir     | One of `dir`, `lang`, `ext`                  |

### `GET /api/paths`

Find paths between two nodes via `nx.all_simple_paths` (or
`shortest_path` when `shortest_only=true`) over an undirected,
edge-type-filtered view.

| Param           | Type    | Default | Description                                  |
|-----------------|---------|---------|----------------------------------------------|
| `start`         | string  | —       | Start node ID                                |
| `end`           | string  | —       | End node ID                                  |
| `max_length`    | int     | 5       | 1..8                                         |
| `max_paths`     | int     | 5       | 1..20                                        |
| `edge_types`    | csv     | —       | e.g. `calls,imports`                         |
| `shortest_only` | boolean | false   | Return only the shortest path                |

### `POST /api/subgraph`

Subgraph induced by N seeds plus depth-K neighbours.

```json
{ "seed_node_ids": ["func::main.py::main"], "depth": 1, "max_nodes": 200 }
```

### `GET /api/inheritance/{class_id}`

Full ancestor chain + descendants for a class node.

| Param              | Type    | Default | Description                              |
|--------------------|---------|---------|------------------------------------------|
| `include_methods`  | boolean | false   | Roll up `defines→method` edges per class |

### `GET /api/imports/{file_id}`

Transitive import set in either direction.

| Param       | Type   | Default | Description                                      |
|-------------|--------|---------|--------------------------------------------------|
| `direction` | string | in      | `in`, `out`, or `both`                           |
| `max_depth` | int    | 5       | 1..10                                            |

### `GET /api/metrics`

Top-N most complex / largest functions project-wide.

| Param     | Type   | Default     | Description                              |
|-----------|--------|-------------|------------------------------------------|
| `top_n`   | int    | 20          | 1..100                                   |
| `sort_by` | string | complexity  | `complexity`, `loc`, or `param_count`    |

### `POST /api/signature/search`

Find functions whose parameter list matches a pattern. Cannot be
answered correctly by grep — the indexer's resolved param list is the
only ground truth.

```json
{ "param_names": ["user_id", "amount"], "fuzzy": false, "top": 20 }
```

Either `param_names`, `param_annotations`, or `signature_hash` must be
supplied.

### `GET /api/tests/{node_id}`

Probable tests covering a function/class node — explicit `tests` edges
first, then heuristic matches (`test_<name>`, `Test<Name>`).

| Param               | Type    | Default | Description                  |
|---------------------|---------|---------|------------------------------|
| `include_heuristic` | boolean | true    | Include name-pattern matches |

### `GET /api/entry-points`

Probable entry points: `__main__` markers, FastAPI/Flask/Django routes,
Click/Typer CLI commands, well-known basenames.

| Param   | Type | Description                                                |
|---------|------|------------------------------------------------------------|
| `kinds` | csv  | Optional filter (`cli`, `http_route`, `main`, …)           |

### `GET /api/git/blame`

`git log` + `git blame -L` for a file (or function/line range). Returns
`{ git_available: false }` cleanly on non-git roots and on missing
`git` binaries — never raises.

| Param        | Type   | Default | Description                                           |
|--------------|--------|---------|-------------------------------------------------------|
| `path`       | string | —       | Project-relative file path                            |
| `name`       | string | —       | Optional function/class name → resolved to a range    |
| `line_start` | int    | —       | Start line for blame (inclusive)                      |
| `line_end`   | int    | —       | End line for blame (inclusive)                        |
| `limit`      | int    | 10      | 1..30 — max recent commits returned                   |

---

## Realtime

### `WS /ws`

WebSocket channel for live graph updates. Clients open a WebSocket
connection at `/ws` and receive JSON frames pushed from the server when
the file watcher detects changes (`graph_update` events). No client
messages are required; received text frames are read but ignored.

---

## Error Handling

All error responses share a consistent JSON shape:

```json
{
  "status_code": 400,
  "error": "Bad Request",
  "detail": "Not a directory: /foo"
}
```

| Code | Meaning             |
|------|---------------------|
| 400  | Bad input           |
| 404  | Resource not found  |
| 422  | Validation error    |
| 500  | Internal server error |
| 501  | Not supported       |
| 503  | Service unavailable |
