# Graph Search — API Reference

Quick reference for the Graph Search REST API.
For the full machine-readable spec see [`openapi.yaml`](openapi.yaml).
FastAPI also serves interactive docs at [`/docs`](http://localhost:8000/docs) (Swagger UI) and [`/redoc`](http://localhost:8000/redoc).

---

## System

### `GET /api/env`

Returns runtime environment capabilities.

**Response**
```json
{ "native_picker": true }
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

Returns term frequencies for a word-cloud visualization.

| Param  | Type   | Description              |
|--------|--------|--------------------------|
| `path` | string | Filter to a path prefix  |

**Response**
```json
[{ "name": "parse", "value": 42 }, { "name": "render", "value": 17 }]
```

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

### `POST /api/project/search`

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

### `POST /api/file/search`

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

## Settings

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
