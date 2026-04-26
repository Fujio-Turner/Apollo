# Plan: Incremental Re-Index Strategies (Options 1 & 2)

Apollo indexes codebases of hundreds to thousands of files, ranging
20 KB – 50 MB total. Full re-index is too slow for the inner-loop UX,
but pure file-level incremental indexing leaves **edge rot** when
unchanged files reference symbols in changed/new/removed files.

This doc captures two incremental strategies — both will be
implemented and benchmarked side-by-side. The system will also expose
a config knob so users / sessions can pick which one runs.

---

## 0. Context & Today's Behavior

- `graph/builder.py` already supports `build_incremental(root, prev_hashes)`.
  It re-parses only files whose `(mtime_ns, size, sha256)` changed and
  calls `_resolve_calls` for those parsed files.
- Edges produced by `_resolve_calls` rely on an in-memory
  `_symbol_table` that is **rebuilt from scratch** every run from the
  files currently being parsed. That means:
  - When skipping unchanged files, their symbols are **not** in the
    table → calls/imports from changed files into unchanged files
    can fail to resolve.
  - When unchanged files reference changed/new/removed targets,
    their edges remain stale because `_resolve_calls` is never run
    for them.
- `JsonStore.save()` rewrites the whole file (cheap for small JSON).
- `CouchbaseLiteStore.save()` purges all docs then re-inserts —
  expensive and not truly incremental on disk.

---

## 1. Goals

1. Two interchangeable incremental strategies (Options 1 & 2 below)
   selectable at runtime; both must produce **edge-correct** graphs.
2. Diff-based persistence for the CBL backend so disk writes scale
   with churn, not graph size.
3. Telemetry: per-run timing, files parsed, edges added/removed,
   bytes written. Captured to `data/_meta.json` /
   `apollo_meta::reindex_history` so users can see real costs.
4. Optional **deferred / background** correctness sweep so the
   "interactive" save path can return fast.

## 2. Non-Goals

- Cross-language incremental resolution (only Python today).
- Live coalescing of rapid file-save bursts (the watcher already
  debounces).
- Schema migrations.

---

## 3. Strategy Options

### Option 1 — "Parse incremental, resolve full"

```
1. Walk root, hash files.
2. Skip files whose (mtime,size,sha256) unchanged → reuse cached AST/nodes.
3. Re-parse changed files only.
4. Rebuild the full symbol table from ALL nodes (changed + cached).
5. Re-resolve edges for ALL files using that table.
6. Diff old graph vs new → upsert/purge per backend.
```

**Pros**
- Edge-correct by construction.
- Resolution is fast: hash-table lookups, no AST work.
- Simple to reason about and test.
- Works well when changes are concentrated (typical dev loop).

**Cons**
- O(total edges) per run. For 100k-edge graphs this is still
  milliseconds, but for >1M edges it adds up.
- Must keep cached parsed metadata in memory (or reload from store)
  for unchanged files.

### Option 2 — "Parse incremental, resolve dirty + dependents"

```
1. Same parse step as Option 1.
2. Build/maintain a reverse-dependency index:
     dep_index[file] = set(files that import / call into file)
3. dirty = changed_files ∪ removed_files ∪ added_files
4. affected = dirty ∪ {f for d in dirty for f in dep_index[d]}
   (one-hop closure; could expand to N-hop for transitive imports)
5. Re-resolve edges only for `affected` files.
6. Garbage-collect edges whose endpoints no longer exist.
7. Diff & persist.
```

**Pros**
- Best-case work: O(affected files), often << total.
- Big repos with locally-scoped changes win heavily.

**Cons**
- Reverse-dep index must be persisted and kept consistent.
- Edge cases:
  - Renames in target files invalidate edges in *non-affected*
    callers if they used dynamic / string-based lookups. Requires
    pessimistic invalidation rule or a "fallback to Option 1
    weekly".
  - Wildcard imports (`from x import *`) blow up affected set.
  - First-run / no-prior-graph still needs full index.
- More code paths → more bugs.

### Hybrid we will likely settle on

- **Default:** Option 2 for foreground (sub-second feel).
- **Background:** Option 1 sweep on session load and every N minutes
  (catch any edge rot Option 2 missed).
- **Force full** still available behind a "Rebuild Index" button.

---

## 4. Design

### 4.1 Strategy interface

`graph/incremental.py`:

```python
class IncrementalStrategy(Protocol):
    name: str
    def run(
        self,
        root_dir: str,
        graph_in: nx.DiGraph,
        prev_hashes: dict[str, dict],
        prev_dep_index: dict[str, set[str]] | None = None,
    ) -> IncrementalResult: ...

@dataclass
class IncrementalResult:
    graph_out: nx.DiGraph
    new_hashes: dict[str, dict]
    new_dep_index: dict[str, set[str]]
    diff: GraphDiff
    stats: ReindexStats

@dataclass
class GraphDiff:
    nodes_added: list[str]
    nodes_modified: list[str]
    nodes_removed: list[str]
    edges_added: list[tuple[str, str, str]]   # (src, etype, dst)
    edges_removed: list[tuple[str, str, str]]

@dataclass
class ReindexStats:
    strategy: str            # "full" | "incremental_resolve_full" | "incremental_resolve_local"
    started_at: float
    duration_ms: int
    files_total: int
    files_parsed: int
    files_skipped: int
    affected_files: int      # Option 2 only
    edges_resolved: int
    edges_added: int
    edges_removed: int
    bytes_written: int
```

Two concrete implementations:
- `ResolveFullStrategy` — Option 1.
- `ResolveLocalStrategy` — Option 2.
(plus existing `FullBuildStrategy` for "Force Full").

### 4.2 Symbol-table caching

For Option 1 we need symbols of unchanged files without re-parsing.
Two ways:

a. **In-memory cache** keyed by `rel_path → list[symbol_record]` kept
   on the singleton builder. Lost on process restart.
b. **Reconstruct from the loaded graph**: every node we already
   persist has `name`, `qualified_name`, `path`, `type`. Walking the
   graph gives us the symbol table for free.

→ Use (b) on first run after restart; populate (a) thereafter for
speed.

### 4.3 Reverse-dependency index (Option 2)

Persist as `apollo_meta::dep_index`:

```json
{
  "src/utils/log.py": ["src/api/handlers.py", "src/cli.py"],
  ...
}
```

Built by inverting `imports` and `calls` edges after each run.
On graph load we check it exists and is consistent (count of edges
matches); if not, fall back to Option 1 once.

Wildcard imports → mark target file as `*-imported-by` set, treated
as a tripwire that forces resolution of every importer when the
target changes.

### 4.4 Diff-based persistence

- Computing the diff is O(nodes + edges) but only over the small
  change-set.
- `Store.save_diff(diff: GraphDiff)` becomes part of the storage
  protocol.
  - **JSON**: simplest is to keep `save(graph)` (full rewrite) since
    the file is small. We *also* implement `save_diff` that just
    re-saves; the API stays uniform.
  - **CBL**: real per-doc upserts/purges inside one transaction.
    Edge IDs are deterministic (`{src}--{etype}-->{dst}`) so this
    is straightforward.

### 4.5 Background sweep

- A FastAPI background task or `asyncio.create_task` triggered on:
  - App startup (after first user interaction, not blocking boot).
  - Every N minutes (config: `reindex.sweep_interval_minutes`,
    default 30).
  - Any time the freshness indicator shows >0 stale files for
    >M minutes.
- Sweep always runs `ResolveFullStrategy` so any edge rot from the
  fast Option 2 path gets cleaned up.
- Logs `apollo_meta::reindex_history` entry tagged `kind: "sweep"`.

### 4.6 Config

```toml
[reindex]
strategy = "auto"            # "auto" | "full" | "resolve_full" | "resolve_local"
sweep_interval_minutes = 30
sweep_on_session_start = true
local_max_hops = 1           # Option 2 dependency expansion depth
force_full_after_runs = 50   # safety: every Nth incremental, do a full pass
```

`auto`: Option 2 in foreground, Option 1 in background sweep.

### 4.7 Telemetry & UX

- Each run appends to `apollo_meta::reindex_history` (capped at last
  100 runs).
- New endpoint: `GET /api/index/history` → returns the array.
- New endpoint: `GET /api/index/last` → returns most recent
  `ReindexStats`.
- Status bar dot tooltip can include "Last reindex: 240ms (parsed 3
  of 412 files, +18/-6 edges)".

---

## 5. Implementation Phases

### Phase A — Diff plumbing *(small, prerequisite)*
- [ ] Define `GraphDiff` and `ReindexStats` dataclasses.
- [ ] Implement `compute_diff(old_graph, new_graph) -> GraphDiff`.
- [ ] Add `save_diff(diff)` to JSON store (full rewrite under the
      hood is fine).
- [ ] Add real `save_diff(diff)` to CBL store.
- [ ] Tests: round-trip add/modify/remove via diff.

### Phase B — Strategy interface + Option 1 *(medium)*
- [ ] `graph/incremental.py` with `IncrementalStrategy` protocol.
- [ ] Port existing `build_incremental` into
      `ResolveFullStrategy.run(...)`.
- [ ] Symbol table seeded from loaded graph (no re-parse).
- [ ] Tests: change one file, verify edges into unchanged target are
      preserved when target unchanged, and re-resolved when target
      modified/removed/renamed.

### Phase C — Reverse-dep index + Option 2 *(medium)*
- [ ] Build/persist `dep_index` (`apollo_meta::dep_index`).
- [ ] `ResolveLocalStrategy.run(...)` consuming `dep_index`.
- [ ] Wildcard-import tripwire.
- [ ] Tests:
  - Local change resolves only affected files.
  - Removing a target file invalidates dependent edges.
  - Renaming a public symbol invalidates only direct dependents
    (one hop) — and is caught by background sweep when needed.

### Phase D — Background sweep + config *(small)*
- [ ] Config loader for `[reindex]` section.
- [ ] Background task runner inside FastAPI app.
- [ ] Trigger sweep on session start (with delay) and on interval.
- [ ] Skip sweep when watcher is actively re-indexing.

### Phase E — Telemetry & UI *(small)*
- [ ] Persist `ReindexStats` history.
- [ ] `/api/index/history` and `/api/index/last`.
- [ ] Status bar tooltip uses last stats.
- [ ] Optional: small "Re-index history" panel in the UI.

### Phase F — Benchmark harness *(small, do early)*
- [ ] Script `scripts/bench_reindex.py` that:
  - Loads a known graph.
  - Mutates K random files in a temp clone.
  - Runs each strategy and prints `ReindexStats` table.
- [ ] Recorded results checked into `docs/work/REINDEX_BENCHMARKS.md`
      so we can compare across changes.

---

## 6. Correctness Tests (apply to ALL strategies)

Each scenario asserts `final_graph == FullBuildStrategy(graph)` for the
node/edge sets that matter (edges may have small cosmetic differences;
we compare canonical tuples).

1. **Touch only**: file's mtime bumps, content unchanged → no work,
   identical graph.
2. **Body edit, public API stable**: nodes for that file change,
   edges referencing its symbols stay valid.
3. **Add new function**: new node + edges from any caller resolve.
4. **Rename a public function**: old edges removed, new resolved
   (Option 2 must catch this for direct callers; sweep catches
   transitive).
5. **Delete a file**: file node + descendants removed; orphan edges
   purged.
6. **Add a new file with imports into existing files**: new edges
   added, existing edges unchanged.
7. **Wildcard import target changed**: every wildcard-importer
   re-resolved.
8. **Cyclic imports**: no infinite loops in dep-index expansion.
9. **Move/rename a file**: treated as delete+add; edges to it
   updated.

---

## 7. Risks & Open Questions

- **Stale-edge invisibility**: Option 2 may leave wrong edges in
  rare cases (renames in non-affected branches). Mitigation:
  background sweep + "force full" every Nth run.
- **Symbol-table memory**: keeping in-memory cache may double RAM.
  Mitigation: lazy load from graph on demand; LRU evict.
- **CBL transaction size**: huge diffs in one txn may exceed limits.
  Chunk into batches of e.g. 5k operations.
- **Watcher vs. UI race**: both could trigger reindex simultaneously.
  Use a per-process `asyncio.Lock` to serialize.
- **Recently-touched window**: user mentioned "most people only need
  the most recent today to 90 days of files". Worth exposing a
  `recent_window_days` filter for the freshness indicator and the
  initial graph view, but **not** for indexing — partial indexes are
  worse than fresh ones because edges to "old" files just disappear.

## 8. What we expect to learn from running both

- Median / p95 latency for typical edits (1–5 files).
- Cost crossover: at what change-size does Option 2 stop beating
  Option 1?
- Frequency of "edge rot" caught by the background sweep — tells us
  if Option 2 is safe enough for foreground default.
- Disk-write volume per run for CBL.

This data lives in `docs/work/REINDEX_BENCHMARKS.md` (created in
Phase F) and informs whether we eventually retire one strategy.
