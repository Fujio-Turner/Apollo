# Apollo — Implementation Plan

## Completed Steps

### Step 1 — Python AST Parser ✅
Built `apollo/parser/` — extracts functions, classes, imports, variables, and call sites from Python files using the `ast` module.

### Step 2 — Graph Builder ✅
Built `apollo/graph/builder.py` — walks a directory tree, creates nodes (`dir`, `file`, `func`, `class`, `method`, `import`, `var`) and edges (`contains`, `defines`, `calls`, `imports`, `inherits`). Includes cross-file symbol resolution via a global symbol table.

### Step 3 — Storage Backends ✅
Built `apollo/storage/` — `GraphStore` protocol with two backends:
- `json_store.py` — JSON file persistence
- `cblite/` — Couchbase Lite backend with SQL++ queries

### Step 4 — Embedding & Semantic Search ✅
Built `apollo/embeddings/` and `apollo/search/` — generates embeddings for node source text, supports cosine-similarity semantic search and CBL-native vector search.

### Step 5 — Couchbase Lite Backend ✅
Set up `libcblite` Python bindings, migrated graph + vector storage to Couchbase Lite, SQL++ queries for combined structural + semantic search.

---

### Step 6 — Spatial Coordinates (3DJSON Integration) ✅
Built `apollo/spatial.py` — `SpatialMapper` class computes (x, y, z) coordinates and face assignments for all graph nodes. X-axis uses PCA/UMAP dimensionality reduction on embeddings (conceptual domain), Y-axis uses BFS depth from entry points (structural depth), Z-axis uses PageRank/degree centrality (importance). Built `apollo/search/spatial.py` — `SpatialSearch` class with range queries, face queries, near-node search, spatial walk (concentric ring expansion), and combined spatial+structural queries. CLI commands: `spatial` (--near, --at, --face) and `spatial-walk`. Integrated into indexing pipeline.

---

## Step 6 — Spatial Coordinates (3DJSON Integration) — Design Notes

### Concept

Inspired by [househippo/3DJSON](https://github.com/househippo/3DJSON) — a project that gives JSON data spatial dimensionality by embedding `(X,Y)` degree coordinates into key names, turning flat data into a queryable 3D surface.

We adapt this idea: every node in the knowledge graph gets a **computed spatial coordinate** `(x, y, z)` that encodes its *conceptual position* in the codebase. This enables a third search mode — **spatial search** — alongside structural (graph traversal) and semantic (vector similarity).

Instead of 3DJSON's convention of encoding coordinates in key names (`"90,0~Products"`), we store coordinates as first-class fields in each node document. The *philosophy* is the same — data has a *place*, and nearby data is related — but the implementation fits our Couchbase Lite / NetworkX storage model.

### Why

- **Structural search** finds connections by following edges — great for "who calls X?" but limited to known relationships.
- **Semantic search** finds similarity by embedding distance — great for "code like X" but operates in high-dimensional space humans can't visualize.
- **Spatial search** maps nodes to a low-dimensional human-interpretable coordinate space. You can *see* where code lives, query by *region*, and discover clusters that share spatial proximity. It bridges the gap: more intuitive than 384-dim embeddings, more flexible than edge traversal.

### Node Schema (updated)

Current node document in Couchbase Lite:
```json
{
  "_id": "func::src/mailer.py::emails",
  "type": "function",
  "name": "emails",
  "path": "src/mailer.py",
  "line_start": 10,
  "line_end": 25,
  "source": "def emails(): ...",
  "embedding": [0.12, -0.34, ...]
}
```

Updated with spatial coordinates:
```json
{
  "_id": "func::src/mailer.py::emails",
  "type": "function",
  "name": "emails",
  "path": "src/mailer.py",
  "line_start": 10,
  "line_end": 25,
  "source": "def emails(): ...",
  "embedding": [0.12, -0.34, ...],
  "spatial": {
    "x": 87.5,
    "y": 180.0,
    "z": 0.72,
    "face": 2
  }
}
```

### Coordinate Axes

| Axis | Range | What It Encodes | How It's Computed |
|------|-------|-----------------|-------------------|
| **X** | 0–360° | **Conceptual domain** — which *topic cluster* a node belongs to | Dimensionality reduction (UMAP or t-SNE) on the node's embedding vector → project first component to `[0, 360]` |
| **Y** | 0–360° | **Structural depth** — how deep a node sits in the call/containment hierarchy | BFS distance from root entry points (e.g., `main()`, top-level scripts) mapped to `[0, 360]`. Shallow = low Y, deeply nested = high Y |
| **Z** | 0.0–1.0 | **Importance / centrality** — how connected or central a node is | PageRank or degree centrality (via NetworkX), normalized to `[0, 1]`. Hub functions → 1.0, leaf nodes → 0.0 |

This mirrors 3DJSON's coordinate model:
- **X, Y** are angular (degree) axes, just like 3DJSON's `"X,Y~Key"` prefix
- **Z** is a scalar depth axis, exactly as described in 3DJSON's planned hit list (`z ∈ [0, 1]` for depth within a face)

### Face Mapping

3DJSON groups data onto 6 faces of a cube. We adapt this to architectural layers:

| Face | Coordinate | Architectural Role | Node Filter |
|------|------------|--------------------|-------------|
| 1 (Front) | `0,0` | **Entry points** — CLI commands, `main()`, API endpoints | Nodes with 0 incoming `calls` edges |
| 2 (Left) | `90,0` | **Business logic** — core domain functions and classes | Nodes with both incoming and outgoing `calls` edges |
| 3 (Back) | `180,0` | **Data access** — storage, DB, file I/O | Nodes in storage/, db/, or data-related modules |
| 4 (Right) | `270,0` | **Utilities** — helpers, formatters, pure functions | Nodes with high out-degree but low in-degree from diverse callers |
| 5 (Top) | `0,90` | **Config & constants** — settings, env vars, top-level assignments | `variable` type nodes, config modules |
| 6 (Bottom) | `0,270` | **Tests** — test files and fixtures | Nodes in `test_*` or `*_test.py` files |

**Negative faces (inside the cube)** — private/internal symbols:

| Face | Coordinate | Meaning |
|------|------------|---------|
| -1 | `360,0` | Internal entry points (private `_main()` helpers) |
| -2 | `-270,0` | Internal business logic (methods prefixed with `_`) |
| -3 | `-180,0` | Internal data access |
| -4 | `-90,0` | Internal utilities |

Convention: nodes whose name starts with `_` (Python private) get negative-face coordinates.

### Computing Spatial Coordinates

New module: `graph_search/spatial.py`

```
SpatialMapper
├── compute_x(graph, embedder) → dict[node_id, float]
│   ├── Collect all node embeddings
│   ├── Run UMAP (n_components=1) to reduce to 1D
│   └── Scale to [0, 360]
│
├── compute_y(graph) → dict[node_id, float]
│   ├── Find entry-point nodes (0 incoming calls edges)
│   ├── BFS from entry points, record depth per node
│   └── Scale max_depth to [0, 360]
│
├── compute_z(graph) → dict[node_id, float]
│   ├── Run nx.pagerank(graph) or nx.degree_centrality(graph)
│   └── Normalize to [0, 1]
│
├── assign_face(node_data, x, y) → int
│   ├── Classify by node type, file path, edge pattern
│   └── Return face number (1–6 or negative for private)
│
└── compute_all(graph, embedder) → dict[node_id, SpatialCoord]
    ├── Calls compute_x, compute_y, compute_z, assign_face
    └── Returns {"x": float, "y": float, "z": float, "face": int}
```

Integrated into the indexing pipeline:
1. `GraphBuilder.build()` creates the graph (existing)
2. `Embedder.embed_graph()` generates embeddings (existing)
3. **`SpatialMapper.compute_all()`** computes coordinates (new)
4. `store.save()` persists everything (existing — schema extended)

### Spatial Queries

#### 6.1 Range Query (3DJSON's `threeDRange`)

The core spatial primitive — find all nodes within ±N degrees of a center point:

**CLI:**
```
python main.py spatial --near "func::src/mailer.py::emails" --range 30
python main.py spatial --at 90,180 --range 45 --top 20
```

**JSON DSL:**
```json
{
  "spatial": {
    "center": [90, 180],
    "range": 30,
    "z_min": 0.5
  }
}
```

**SQL++ (Couchbase Lite):**
```sql
SELECT *
FROM nodes
WHERE spatial.x BETWEEN ($cx - $range) AND ($cx + $range)
  AND spatial.y BETWEEN ($cy - $range) AND ($cy + $range)
  AND spatial.z >= $z_min
ORDER BY ABS(spatial.x - $cx) + ABS(spatial.y - $cy) ASC
LIMIT $top
```

This is the same axis-aligned bounding box (AABB) query that 3DJSON's `threeDRange()` performs — but backed by Couchbase Lite indexes instead of iterating over all keys.

#### 6.2 Face Query (3DJSON's `threeDGet`)

Get all nodes on a specific architectural face:

**CLI:**
```
python main.py spatial --face 1          # all entry points
python main.py spatial --face 6          # all tests
python main.py spatial --face -2         # private business logic
```

**SQL++:**
```sql
SELECT * FROM nodes WHERE spatial.face = $face
```

#### 6.3 Spatial + Structural (combined)

Find spatially nearby nodes, then expand via graph traversal:

**JSON DSL:**
```json
{
  "spatial": {
    "center": [90, 180],
    "range": 30
  },
  "traverse": {
    "direction": "in",
    "edge": "calls",
    "depth": 2
  }
}
```

This answers questions like: "find code conceptually near the email module, then show me who calls it."

#### 6.4 Spatial + Semantic (combined)

Use spatial coordinates to pre-filter before running expensive vector similarity:

**JSON DSL:**
```json
{
  "spatial": {
    "face": 2,
    "z_min": 0.3
  },
  "search": {
    "text": "send notification",
    "top": 10
  }
}
```

This answers: "search for 'send notification' but only among important business logic nodes."

### Traversal: Spatial Walk

A new traversal method that moves through coordinate space instead of along edges:

**Concept:** Start at a node's `(x, y)` position. Expand outward in concentric rings (increasing range). At each ring, collect nodes and optionally follow their graph edges.

```
SpatialWalk(start_node, step=15, max_rings=5)

Ring 0: range=0   → the start node itself
Ring 1: range=15  → nodes within ±15° of start
Ring 2: range=30  → nodes within ±30° of start
Ring 3: range=45  → nodes within ±45° of start
...
```

**Use case:** "Show me what code lives near `emails()` and progressively expand outward" — like zooming out on a map. Different from BFS (which follows edges regardless of conceptual proximity).

**CLI:**
```
python main.py spatial-walk "func::src/mailer.py::emails" --step 15 --rings 4
```

### Couchbase Lite Indexes

Add to the CBL storage backend:

```python
# Value indexes for spatial range queries
collection.create_index("idx_spatial_x",
    ValueIndexConfiguration(["spatial.x"]))
collection.create_index("idx_spatial_y",
    ValueIndexConfiguration(["spatial.y"]))
collection.create_index("idx_spatial_face",
    ValueIndexConfiguration(["spatial.face"]))
```

### Web UI Integration

The spatial coordinates feed directly into the ECharts visualization:

- **3D scatter view** — X, Y as angular position on a sphere/cube, Z as node size or color intensity. Rotate to explore different faces.
- **2D projected view** — X as horizontal, Y as vertical. Nodes positioned by conceptual domain (left-right) and structural depth (top-bottom). Node size = Z (importance).
- **Face selector** — click a cube face in the sidebar to filter the graph to that architectural layer.
- **Spatial search bar** — "show me code near (90, 180)" draws a range circle on the 2D projection and highlights matching nodes.

### API Endpoints (additions)

| Endpoint | Method | Input | Output |
|----------|--------|-------|--------|
| `/api/spatial` | POST | `{ "center": [x,y], "range": n, "z_min": z }` | Nodes within spatial range |
| `/api/spatial/face/:id` | GET | — | All nodes on a face |
| `/api/spatial/walk` | POST | `{ "start": node_id, "step": n, "rings": n }` | Concentric ring results |

---

### Step 7 — Tree-sitter Multi-Language Parser ✅
Built `graph_search/parser/treesitter_parser.py` — `TreeSitterParser` backend using `py-tree-sitter` (v0.23+). Supports Python, JavaScript/JSX, TypeScript/TSX, Go, and Rust via pip-installable grammar packages. Extracts functions, classes, imports, variables, and call sites from all languages into a unified schema. Abstract `BaseParser` interface (`graph_search/parser/base.py`) makes parser backends swappable. `GraphBuilder` accepts a list of parsers and auto-selects the right one per file extension. Incremental parsing via `build_incremental()` — uses SHA-256 content hashing to skip unchanged files. CLI flags: `--parser {auto,ast,tree-sitter}` and `--incremental`.

### Step 8 — File Watcher & Live Updates ✅
Built `graph_search/watcher.py` — `FileWatcher` class using `watchdog` to monitor an indexed directory for file changes (create/modify/delete/move). Debounces rapid events (1 second) before triggering incremental re-index. Only re-parses changed files, regenerates embeddings for affected nodes, and recomputes spatial coordinates. WebSocket endpoint (`/ws`) in the FastAPI server pushes `graph_update` events to connected browsers with lists of updated/removed node IDs. CLI: `python main.py watch <dir>` for standalone watching, `python main.py serve --watch-dir <dir>` for integrated web+watch mode. API endpoints: `/api/watch/start`, `/api/watch/stop`, `/api/watch/status`.

### Step 9 — Non-Code File Support ✅
Built `graph_search/parser/text_parser.py` — `TextFileParser` backend that indexes Markdown, JSON, YAML, CSV, TOML, and plain text files. Extracts full-text content (with JSON flattening and CSV row-to-text conversion) into `document` type nodes with `source` field for embedding. `GraphBuilder` extended with `documents` handling — creates `doc::<path>` nodes linked to their parent file via `defines` edges. `TextFileParser` always included in the parser list so non-code files are indexed alongside code. File watcher picks up non-code changes automatically via the unified `_SOURCE_EXTENSIONS` set. Spatial coordinates and semantic search work unchanged — document nodes get embeddings from their content just like code nodes.
