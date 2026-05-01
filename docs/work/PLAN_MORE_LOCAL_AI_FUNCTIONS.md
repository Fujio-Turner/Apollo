# PLAN — More Local AI Tool-Functions for the Chat Agent

**Owner:** chat / graph / api
**Status:** Phases 1-4 done — all 14 tools registered + tested
**Source proposal:** [`docs/AI_MORE_LOCAL_FUNCTIONS.md`](../AI_MORE_LOCAL_FUNCTIONS.md)
**Reference:** [`docs/DESIGN.md §8`](../DESIGN.md) (chat / tool architecture, TOON-encoded tool results, 3-round budget rule)

---

## 0. Goal

Extend the chat agent's tool catalog with high-leverage, **read-only** local
functions that:

- Cut tool-call rounds (batching, multi-range reads, multi-node fetch).
- Answer architecture / refactoring questions the current tools can't
  (`get_paths_between`, `get_inheritance_tree`, `get_transitive_imports`,
  `find_test_correspondents`, `detect_entry_points`).
- Surface cheap signal already present in the graph but not exposed
  (`get_code_metrics`, `search_graph_by_signature`, `project_stats_detailed`).
- Add a real "ls -R" view (`get_directory_tree`) and git context
  (`get_git_context`) — both are repeatedly missing in real sessions.
- Make the user's own annotations searchable
  (`search_notes_fulltext`).

All new tools MUST:

1. Be **read-only**. The system prompt's "no write access" rule is preserved.
2. Return results that survive the **TOON re-encoding** path in
   `chat.service._to_toon_for_llm` — i.e. uniform-shape arrays so we keep the
   30–50% byte reduction.
3. Be registered both in [`ai/chat_request.json`](../../ai/chat_request.json)
   (tool schema) and in [`chat/service.py`](../../chat/service.py) `_exec_tool_impl`.
4. Stay inside the **3 tool-call rounds** budget rule (system prompt §workflow).
   New tools should reduce rounds, never tempt the model into more of them.
5. Have a **matching `/api/...` HTTP endpoint** when the data is also useful
   to the human-facing UI (parity with `get_neighbors` / `get_wordcloud`).

---

## 0.1 Why these beat brute-force `grep`

Every tool below replaces an N-round grep dance with a single resolved
fact. `grep` finds **strings**; the graph already knows **what each string
means** (function definition? call site? import? inheritance edge?). Each
time the model falls back on `project_search`, it has to:

1. Pay tokens for surrounding noise (whitespace, comments,
   similarly-named symbols).
2. Disambiguate `def foo` vs `foo(` vs `import foo` vs `# foo`
   itself — burning rounds on follow-up reads.
3. Re-discover relationships the indexer already wrote down once at
   index time.

A graph tool returns *resolved facts*, not text matches. That is the
single justification for every tool in this plan.

### End-to-end worked example — the leverage in one picture

> User: *"I want to refactor `MailService` — what depends on it and
> what tests cover it?"*

**Without these tools (today, ~6 rounds, low confidence):**

1. `search_graph "MailService"` → finds the class node.
2. `get_neighbors(id, depth=1)` → direct callers / subclasses only.
3. `get_neighbors(...)` again on each subclass — depth-2 of a cluster
   isn't one call.
4. `project_search "MailService"` to catch string references the
   graph missed.
5. `project_search "test_mail"` guessing the test naming convention.
6. Round budget exhausted → `return_result` with `confidence=low`.

**With Phases 1–3 of this plan (~2 rounds, high confidence):**

1. *Parallel in one round:* `get_inheritance_tree(id, include_methods=true)`
   + `get_transitive_imports(file_id, direction="in")`
   + `find_test_correspondents(id)`.
2. `batch_get_nodes([…top 5 callers…])` to read source.
3. `return_result` with `confidence=high`.

That delta — **fewer rounds, higher confidence, no grep noise, no
false positives from comments / strings / vendor code** — is the
deliverable for every phase below.

---

## 1. What's already in Apollo (do NOT re-implement)

Cross-checked against [`ai/chat_request.json`](../../ai/chat_request.json) and
[`chat/service.py`](../../chat/service.py):

| Tool                  | Lives in                                  |
|-----------------------|-------------------------------------------|
| `search_graph`        | `chat/service.py:275`                     |
| `search_graph_multi`  | `chat/service.py:326`                     |
| `get_node`            | `chat/service.py:297`                     |
| `get_neighbors`       | `chat/service.py:365`                     |
| `get_stats`           | `chat/service.py:323`                     |
| `get_wordcloud`       | `chat/service.py:459`                     |
| `file_stats`          | `chat/service.py:389`                     |
| `get_file_section`    | `chat/service.py:391`                     |
| `get_function_source` | `chat/service.py:397`                     |
| `file_search`         | `chat/service.py:403`                     |
| `project_search`      | `chat/service.py:411`                     |
| `list_notes`          | `chat/service.py:433`                     |
| `notes_by_target`     | `chat/service.py:441`                     |
| `notes_by_tag`        | `ai/chat_request.json` (handler near 441) |
| `return_result`       | terminal tool (handled outside `_exec_tool_impl`) |

Anything below is net-new.

---

## 2. Phasing — 4 batches, each independently shippable

Order is by **leverage per LOC**, not by the order they appear in the
source proposal. Phase 1 is "biggest impact, smallest blast radius."

### Phase 1 — Batching + cheap graph reads  ☑ ship together

Smallest code, largest reduction in tool rounds. Pure additions over
existing query primitives, no new graph algorithms required.

#### 1.1 `batch_get_nodes`

**Why:** `get_node` is single-shot; multi-symbol questions burn 4–8 rounds.
**Schema:**
```json
{
  "name": "batch_get_nodes",
  "description": "Get full details (source, edges_in, edges_out, metadata) for up to 20 nodes in one call. Use this instead of multiple sequential get_node calls when you need to inspect a cluster of related nodes.",
  "parameters": {
    "type": "object",
    "properties": {
      "node_ids": { "type": "array", "items": { "type": "string" }, "maxItems": 20 },
      "include_source": { "type": "boolean", "default": true },
      "include_edges":  { "type": "boolean", "default": true }
    },
    "required": ["node_ids"]
  }
}
```
**Implementation:** thin loop over the existing `get_node` payload builder
in `apollo/api/...` that powers `/api/node/:id`. Cap at 20 IDs to keep TOON
output bounded. Drop unknown IDs silently with a `missing[]` field so the
LLM doesn't waste a round chasing typos.

**HTTP:** `POST /api/nodes/batch` body `{ "ids": [...] }`.

#### 1.2 `batch_file_sections`

**Why:** Same logic for files. Today the model has to call
`get_file_section` once per range to read the head, the function body, and
the failing test in one file.
**Schema:**
```json
{
  "name": "batch_file_sections",
  "description": "Read multiple line ranges from one or more files in a single call. Each entry returns its own (path, start, end, source).",
  "parameters": {
    "type": "object",
    "properties": {
      "ranges": {
        "type": "array",
        "maxItems": 10,
        "items": {
          "type": "object",
          "properties": {
            "path":  { "type": "string" },
            "start": { "type": "integer" },
            "end":   { "type": "integer" }
          },
          "required": ["path", "start", "end"]
        }
      }
    },
    "required": ["ranges"]
  }
}
```
**Implementation:** loop over existing `get_file_section`. Return one
uniform array → TOON friendly. Hard cap at 10 ranges and a per-range max
of 400 lines (matches current `get_file_section` behaviour).

**HTTP:** `POST /api/files/sections`.

#### 1.3 `get_directory_tree`

**Why:** `project_search` is grep, not `ls -R`. The model currently has
no way to learn the *shape* of an unfamiliar repo.
**Schema:**
```json
{
  "name": "get_directory_tree",
  "description": "Recursive directory listing under the indexed project. Returns a flat array of {path, kind, depth, size_bytes, lang, node_count}. Honours the same plugin ignore lists used by the indexer.",
  "parameters": {
    "type": "object",
    "properties": {
      "root":       { "type": "string", "default": "." },
      "depth":      { "type": "integer", "default": 3, "maximum": 6 },
      "glob":       { "type": "string", "description": "Optional fnmatch pattern, e.g. '*.py'" },
      "include_dirs": { "type": "boolean", "default": true }
    }
  }
}
```
**Implementation:** reuse `/api/tree` (already exists in DESIGN §7.3 row
`/api/tree`). New tool wraps it, flattens the nested response into a
uniform array (`{path,kind,depth,size_bytes,lang,node_count}`) so TOON
collapses it to one header row.

**HTTP:** `/api/tree` already exists; just confirm it accepts `glob` and
`depth` query params. Add them if missing.

#### 1.4 `project_stats_detailed`

**Why:** `get_stats` already returns totals. The model often wants to
know "where is the code actually concentrated?" — top files by LOC, by
function count, by edge degree.
**Schema:**
```json
{
  "name": "project_stats_detailed",
  "description": "Deeper project breakdown: LOC and node counts grouped by directory, top 20 largest files, top 20 most-connected nodes, language breakdown.",
  "parameters": {
    "type": "object",
    "properties": {
      "top_n":  { "type": "integer", "default": 20, "maximum": 50 },
      "group":  { "type": "string", "enum": ["dir", "lang", "ext"], "default": "dir" }
    }
  }
}
```
**Implementation:** pure aggregation over the in-memory graph
(`graph/query.py`). No new state, no new edges.

**HTTP:** `/api/stats/detailed`.

**Phase 1 done-when:**
- [x] 4 tool entries in `ai/chat_request.json`
- [x] 4 handlers in `chat/service.py:_exec_tool_impl`
- [x] 4 endpoints in `web/server.py` (`/api/nodes/batch`, `/api/files/sections`,
      `/api/stats/detailed`; `/api/tree` already existed and is reused)
- [x] System prompt §workflow updated — consolidated cheat-sheet block points
      the model at `batch_get_nodes` / `batch_file_sections` / `get_directory_tree`
- [x] Unit tests in `tests/test_chat_local_tools.py` covering: 20-ID cap,
      missing-ID handling, 10-range cap, glob filter, uniform-shape output

---

### Phase 2 — Real graph algorithms

These add capability the graph store has the data for but no tool exposes.

#### 2.1 `get_paths_between`

**Why:** Single most-asked architecture question — *"how is X connected
to Y?"*. Current workaround is multiple `get_neighbors` calls.
**Schema:**
```json
{
  "name": "get_paths_between",
  "description": "Find paths between two nodes in the knowledge graph. Returns up to `max_paths` paths, each as an ordered list of node IDs and the edge type at each step.",
  "parameters": {
    "type": "object",
    "properties": {
      "start_node_id": { "type": "string" },
      "end_node_id":   { "type": "string" },
      "max_length":    { "type": "integer", "default": 5, "maximum": 8 },
      "max_paths":     { "type": "integer", "default": 5, "maximum": 20 },
      "edge_types":    { "type": "array", "items": { "type": "string" } },
      "shortest_only": { "type": "boolean", "default": false }
    },
    "required": ["start_node_id", "end_node_id"]
  }
}
```
**Implementation:** NetworkX `all_simple_paths` (cap by `max_length` and
`max_paths`) or `shortest_path` when `shortest_only=true`. Filter edges by
`edge_types`. Direction is *undirected* by default (the model usually
just wants to know if there's *any* relationship), with an optional
`direction: in|out|both` knob if needed later.

**HTTP:** `GET /api/paths?start=…&end=…&max_length=…`.

#### 2.2 `get_subgraph`

**Why:** Generalises `get_neighbors` to multiple seed nodes. Useful for
"explain this cluster."
**Schema:**
```json
{
  "name": "get_subgraph",
  "description": "Extract the subgraph induced by `seed_node_ids` plus their `depth` neighbours. Returns nodes[] and edges[].",
  "parameters": {
    "type": "object",
    "properties": {
      "seed_node_ids": { "type": "array", "items": { "type": "string" }, "maxItems": 10 },
      "depth":         { "type": "integer", "default": 1, "maximum": 3 },
      "edge_types":    { "type": "array", "items": { "type": "string" } },
      "max_nodes":     { "type": "integer", "default": 200, "maximum": 500 }
    },
    "required": ["seed_node_ids"]
  }
}
```
**Implementation:** BFS from each seed, union the visited sets, slice
edges by `edge_types`, cap at `max_nodes` (drop the lowest-degree
nodes first to preserve hubs).

**HTTP:** `POST /api/subgraph`.

#### 2.3 `get_inheritance_tree`

**Why:** OOP codebases. Existing `inherits` edge is single-hop only via
`get_neighbors`.
**Schema:**
```json
{
  "name": "get_inheritance_tree",
  "description": "For a class node, returns its full ancestor chain and all descendants reachable via `inherits` edges.",
  "parameters": {
    "type": "object",
    "properties": {
      "class_node_id": { "type": "string" },
      "include_methods": { "type": "boolean", "default": false }
    },
    "required": ["class_node_id"]
  }
}
```
**Implementation:** transitive closure on `inherits` edges (both
directions). When `include_methods=true`, also pull each class's
`defines→method` edges so the model can answer "where is this method
overridden?" in one round.

**HTTP:** `GET /api/inheritance/:class_id`.

#### 2.4 `get_transitive_imports`

**Why:** Answers *"what will break if I change this module?"* in a
single call instead of recursive `get_neighbors`.
**Schema:**
```json
{
  "name": "get_transitive_imports",
  "description": "For a file or module node, returns the full transitive import set in either direction. `direction='out'` = what this file depends on; `direction='in'` = what depends on this file.",
  "parameters": {
    "type": "object",
    "properties": {
      "file_node_id": { "type": "string" },
      "direction":    { "type": "string", "enum": ["in", "out", "both"], "default": "in" },
      "max_depth":    { "type": "integer", "default": 5 }
    },
    "required": ["file_node_id"]
  }
}
```
**Implementation:** BFS on `imports` edges, dedupe, return one flat
array (TOON friendly).

**HTTP:** `GET /api/imports/:file_id?direction=…`.

**Phase 2 done-when:**
- [x] 4 tool entries + handlers + endpoints (`/api/paths`, `/api/subgraph`,
      `/api/inheritance/{class_id}`, `/api/imports/{file_id}`)
- [x] System prompt §workflow cheat-sheet points to `get_paths_between`,
      `get_transitive_imports`, and `get_inheritance_tree`
- [x] Tests in `tests/test_chat_local_tools.py` cover paths, subgraph,
      inheritance, transitive imports (8 tests across phase 2)
- [x] `max_*` caps in place: `max_length≤8`, `max_paths≤20`, `max_nodes≤500`,
      `max_depth≤10` — enforced inside helpers, not just the schema

---

### Phase 3 — Cheap signal already in the index

These tools surface metadata the indexer already records (DESIGN §6
Phase 6 — `complexity`, `loc`, `signature_hash`, `is_test`, `tests` edges,
`patterns` on file nodes) but no tool exposes today.

#### 3.1 `get_code_metrics`

**Why:** Cyclomatic complexity, LOC, signature hash are all already on
node payloads (DESIGN §10 Phase 6). Right now the model can only see
them by calling `get_node` per-function.
**Schema:**
```json
{
  "name": "get_code_metrics",
  "description": "Return code metrics (LOC, cyclomatic complexity, parameter count, signature hash) for one or more functions/methods/files. If no node_ids given, returns the top `top_n` most complex nodes project-wide.",
  "parameters": {
    "type": "object",
    "properties": {
      "node_ids": { "type": "array", "items": { "type": "string" }, "maxItems": 50 },
      "top_n":    { "type": "integer", "default": 20, "maximum": 100 },
      "sort_by":  { "type": "string", "enum": ["complexity", "loc", "param_count"], "default": "complexity" }
    }
  }
}
```
**Implementation:** scan node payloads in `graph/query.py`; no recompute
needed.

**HTTP:** `GET /api/metrics?sort=complexity&top=20`.

#### 3.2 `search_graph_by_signature`

**Why:** Finds *all* functions that take, e.g., `(user_id: str, …)`.
The `signature_hash` field is already computed (DESIGN §6 Phase 6).
**Schema:**
```json
{
  "name": "search_graph_by_signature",
  "description": "Find functions/methods whose parameter list matches a pattern. Either pass `param_names` (ordered list of names) or `signature_hash` (exact match).",
  "parameters": {
    "type": "object",
    "properties": {
      "param_names":      { "type": "array", "items": { "type": "string" } },
      "param_annotations":{ "type": "array", "items": { "type": "string" } },
      "signature_hash":   { "type": "string" },
      "top": { "type": "integer", "default": 20, "maximum": 50 }
    }
  }
}
```
**Implementation:** linear scan over function nodes in `graph/query.py`.
For `param_names`, exact ordered match by default; allow
`?fuzzy=true` for "starts with these N names." Cheap on a 10K-node graph,
no index needed.

#### 3.3 `find_test_correspondents`

**Why:** `tests` edges already exist (DESIGN §6 Phase 6). Today no
tool follows them.
**Schema:**
```json
{
  "name": "find_test_correspondents",
  "description": "Given a function/class/method node, return likely test functions covering it: explicit `tests` edges first, then heuristic matches (`test_<name>`, `Test<Name>`, decorator parametrize).",
  "parameters": {
    "type": "object",
    "properties": {
      "node_id": { "type": "string" },
      "include_heuristic": { "type": "boolean", "default": true }
    },
    "required": ["node_id"]
  }
}
```
**Implementation:** (1) follow `tests` edges; (2) fall back to name
heuristic over nodes with `is_test=true`.

#### 3.4 `detect_entry_points`

**Why:** "Where does this app start?" is the first question for an
unfamiliar repo. Cheap to compute, never asked by the existing tools.
**Schema:**
```json
{
  "name": "detect_entry_points",
  "description": "Find probable entry points: `if __name__ == '__main__'`, FastAPI/Flask/Django routes, Click/Typer CLI commands, pytest fixtures, top-level scripts. Returns kind + path + line.",
  "parameters": {
    "type": "object",
    "properties": {
      "kinds": { "type": "array", "items": { "type": "string" },
                 "description": "Optional filter, e.g. ['cli','http_route','main']" }
    }
  }
}
```
**Implementation:** scan function nodes for:
- `name == "__main__"` markers,
- decorator names matching `@app.get|post|route|FastAPI|click.command|typer.…`,
- file paths matching `main.py` / `cli.py` / `manage.py`,
- existing `patterns` field on file nodes (already populated in Phase 6).

Pure scan over node payloads, no new edges.

**Phase 3 done-when:**
- [x] 4 tool entries + handlers + endpoints (`/api/metrics`,
      `/api/signature/search`, `/api/tests/{node_id}`, `/api/entry-points`)
- [x] Synthetic-graph tests cover `detect_entry_points` (`main_block`
      pattern → `kind=main`), test correspondents (heuristic match),
      signature search (hash + ordered names), and metrics ranking.
      *Note:* dogfood-against-Apollo integration test still TODO —
      requires a built index fixture.
- [x] System prompt §workflow gained the "first contact with an unknown
      repo" cheat-sheet:
      `detect_entry_points` → `get_directory_tree` → `get_wordcloud(strong)`
      → drill in.

---

### Phase 4 — Git + notes full-text + nice-to-haves

Data sources outside the existing graph index. Done last because each
needs new plumbing rather than just exposing existing data.

#### 4.1 `get_git_context`

**Why:** "Who changed this and why?" comes up in nearly every real
debugging session.
**Schema:**
```json
{
  "name": "get_git_context",
  "description": "Git blame + recent commits for a file (optionally narrowed to a function name or line range). Returns commits[] and (if line range given) blame[] entries with author, date, sha, summary.",
  "parameters": {
    "type": "object",
    "properties": {
      "path":       { "type": "string" },
      "name":       { "type": "string", "description": "Optional function/class name; resolves to its line range via the graph." },
      "line_start": { "type": "integer" },
      "line_end":   { "type": "integer" },
      "limit":      { "type": "integer", "default": 10, "maximum": 30 }
    },
    "required": ["path"]
  }
}
```
**Implementation:**
1. Shell out to `git -C <repo> log --pretty=...` and `git blame -L`.
   Use `subprocess.run` with `check=False`, `timeout=5` — never raise
   into the chat stream; on failure return `{"git_available": false}`.
2. If `name` given, resolve to line range via existing function-node
   metadata first, then call `git blame -L`.
3. **Skip silently** if the indexed root is not a git repo (don't
   register the tool? Or register but return `git_available=false`?
   — pick the latter so the model gets a clean negative answer instead
   of an unknown-tool error).

**HTTP:** `GET /api/git/blame?path=...&line_start=...&line_end=...`.

#### 4.2 `search_notes_fulltext`

**Why:** Right now `list_notes` only filters by tag/target/type. The
user can't ask "which notes mention couchbase?"
**Schema:**
```json
{
  "name": "search_notes_fulltext",
  "description": "Full-text search across all user notes, bookmarks, and highlights. Returns id, type, target, content, tags, score.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": { "type": "string" },
      "type":  { "type": "string", "enum": ["note", "bookmark", "highlight"] },
      "top":   { "type": "integer", "default": 10, "maximum": 50 }
    },
    "required": ["query"]
  }
}
```
**Implementation:** simple substring/token match over the notes store
in `apollo/api/...`. If notes already live in Couchbase Lite (DESIGN
§5), use the existing FTS-style index; otherwise scan in Python — note
volume is small.

#### 4.3 Nice-to-have hold list (defer until a real use case shows up)

These were mentioned in the proposal but should NOT be built speculatively:

- `find_graph_paths` with weighted edges — wait until users ask for it
- `search_graph_by_signature` with annotation matching — already in 3.2
  scope
- A `web_fetch` tool — explicitly out of scope (chat is local-first)

**Phase 4 done-when:**
- [x] 2 new tools registered (`get_git_context` + `search_notes_fulltext`).
      `get_git_context` is also exposed at `GET /api/git/blame`.
      `search_notes_fulltext` ships as an AI-only tool — no HTTP endpoint
      added since the existing `/api/annotations*` surface already covers
      the human-facing UI for notes.
- [x] `get_git_context` returns `{git_available: false}` cleanly on
      non-git roots and on `git` binary missing — covered by
      `test_get_git_context_no_repo` and the silent-degrade branch in
      `_run`. A real-repo round-trip test
      (`test_get_git_context_real_repo`) `git init`s a tmp dir and asserts
      the `init` commit is returned.
- [x] System prompt cheat-sheet now lists `get_git_context` for
      "Who changed this?" and `search_notes_fulltext` for
      "Notes mentioning X?", and the notes workflow explicitly says
      *"For keyword queries, prefer `search_notes_fulltext` over `list_notes`."*

---

## 3. Cross-cutting concerns

### 3.1 TOON shape audit

Every new tool returns either:

- A uniform array of objects (the TOON sweet spot — header-once, CSV rows), or
- A single-key object containing such an array.

**No nested heterogeneous payloads.** If a tool naturally returns mixed
shapes (e.g. `get_git_context` returning both `commits[]` and `blame[]`),
make it return a top-level object with two homogeneous arrays so each
collapses cleanly.

### 3.2 Round-budget impact

Phase 1 alone should cut average rounds-per-question by **30–50 %**:

| Before                                          | After                              |
|------------------------------------------------|-------------------------------------|
| 5× sequential `get_node`                       | 1× `batch_get_nodes`                |
| 3× `get_file_section` for head/body/test       | 1× `batch_file_sections`            |
| 2× `project_search` to learn folder shape      | 1× `get_directory_tree`             |

We should keep the **3-round cap** in the system prompt — the goal is
never "more rounds," it's "more answered per round."

### 3.3 System prompt updates (single coordinated edit)

After all four phases land, do **one** edit to
[`ai/chat_request.json`](../../ai/chat_request.json) `messages[0].content`
adding a consolidated workflow block:

```
Tool selection cheat-sheet:
- Multiple nodes?            → batch_get_nodes
- Multiple file ranges?      → batch_file_sections
- Folder shape?              → get_directory_tree
- "Connected to?"            → get_paths_between
- "What would break?"        → get_transitive_imports / get_inheritance_tree
- "Who changed this?"        → get_git_context
- "Where does this start?"   → detect_entry_points
- "Most complex code?"       → get_code_metrics
- "Tests for X?"             → find_test_correspondents
- "Notes mentioning X?"      → search_notes_fulltext
```

Keep the new block **short** — the system prompt is already long and
every token is paid on every turn.

### 3.4 Backwards compatibility

- Don't rename or change existing tools.
- Preserve `chat_request_v1.json` rollback file (already convention).
- New endpoints under `/api/...` are additive.

### 3.5 Tests + docs (every phase)

Each phase ships with:

1. Unit tests under `tests/chat/test_tools_<phase>.py` and
   `tests/api/test_<endpoint>.py`.
2. A short note appended to [`docs/DESIGN.md §8`](../DESIGN.md) listing
   the new tools (one-liner each).
3. An entry in this file's Status header
   (`Status: Phase N done` like `PLAN_PLUGIN_CONFIGS.md` does).

---

## 4. Sequencing summary

| Phase | Tools                                                                                  | Risk   | Est. PRs |
|-------|----------------------------------------------------------------------------------------|--------|----------|
| 1     | `batch_get_nodes`, `batch_file_sections`, `get_directory_tree`, `project_stats_detailed` | low    | 1        |
| 2     | `get_paths_between`, `get_subgraph`, `get_inheritance_tree`, `get_transitive_imports`   | medium | 1        |
| 3     | `get_code_metrics`, `search_graph_by_signature`, `find_test_correspondents`, `detect_entry_points` | low | 1 |
| 4     | `get_git_context`, `search_notes_fulltext`                                              | medium | 1        |

Ship phase 1 first — it pays for itself the moment the model picks up
`batch_get_nodes` because every multi-symbol question gets cheaper.

---

## 5. Resolved design decisions (with worked examples)

The four open questions from an earlier draft have been resolved.
The reasoning is captured here so future contributors don't re-litigate
them. Each example shows the brute-force `grep` baseline next to the
new-tool path so the leverage is concrete.

### 5.1 `get_git_context` — ship **both** modes, parameter-controlled

> User: *"Why does `emails()` use `bcc` instead of `to`?"*

| Path                     | Steps                                                                                                |
|--------------------------|------------------------------------------------------------------------------------------------------|
| **`grep` baseline**      | Ask user to paste `git log`; or grep for `bcc` and read every match.                                  |
| **`get_git_context`**    | `search_graph "emails"` → resolves `func::src/mailer.py::emails` (lines 10–25). One `get_git_context(path="src/mailer.py", name="emails")` returns the 3 commits touching that range, including `"fix(privacy): use bcc to hide recipient list — closes #412"`. Answer cites commit + PR in 1 round. |

**Why both modes:** "Who owns this file?" wants log-only (cheap).
"Why is this *line* weird?" wants blame on a 5-line range. One
parameter on the same tool serves both — forcing one mode pessimises
the other.

### 5.2 `get_directory_tree` — **honour the plugin ignore list by default**

> User: *"What's the layout of this project?"*

| Path                       | Steps                                                                                                |
|----------------------------|------------------------------------------------------------------------------------------------------|
| **`grep` baseline**        | `project_search "*.py"` returns matches inside `htmlcov/`, `venv/`, `__pycache__/`, `target/`, `_dev_only/`. Half the context is HTML coverage reports. |
| **`get_directory_tree`**   | One call returns ~80 real entries instead of the ~12 000 hiding under ignored dirs. Clean answer in round 1. |

**Why honour ignores by default:** the ignore list (PLAN_PLUGIN_CONFIGS.md)
is *already* the user's curated definition of "what counts as my
project." The HTTP `/api/tree` endpoint already behaves this way — same
posture for the AI keeps the human UI and the model in sync. An
opt-out `?include_ignored=true` knob is cheap to add for the rare audit
case.

### 5.3 `detect_entry_points` — **Python-only first**, plugin-extensible later

> User: *"How do I run this thing?"*

| Path                          | Steps                                                                                                |
|-------------------------------|------------------------------------------------------------------------------------------------------|
| **`grep` baseline**           | 4 separate searches: `if __name__`, `@app.route`, `FastAPI(`, `click.command`. Each leaks matches from `.venv/site-packages/...` because grep doesn't know what's user code. |
| **`detect_entry_points`**     | One call returns:<br>`main.py:42  kind=main`<br>`apollo/api/server.py:15  kind=fastapi_app`<br>`apollo/api/routes/chat.py:23  kind=http_route POST /api/chat`<br>`watcher.py:8  kind=cli typer`<br>Model answers "run `python main.py` or `uvicorn apollo.api.server:app`" in round 1 with line citations. |

**Why Python-only first:** matches current parser maturity. JS/TS,
Rust, Docker entry-point detection extends the *same* tool when those
plugins land (per PLUGINS_CHECKLIST.md). Per-language heuristics live
inside the plugin, not in this tool.

### 5.4 `search_graph_by_signature` — **ship now**

> User: *"I'm reordering the args of `charge(user_id, amount)` —
> what else has that signature?"*

| Path                              | Steps                                                                                            |
|-----------------------------------|--------------------------------------------------------------------------------------------------|
| **`grep` baseline**               | `grep -rn "def .*user_id.*amount" .` — misses kwargs, misses defaults, hits docstrings, hits string literals. Model has to read every match to confirm. **Categorically wrong answer set.** |
| **`search_graph_by_signature`**   | One call with `param_names=["user_id","amount"]` returns the 7 real functions across the repo with that ordered param list. Model answers in round 1 with a precise blast-radius list. |

**Why now:** `signature_hash` and the resolved param list are already
on every function node from Phase 6 indexing. The tool is ~30 LOC and
the alternative (`grep`) cannot answer this question correctly at all.

---

## 6. Take-away

The whole catalog above shares one shape: **the indexer already did the
hard work; these tools just expose it.** Wherever the model would
otherwise reach for `project_search` and burn rounds disambiguating
text, a graph-shaped tool returns the resolved fact in one call. That
is the only justification needed to add each one — and the only test
that should be applied to any future tool proposal.

---

## 7. Completion log

### 7.1 Where each piece landed

| Concern | File |
|--------|------|
| All 14 tool helpers (uniform-shape, TOON-friendly) | [`chat/local_tools.py`](../../chat/local_tools.py) |
| Tool dispatch + system prompt cheat-sheet | [`chat/service.py`](../../chat/service.py), [`ai/chat_request.json`](../../ai/chat_request.json) |
| 12 HTTP endpoints (`/api/nodes/batch`, `/api/files/sections`, `/api/stats/detailed`, `/api/paths`, `/api/subgraph`, `/api/inheritance/{id}`, `/api/imports/{id}`, `/api/metrics`, `/api/signature/search`, `/api/tests/{id}`, `/api/entry-points`, `/api/git/blame`) | [`web/server.py`](../../web/server.py) |
| Unit tests (30 tests, all passing; full suite stays green at 632 + 1 skipped) | [`tests/test_chat_local_tools.py`](../../tests/test_chat_local_tools.py) |
| OpenAPI paths + 14 reusable schemas + new `Git` tag | [`docs/openapi.yaml`](../openapi.yaml) |
| Markdown reference (12 endpoint sections under "Local AI Tools") | [`docs/API.md`](../API.md) |
| Architectural overview + cross-cutting contracts | [`docs/DESIGN.md` §8.9](../DESIGN.md) |
| Tag-table update for the new `Git` tag | [`guides/API_OPENAPI.md`](../../guides/API_OPENAPI.md) |

### 7.2 Doc-followup (per `guides/`)

After landing the code, all three doc guides were applied:

1. **`guides/API_OPENAPI.md`** — every new endpoint has a unique
   `operationId`, the correct tag, success response, and `$ref`'d
   error responses (`BadRequest`, `NotFound`); the `Git` tag was added
   to both `tags:` in `openapi.yaml` and the table in
   `guides/API_OPENAPI.md`.
2. **`guides/SCHEMA_DESIGN.md`** — 14 new reusable response schemas
   under `components/schemas/` (`BatchNodesResult`, `BatchFileSectionsResult`,
   `DetailedStats`, `PathsResult`, `SubgraphResult`, `InheritanceTreeResult` /
   `InheritanceTreeNode`, `TransitiveImportsResult`, `CodeMetricsRow` /
   `CodeMetricsResult`, `SignatureSearchResult`, `TestCorrespondentsResult` /
   `TestCorrespondent`, `EntryPointsResult`, `GitContextResult`). Nullable
   line numbers use the `["integer", "null"]` type-array idiom from the
   guide. No new persisted-document schemas — these are HTTP response
   shapes only — so the `schema/` Quick Reference table is unchanged.
3. **`guides/LOGGING.md`** — `chat/local_tools.py` uses
   `logger = logging.getLogger(__name__)` at the top of the module;
   `get_git_context._run` debug-logs subprocess failures (so operators
   can tell "git missing" from "git timeout"); `batch_file_sections`
   uses `logger.exception(...)` in its defensive catch-all. No `print()`.

### 7.3 Known follow-ups (not blocking)

- **Dogfood integration test for `detect_entry_points`** against a
  built Apollo index (caught by Phase 3's done-when item but punted —
  needs a fresh-index fixture).
- **`/api/tree` query params** (`depth`, `glob`) — the AI tool
  `get_directory_tree` honours them via the in-memory helper, but the
  existing HTTP endpoint still returns the full tree. Add the params
  if/when a UI consumer asks for them.

---

## 8. Phase 5 — File-shaped tools that *match how users phrase questions*

**Status:** ✅ Shipped (see §8.12 *Completion log*) — code, tool schema, HTTP endpoints, and tests all landed; benchmark re-run is the only remaining done-when item.
**Driving evidence:** [`BENCHMARK_ROUND_REDUCTION.md`](./BENCHMARK_ROUND_REDUCTION.md)
(3 recorded traces of the same question)
**Sibling plan:** [`PLAN_LLM_ROUND_REDUCTION.md`](./PLAN_LLM_ROUND_REDUCTION.md)
(payload-compaction work — pairs with this phase)

### 8.1 What three traces taught us about tool selection

The benchmark question — *"in folder/file en/index.html, what
function(s) is/are used to cache processed data?"* — was run three
times. All three runs produced the same broad failure mode: round 0
issued a fat regex via `file_search` (144–239 KB payloads), round 1
drilled into the line ranges those hits surfaced, round 2 burned 4–11
seconds composing the answer. Total wall time: 15.6–20.9 s. Final
answer was correct each time, but the round shape never improved.

**The model isn't misbehaving — it's making locally-rational choices
given the catalog it has.** It picks a tool by ranking signals roughly
in this order:

```diagram
What drives LLM tool choice (strongest → weakest):
╭─────────────────────────────────────────────────────────────╮
│ 1. Tool NAME shape match            ████████████████  ~40%  │
│ 2. Tool DESCRIPTION first sentence  █████████████     ~30%  │
│ 3. Parameter shape match            ███████           ~15%  │
│ 4. System prompt cheat-sheet        ████              ~10%  │
│ 5. Generic prompt rules             ██                ~5%   │
╰─────────────────────────────────────────────────────────────╯
```

Cheat-sheet tweaks (rank #4) cannot beat catalog gaps (rank #1–2). The
existing prompt already nudges the model toward `search_graph_multi`
and away from sequential `search_graph` calls; Run #2 still issued
separate `file_search` + `project_search` + `search_graph` because no
existing tool's *name and first sentence* matched the question shape
*"where is X declared/used in file Y"* better than `file_search`'s
"Grep within a single file."

### 8.2 The diagnosis — catalog gap, not prompt gap

Running the model's tool-pick ranking against the question shape:

| Tool                  | Description (first sentence)                                | Match score for "where is X cached in file Y" |
|-----------------------|-------------------------------------------------------------|-----------------------------------------------|
| `file_search`         | *"Grep within a single file."*                              | ⭐ best fit — single file ✓ + search ✓        |
| `project_search`      | *"Grep across the indexed project."*                        | ⭐ also fits — same shape, wider net          |
| `search_graph`        | *"Search the knowledge graph by keyword."*                  | partial fit — semantic, but file-agnostic    |
| `get_function_source` | requires a function name (model doesn't have one yet)       | not yet — bootstrap problem                  |
| `get_file_section`    | requires line numbers (model doesn't have them yet)         | not yet — bootstrap problem                  |
| **(missing)** `list_declarations`     | *"List every declaration in a file with line ranges."*           | ⭐⭐ **direct match** |
| **(missing)** `find_symbol_usages`    | *"Find every reference to a symbol in a file, classified."*     | ⭐⭐⭐ **exact match** |
| **(missing)** `outline_file`          | *"Return the structural outline of a file."*                    | ⭐⭐ **first-contact match** |

The three missing tools each match a question shape the model
currently has to assemble from regex + context-window reads. Adding
them moves the rational pick from `file_search` to one tool that
returns a resolved fact in round 0.

### 8.3 Three new tools (specs)

All three follow the constraints from §0: read-only, TOON-friendly
uniform-array shape, hard caps, single-round answer, paired HTTP
endpoint.

#### 8.3.1 `list_declarations`

**Question it answers:** *"What's declared in this file?"* — the
file-shaped analogue of `get_directory_tree`.

```json
{ "name": "list_declarations",
  "description": "List every top-level declaration in a file: functions, classes, methods, and module/file-level identifiers (const/let/var/def, including new Map() / WeakMap() / Set() / WeakSet() bindings). Returns name, kind, line range, export status, and parent. Use this INSTEAD of file_search with a regex like `function\\|class\\|def` — it is exact, AST-aware, and ~100× smaller.",
  "parameters": { "properties": {
    "path": { "type": "string" },
    "kinds": { "type": "array", "items": { "type": "string",
      "enum": ["function","class","method","const","let","var","def",
               "map_decl","weakmap_decl","set_decl","weakset_decl"] } },
    "limit": { "type": "integer", "default": 200 }
  }, "required": ["path"] } }
```

**TOON shape:**

```
declarations[N,]{name,kind,line_start,line_end,is_exported,parent}:
  parseTimeCache,map_decl,3225,3225,false,
  normalizeStatementCache,map_decl,3226,3226,false,
  clearCaches,function,3237,3249,true,
  logCacheStats,function,3251,3258,true,
  getOperators,function,3260,3320,true,
```

**HTTP parity:** `GET /api/files/declarations?path=…&kinds=…&limit=…`

**Implementation:** Read declarations from the parser's already-built
graph payload when available (Python AST, Markdown headings,
tree-sitter `function_declaration` / `class_declaration` /
`lexical_declaration`). For files indexed by `text_parser` fallback,
run a single regex pass with the Phase 1 classifier from
[`PLAN_LLM_ROUND_REDUCTION.md`](./PLAN_LLM_ROUND_REDUCTION.md) §1.3 and
flag `accuracy: "regex"` on the response.

#### 8.3.2 `find_symbol_usages`

**Question it answers:** *"Where is symbol X declared, read, written,
or called inside file Y?"* — the file-shaped analogue of
`get_neighbors` for graph nodes.

```json
{ "name": "find_symbol_usages",
  "description": "Find every line in a file that references a given symbol, classified as declaration / read / write / call / comment / string. Returns line numbers + a single trimmed line per hit, no surrounding context. Use this INSTEAD of file_search when you already know the symbol name — it returns ~10× less data and tells you what each line is doing.",
  "parameters": { "properties": {
    "path":   { "type": "string" },
    "symbol": { "type": "string" },
    "kinds":  { "type": "array", "items": { "type": "string",
      "enum": ["declaration","read","write","call","comment","string"] } }
  }, "required": ["path","symbol"] } }
```

**TOON shape:**

```
usages[N,]{line_no,kind,text}:
  3225,declaration,"const parseTimeCache = new Map();"
  3009,read,"if (parseTimeCache.has(s)) return parseTimeCache.get(s);"
  3015,write,"parseTimeCache.set(s, ms);"
  3239,call,"parseTimeCache.clear();"
```

**HTTP parity:** `GET /api/files/usages?path=…&symbol=…&kinds=…`

**Implementation:** AST-aware where the parser supports identifier
resolution (Python, JS via tree-sitter); falls back to text-search
with the Phase 1 classifier and `accuracy: "text"` on the response.
The classifier itself is shared code (`file_inspect/_classify_hit`)
re-used across `file_search`, `project_search`, and this tool.

#### 8.3.3 `outline_file`

**Question it answers:** *"What's in this file?"* — sub-second
first-contact look at any single file, regardless of language.

```json
{ "name": "outline_file",
  "description": "Sub-second outline of a file. Source files: top-level declaration tree (kinds + line ranges). Markdown: heading tree. HTML: tag tree (head/body/script). Use this on first contact with any unfamiliar file BEFORE reaching for file_search or get_file_section.",
  "parameters": { "properties": {
    "path":  { "type": "string" },
    "depth": { "type": "integer", "default": 2 }
  }, "required": ["path"] } }
```

**TOON shape:** uniform `outline[N,]{kind,name,line_start,line_end,depth}:`

**HTTP parity:** `GET /api/files/outline?path=…&depth=…`

**Implementation:** Pure read off the parser-produced graph payload —
no new parsing pass. For `text_parser` fallback files, return
`outline: []` and `accuracy: "none"` rather than guessing.

### 8.4 Existing-tool description rewrites (rank #2 lever)

New tools alone aren't enough — the model also needs the existing
tools to look *less* attractive for question shapes they shouldn't
win. These are zero-risk, prompt-only edits to
[`ai/chat_request.json`](../../ai/chat_request.json):

| Tool             | Today's first sentence                              | Proposed first sentence                                                                                                  |
|------------------|-----------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| `file_search`    | *"Grep within a single file."*                      | *"Regex grep within a single file. Use only when you DON'T know a symbol name. If you have a symbol, use `find_symbol_usages` instead. If you want a structural map, use `outline_file` or `list_declarations`."* |
| `project_search` | *"Grep across the indexed project."*                | *"Regex grep across the project. Last-resort tool — try `search_graph_multi`, `find_symbol_usages`, or `list_declarations` first."* |
| `search_graph`   | *"Search the knowledge graph by keyword or semantic similarity."* | *"Search across files for symbols by name or semantic similarity. For 'where is X used in file Y', use `find_symbol_usages` instead."* |

This is the cheapest change in the plan and pays back at signal-rank
#2 — measurably stronger than any cheat-sheet edit at #4.

### 8.5 Cheat-sheet entries (rank #4 — last-mile, not the lever)

To be added at the end of the system prompt cheat-sheet block:

```
- 'What's in file X?'                        → outline_file (NOT file_search)
- 'Where is Y declared in file X?'           → list_declarations (NOT file_search)
- 'Where is Y used inside file X?'           → find_symbol_usages (NOT file_search)
- file_search is for LAST RESORT only — when no symbol name is known
  AND the file isn't outlineable. Default context to 2 lines, never 10.
```

### 8.6 Why this beats the alternatives we considered

| Alternative                                            | Why we rejected it                                                                                       |
|--------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| Add stronger cheat-sheet rules only                    | Already tried — Run #2 ignored existing rules. Rank #4 < rank #1.                                         |
| Cap `file_search` byte size aggressively (Phase 1 of sibling plan) | Necessary, but doesn't change *which* tool gets picked — the model just gets a truncated regex result and follows up. |
| Make `file_search` fail loudly above N matches         | Hostile UX — the human-facing API uses the same function via `/api/file/search`.                          |
| Rename `file_search` → `regex_grep_in_file`            | Breaking change for plugins / API consumers; the description rewrite gets ~80% of the effect.             |
| Add yet another regex variant tool                     | Adds catalog noise without solving the resolution-vs-text problem the new tools are designed for.         |

### 8.7 Round-budget impact (projected against the benchmark)

| Question                                                    | Today (3 runs avg) | After Phase 5 |
|-------------------------------------------------------------|--------------------|---------------|
| "Where is X cached/declared in file Y?"                     | 3 rounds, ~17 s    | 1 round, < 5 s |
| "What's in this file?"                                      | 2–3 rounds         | 1 round       |
| "Where is symbol X used inside file Y?"                     | 2–3 rounds         | 1 round       |
| `return_result.node_refs` (graph IDs cited in answer)       | 0 (text hits only) | ≥ 5 (resolved) |

### 8.8 TOON shape audit

All three new tools return single uniform-shape arrays
(`declarations[]`, `usages[]`, `outline[]`) — exactly the pattern the
TOON encoder collapses best. Expected byte savings ≥ 30 %, matching
`search_graph_multi` and `get_neighbors`.

### 8.9 Sequencing — ship in this order

| Step | Scope                                                                          | Risk   | Wall to ship |
|------|--------------------------------------------------------------------------------|--------|--------------|
| 1    | **Description rewrites** for `file_search` / `project_search` / `search_graph` (8.4) | none   | 30 min       |
| 2    | **Cheat-sheet entries** (8.5)                                                  | none   | 15 min       |
| 3    | **`outline_file`** — pure read off existing graph payload                      | low    | 1 PR          |
| 4    | **`list_declarations`** — same source, plus map/weakmap regex for fallback    | low    | 1 PR          |
| 5    | **`find_symbol_usages`** — shared classifier with sibling-plan Phase 1        | medium | 1 PR          |
| 6    | Re-run benchmark, paste trace into AFTER section of `BENCHMARK_*.md`           | none   | 5 min         |

Steps 1–2 are "free" — they ship as a single config edit and can be
A/B-tested against the benchmark before any code lands. If the prompt
edits alone close the gap to ~10 s / 1 round, that's a strong signal
the catalog gap is the dominant problem and the new tools (steps 3–5)
will close the rest.

### 8.10 Done-when

- [x] `ai/chat_request.json` description rewrites + cheat-sheet entries shipped
- [x] `outline_file`, `list_declarations`, `find_symbol_usages`
      registered in tool schema + `chat/service.py::_exec_tool_impl`
- [x] Three matching `/api/files/...` HTTP endpoints with OpenAPI
      entries (`docs/openapi.yaml`, `docs/API.md`, `docs/DESIGN.md` §8.9)
- [x] Shared classifier extracted to `file_inspect/_classify_hit`
      (used by Phase 1 of sibling plan + `find_symbol_usages`)
- [ ] Benchmark question re-run; trace pasted into
      [`BENCHMARK_ROUND_REDUCTION.md`](./BENCHMARK_ROUND_REDUCTION.md)
      § *After Phase 4 / Phase 5* — **deferred** (requires a live LLM run)
- [ ] Target met: 1 round, < 5 s total, `node_refs ≥ 5`,
      confidence=high, answer parity with the BEFORE runs — **deferred**
- [x] No regression on existing tool tests; existing `file_search` /
      `project_search` HTTP endpoints unchanged
      (full suite: **1057 passed, 1 skipped**)

### 8.11 Take-away (one sentence)

The model's tool-selection problem is **not a discipline problem**
(prompt rules can't fix it) and **not a documentation problem**
(cheat-sheet edits are weak) — it is a **catalog-shape problem**
solved by adding three tools whose names and first-sentence
descriptions exactly match the noun phrases users put in their
questions, then re-pointing the existing grep tools at last-resort
status.

### 8.12 Completion log

**Where each piece landed**

| Concern | File |
|---------|------|
| Three new tool helpers (`outline_file`, `list_declarations`, `find_symbol_usages`) | [`chat/local_tools.py`](../../chat/local_tools.py) (new §"Phase 8 (§8.3)" block) |
| Shared text classifier (`_classify_hit`) | [`file_inspect.py`](../../file_inspect.py) |
| Tool dispatch wiring | [`chat/service.py::_exec_tool_impl`](../../chat/service.py) |
| Tool schema (3 new entries) + description rewrites for `search_graph` / `file_search` / `project_search` + cheat-sheet entries | [`ai/chat_request.json`](../../ai/chat_request.json) |
| Three HTTP endpoints (`GET /api/files/declarations`, `GET /api/files/usages`, `GET /api/files/outline`) | [`web/server.py`](../../web/server.py) |
| OpenAPI paths + 3 reusable response schemas (`ListDeclarationsResult`, `FindSymbolUsagesResult`, `OutlineFileResult`) | [`docs/openapi.yaml`](../openapi.yaml) |
| Markdown reference (3 new endpoint sections under "Files") | [`docs/API.md`](../API.md) |
| Architectural overview row (Phase 8 entries in the catalog table) + new "Shared text classifier" cross-cutting contract | [`docs/DESIGN.md` §8.9](../DESIGN.md) |
| Unit tests for the three tools + classifier (7 new) | [`tests/test_chat_local_tools.py`](../../tests/test_chat_local_tools.py) |
| HTTP-route tests for the three endpoints (4 new) | [`tests/test_file_routes.py`](../../tests/test_file_routes.py) |

**Highest-leverage rank #1–2 changes (catalog shape)**

- Three tool **names** ship with the exact noun phrases users use
  (*"outline a file"*, *"list declarations"*, *"find symbol usages"*).
- Each tool's **first sentence** opens with what the tool returns and
  closes with an explicit *"Use this INSTEAD of file_search"* nudge.
- `file_search` / `project_search` first sentences were rewritten to
  the **last-resort** framing called for in §8.4 — the grep tools no
  longer look like attractive picks for "where is X in file Y".

**Schema enums (mirrors `chat_request.json`)**

- `list_declarations.kinds`: `function | class | method | const | let |
  var | def | map_decl | weakmap_decl | set_decl | weakset_decl`
- `find_symbol_usages.kinds`: `declaration | read | write | call |
  comment | string`

**Implementation notes**

- `outline_file` is a pure read off the parser-built `defines` edges
  — no new parsing pass; for files outside the graph (text_parser
  fallback) it returns `outline: []` and `accuracy: "none"` rather than
  guessing, exactly per §8.3.3.
- `list_declarations` reads from the graph first; runs a single regex
  pass for `Map`/`WeakMap`/`Set`/`WeakSet` bindings (which the parser
  does NOT distinguish from generic `var/let/const`), and falls back
  to a full-file regex pass when the file has no graph entry. Sets
  `accuracy: "ast" | "regex" | "graph_only"` so the LLM can tell.
- `find_symbol_usages` shares `file_inspect._classify_hit` with the
  Phase 1 work in [`PLAN_LLM_ROUND_REDUCTION.md`](./PLAN_LLM_ROUND_REDUCTION.md)
  — single source of truth for what counts as a declaration / read /
  write / call / comment / string.

**Verification**

- `tests/test_chat_local_tools.py` — 37 passed (7 new for Phase 8).
- `tests/test_file_routes.py` — 16 passed (4 new for Phase 8).
- Full suite — **1057 passed, 1 skipped, 0 failed**.

**Known follow-ups (not blocking)**

- **Benchmark re-run** — needs a live LLM round and a UI. The
  expected outcome (1 round, <5 s, `node_refs ≥ 5`, confidence=high)
  is wired up; just needs to be exercised against the recorded BEFORE
  traces in [`BENCHMARK_ROUND_REDUCTION.md`](./BENCHMARK_ROUND_REDUCTION.md).
- **AST-resolved `find_symbol_usages`** — the heuristic classifier is
  good enough to win the benchmark, but a future pass could lift
  accuracy to `"ast"` for Python (and tree-sitter languages) by
  walking the parser's identifier index instead of regex. Defer until
  a real failure shows up.

### 8.13 Post-ship benchmark regression + tightening

**Trace (xai/grok-4-1-fast-non-reasoning, recorded after §8.10 shipped):**

```
round 0  → file_search(cache|Cache|...)  +  file_stats  +  search_graph  +  project_search
round 1  → 3× get_file_section            +  search_graph
round 2  → return_result                  (3032 B · 18.1 s)
```

Same 3-round / ~18 s shape as the BEFORE benchmark. The new tools
(`outline_file`, `list_declarations`, `find_symbol_usages`) were
**not picked once**. The catalog-shape edits (rank #1–2) didn't
dominate this model's tool selection — even with the rewritten
first sentences and the cheat-sheet entries.

**Diagnosis:** The non-reasoning Grok variant appears to anchor
strongly on tool *order* in the catalog and on the *first sentence
verb* (`Grep within a single file` ≈ matches "find caching"). Rank
#3 (parameter-shape match) is also pulling weight here — the user's
question (`cache|Cache|store|memo|memory`) trivially fits a
`pattern: string` parameter, while `outline_file` requires only a
`path`, which feels less like "search" to the model.

**Three additional levers applied (no new tools, no schema break):**

1. **System-prompt FORBIDDEN rule (rank #5 turned into a hard rule).**
   Added at the very top of [`ai/chat_request.json`](../../ai/chat_request.json)
   system message:
   > **FILE-NAMED QUESTIONS — read this first.** When the user names
   > a specific file …, your FIRST tool call MUST be `outline_file(path)`
   > — NOT `file_search`, NOT `project_search`, NOT a fat regex. …
   > `file_search` is FORBIDDEN as the first call for any question
   > that names a single file. Issuing a kitchen-sink regex like
   > `cache|Cache|store|memo|memory` is a bug — do not do it.
2. **Tool-list reordering.** `outline_file` / `list_declarations` /
   `find_symbol_usages` are now **the first three tools** in the
   `tools[]` array, before `search_graph`. Models that scan the
   catalog top-down see them first.
3. **`file_search` description hardened.** First sentence now opens
   with *"DO NOT call this as your first tool when the user names a
   single file — call `outline_file(path)` first"* and explicitly
   names the kitchen-sink-regex anti-pattern as forbidden.

These are **prompt-only** changes — zero code, zero schema break,
zero risk to the existing `/api/file/search` HTTP consumers.

**Re-run benchmark:** still pending (same blocker as §8.10:
needs a live LLM round). If the trace shape *still* doesn't change
after these three tightenings, the next step is to remove
`file_search` and `project_search` from the catalog entirely for
file-named questions (a context-aware tool list assembled per
request) — that's a code change in
[`chat/service.py`](../../chat/service.py) rather than a prompt edit.
