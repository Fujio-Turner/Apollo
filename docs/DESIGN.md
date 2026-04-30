# Graph Search — Design Document

  
[`guides/SCHEMA_DESIGN.md`](../guides/SCHEMA_DESIGN.md) — Datbase Schema design rules and conventions  
[`guides/STYLE_HTML_CSS.md`](../guides/STYLE_HTML_CSS.md) — HTML/CSS styling standards  
[`guides/API_OPENAPI.md`](../guides/API_OPENAPI.md) — API & OpenAPI maintenance guide  
[`docs/API.md`](API.md) — REST API quick reference  
[`docs/openapi.yaml`](openapi.yaml) — OpenAPI 3.1 specification (machine-readable)  


---

## 1. Vision

An **Obsidian-for-your-filesystem** — a browser-based tool that scans any directory of files and folders, builds a **knowledge graph** of the content and its relationships, and lets you visually explore, search, and ask questions about your world of files.

Inspired by [Obsidian.md](https://obsidian.md/)'s graph view and linking philosophy, but instead of manually linking notes, Graph Search **automatically discovers** connections between files — function calls, imports, shared topics, similar content — and renders them as an interactive, explorable graph.

A chat window powered by the **Grok AI API** lets you ask natural-language questions. The system searches the local graph for relevant files and topics, sends the context to Grok, and returns intelligent answers grounded in *your* actual files — not generic knowledge.

### What it feels like

- Open the app → see your entire project as a living graph of connected nodes.
- Click a node → see the source, its relationships, who calls it, what it depends on.
- Click a word cloud term → the graph filters to show only related content.
- Type a question in the chat → the AI reads the relevant files, understands the graph context, and answers with specific references to your code/notes/docs.
- Discover connections you didn't know existed.

---

## 2. Core Concepts

### 2.1 Code Knowledge Graph

The graph has two primitives:

- **Nodes** — represent code entities:
  | Node Type   | Description                          | Example                    |
  |-------------|--------------------------------------|----------------------------|
  | `file`      | A source file                        | `src/utils/mailer.py`      |
  | `directory` | A folder in the project tree         | `src/utils/`               |
  | `function`  | A function or method definition      | `emails()`                 |
  | `class`     | A class definition                   | `class MailService`        |
  | `variable`  | A module-level or class-level var    | `SMTP_HOST`                |
  | `import`    | An import statement                  | `import smtplib`           |

- **Edges** — represent relationships:
  | Edge Type    | Meaning                              | Example                            |
  |--------------|--------------------------------------|-------------------------------------|
  | `defines`    | A file/class defines an entity       | `mailer.py --defines--> emails()`   |
  | `calls`      | A function calls another function    | `send_report() --calls--> emails()` |
  | `imports`    | A file imports a module/symbol       | `app.py --imports--> mailer`        |
  | `references` | A function reads/writes a variable   | `emails() --references--> SMTP_HOST`|
  | `contains`   | A directory contains a file/dir      | `src/ --contains--> utils/`         |
  | `inherits`   | A class inherits from another        | `GmailService --inherits--> MailService` |

Each node carries a **payload**: the raw source text of that entity, its location (file, line range), language, and an optional vector embedding.

### 2.2 Two Search Modes

| Mode         | Input                              | Mechanism                | Output                          |
|--------------|------------------------------------|--------------------------|---------------------------------|
| **Structural** | Symbol name or pattern            | Graph traversal (BFS/DFS) | All nodes connected by edges   |
| **Semantic**   | Natural language or code snippet  | Vector similarity search  | Ranked list of similar nodes   |

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        CLI (main.py)                    │
│   Commands: index, search, query, inspect, status       │
└────────────┬────────────────────────┬───────────────────┘
             │                        │
     ┌───────▼────────┐      ┌────────▼────────┐
     │  Parser Layer   │      │  Query Engine    │
     │  (AST /         │      │  (structural +   │
     │   Tree-sitter)  │      │   semantic)      │
     └───────┬────────┘      └────────┬────────┘
             │                        │
     ┌───────▼────────────────────────▼───────┐
     │            Graph Builder                │
     │  (constructs nodes + edges from parse   │
     │   results, generates embeddings)        │
     └───────────────┬────────────────────────┘
                     │
     ┌───────────────▼────────────────────────┐
     │          Storage Backend                │
     │  ┌─────────────┐  ┌─────────────────┐  │
     │  │  Graph Store │  │  Vector Index   │  │
     │  │  (adjacency  │  │  (embeddings +  │  │
     │  │   + payloads)│  │   ANN search)   │  │
     │  └─────────────┘  └─────────────────┘  │
     └────────────────────────────────────────┘
```

---

## 4. Component Breakdown

### 4.1 Parser Layer

Responsible for reading source files and extracting a structured representation of code entities.

**Option A — Python `ast` module**
- Built-in, zero dependencies.
- Handles Python only.
- Extracts functions, classes, imports, assignments, call sites.
- Well-documented, stable API.

**Option B — Tree-sitter (via `py-tree-sitter`)**
- Supports 100+ languages with grammar plugins.
- Incremental parsing (fast re-index on file changes).
- Produces a concrete syntax tree (CST) — more detail than an AST.
- Requires compiling/downloading grammar `.so` files per language.

**Recommendation**: Start with `ast` for Python-only support. Add Tree-sitter as a second parser backend when multi-language support is needed. The parser interface should be abstract so backends are swappable.

#### 4.1.1 Plugin Architecture (implemented)

Language support is now organized as a **drop-in plugin system**:

```
parser/                       # Generic plumbing
├── base.py                   #   BaseParser ABC
├── text_parser.py            #   Generic TextFileParser fallback
└── treesitter_parser.py      #   Tree-sitter backend (multi-language)

plugins/                      # One folder per language/format
├── __init__.py               #   discover_plugins() — auto-discovery
├── python3/                  #   Built-in: Python 3 (AST)
│   ├── __init__.py           #     exports PLUGIN
│   └── parser.py             #     PythonParser implementation
└── markdown_gfm/             #   Built-in: GitHub Flavored Markdown
    ├── __init__.py
    └── parser.py
```

**Contract** — each plugin exposes a `PLUGIN` attribute pointing to a
`BaseParser` subclass. `plugins.discover_plugins()` walks the package
and instantiates everything it finds, in alphabetical order.

**Why subpackages, not single files** — a plugin can grow to need
helpers, vendored support code, sample data, or a third-party library
loaded lazily. Putting everything in `plugins/<name>/` keeps each
plugin self-contained: removing a language is one
`rm -rf plugins/<name>/` away, and one plugin's deps can't break
another's.

**Backward compatibility** — the old import paths
`from apollo.parser import PythonParser, MarkdownParser` still work via
re-exports in `parser/__init__.py`, so the rest of the codebase didn't
need to change when the move happened.

**Adding a new language** — see [`guides/making_plugins.md`](../guides/making_plugins.md).
The guide covers the folder layout, the result-dict shape, naming
conventions (`go1/`, `java17/`, `markdown_common/`, `pdf_pypdf/`, …),
how to handle third-party deps with lazy imports + per-plugin
`requirements.txt`, and a complete worked example.

### 4.2 Graph Builder

Takes parser output and constructs the graph:

1. Walk the directory tree → create `directory` and `file` nodes with `contains` edges.
2. For each file, run the parser → create `function`, `class`, `variable`, `import` nodes with `defines` edges.
3. Resolve call sites → create `calls` edges (intra-file first, then cross-file via import resolution).
4. Resolve inheritance → create `inherits` edges.
5. Optionally generate vector embeddings for each node's source text.

Cross-file resolution is the hardest part. Strategy:
- Build a global symbol table: `{qualified_name: node_id}`.
- When a call like `mailer.emails()` is found, look up `mailer` in imports → resolve to `src/utils/mailer.py` → find `emails` in that file's symbols.

### 4.3 Storage Backend

#### 4.3.1 Graph Store

Stores nodes, edges, and node payloads (source text, file path, line range, metadata).

| Option               | Pros                                          | Cons                                        |
|----------------------|-----------------------------------------------|---------------------------------------------|
| **NetworkX**         | Pure Python, rich traversal API, easy to start | In-memory only, no persistence (needs pickling or JSON export) |
| **SQLite**           | Built-in, persistent, fast, SQL queries        | Graph traversal requires recursive CTEs     |
| **Couchbase Lite**   | Persistent, JSON document model, SQL++ queries, built-in vector index | C binding complexity, less Python ecosystem support |

#### 4.3.2 Vector Index

Stores embeddings and supports approximate nearest-neighbor (ANN) search.

| Option                          | Pros                                          | Cons                                        |
|---------------------------------|-----------------------------------------------|---------------------------------------------|
| **Couchbase Lite Vector Index** | Integrated with document store, single storage layer, SQL++ queries combine structural + vector search | Experimental Python bindings, must wrap C API via ctypes/cffi, limited community examples |
| **FAISS (Facebook)**            | Battle-tested, very fast ANN, supports GPU     | Separate from graph store, no persistence without manual save/load, C++ core |
| **Hnswlib**                     | Fast, lightweight, easy Python API             | Separate from graph store, limited filtering |
| **SQLite + sqlite-vss**         | Single-file persistence, combines with SQL graph store | Extension must be compiled, less mature than FAISS |
| **ChromaDB**                    | Simple Python API, built-in persistence        | Another server/process, heavier dependency  |
| **Roll our own (brute-force cosine)** | Zero dependencies, full control, easy to understand | Slow at scale (O(n) per query), no ANN optimizations |

### 4.4 Embedding Generation

Each node's source text is converted to a fixed-length vector.

| Option                              | Dims | Speed       | Quality  | Cost     |
|-------------------------------------|------|-------------|----------|----------|
| `all-MiniLM-L6-v2` (sentence-transformers) | 384  | Fast (local) | Good     | Free     |
| `codeBERT` / `codebert-base`        | 768  | Medium      | Better for code | Free |
| OpenAI `text-embedding-3-small`     | 1536 | API latency | Best     | ~$0.02/1M tokens |

**Recommendation**: Default to a local model (`all-MiniLM-L6-v2` or `codeBERT`) for privacy and zero cost. Allow an optional flag to use OpenAI for higher quality.

### 4.5 Query Engine

#### Structural Queries

Find entities by name or pattern and traverse the graph.

Example queries and what they do:

```
# Find where emails() is defined
> query "emails" --type function

# Find all callers of emails()
> query "emails" --callers

# Find the full call chain (transitive)
> query "emails" --callers --depth 5

# Find all functions in a file
> query "src/utils/mailer.py" --type function

# Find what a function depends on (callees, imports, references)
> query "send_report" --dependencies
```

Implementation: BFS/DFS from the matched node, following edges of the requested type, up to the specified depth.

#### Semantic Search

Find code similar to a query string by vector distance.

```
# Find code related to "sending email notifications"
> search "sending email notifications" --top 10

# Find code similar to a specific function
> search --like "src/utils/mailer.py:emails" --top 5
```

Implementation: Embed the query → ANN search in the vector index → return top-k nodes with similarity scores.

#### Combined Queries

The most powerful mode — vector search to find candidates, then graph traversal to expand context:

```
# Find email-related code and show their callers
> search "email" --top 5 --expand callers
```

---

## 5. Couchbase Lite Deep Dive — Pros & Cons

### Why Consider Couchbase Lite

Couchbase Lite is an embedded NoSQL document database with a C core (`libcblite`) and experimental Python bindings. Recent versions added **vector search** support, making it a potential single-storage solution for both the graph and vector index.

### Architecture with Couchbase Lite

```
┌──────────────────────────────────────────────┐
│              Couchbase Lite                  │
│                                              │
│  Collection: "nodes"                         │
│  ┌─────────────────────────────────────────┐ │
│  │ {                                       │ │
│  │   "_id": "func::src/mailer.py::emails", │ │
│  │   "type": "function",                   │ │
│  │   "name": "emails",                     │ │
│  │   "file": "src/mailer.py",              │ │
│  │   "line_start": 10,                     │ │
│  │   "line_end": 25,                       │ │
│  │   "source": "def emails(): ...",        │ │
│  │   "embedding": [0.12, -0.34, ...],      │ │
│  │   "edges_out": [                        │ │
│  │     {"type": "calls", "target": "..."}  │ │
│  │   ]                                     │ │
│  │ }                                       │ │
│  └─────────────────────────────────────────┘ │
│                                              │
│  Vector Index: on "embedding" field          │
│  Value Index: on "type", "name", "file"      │
│                                              │
│  Query via SQL++:                            │
│    SELECT * FROM nodes                       │
│    WHERE type = "function"                   │
│    ORDER BY APPROX_VECTOR_DISTANCE(          │
│      embedding, $query_vec)                  │
│    LIMIT 10                                  │
│                                              │
└──────────────────────────────────────────────┘
```

### Pros

1. **Single storage layer** — graph data + vector embeddings in one database, one file on disk.
2. **SQL++ queries** — powerful query language that can combine structural filters with vector search in one query (e.g., "find functions similar to X that are in directory Y").
3. **Persistence** — automatic, durable, no manual save/load.
4. **Sync capability** — Couchbase Lite can sync to Couchbase Server if you ever want a shared/team knowledge graph.
5. **Offline-first** — fully embedded, no server process needed.
6. **JSON document model** — natural fit for storing node payloads with varying schemas.

### Cons

1. **C binding complexity** — The Python bindings (`cblite` Python package) are experimental. You may need to use `ctypes` or `cffi` to wrap `libcblite` directly, which means managing memory, error handling, and platform-specific shared libraries.
2. **Graph traversal** — Couchbase Lite is a document store, not a graph database. Multi-hop traversals (e.g., "find all transitive callers of X") require multiple queries or application-side recursion. No native `MATCH` or `TRAVERSE` syntax.
3. **Limited community** — Fewer Python examples and Stack Overflow answers compared to SQLite or FAISS.
4. **Build/distribution** — `libcblite` must be compiled or distributed as a platform-specific binary. This complicates installation (`pip install` won't just work out of the box).
5. **Vector index maturity** — Vector search in Couchbase Lite is newer and less battle-tested than FAISS or Hnswlib for ANN workloads.

### Verdict

Couchbase Lite is a strong choice **if** you value the single-storage-layer simplicity and are comfortable with the C binding work. For a faster start, **SQLite (graph) + FAISS or Hnswlib (vectors)** gives you proven, well-documented components at the cost of managing two storage systems.

**Hybrid approach**: Use Couchbase Lite for document storage and simple vector queries, but keep an in-memory NetworkX graph for multi-hop traversals. Load the graph from Couchbase Lite on startup.

---

## 6. Query Language Options

How should users express structural queries?

| Option                     | Example                                          | Pros                                  | Cons                                |
|----------------------------|--------------------------------------------------|---------------------------------------|-------------------------------------|
| **CLI flags**              | `query emails --callers --depth 3`               | Simple, discoverable, tab-completable | Limited expressiveness              |
| **Dot-path syntax**        | `query "mailer.emails.callers[depth=3]"`         | Compact, familiar                     | Custom parser needed                |
| **JSON query DSL**         | See examples below                               | Machine-friendly, composable, easy to serialize/store/replay | More verbose for simple queries |
| **Cypher-like (Neo4j)**    | `MATCH (f)-[:CALLS]->(e {name:"emails"}) RETURN f` | Expressive, well-known for graphs   | Heavy to implement, overkill for CLI |
| **SQL++ (Couchbase)**      | `SELECT * FROM nodes WHERE name = "emails"`      | Native if using CBL, powerful         | Not graph-aware                     |
| **Python API only**        | `g.find("emails").callers(depth=3)`              | Flexible, no parser needed            | Not usable from CLI                 |

### JSON Query DSL — Examples

A MongoDB/Elasticsearch-inspired format where queries are JSON objects. Easy to parse (`json.loads`), easy to build programmatically, and easy to save/share as files.

**Find where `emails()` is defined:**
```json
{
  "find": { "name": "emails", "type": "function" }
}
```

**Find all callers of `emails()`:**
```json
{
  "find": { "name": "emails", "type": "function" },
  "traverse": { "direction": "in", "edge": "calls", "depth": 1 }
}
```

**Transitive call chain (who calls the callers?):**
```json
{
  "find": { "name": "emails", "type": "function" },
  "traverse": { "direction": "in", "edge": "calls", "depth": 5 }
}
```

**Semantic search:**
```json
{
  "search": { "text": "sending email notifications", "top": 10 }
}
```

**Combined — semantic search + graph expansion:**
```json
{
  "search": { "text": "email handling", "top": 5 },
  "traverse": { "direction": "in", "edge": "calls", "depth": 2 }
}
```

**Filter by file path:**
```json
{
  "find": { "type": "function", "file": { "$glob": "src/utils/**" } },
  "traverse": { "direction": "out", "edge": "references" }
}
```

**Advantages of the JSON DSL:**
- Trivial to parse in Python — just `json.loads()`, no custom grammar.
- Queries can be saved to `.json` files and re-run (repeatable, scriptable).
- Easy to build a web UI or API on top — the query *is* the request body.
- Composable — tools can generate queries programmatically.
- Maps directly to Couchbase Lite's SQL++ in Phase 3 (a JSON query can be transpiled to SQL++).

**Recommendation**: Start with **CLI flags** for quick interactive use, backed by a **JSON query DSL** as the internal query representation. The CLI parses flags into a JSON query object, which the query engine executes. This gives you both simplicity at the command line and a powerful composable format for scripting, saved queries, and future API use. The **Python API** (`g.find(...).callers(...)`) becomes a builder that produces JSON query objects internally.

---

## 7. Browser-Based UI

A local web application that lets you visually explore the knowledge graph, filter by file/folder/topic, and drill into code — all in the browser.

### 7.1 Visualization Types

| View                   | Library                   | Purpose                                                   |
|------------------------|---------------------------|-----------------------------------------------------------|
| **Force-directed graph** | ECharts `type: 'graph'`  | Interactive node-link diagram of code relationships (like the [WebKit dep example](https://echarts.apache.org/examples/en/editor.html?c=graph-webkit-dep)). Nodes = code entities, edges = calls/imports/references. Draggable, zoomable, pannable. |
| **Idea Cloud**          | ECharts `echarts-wordcloud` extension | Symbols sized by **graph strength** (sum of in+out degree, aggregated by name) — not raw frequency. Three tiers: *strong* (top 30 hubs), *relevant* (top 100, strength ≥ 2), *all* (full long tail). Click a word to seed an AI question or filter the graph. See §7.6 for the impact-analysis workflow. |
| **Treemap**             | ECharts `type: 'treemap'` | Visualize the directory/file structure sized by number of entities, lines of code, or connection count. |
| **Sunburst**            | ECharts `type: 'sunburst'`| Hierarchical view: directory → file → class → function. Good for understanding project structure at a glance. |

### 7.2 Key UI Features

```
┌──────────────────────────────────────────────────────────────┐
│  ┌──────────────┐  ┌──────────────────────────────────────┐  │
│  │  Sidebar      │  │  Main Canvas                        │  │
│  │               │  │                                      │  │
│  │  📁 File Tree │  │  ┌─────────────────────────────┐    │  │
│  │  ☁️ Word Cloud │  │  │                             │    │  │
│  │               │  │  │   Force-Directed Graph       │    │  │
│  │  Filters:     │  │  │                             │    │  │
│  │  □ functions  │  │  │   ○ emails()                │    │  │
│  │  □ classes    │  │  │   ├── ○ send_report()       │    │  │
│  │  □ imports    │  │  │   └── ○ MailService         │    │  │
│  │               │  │  │                             │    │  │
│  │  Search:      │  │  └─────────────────────────────┘    │  │
│  │  [__________] │  │                                      │  │
│  │               │  │  ┌─────────────────────────────┐    │  │
│  │  Depth: [3]   │  │  │  Source Preview Panel       │    │  │
│  │               │  │  │  def emails():              │    │  │
│  │  Edge types:  │  │  │      smtp = connect()       │    │  │
│  │  □ calls      │  │  │      ...                    │    │  │
│  │  □ imports    │  │  └─────────────────────────────┘    │  │
│  │  □ inherits   │  │                                      │  │
│  └──────────────┘  └──────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

**Interactions:**
- **Click a node** → highlight its edges, show source code in preview panel.
- **Click a word cloud term** → filter the graph to nodes matching that topic/symbol.
- **Toggle edge types** → show/hide call edges, import edges, inheritance edges.
- **Search bar** → structural query (symbol name) or semantic search (natural language). Results highlight in the graph.
- **Filter by directory** → click a folder in the file tree or treemap to scope the graph to that subtree.
- **Hover a node** → tooltip with file path, line number, connection count.
- **Right-click a node** → "Show callers", "Show callees", "Find similar" (semantic search seeded from that node).

### 7.3 Architecture

```
┌────────────────────┐         ┌──────────────────────────┐
│  Python Backend     │  HTTP   │  Browser Frontend        │
│  (Flask / FastAPI)  │◄───────►│  (Single HTML + JS)      │
│                     │  JSON   │                          │
│  /api/graph         │         │  ECharts force graph     │
│  /api/search        │         │  ECharts word cloud      │
│  /api/query         │         │  ECharts treemap         │
│  /api/node/:id      │         │  Source code panel       │
│  /api/wordcloud     │         │  Filter sidebar          │
└────────┬───────────┘         └──────────────────────────┘
         │
         ▼
  ┌──────────────┐
  │ Storage +    │
  │ Query Engine │
  └──────────────┘
```

The backend exposes a small REST API. The frontend is a single-page app (could be a single `index.html` with inline JS — no build step needed).

**API Endpoints:**

| Endpoint             | Method | Input                        | Output                                  |
|----------------------|--------|------------------------------|-----------------------------------------|
| `/api/graph`         | GET    | `?path=src/&depth=2&edges=calls,imports` | `{ nodes: [...], edges: [...], categories: [...] }` — ECharts-ready |
| `/api/search`        | POST   | `{ "text": "email", "top": 10 }` | `{ results: [{ node, score }] }`       |
| `/api/query`         | POST   | JSON query DSL object        | `{ nodes: [...], edges: [...] }`        |
| `/api/node/:id`      | GET    | —                            | `{ source, file, lines, edges, embedding_preview }` |
| `/api/wordcloud`     | GET    | `?path=src/&mode=strong`     | `{ items: [{ name: "GraphQuery", value: 84.0, count: 1 }, ...], total, shown, mode, min_strength }` — `value` = graph strength (in+out degree summed by name) |
| `/api/tree`          | GET    | `?path=/`                    | Nested directory/file tree with counts  |

### 7.4 Idea Cloud — Strength-Weighted Tiers

The Idea Cloud weights every name by **graph strength** — the sum of in+out
degree across every node that shares that display name. This is the
"connectivity" mode of earlier drafts, promoted to the default because it is
the only metric that meaningfully answers *"which symbols actually matter to
this project?"* Raw frequency was too noisy (`__init__`, `name`, `get` always
won), and semantic topic / recent-change modes are tracked separately under
the embeddings and watcher subsystems.

Three render tiers, exposed as both a UI cycle button and an API parameter
(`?mode=`):

| Tier         | Cap | Floor              | Font range | Intent                                                  |
|--------------|-----|--------------------|------------|---------------------------------------------------------|
| **strong**   | 30  | strength ≥ 2       | 18–48 px   | Default. The hub symbols — readable headline.           |
| **relevant** | 100 | strength ≥ 2       | 12–40 px   | "Show More" — broader context, still filtered.          |
| **all**      | 500 | none (singletons OK) | 8–28 px  | Explicit opt-in. Long tail; useful only for completeness.|

Sizing inside ECharts uses `Math.log2(value + 1)` so a few hub nodes don't
collapse the rest into 8 px. A small legend (`showing 30 of 412, strength ≥ 2`)
makes the filtering visible so missing items feel intentional. Tooltips show
the raw `strength` and `count` for each word.

The same tiers are exposed to the AI through the `get_wordcloud` tool with a
`mode` argument; see §7.6.

### 7.5 Tech Choices for Frontend

| Option                          | Pros                                    | Cons                              |
|---------------------------------|-----------------------------------------|-----------------------------------|
| **ECharts (recommended)**       | Rich graph/treemap/wordcloud built-in, great interaction APIs, well-documented, Apache licensed | Larger bundle (~1MB), Chinese-origin docs (English available) |
| **D3.js**                       | Maximum flexibility, industry standard  | Much more code to write, no built-in components |
| **vis.js / vis-network**        | Purpose-built for network graphs        | Less rich for non-graph views (no treemap, word cloud) |
| **Cytoscape.js**                | Excellent graph library, graph-specific layouts | No word cloud or treemap support  |

**Recommendation**: **ECharts** — it covers all four visualization types (force graph, word cloud, treemap, sunburst) in one library with consistent APIs. The WebKit dependency example you found is almost exactly our use case. The `echarts-wordcloud` extension adds word cloud support.

### 7.6 Impact-Analysis Workflow (UI + AI)

The Idea Cloud is more than decoration — it is the **entry point for impact
analysis**. A user (or the AI) typically wants to answer one of two related
questions:

> *"If I change X, what else will break?"*
> *"How central is X to the project, really?"*

The strength-weighted cloud directly ranks symbols by how plausibly the
answer to those questions is "a lot." The full loop combines tree, graph,
cloud, and AI chat into a single discovery → drill-in → assess flow:

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│   1. Discover (Idea Cloud, mode=strong)                          │
│        │  hub names sized by strength                            │
│        ▼                                                         │
│   2. Locate (search_graph / find: badge)                         │
│        │  resolve hub name → concrete node IDs                   │
│        ▼                                                         │
│   3. Drill-in (/api/graph + /api/node/:id, depth 1–2)            │
│        │  enumerate callers, callees, inheritance, imports       │
│        ▼                                                         │
│   4. Assess (AI chat with the above context attached)            │
│           "what relies on this? what would break if I change     │
│            its signature?"                                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### What "strength" actually estimates

For a name `n` aggregating nodes `N₁ … Nₖ`:

```
strength(n) = Σᵢ (in_degree(Nᵢ) + out_degree(Nᵢ))
```

This is a cheap, language-agnostic proxy for *blast radius*: edges in this
graph are imports, calls, inherits, contains, references — every edge
touching a node is one place a change could ripple to or be triggered by.
It deliberately ignores edge **type** weighting (a `calls` edge is treated
the same as an `imports` edge) because over-weighting any one type would
bias the cloud toward whatever language/plugin happens to emit the most of
it. A future enhancement (§14) could add per-edge-type weights once we have
real signal that one matters more than another.

#### How the AI uses it

The `get_wordcloud` tool now mirrors the HTTP endpoint and accepts the same
`mode` parameter (`strong` / `relevant` / `all`). Recommended AI playbook:

1. **`get_wordcloud(mode="strong")`** to pull the project's hub vocabulary.
2. **`search_graph(name)`** for a candidate hub → resolve to node IDs.
3. **`get_neighbors(node_id, depth=1)`** (or `query_callers` / `query_callees`
   when added) to count and inspect direct dependents.
4. Compose an answer that ties the user's proposed change to a concrete
   ranked list of files/functions likely to be impacted, citing the
   `strength` and direct-dependent count as evidence.

Tier guidance for the AI:

| User intent                                       | Recommended `mode` |
|---------------------------------------------------|--------------------|
| "Give me an overview of what this project is about." | `strong`           |
| "What other things depend on `X`?" (X is a likely hub) | `strong` then `search_graph` |
| "Find every place we touch couchbase / SMTP / etc." | `relevant` (broader net) |
| "Audit dead code / orphans / single-use helpers."   | `all` (long tail is the point) |

The AI should default to `strong`. `all` returns up to 500 entries and is
mostly low-signal noise for normal questions — it should be opt-in, just
like the user-facing button.

### 7.7 Updated UI Wireframe (with Chat)

```
┌──────────────────────────────────────────────────────────────────┐
│  ┌──────────────┐  ┌──────────────────────────────────────────┐  │
│  │  Sidebar      │  │  Main Canvas                            │  │
│  │               │  │                                          │  │
│  │  📁 File Tree │  │  ┌─────────────────────────────────┐    │  │
│  │  ☁️ Word Cloud │  │  │                                 │    │  │
│  │               │  │  │    Force-Directed Graph          │    │  │
│  │  Filters:     │  │  │                                 │    │  │
│  │  □ functions  │  │  │    ○ emails()                   │    │  │
│  │  □ classes    │  │  │    ├── ○ send_report()          │    │  │
│  │  □ imports    │  │  │    └── ○ MailService            │    │  │
│  │               │  │  │                                 │    │  │
│  │  Search:      │  │  └─────────────────────────────────┘    │  │
│  │  [__________] │  │                                          │  │
│  │               │  │  ┌─────────────────────────────────┐    │  │
│  │  Depth: [3]   │  │  │  Source Preview Panel            │    │  │
│  │               │  │  │  def emails():                   │    │  │
│  │  Edge types:  │  │  │      smtp = connect()            │    │  │
│  │  □ calls      │  │  │      ...                         │    │  │
│  │  □ imports    │  │  └─────────────────────────────────┘    │  │
│  │  □ inherits   │  │                                          │  │
│  └──────────────┘  └──────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  💬 AI Chat                                          [⌃]  │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │ You: Where is the emails() function used?            │  │  │
│  │  │                                                      │  │  │
│  │  │ Grok: emails() is defined in src/utils/mailer.py:10  │  │  │
│  │  │ and called from 3 locations:                         │  │  │
│  │  │  • src/reports/daily.py:45  (send_report())          │  │  │
│  │  │  • src/alerts/notify.py:22  (trigger_alert())        │  │  │
│  │  │  • tests/test_mailer.py:8   (test_emails())          │  │  │
│  │  │ [nodes highlighted in graph above ☝]                 │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  │  [Ask about your files...                        ] [Send]  │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 8. AI Chat — Grok API Integration

### 8.1 Concept

A chat panel docked at the bottom of the UI. When you ask a question, the system:

1. **Searches the local graph** — vector similarity + structural query to find relevant nodes.
2. **Ranks and selects context** — picks the most applicable files/functions/topics based on the question and current graph view.
3. **Sends to Grok API** — constructs a prompt with the question + relevant file contents + graph context.
4. **Returns a grounded answer** — Grok's response references specific files, line numbers, and graph relationships from *your* codebase.
5. **Highlights in the graph** — referenced nodes light up in the force-directed graph, making the answer visually explorable.

### 8.2 How It Works — RAG Pipeline

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  User asks:  │     │  Local Search     │     │  Context Builder │
│  "Where is   │────►│                   │────►│                  │
│  email logic  │     │  1. Embed query   │     │  3. Rank by      │
│  used?"      │     │  2. Vector search │     │     relevance    │
│              │     │     + graph walk  │     │  4. Read source  │
│              │     │                   │     │     from files   │
└──────────────┘     └──────────────────┘     └───────┬──────────┘
                                                       │
                                              ┌────────▼─────────┐
                                              │  Grok API Call    │
                                              │                  │
                                              │  System prompt:  │
                                              │  "You are a code │
                                              │  assistant. Here │
                                              │  is the graph    │
                                              │  context..."     │
                                              │                  │
                                              │  + file contents │
                                              │  + graph edges   │
                                              │  + user question │
                                              └────────┬─────────┘
                                                       │
                                              ┌────────▼─────────┐
                                              │  Response        │
                                              │  + answer text   │
                                              │  + referenced    │
                                              │    node IDs      │
                                              │  → highlight in  │
                                              │    graph view    │
                                              └──────────────────┘
```

### 8.3 Grok API Details

The xAI API is **OpenAI-compatible**, so we can use the `openai` Python SDK with a different base URL.

**Configuration:**
```python
# Using the openai Python SDK
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["XAI_API_KEY"],
    base_url="https://api.x.ai/v1",
)
```

**Model options:**

| Model                        | Input Cost     | Output Cost    | Use Case                          |
|------------------------------|----------------|----------------|-----------------------------------|
| `grok-4-1-fast-non-reasoning`| $0.20 / 1M tok | $0.50 / 1M tok | Default — fast, cheap, good for Q&A |
| `grok-4-1-fast-reasoning`    | $0.20 / 1M tok | $0.50 / 1M tok | Complex analysis requiring chain-of-thought |
| `grok-4.20-non-reasoning`    | $2.00 / 1M tok | $6.00 / 1M tok | Deep analysis, architecture review |

**Recommendation**: Default to `grok-4-1-fast-non-reasoning` for everyday questions (very cheap at $0.20/1M input). Allow users to switch to the heavier models for deeper analysis via a model selector in the chat UI.

**Context window**: All Grok models support **2M tokens** — large enough to send substantial file context without aggressive truncation.

### 8.4 System Prompt Design

The system prompt tells Grok how to behave as a codebase assistant:

```
You are a code and file exploration assistant. You have access to a knowledge
graph of the user's local files.

When answering:
- Reference specific files and line numbers.
- Explain relationships (who calls what, what imports what).
- If you identify nodes from the graph context, include their IDs so the UI
  can highlight them.
- Be concise but thorough.
- If the graph context doesn't contain enough information to answer, say so.

Graph context for this question:
{graph_context}

Files referenced:
{file_contents}
```

### 8.5 Chat Features

| Feature                  | Description                                                |
|--------------------------|------------------------------------------------------------|
| **Graph-aware answers**  | AI responses include node IDs → clicking highlights them in the graph |
| **Context from view**    | If the user has filtered the graph (e.g., to a directory), the chat automatically scopes its search to that subset |
| **Follow-up questions**  | Conversation history is maintained so you can drill deeper: "What about the MailService class?" |
| **"Ask about this node"**| Right-click a node → "Ask AI" pre-fills the chat with context about that node |
| **Cost indicator**       | Show estimated token count / cost before sending (since it's a paid API) |
| **Streaming responses**  | Use Grok's streaming API for real-time token-by-token display |
| **Offline fallback**     | If no API key is configured, chat is disabled but all local features (graph, search, word cloud) still work |

### 8.6 Example Conversations

**Q: "What does the emails() function do and who uses it?"**
→ System searches graph for `emails` node, finds callers via edge traversal, reads source from disk, sends to Grok.
→ Grok answers with a summary + caller list + highlights 4 nodes in the graph.

**Q: "Are there any files related to authentication that aren't connected to the main app?"**
→ System does semantic search for "authentication", then checks graph connectivity to `app.py`.
→ Grok identifies isolated auth-related files — potential dead code or missing integrations.

**Q: "Summarize what the src/utils/ directory does"**
→ System pulls all nodes under `src/utils/`, reads their source, sends to Grok.
→ Grok provides a high-level summary of each module's purpose.

**Q: "How should I refactor the email handling?"**
→ System gathers all email-related nodes + their dependency graph + source code.
→ Grok suggests refactoring strategies based on the actual code structure.

### 8.7 TOON-Encoded Tool Results

Every tool result returned to the LLM is re-encoded from JSON to **TOON**
(Token-Oriented Object Notation) before being appended to the message
history. TOON is a CSV/YAML hybrid optimised for LLM context: arrays of
uniform objects collapse from `[{id:1,name:"f"},{id:2,name:"g"}]` into a
header-once table:

```
results[2,]{id,name,type,path,line_start}:
  "func::a.py::f",f,function,a.py,1
  "func::a.py::g",g,function,a.py,5
```

In practice we see **30–50% byte reduction** on `search_graph`,
`search_graph_multi`, `get_neighbors`, `get_wordcloud`, `list_notes`,
and similar tabular tools. That directly translates into more tool
rounds fitting under the model's context window before
`rounds_exhausted` kicks in.

The conversion lives in `chat.service._to_toon_for_llm`. It is
defensively wrapped: if `python-toon` is not installed, the JSON
isn't parseable, the encoder raises, *or* the TOON output would be
*larger* than the JSON (rare, happens for very small / heterogeneous
payloads), the original JSON string is passed through unchanged.

The trace panel (§8.8) shows both numbers per tool call:

```
↩ search_graph → 1513 B · 6.9s · toon 921 B (-39.1%)
```

The system prompt tells the model to expect TOON, so it can read the
header line for field names and treat each subsequent row as one
object positionally mapped to those fields.

### 8.8 AI Trace Panel & Step-Event Protocol

Because chat is the primary way users interact with the indexed graph, every
assistant response carries a **per-message audit trail** so the user — and the
developer triaging a bug report — can see exactly what the model did.

#### What the user sees

Directly under each assistant bubble there is a thin collapsible strip:

```
▸ Trace · 7 steps · 3 tool calls · 2.41s
```

Clicking it expands a monospaced log with one row per pipeline step:

| Icon | Phase             | Shows                                                              |
|------|-------------------|---------------------------------------------------------------------|
| ➤    | `request`         | provider/model, history length, currently-selected graph node      |
| ↻    | `round`           | LLM round index, finish reason, dt, # tool calls returned          |
| 🔧   | `tool_call`       | tool name + truncated JSON args                                    |
| ↩    | `tool_return`     | tool name, byte size, dt, 240-char preview of the JSON result      |
| ✓    | `return_result`   | counts of files / node refs / confidence / total elapsed           |
| ✎    | `stream_begin`    | timestamp the final SSE stream started                             |
| ●    | `done`            | tokens, bytes, stream_dt, total_dt, terminal `reason`              |
| ⚠    | `rounds_exhausted`| 5-round cap hit; falls through to a tool-less stream               |
| ✗    | `error`           | which phase blew up (`tools` / `stream`) and the exception message |

The summary line and the body are updated live as events arrive, so the user
gets immediate feedback ("the model is now calling `search_graph`…") instead
of staring at a typing-dots animation.

#### Wire format

`chat.service.chat_stream` no longer yields raw strings — it yields tagged
event dicts:

```python
{"type": "text", "content": "..."}                  # final-answer token
{"type": "step", "phase": "request", ...}           # pipeline trace
{"type": "step", "phase": "tool_call", "name": "search_graph", "args_preview": "..."}
{"type": "step", "phase": "tool_return", "name": "search_graph",
 "bytes": 1234, "dt": 0.51, "preview": "..."}
{"type": "step", "phase": "done", "reason": "stream",
 "tokens": 42, "bytes": 1024, "total_dt": 1.2}
```

The `/api/chat` SSE endpoint serializes them as two distinct frame kinds:

```
data: <escaped text token>\n\n             ← regular token (existing format)
data: [STEP] {"type":"step","phase":"...",...}\n\n
data: [DONE]\n\n
data: [ERROR] <exception>\n\n               ← only on backend failure
```

The client SSE parser (`_streamAssistantResponse` in `web/static/app.js`) is
line-buffered across `read()` chunk boundaries so a `[DONE]` or `[STEP]` frame
that straddles a TCP packet boundary is never dropped (this used to make the
UI hang forever with the typing-dots indicator).

#### Server-side correlation

Each request gets an 8-char `rid` (e.g. `id=a1b2c3d4`) that appears on every
`apollo.log` line *and* on every step event sent to the UI:

```
chat.request id=a1b2c3d4 mode=stream provider=xai model=grok-4-1-fast-… msg=...
tool.call    name=search_graph args={"query":"emails"}
tool.return  name=search_graph bytes=872 dt=0.41s preview={"results":[...]}
chat.round   id=a1b2c3d4 round=0 finish=tool_calls dt=1.22s tool_calls=1
chat.stream_begin id=a1b2c3d4 elapsed=1.74s
chat.done    id=a1b2c3d4 reason=stream tokens=87 bytes=412 total_dt=2.41s
sse.close    id=a1b2c3d4 reason=done tokens=87 bytes=412 steps=6 dt=2.41s
```

Filter the log with `tail -f .apollo/logs/apollo.log | grep -E 'chat\.|tool\.|sse\.'`
to watch a live request, or grep by `id=…` to follow a single conversation
turn end-to-end. When a user reports "the AI got stuck", the `rid` shown in
their browser console (printed as `[chat] stream closed { … }`) is the same
ID the operator can grep in the server log.

---

## 9. Structured Indexing — Smarter Classification

### 9.1 Problem with Current Approach

The current indexer treats every text token as a node — indexing raw words from file contents. For a medium-sized codebase this produces ~90K nodes, most of which are low-value (individual words, punctuation, string literals). The browser crawls trying to render 90K nodes in a force graph, and the 367MB JSON index is bloated with data that doesn't help you understand code structure.

### 9.2 What We Actually Want  ✅ Implemented

Index only **structurally meaningful entities** and their relationships. For a Python file like `email.py`:

```python
# email.py
def foo(a, b={}):
    stuff
```

The valuable information is:

| What to Extract         | Example                        | Why It Matters                              |
|-------------------------|--------------------------------|---------------------------------------------|
| **Function name**       | `foo`                          | Primary node — the thing you search for     |
| **Parameters**          | `a`, `b`                       | Tells you how to call it                    |
| **Parameter defaults**  | `b = {}`                       | `b` expects a dict — API contract           |
| **File it lives in**    | `email.py`                     | Location                                    |
| **Who imports it**      | `import email` in `app.py`     | Dependency edge                             |
| **Who calls it**        | `foo(name, data)` in `app.py`  | Usage edge — with actual argument names     |
| **Call-site arguments** | `name`, `data`                 | How callers actually use the function       |

What we do **not** need to index: every word inside the function body (at this stage), comments as individual nodes, string literals, etc.

### 9.3 Extraction Rules by File Type  ✅ Implemented (Python)

#### Python (`*.py`) — Implemented

| Entity       | What to Capture                                                                 |
|--------------|---------------------------------------------------------------------------------|
| `function`   | Name, parameters (name + default + type annotation if present), decorator list, line range, return annotation |
| `class`      | Name, base classes, line range                                                  |
| `method`     | Same as function, plus parent class                                             |
| `import`     | Module path, imported names, aliases (`from x import y as z`)                   |
| `call_site`  | Caller function, callee name, actual arguments passed, line number              |
| `variable`   | Module-level and class-level assignments only (skip local vars for now)         |

#### Other file types (future)

| File Type    | Valuable Entities                                                               |
|--------------|---------------------------------------------------------------------------------|
| `*.js / *.ts`| `function`, `class`, `import/require`, `export`, call sites                     |
| `*.md`       | Headings, links (internal `[[...]]` and external), code blocks                  |
| `*.json`     | Top-level keys, schema shape                                                    |
| `*.yaml`     | Top-level keys, structure                                                       |

### 9.4 Change Detection via MD5 Hashing  ✅ Implemented

To avoid re-indexing unchanged code, compute hashes at two levels:

```
File level:     md5(entire file contents)     → "has anything changed?"
Function level: md5(full function source)     → "which functions changed?"
```

**Stored per file:**
```json
{
  "file": "email.py",
  "file_md5": "a1b2c3...",
  "functions": {
    "foo": {
      "md5": "d4e5f6...",
      "line_start": 5,
      "line_end": 12,
      "params": [
        {"name": "a", "default": null, "annotation": null},
        {"name": "b", "default": "{}", "annotation": null}
      ]
    }
  }
}
```

**Re-index logic:**

1. Scan directory → compute `md5(file)` for each file.
2. Compare against stored `file_md5`:
   - **Match** → skip entirely.
   - **Mismatch** → re-parse the file, compute per-function `md5`:
     - Function `md5` matches stored → keep existing node, skip re-embedding.
     - Function `md5` changed → update node, re-embed, re-resolve call sites.
     - Function missing from new parse → delete node + edges.
     - New function in parse → create node + edges.
3. After per-file updates, re-resolve cross-file edges (imports/calls) only for changed files.

This makes re-indexing proportional to what actually changed, not the whole codebase.

### 9.5 Node Count Target

With structured extraction, a typical codebase should produce:

| Codebase Size  | Old Node Count (word-level) | New Node Count (structural) | Reduction |
|----------------|----------------------------|-----------------------------|-----------|
| 100 files      | ~10,000                    | ~500–1,500                  | ~10×      |
| 1,000 files    | ~90,000                    | ~3,000–10,000               | ~10–30×   |

This keeps the browser graph responsive without needing the `limit=2000` truncation hack.

### 9.6 Dev-Mode UI Controls  ✅ Implemented (refined in Phase 12)

During development we need to see what's in the index before committing to a heavy render. A slim **dev toolbar** sits at the top of the main canvas:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          📊 1,247 nodes │ 🔗 3,902 edges │ [Load] [Delete] │
└─────────────────────────────────────────────────────────────────────────┘
```

| Control             | Behavior                                                                    |
|---------------------|-----------------------------------------------------------------------------|
| **Node count badge**| Shows current node count from `/api/stats` on page load — **before** loading the graph. User sees the number instantly. |
| **Edge count badge**| Shows current edge count from `/api/stats`. Added in Phase 12 to give a fuller picture of index size. |
| **[Load] button**   | Explicitly loads and renders the graph. Graph is **not** auto-loaded on page open — the user decides when to render after seeing the count. |
| **[Delete] button** | Destroys the current index (deletes the database file / clears the store) and creates a fresh empty one. Confirms with a dialog first. Resets node + edge counts to 0. |

This prevents the "page freezes on load" problem entirely — you see `📊 85,595 nodes` and know to filter or re-index before hitting Load.

> **Note:** The standalone search input that previously lived alongside these controls was merged into the AI Chat input in Phase 12. See section "Phase 12 — Search/Chat Consolidation" below.

### 9.7 Local-First Development Priority

AI chat (Grok API) is a later phase. For now, focus on what works 100% locally:

1. ✅ **Structured indexing** — rich params, call-site args, return annotations (`python_parser.py`)
2. ✅ **Change detection** — file-level + function-level MD5 hashing (`builder.py`)
3. ✅ **Graph rendering** — only render after user clicks Load; show count first (`app.js`)
4. ✅ **Index management** — Load / Delete controls in top bar (`index.html`, `app.js`, `server.py`)
5. ✅ **Structural search** — find by symbol name, traverse callers/callees (`query.py`)

Semantic search (embeddings) and AI chat come after the structural index is solid.

---

## 10. Implementation Phases

### Phase 1 — MVP (Python-only, in-memory)
- [x] Python AST parser → extract functions, classes, imports, calls
- [x] Rich parameter extraction (name, default, type annotation, return annotation)
- [x] Call-site argument capture (actual args passed at each call)
- [x] NetworkX graph builder with cross-file symbol resolution
- [x] CLI: `index <dir>` and `query <symbol> --callers/--callees`
- [x] Persist graph to disk via JSON
- [ ] Basic tests

### Phase 2 — Semantic Search
- [x] Embed node source text with `sentence-transformers`
- [x] Brute-force cosine similarity search (in-memory)
- [x] CLI: `search <text> --top k`
- [ ] Combined search: vector results + graph expansion

### Phase 3 — Browser UI
- [x] FastAPI backend with REST API endpoints
- [x] Single-page frontend with ECharts force-directed graph
- [x] Word cloud view (echarts-wordcloud extension)
- [ ] Treemap / sunburst directory views
- [x] Source code preview panel (left-pane detail with markdown)
- [x] Sidebar filters (node types, edge types)
- [x] Search bar (structural + semantic)
- [x] Dev-mode controls: node count badge, Load button, Delete button
- [x] Graph not auto-loaded — user sees node count first, clicks Load explicitly
- [x] `DELETE /api/index` endpoint — destroy index and reset
- [x] Large graph performance: server-side `limit` param, disabled animation for 500+ nodes
- [x] Edge cap: `/api/graph` limits edges to 3× node count (prioritised by endpoint degree) to prevent browser freeze on large graphs (e.g. 200K edges → ~6K sent)
- [x] Status bar shows truncation info for both nodes (`Nodes: 2000/89576`) and edges (`Edges: 6000/205309`)

### Phase 4 — AI Chat (Grok API)
- [x] Grok API integration via `openai` Python SDK
- [x] ~~RAG pipeline~~ → replaced by tool-calling architecture (Phase 11)
- [x] Chat panel UI with streaming responses
- [x] Graph highlighting from AI responses (node ID references via `[[node_id]]`)
- [ ] "Ask about this node" right-click action
- [x] Conversation history and follow-up questions
- [x] Model selector (fast vs reasoning vs flagship)
- [ ] Cost indicator and offline fallback

### Phase 5 — Couchbase Lite Backend
- [x] Set up `libcblite` Python bindings (ctypes/cffi wrapper)
- [x] Migrate graph + vector storage to Couchbase Lite
- [x] SQL++ queries for combined structural + semantic search
- [x] Benchmark against Phase 3 storage

### Phase 6 — Structured Indexing + Change Detection
- [x] Rich parameter extraction: defaults, type annotations, return annotations
- [x] `*args`/`**kwargs` support with `kind` field on params
- [x] Call-site argument tracking on edges (`call_args`, `call_line`)
- [x] File-level MD5 hashing (`file_md5` on file nodes)
- [x] Function-level MD5 hashing (`source_md5` on function nodes)
- [x] Incremental re-indexing via file hash comparison (`build_incremental`)
- [ ] Function-level incremental re-indexing (compare per-function MD5, only re-process changed functions)
- [x] File watcher for live updates
- [ ] Tree-sitter parser backend for JS/TS/Go/Rust/etc.
- [x] Docstring extraction (function, class, method, module)
- [x] Async function detection (`is_async` flag)
- [x] Nested function detection (`is_nested` flag)
- [x] Test function detection (`is_test` flag) + `tests` edges
- [x] Class variable extraction (`class_vars`)
- [x] Dataclass / NamedTuple detection
- [x] Relative import level tracking
- [x] `__all__` / `__version__` value extraction
- [x] TODO/FIXME/NOTE/HACK/XXX comment extraction → `comment` nodes
- [x] `if TYPE_CHECKING:` import extraction
- [x] Magic string extraction (SQL, URL, regex) → `string` nodes
- [x] Signature fingerprint hashing (`signature_hash`)
- [x] Cyclomatic complexity scoring (`complexity`)
- [x] LOC per function (`loc`)
- [x] Context manager extraction
- [x] Exception handler extraction
- [x] Framework/library pattern detection (`patterns` on file nodes)
- [x] Decorator extraction on methods

### Phase 7 — Markdown Structured Indexing  ✅ Implemented

Treat Markdown files like code: parse structure with `mistune` AST, extract meaningful entities, store in the knowledge graph with rich metadata. Replaces the flat text-blob indexing from `TextFileParser` with hierarchical, entity-based Markdown parsing.

- [x] `MarkdownParser` (`parser/markdown_parser.py`) — AST-based parser using `mistune` v3
- [x] YAML frontmatter extraction via `python-frontmatter` (title, tags, date, author, etc.)
- [x] Title derivation (frontmatter `title` key or first h1 heading)
- [x] Section extraction (h1–h6) with hierarchical parent tracking via stack
- [x] Section content: heading + all content until next heading of same/higher level
- [x] Fenced code block extraction with language tag
- [x] Link & image extraction with classification (internal / external / anchor)
- [x] Table extraction (headers + rows as structured data)
- [x] Task item extraction (`- [x]` / `- [ ]`) with checked state
- [x] Line number tracking for all entities (via raw-text search)
- [x] Whole-document `documents` entry preserved for embedding pipeline compatibility
- [x] Graph builder creates new node types: `section`, `code_block`, `link`, `table`, `task_item`
- [x] `TextFileParser` no longer handles `.md`/`.markdown` (delegated to `MarkdownParser`)
- [x] Node schema updated with new types and properties
- [ ] Semantic chunking by section (embed heading + content per section)
- [ ] Cross-reference tracking: links between Markdown and Python files
- [ ] Auto-tagging: detect patterns like `#TODO`, API endpoints, version numbers
- [ ] Per-section MD5 hashing for change detection

#### New Node Types (Markdown)

| Node Type    | ID Pattern                      | Description                           |
|--------------|----------------------------------|---------------------------------------|
| `section`    | `section::path::Lline`           | Heading + content block (h1–h6)       |
| `code_block` | `codeblock::path::Lline`         | Fenced or indented code block         |
| `link`       | `link::path::Lline`              | Hyperlink or image reference          |
| `table`      | `table::path::Lline`             | Parsed table (headers + rows)         |
| `task_item`  | `task::path::Lline`              | Task list item (`[ ]` / `[x]`)        |

### Phase 8 — Indexing Progress & Performance  ✅ Implemented

Real-time indexing progress feedback in both the terminal and the browser UI, plus embedding performance tuning.

- [x] **Terminal progress logging**: 4-step progress output with emoji indicators, timings per phase, and file/node/edge counts
- [x] **`/api/indexing-status` endpoint**: `GET` endpoint returning current indexing step, label, and detail for UI polling
- [x] **Non-blocking indexing**: `POST /api/index` runs heavy work in a thread pool (`run_in_executor`) so the event loop stays free to serve status polls
- [x] **DaisyUI progress modal**: `steps steps-vertical` stepper UI that opens on index start, polls `/api/indexing-status` every 2s, lights up steps progressively, shows spinner during work and ✅ on completion
- [x] **Poll guard**: in-flight flag prevents stacking poll requests; timer auto-clears on completion
- [x] **File count in status**: parsing step reports file count (e.g. `342 files → 89576 nodes, 205309 edges`)
- [x] **Embedding batch size**: increased from default 32 → 256 for reduced per-batch overhead
- [x] **Embedding progress bar**: enabled `tqdm` progress bar in terminal during `model.encode()`
- [ ] Per-step elapsed time shown in the UI modal (not just terminal)
- [ ] WebSocket-based push notifications (replace polling)

#### Document Node Enhancements

| Field         | Type              | Description                              |
|---------------|-------------------|------------------------------------------|
| `frontmatter` | `object \| null`  | YAML frontmatter metadata                |
| `title`       | `string \| null`  | Title from frontmatter or first h1       |

### Phase 9 — Indexing Hot Path Optimization  ✅ Implemented

Applied the optimization hierarchy: **Do less → Do it less often → Do it faster**. Reduced file I/O, AST walks, embedding scope, and serialization overhead across the entire indexing pipeline.

#### Do Less

- [x] **Single filesystem walk**: Replaced dual traversal (`_build_directory_tree` + `rglob("*")`) with one `os.walk` pass. Directory nodes created lazily from only directories that contain indexed files.
- [x] **Dependency directory auto-skip**: `_discover_files` now prunes `venv/`, `.venv/`, `node_modules/`, `site-packages/`, `target/`, `build/`, `dist/`, `.tox/`, `.eggs/`, and 20+ other known dependency/build/IDE directories via a `_SKIP_DIRS` blocklist. Also detects custom-named virtualenvs by checking for `pyvenv.cfg` or `conda-meta` sentinel files (`_is_venv_dir()`).
- [x] **No duplicate method extraction**: `_extract_functions()` now skips AST nodes whose parent is `ClassDef`. Methods are extracted only once inside `_extract_classes()`, eliminating double analysis and duplicate graph nodes.
- [x] **Fused per-callable analysis**: Replaced 4 separate `ast.walk` passes per function/method (`_extract_calls`, `_compute_complexity`, `_extract_context_managers`, `_extract_exceptions`) with a single `_analyze_callable()` that does one walk and returns all four results.
- [x] **`splitlines()` called once**: Source text is split once per file and the `source_lines` list is passed to all extraction methods, eliminating redundant splits in `_extract_functions`, `_extract_classes`, and `_extract_comments`.
- [x] **Embedding type allowlist**: `embed_graph()` now only embeds nodes of type `function`, `method`, `class`, `document`, or `section` with a minimum 40-character source threshold (`_EMBED_TYPES`, `_MIN_TEXT_LENGTH`). Skips imports, comments, strings, variables, code blocks, links, tables, and task items.
- [x] **Methods carry `source`**: Method nodes now store `source` text so they are searchable and embeddable after the deduplication fix.

#### Do It Less Often

- [x] **Stat-based incremental prefilter**: `build_incremental()` uses `(mtime_ns, size)` from `os.stat()` to skip unchanged files without reading them. Only reads and hashes files whose metadata changed. Measured **261× speedup** on no-change incremental runs.
- [x] **Single file read passthrough**: Added `parse_source(source, filepath)` to `BaseParser` and all 4 parser implementations (`PythonParser`, `TextFileParser`, `MarkdownParser`, `TreeSitterParser`). Changed files are read once in the builder, and the content is passed through for both hashing and parsing — no re-reads.
- [x] **New hash format**: Incremental hashes now store `{sha256, mtime_ns, size}` per file (backward-compatible with legacy plain-hash format).

#### Do It Faster

- [x] **Compact JSON serialization**: `JsonStore.save()` now uses `json.dump()` with `separators=(",",":")` streamed directly to the file handle, producing 2–4× smaller output with less memory than the previous `json.dumps(indent=2)` approach.

#### Skipped Directories (`_SKIP_DIRS`)

| Category | Directories |
|----------|-------------|
| Python | `venv`, `.venv`, `env`, `.env`, `virtualenv`, `site-packages`, `dist-packages`, `.eggs`, `.tox`, `.nox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `__pypackages__` |
| JavaScript | `node_modules`, `bower_components` |
| Go / Rust | `vendor`, `target` |
| Build / Dist | `build`, `dist`, `_build`, `.build` |
| Coverage | `htmlcov`, `.coverage` |
| IDE | `.idea`, `.vscode` |

Plus sentinel-based detection: any directory containing `pyvenv.cfg` or `conda-meta` is treated as a virtualenv.

### Phase 10 — Depth Slider & Graph Cache  ✅ Implemented

Replaced the fixed 2,000-node graph load with a user-controlled "Depth" slider and added client-side caching to eliminate redundant API calls.

- [x] **Depth slider**: DaisyUI `range` input (`range-xs range-primary`) placed to the left of the search bar. 20 logarithmic stops from 5 to 5,000 nodes. Default position: 20 nodes / 60 edges for a consumable starting view.
- [x] **Live label**: Displays `nodes / edges` count, updates instantly during drag via the `input` event.
- [x] **Debounced reload**: Slider fires `loadGraph()` with a 400ms debounce — dragging min→max quickly triggers only one API call at the final position.
- [x] **Edge cap passthrough**: Added `max_edges` query parameter to `/api/graph`. The slider sends edges capped at 3× the node limit (max 5,000). Server falls back to `3× node count` if `max_edges=0`.
- [x] **Client-side graph cache**: 5-minute TTL cache keyed by the full API URL (depth + node type filters + edge type filters). Sliding back to a previously-visited position returns instantly from cache.
- [x] **Cache invalidation**: Cache is cleared on index delete, native folder re-index, and browser folder re-index.
- [ ] Persist slider position in `localStorage` across sessions
- [ ] Show "cached" indicator in status bar when serving from cache

### Phase 11 — AI Chat v2: Tool-Calling Architecture  ✅ Implemented

Replaced the Phase 4 RAG pipeline (pre-fetch graph context → stuff into prompt) with a tool-calling architecture where Grok decides when and what to query.

#### Architecture

```
User question
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  Grok API (with tool definitions)                        │
│                                                          │
│  System prompt: "You have tools to query the graph.      │
│  Use them when the question is about files/code.         │
│  For general questions, answer directly."                │
│                                                          │
│  ┌─ Tool call? ──────────────────────────────────────┐   │
│  │  Yes → execute internally → feed results back     │   │
│  │        (up to 5 rounds)                           │   │
│  │  No  → stream final text response                 │   │
│  └───────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
    │
    ▼
Streamed response to UI (DaisyUI chat bubbles)
```

#### What changed

- [x] **Tool-calling over RAG**: Grok receives tool definitions for internal APIs and calls them on-demand instead of always pre-fetching graph context. General questions (greetings, explanations) skip the graph entirely.
- [x] **4 internal tools**: `search_graph`, `get_node`, `get_stats`, `get_wordcloud` — all execute in-process against the NetworkX graph (no HTTP round-trips).
- [x] **Multi-round tool loop**: Grok can chain tool calls (e.g., search → get_node → get_node for a caller) up to 5 rounds before the final streamed response.
- [x] **Embedded chat panel**: Chat moved from a separate nav view into the left pane (bottom half), always visible alongside the graph. Draggable vertical split handle between Node Detail and Chat.
- [x] **DaisyUI chat bubbles**: User messages use `chat-end chat-bubble-primary`, Grok responses use `chat-start chat-bubble-neutral`. AI responses rendered with `marked.js` + `highlight.js` for markdown and syntax highlighting.
- [x] **API key auto-recovery**: `checkChatStatus()` does a fallback PUT to `/api/settings` to re-apply saved keys if the env var wasn't set on boot.

#### Tool Definitions

| Tool | Description | Parameters |
|------|-------------|------------|
| `search_graph` | Keyword or semantic search across all indexed nodes | `query` (required), `top`, `type` |
| `get_node` | Full node detail: source, metadata, all edges (callers, callees, imports) | `node_id` (required) |
| `get_stats` | Graph summary: total nodes/edges, counts by type | — |
| `get_wordcloud` | Hub symbols ranked by graph strength (in+out degree, aggregated by name). Returns `{items, total, shown, mode, min_strength}`; each item has `name`, `strength`, `count`. | `mode` (`strong`/`relevant`/`all`), `limit` |

#### Future — Planned Tools & Enhancements

- [ ] **`query_callers` tool**: Given a node ID, return all transitive callers up to depth N. Wraps `GraphQuery.callers()`.
- [ ] **`query_callees` tool**: Given a node ID, return all transitive callees. Wraps `GraphQuery.callees()`.
- [ ] **`get_file_source` tool**: Return full source of a file by path (for when Grok needs more context than the truncated node source).
- [ ] **`search_by_pattern` tool**: Regex search across node names and source text.
- [ ] **Bookmarks / Notes tools**: CRUD tools for user-created bookmarks and notes — Grok can save, retrieve, and search personal annotations.
- [ ] **Graph mutation tools**: Let Grok suggest adding custom edges or tags (e.g., "these two functions are related because...").
- [ ] **Multi-agent routing**: Use Grok 4.20 multi-agent model to dispatch sub-questions to specialized tool-calling agents (one for code analysis, one for docs, one for architecture).
- [ ] **Chat history persistence**: Save conversation threads to disk or Couchbase Lite so users can resume sessions across browser reloads.
- [ ] **Cost tracking**: Token usage counters per session, displayed in the chat header. Budget alerts.
- [ ] **Streaming tool status**: Show a subtle "Searching graph..." / "Reading node..." indicator in the chat bubble while Grok's tool calls execute.
- [ ] **Context-aware tool selection**: When a node is selected in the graph, auto-attach its ID as a hint so Grok can reference it without the user re-typing.

---

### Phase 12 — Search/Chat Consolidation  ✅ Implemented

The standalone search input in the top bar was merged into the AI Chat input. The two affordances confused users: typing in the top bar gave node lookups, typing in the chat gave AI answers — but most users wanted both ("find these files **and** tell me about them"). Phase 12 unifies them into a single chat input with an inline mode badge.

#### Architecture

A single `<textarea id="chat-input">` mounted with [Tagify](https://yaireo.github.io/tagify/) in **mix mode**. Two colored badges (`find:` green, `chat:` orange) sit at the start of the input and indicate what mode each keystroke will run in. The user can remove either badge to narrow the intent. The default (both badges present) routes to the AI chat pipeline, where Grok's existing tool-calling (Phase 11) decides when to invoke `search_graph` for the user.

| Badge        | Color  | Behavior                                                                                                |
|--------------|--------|---------------------------------------------------------------------------------------------------------|
| `find:`      | green  | Pure graph search. Live debounced lookup as the user types; clicking a result jumps to that node. **No AI call, no tool-calling.** |
| `chat:`      | orange | Send the message to Grok. Tool-calling is enabled server-side, so Grok will call `search_graph` itself when the question warrants it. |
| *(both, default)* | — | Same as `chat:`. The two badges visible together signal that **both capabilities are on**: the user can either type a literal lookup (which Grok's tool will resolve) or just ask a question. |

**Both badges are pre-populated by default** so the modes are discoverable without needing to focus or read docs. The user can:
- Click `×` on `chat:` → input becomes `find:`-only (purely local).
- Click `×` on `find:` → input becomes `chat:`-only (purely AI).
- Click `×` on both → still defaults to `chat:` semantics (AI with tools).
- Re-add via the dropdown (focus → pick) or by typing `find:` / `chat:` literally — the prefix is auto-converted to a badge.

Repeated typed prefixes (`find: find: chat: query`) are de-duplicated: `Tagify duplicates: false` blocks duplicate badges, and the input handler strips ALL leading `(find|chat):` prefixes from the typed text in a loop.

#### UI Changes

- **Removed**: Top search bar (`#search-input`, `.top-search-bar`).
- **Relocated** to a slim dev toolbar at the top: node count, **new edge count**, Load button, Delete button. (Section 9.6 updated.)
- **Search results dropdown** now opens **upward** (`bottom: 100%`) anchored above the chat input.
- **Chat input** swapped from `<input>` to `<textarea>` (Tagify mix mode requires it). Enter sends, Shift+Enter inserts a newline, Enter while the badge dropdown is open picks the suggestion.
- **Word cloud click** still triggers a graph search, but now drives the same `searchNodes()` path used by the chat input.

#### Why We Don't Pre-RAG Anymore

An earlier draft of Phase 12 ran `/api/search?top=5` on the raw user message and prepended the hits to the prompt. **That was wrong** — it forced a literal vector search of natural-language text like *"which files have couchbase files"*, which scores poorly against the indexed entity names. The Phase 11 tool-calling architecture already solves this correctly: Grok parses the user's intent, extracts a focused query (e.g. `couchbase`), and calls `search_graph("couchbase")` itself. Phase 12 now defers entirely to that pipeline for `chat:` and the default mode — the only client-side search is the live `find:`-mode dropdown, which uses the user's literal typed string as intended.

#### Files Changed

- `graph_search/web/static/index.html` — removed top search bar, added dev toolbar with edge count, swapped input → textarea, added Tagify CDN, moved `#search-results` into the chat-input row.
- `graph_search/web/static/app.css` — added Tagify mode-badge styles (green/orange pills), flipped `.search-results` to `bottom: 100%`.
- `graph_search/web/static/app.js` — Tagify init on `#chat-input`, new `parseChatMode()` helper, refactored `sendChatMessage()` to route find/chat/both, edge-count wired into `fetchIndexCount()` and `deleteIndex()`.

#### Phase 12.1 — Expanded Tool Set for Multi-Step Reasoning  🟡 Partial (12.1a ✅ Implemented, 12.1b 📋 Planned)

The current Phase 11 toolkit (`search_graph`, `get_node`, `get_stats`, `get_wordcloud`) handles single-shot lookups well, but questions like *"tell me about all my files with couchbase"* need **discovery → drill-in → synthesize → answer**. We'll add:

##### Discovery tools (find candidates)

| Tool | Signature | Purpose |
|---|---|---|
| **`search_graph_multi`** | `(queries: string[], top?: int, type?: string)` | Run N searches in parallel, dedupe by node ID, merge scores. Lets Grok cast a wide net (`["couchbase", "cblite", "lite"]`) in one round-trip instead of N. |
| **`search_source`** | `(pattern: string, regex?: bool)` | Substring or regex search across **node `source` text** (not just names). Catches `import couchbase` even when the symbol name doesn't contain "couchbase". |
| **`list_files`** | `(path_glob?: string, type?: string)` | Cheap enumeration of file/directory nodes. Good when the user says "all my files". |
| **`search_by_path`** | `(path_glob: string)` | Find nodes whose path matches a glob like `**/couchbase*.py`. |

##### Drill-in tools (expand a candidate)

| Tool | Signature | Purpose |
|---|---|---|
| **`get_neighbors`** | `(node_id, depth?: int, edge_types?: string[])` | Walk the graph: callers, callees, importers. Lets Grok answer "who uses this?" without N round-trips of `get_node`. |
| **`get_file`** | `(path)` | Whole file source by path (not truncated). Useful when node-level source isn't enough context. |
| **`get_subgraph`** | `(node_ids: string[])` | Bulk fetch nodes + their interconnecting edges. Good for "summarize this cluster". |

##### Termination tool

| Tool | Signature | Purpose |
|---|---|---|
| **`return_result`** | `(summary: string, files?: string[], node_refs?: string[], confidence?: 'high' \| 'med' \| 'low')` | Explicit "I'm done analyzing" signal with structured citations. **Bounds** the tool-call loop (no wasted rounds after the AI thinks it has the answer) and gives the **UI structured data** to render (clickable file/node chips) instead of relying on `[[node_id]]` markdown parsing. |

##### System prompt update

The Phase 11 prompt should be amended to teach the discovery → drill-in → return pattern:

> Workflow when the user asks a multi-file/multi-symbol question:
> 1. Use `search_graph_multi` with synonyms when the topic is fuzzy.
> 2. Use `get_neighbors` (not multiple `get_node` calls) when exploring a cluster.
> 3. Call `return_result(summary, files, node_refs)` to finalize — do **not** just emit prose.

##### Rollout plan

- **Phase 12.1a (small, high value)**: `search_graph_multi`, `return_result`, `get_neighbors`. Deliverable: backend tool implementations in `chat/service.py` + corresponding handlers in `query.py`/`graph_query.py`, plus the workflow line in the system prompt. Both also exposed as HTTP endpoints (`POST /api/search/multi`, `GET /api/neighbors/{node_id}?depth=&edge_types=&direction=`) so the frontend and external clients share the same operations as the AI.
- **Phase 12.1b (later)**: `search_source`, `get_file`, `list_files`, `search_by_path`, `get_subgraph`.

#### Phase 12.3 — Read-Only File & Source Inspection  🟡 12.3a Implemented

The graph already gives the AI rich **metadata** (functions, classes, imports, callers/callees, paths, line numbers). What it cannot answer well today:

- "Show me lines 1240–1290 of `parser.py`."
- "Where in the project is `requests.put` called?"
- "Has the file changed since I last read it?"
- "What does function `foo` look like in a file that *isn't* in the graph yet (e.g. a new test file)?"
- "How big is this file — how many lines, classes, functions?"

These are routine debugging operations. Asking the user to paste a 3–8 MB file into chat is impractical. We instead expose a set of **strictly read-only** file inspection tools.

> **Read-only by design.** Phase 12.3 deliberately omits any write/edit/repair/add/delete capability. The AI can describe what should change and *where*, but the user owns every modification. This keeps the trust model simple, makes file watcher invalidation a non-issue, and avoids needing rollback/undo machinery.

##### Security model

Every file path passed to a 12.3 tool MUST resolve to either:
- a path that has a `file` or `directory` node in the current graph, **or**
- a path that lies inside the configured `root_dir` (the indexed directory).

Anything else returns `403 Forbidden`. This means the AI can only see code the user has already chosen to index — no `/etc/passwd`, no climbing out of the project. The check is centralized in a `_safe_path(p)` helper used by every tool.

##### MD5 versioning

`file_stats` returns the file's `md5`. Subsequent tools (`get_file_section`, `file_search`, `get_function_source`) accept an **optional** `expected_md5`. If provided and the file has changed on disk, the call returns `409 Conflict` with the new MD5 so the AI can re-fetch metadata. The AI can chain calls without versioning when the user just asked a one-shot question.

##### Tools (12.3a — implemented)

| Tool | Signature | Purpose |
|---|---|---|
| **`file_stats`** | `(path)` | Returns `{ path, size_bytes, line_count, md5, language, function_count, class_count, top_level_imports[] }`. The AST walk is cheap even on multi-MB files. The AI calls this first, then decides what to drill into. |
| **`get_file_section`** | `(path, start_line, end_line, expected_md5?)` | Inclusive 1-indexed line range. Returns `{ path, start_line, end_line, md5, lines: [{n, text}] }`. Hard cap: 800 lines per call. |
| **`get_function_source`** | `(path, name, expected_md5?)` | AST-extract a function/method by name (handles `Class.method` qualified names). Returns the full source including decorators + docstring. Works even on files not yet in the graph. |
| **`file_search`** | `(path, pattern, context?, regex?, expected_md5?)` | Grep within one file. Returns `[{ line_no, text, context_before[], context_after[] }]`. Default `context=5`, `regex=true`. Hard cap: 200 matches. |
| **`project_search`** | `(pattern, root?, context?, file_glob?, regex?)` | Grep across the indexed project. `root` defaults to the configured index root; if provided it must pass `_safe_path`. `file_glob` defaults to `*.py`; supports `*.py,*.md`. Returns `[{ path, line_no, text, context_before[], context_after[] }]`. Hard caps: 500 matches, 200 KB total snippet bytes. |

The user's planning conversation also proposed a destructive `apply_edits` / `bulk_transform` family of helpers. **Those are intentionally out of scope** — see the read-only note above. If/when we ever revisit, it would be a separate Phase 12.4 with a confirmation UX and explicit user opt-in per call.

##### System-prompt update

Append to the workflow already added in 12.1a:

> When debugging a specific file, follow the inspect → drill → answer pattern:
> 1. Call `file_stats(path)` to see size and structure.
> 2. Use `project_search(pattern)` when the user is unsure which file is involved.
> 3. Use `get_function_source(path, name)` or `get_file_section(path, start, end)` to read targeted slices instead of asking the user to paste code.
> 4. Cite line numbers in your answer (`path:line_no`) so the user can jump directly to them.

##### HTTP endpoints (mirrors of the AI tools)

Following the 12.1a precedent, every tool has a matching HTTP endpoint so the frontend and external clients share the same plumbing:

| Endpoint | Maps to |
|---|---|
| `GET  /api/file/stats?path=...` | `file_stats` |
| `GET  /api/file/section?path=...&start=...&end=...&md5=...` | `get_file_section` |
| `GET  /api/file/function?path=...&name=...&md5=...` | `get_function_source` |
| `POST /api/file/search` | `file_search` |
| `POST /api/project/search` | `project_search` |

##### Out of scope (parking lot for 12.3b)

- `list_files(path_glob)` and `search_by_path(glob)` — partly subsumed by the existing `/api/tree` and the graph's `file`/`directory` nodes.
- `get_callers_of(name)` — already covered by `get_neighbors` with `edge_types=["calls"], direction="in"`.
- `find_large_literals(file)` — niche; revisit on demand.
- Streaming variants of `get_file_section` for huge files — current 800-line cap is enough in practice.

##### Frontend hooks for `return_result`

When the assistant streams a `return_result` tool call, the chat UI should:
- Render `summary` as the message body (markdown).
- Render `files` as a horizontal chip strip beneath the message; clicking jumps to that file's node and selects it in the graph.
- Render `node_refs` similarly using the existing `chatNodeClick(id)` handler.
- Show a small confidence indicator (green/yellow/red dot) next to the assistant header when `confidence` is provided.

#### Other Future Enhancements

- [ ] **Per-result toggle in `find:` dropdown**: Let the user pick which of the top hits get auto-attached as `context_node` for the next chat turn.
- [ ] **Mode persistence per thread**: Remember which badges were active when a thread was created and restore them on `loadChatThread()`.
- [ ] **Inline result preview**: For `find:` mode, optionally render results inline as a system message in the chat thread (so they're scrollable in history) instead of a transient dropdown.
- [ ] **Selected-node hint**: When a node is selected in the graph, auto-pass its ID as `context_node` to `/api/chat` (the field already exists server-side; just stop having to re-type it).

---

## 11. Tech Stack Summary

| Component        | Phase 1                | Phase 2                  | Phase 3            | Phase 4              | Phase 5              |
|------------------|------------------------|--------------------------|--------------------|----------------------|----------------------|
| Parser           | Python `ast`           | Python `ast`             | Python `ast`       | Python `ast`         | + Tree-sitter        |
| Graph Store      | NetworkX + JSON export | NetworkX + JSON export   | NetworkX           | NetworkX             | Couchbase Lite       |
| Vector Index     | —                      | FAISS or Hnswlib         | FAISS or Hnswlib   | FAISS or Hnswlib     | Couchbase Lite vector|
| Embeddings       | —                      | `all-MiniLM-L6-v2`      | same               | same                 | same                 |
| AI / LLM         | —                      | —                        | —                  | Grok API (xAI)       | Grok API (xAI)       |
| Query Interface  | CLI (click/argparse)   | CLI + JSON DSL           | + Browser UI       | + AI chat            | + SQL++              |
| Frontend         | —                      | —                        | ECharts SPA        | + chat panel         | same                 |
| Backend          | —                      | —                        | FastAPI            | + `/api/chat`        | FastAPI              |
| Persistence      | JSON file              | JSON + FAISS index file  | same               | same                 | Couchbase Lite DB    |

---

## 12. Open Questions

1. **Scope of "references"** — Should we track every variable read/write, or only function calls and imports? Full reference tracking is significantly more complex.
2. **Embedding granularity** — ✅ Resolved: Embed at function/method/class/document/section level only, with a 40-char minimum threshold (Phase 9). This balances search quality with embedding cost — skips low-value node types like imports, comments, and strings.
3. **Dynamic languages** — In Python, `getattr(obj, func_name)()` calls are invisible to static analysis. How much dynamic resolution do we attempt?
4. **Large repos** — ✅ Resolved: Three-layer approach (Phase 9). Auto-skip dependency directories (`venv/`, `node_modules/`, etc.) so only user code is indexed. Stat-based incremental prefilter (261× speedup on no-change runs). Depth slider (Phase 10) lets users control how many nodes are rendered, defaulting to 20 for fast initial loads.
5. **Couchbase Lite licensing** — Verify the community edition license is compatible with the intended use.
6. **Grok API key management** — Store in env var, config file, or prompt on first launch? Need to keep it out of any persisted state.
7. **Chat history persistence** — Save conversation history to disk so users can resume sessions? Or ephemeral per browser session?
8. **Non-code files** — ✅ Resolved: Markdown files now get rich AST-based indexing (Phase 7). JSON, YAML, CSV, and text files are indexed as flat documents via `TextFileParser`. The tool supports both `*.py` and `*.md` as first-class file types during development.

---

## 13. Enhanced Python Indexing — Core Plan  ✅ Implemented

### 13.1 What We Extract from Every `*.py` File

All extraction uses Python's built-in `ast` module — robust, zero-dependency, and built into every Python installation.

#### Structure & Signatures (Easy)
- **Functions**: name, parameters (with defaults, type annotations, `*args`/`**kwargs`, kind), decorators, return annotation, docstring, source text
- **Classes**: name, bases (inheritance), methods (with full function-level detail), class-level variables, decorators, docstring
- **Methods**: same details as functions, plus parent class reference
- **Async detection**: `is_async` flag on functions and methods
- **Nested functions**: `is_nested` flag when a function is defined inside another function
- **Test detection**: `is_test` flag for functions starting with `test_`

#### Dependencies (Easy)
- **All imports**: `import X`, `from X import Y as Z`, relative imports (with `level` field)
- **`__all__` / `__version__`**: extracted with their values
- **`if TYPE_CHECKING:` blocks**: imports inside these blocks tracked separately with `type_checking` flag

#### Call Graph (Medium)
- **Every `func()` or `obj.method()` call**: extracted with actual arguments passed
- **Cross-file resolution**: calls resolved via import map + global symbol table
- **Call-site metadata**: caller function, callee name, arguments, line number stored on edges
- **Test→function links**: `tests` edges from test functions to the functions they test

#### Data Flow (Medium)
- **Module-level variables**: assignments with optional values for constants
- **Class-level variables**: `class_vars` with name, line, annotation, and value
- **Dataclass / NamedTuple detection**: `is_dataclass` and `is_namedtuple` flags on classes

#### Documentation (Easy)
- **Docstrings**: extracted from functions, classes, methods, and modules
- **Tagged comments**: `# TODO`, `# FIXME`, `# NOTE`, `# HACK`, `# XXX` — extracted as `comment` nodes

#### Types & Runtime (Easy–Medium)
- **Type hints**: parameter annotations, return annotations (including `typing.*`)
- **`if TYPE_CHECKING:` blocks**: imports separated for type-only dependencies
- **Parameter kinds**: `arg`, `vararg` (`*args`), `kwonly`, `kwarg` (`**kwargs`)

#### Control Flow (Medium)
- **Decorators**: captured on functions, methods, and classes
- **Context managers**: `with` statement expressions extracted per function
- **Exception handling**: exception types caught in `try/except` blocks

#### Metrics (Easy)
- **Cyclomatic complexity**: 1 + count of `if/elif/for/while/except/and/or/assert/with` nodes
- **LOC per function**: `line_end - line_start + 1`
- **Signature fingerprint**: MD5 hash of `name(param1,param2,...)` for similarity matching

#### Patterns (Medium)
- **Framework detection**: auto-tags files using known libraries: `fastapi`, `django`, `flask`, `sqlalchemy`, `pydantic`, `celery`, `pytest`
- **Detected patterns** stored on file nodes for filtering and grouping

#### Tests (Easy)
- **Test functions**: detected by `test_` prefix, linked to production functions via `tests` edges
- **Pytest fixtures**: detected via `pytest` pattern on files

#### Strings (Medium)
- **SQL queries**: strings containing `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE`
- **URL routes**: strings starting with `/` or containing `://`
- **Regex patterns**: strings passed to `re.compile()`, `re.match()`, `re.search()`, etc.
- All stored as `string` nodes with `kind` classification

### 13.2 Versioning & Change Detection

On file change → re-parse only changed functions via per-function MD5:

```python
if file_md5 != stored_file_md5:
    for func in parsed_functions:
        if func_body_md5 != stored_func_md5:
            reprocess_function(func)  # update embeddings, call graph, etc.
```

Two-level hashing:
- **File level**: `MD5(entire file contents)` → skip file entirely if unchanged
- **Function level**: `MD5(function source text)` → skip individual functions if unchanged

### 13.3 Entity Fingerprinting

Each function/method stores a `signature_hash` — an MD5 of the normalized parameter list (ignoring default values):

```
MD5("process(data,config,timeout)")  →  "a1b2c3d4..."
```

This enables: "Show me all functions with a signature similar to `process(data: dict) -> list`"

### 13.4 Cross-Reference Index

- `function_call_sites`: stored as `calls` edges with `call_args` and `call_line`
- `import_sites`: stored as `imports` edges linking files to import nodes
- `tests` edges: linking test functions to the functions they test

### 13.5 New Node Types

| Node Type   | ID Pattern                        | Description                           |
|-------------|-----------------------------------|---------------------------------------|
| `comment`   | `comment::path::Lline`            | Tagged comment (TODO/FIXME/NOTE/etc.) |
| `string`    | `string::path::Lline`             | Notable string (SQL/URL/regex)        |

### 13.6 New Edge Types

| Edge Type   | Meaning                                | Example                                    |
|-------------|----------------------------------------|--------------------------------------------|
| `tests`     | A test function tests a production func | `test_emails() --tests--> emails()`        |
| `references`| A function references a variable        | `send() --references--> SMTP_HOST`         |

### 13.7 Light Semantic Layer (Local)

Using `ast` → convert function to a structured dict → embed the signature + docstring with a small local model (`sentence-transformers/all-MiniLM-L6-v2`). This gives similarity search without needing a full LLM for every query.

---

## 14. Phase 11 — Highlights, Notes, Bookmarks & Web Content Capture

> Detailed implementation plan: [`docs/work/PLAN_NOTES_BOOKMARK_WEB_CONTENT.md`](work/PLAN_NOTES_BOOKMARK_WEB_CONTENT.md)

### 14.1 Overview

Four interconnected features that turn Graph Search from a read-only explorer into a personal knowledge workbench:

| Feature | Description |
|---|---|
| **Highlights** | Select text in the Node Detail panel (`#left-detail-content`), highlight it with a color, anchor it to the node |
| **Notes** | Attach Markdown notes to highlights or directly to nodes. Searchable, editable, soft-deletable |
| **Bookmarks** | Star any node or note for quick access via a sidebar tab |
| **Web Content Capture** | Pull content from URLs (HTML/PDF) into a local `_apollo_web/` folder, convert to Markdown, auto-index into the graph |
| **Trash Can** | Soft-deleted notes go to trash (recoverable). Permanent delete available from trash view |

### 14.2 Data Model — Annotations

Stored in a separate `annotations.json` file in `.graph_search/` to keep user data safe from re-indexing.

#### Highlight

```json
{
  "id": "hl::<uuid>",
  "node_id": "func::src/mailer.py::emails",
  "text": "smtp = connect()",
  "start_offset": 142,
  "end_offset": 158,
  "color": "#fde047",
  "created_at": "2026-04-25T12:00:00Z",
  "updated_at": "2026-04-25T12:00:00Z"
}
```

#### Note

```json
{
  "id": "note::<uuid>",
  "highlight_id": "hl::<uuid>",
  "node_id": "func::src/mailer.py::emails",
  "body": "This sets up the SMTP connection...",
  "tags": ["smtp", "email"],
  "created_at": "2026-04-25T12:00:00Z",
  "updated_at": "2026-04-25T12:00:00Z",
  "deleted_at": null
}
```

`deleted_at` non-null = in trash. Restore nullifies it. Permanent delete removes the record.

#### Bookmark

```json
{
  "id": "bm::<uuid>",
  "target_type": "node",
  "target_id": "func::src/mailer.py::emails",
  "label": "Important email logic",
  "created_at": "2026-04-25T12:00:00Z"
}
```

### 14.3 Data Model — Web Content Capture (`_apollo_web/`)

A local folder at the project root for web-captured content. Each capture gets a slug folder with the original file, a cleaned Markdown extraction, and a single `capture.json` with a versioned array.

#### Folder Structure

```
project_root/
├── _apollo_web/
│   ├── _manifest.json                        ← global capture index
│   ├── docs.python.org_tutorial/
│   │   ├── content.html                      ← latest original (HTML)
│   │   ├── content.md                        ← latest cleaned Markdown (indexed)
│   │   └── capture.json                      ← version history
│   ├── arxiv.org_2301.00001/
│   │   ├── content.pdf                       ← latest original (PDF blob)
│   │   ├── content.md                        ← AI-generated Markdown summary
│   │   └── capture.json                      ← version history
```

#### `capture.json` — Single file, versions array, newest at `[0]`

```json
{
  "url": "https://docs.python.org/3/tutorial/",
  "slug": "docs.python.org_tutorial",
  "current_md5": "d4e5f6...",
  "versions": [
    {
      "content_md5": "d4e5f6...",
      "captured_at": "2026-04-25T12:00:00Z",
      "title": "Python Tutorial",
      "size_bytes": 51340,
      "content_type": "text/html"
    },
    {
      "content_md5": "a1b2c3...",
      "captured_at": "2026-04-20T10:00:00Z",
      "title": "Python Tutorial",
      "size_bytes": 48210,
      "content_type": "text/html"
    }
  ]
}
```

On re-download: MD5 new content → matches `current_md5`? No-op. Different? Overwrite `content.md`/`content.html`, insert new version at `versions[0]`, update `current_md5`. In CBL, query `versions[0]` for the latest — no duplicates in results.

#### Pipeline

```
URL → fetch (httpx) → detect content type
  ├─ HTML → save .html → extract with readability + markdownify → save .md
  └─ PDF  → save .pdf blob → send to Grok API for structured summary → save .md
→ auto-trigger incremental re-index of just that file (near-zero cost)
→ MarkdownParser picks up the .md → sections, links, tables become graph nodes
→ content is searchable via structural + semantic search
```

#### Why auto-index is cheap

`build_incremental` uses stat-based prefilter: unchanged files skip without a read. Adding one `.md` file costs only that file's parse + embed (~1-2 seconds). No full re-index needed.

### 14.4 New API Endpoints

#### Annotations

| Endpoint | Method | Description |
|---|---|---|
| `/api/highlights` | GET/POST | List or create highlights on a node |
| `/api/highlights/{id}` | PUT/DELETE | Update or delete a highlight |
| `/api/notes` | GET/POST | List or create notes (`?q=` search, `?node_id=`, `?trash=true`) |
| `/api/notes/{id}` | GET/PUT/DELETE | Read, update, soft-delete a note |
| `/api/notes/{id}/restore` | POST | Restore from trash |
| `/api/notes/{id}/purge` | DELETE | Permanently delete |
| `/api/bookmarks` | GET/POST | List or create bookmarks |
| `/api/bookmarks/{id}` | DELETE | Remove a bookmark |

#### Web Captures

| Endpoint | Method | Description |
|---|---|---|
| `/api/captures` | GET | List all web captures from manifest |
| `/api/captures` | POST | `{ "url": "..." }` → download + convert + auto-index |
| `/api/captures/{id}` | GET | Get capture metadata + version history |
| `/api/captures/{id}` | DELETE | Remove capture files + manifest entry |
| `/api/captures/{id}/redownload` | POST | Re-fetch URL, version if content changed |

### 14.5 New Schemas (per `guides/SCHEMA_DESIGN.md`)

| Schema File | Describes |
|---|---|
| `highlight.schema.json` | A text highlight anchored to a node |
| `note.schema.json` | A note attached to a highlight or node |
| `bookmark.schema.json` | A bookmark referencing a node or note |
| `capture.schema.json` | A web content capture with version history |

### 14.6 New Edge Types

| Edge Type | Meaning | Example |
|---|---|---|
| `annotates` | A highlight/note annotates a node | `note::abc --annotates--> func::mailer.py::emails` |
| `bookmarks` | A bookmark references a node or note | `bm::xyz --bookmarks--> func::mailer.py::emails` |

### 14.7 Frontend UI

#### Highlights + Notes

- **Text selection**: `window.getSelection()` on `#left-detail-content` → floating toolbar with "Add Note" + color picker
- **Note editor**: Popover with Markdown textarea, save/cancel, shows highlighted text as blockquote
- **Notes sidebar tab** (`📝`): Lists all notes with search bar, highlight snippet, node name, timestamp
- **Trash toggle**: "Active" / "Trash" switch in Notes tab. Trash items show Restore / Permanently Delete

#### Bookmarks

- **Star icon** on nodes and notes in the detail panel
- **Bookmarks sidebar tab** (`⭐`): Clickable list that navigates to the bookmarked node in the graph

#### Web Captures

- **"Capture URL" button** in top bar → modal with URL input
- **Web sidebar tab** (`🌐`): Lists all captures with title, domain, date. Click → navigate to document node in graph
- **Status indicator**: Downloading → Converting → Indexing → Ready ✅ / Error ❌

### 14.8 Dependencies

| Library | Purpose | Status |
|---|---|---|
| `httpx` | Async HTTP download for captures | Check `requirements.txt` |
| `beautifulsoup4` | HTML parsing and text extraction | Check |
| `readability-lxml` | Article extraction (strips nav, ads, chrome) | New |
| `markdownify` | HTML → Markdown conversion | New |
| Grok API | PDF content summarization → structured JSON → `.md` | Already integrated (Phase 4) |
