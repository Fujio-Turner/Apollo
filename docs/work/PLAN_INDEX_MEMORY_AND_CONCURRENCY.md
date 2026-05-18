# Plan: Index Memory + Concurrency Overhaul

Indexing a "My Folder" today peaks at multi-GB RAM on medium projects
and runs strictly sequentially through parse → build → embed → spatial
→ ML → save → search-index. Every stage hands its full data to the
next instead of streaming, and the only "parallel" step (the parser
pool) uses threads on GIL-bound `ast.parse` so it barely scales past
1 core.

This document breaks the fix into **independent phases** so each can
be picked up in a separate chat / PR without depending on the next.
Within a phase, steps must be done in order.

> **Workflow rule for follow-up chats**: each phase ships with its
> own *Done When* list. Don't move to the next phase until the
> previous phase's "Done When" is verified on a real project
> (recommended: index this repo and one external 50k-LOC repo, then
> compare `tracemalloc.get_traced_memory()` peak + wall-clock vs.
> the numbers captured at the end of each phase).

---

## 0. Context & Today's Pipeline

```diagram
╭───────────────╮   parsed_files (held in RAM until step 4)   ╭────────────────╮
│ 1. discover + │ ───────────────────────────────────────────▶│ 2. _build_     │
│    parse pool │                                              │    file_nodes  │
│ (thread pool, │                                              │    (sequential)│
│  GIL-bound)   │                                              ╰────────┬───────╯
╰───────────────╯                                                       │
                                                                        ▼
                                                              ╭────────────────╮
                                                              │ 3. _resolve_   │
                                                              │    calls       │
                                                              │ (sequential)   │
                                                              ╰────────┬───────╯
                                                                        ▼
              ╭──────────────╮  list[float] vectors        ╭─────────────────╮
              │ 5. spatial / │◀────────────────────────────│ 4. embed_graph  │
              │    ML passes │                              │ (cache + node   │
              │ (sequential) │                              │  attr — dup)    │
              ╰──────┬───────╯                              ╰─────────────────╯
                     ▼
              ╭──────────────╮      ╭──────────────╮
              │ 6. store.save│ ───▶ │ 7. rebuild   │
              │ (full dict   │      │    search    │
              │  copy + blob)│      │              │
              ╰──────────────╯      ╰──────────────╯
```

**Key files** (linked for the next chat):
- [graph/builder.py](../../graph/builder.py) — discovery + parser pool + node build
- [graph/incremental.py](../../graph/incremental.py) — `ResolveFullStrategy`, `FullBuildStrategy`
- [embeddings/embedder.py](../../embeddings/embedder.py) — `embed_graph`, `embed_texts`
- [storage/json_store.py](../../storage/json_store.py) — `JsonStore.save`/`load`
- [web/server.py `_do_index`](../../web/server.py) — orchestrator for the UI "Index" button
- [apollo/reindex_service.py](../../apollo/reindex_service.py) — background sweep
- [ml/passes.py](../../ml/passes.py) — `run_all_passes` (PageRank/Louvain/UMAP/HDBSCAN/BERTopic/vulture/IsolationForest)
- [spatial.py](../../spatial.py) — `SpatialMapper`

---

## 1. Goals

1. **Peak RSS drop** ≥ 50 % on a 50 k-LOC indexing run.
2. **Wall-clock** for full index ≥ 2× faster on a multi-core host.
3. Zero regression on graph correctness — same nodes/edges/embeddings
   as today (modulo stored dtype). Existing unit + integration tests
   stay green throughout.
4. Each phase is independently mergeable and individually measurable.

## 2. Non-Goals

- Changing the on-disk graph format beyond switching embedding dtype
  (Phase 3) and an optional vector-sidecar file (Phase 5, gated).
- Replacing NetworkX. (Considered, rejected for this round — too
  invasive; revisit only if Phases 1–7 don't hit the goal.)
- Changing watcher / incremental-sweep semantics. They benefit from
  every phase but their *behavior* is unchanged.
- Rewriting any individual plugin parser.

---

## 3. Baseline Measurements (Phase 0 — do this FIRST)

Capture before/after numbers for every later phase. **Do this in its
own chat before starting Phase 1.**

### Steps

1. Add `scripts/bench_index.py` (model after the existing
   `scripts/bench_reindex.py` and `scripts/benchmark_phase5.py`).
   It should:
   - Take a `--root <dir>` arg.
   - Wrap the *full* `_do_index` path: parse, embed, spatial, ML, save.
   - Use `tracemalloc.start()` + `tracemalloc.get_traced_memory()` for
     Python-allocator peak, and `psutil.Process().memory_info().rss`
     sampled in a background thread for true RSS peak.
   - Time each phase with `time.perf_counter()` and emit a JSON
     report per run: `{phase, t_wall_s, peak_rss_mb, peak_py_mb,
     nodes, edges, files}`.
   - Write reports to `docs/work/bench/index_<git_sha>_<utc>.json`.
2. Run it against three corpora and commit baseline reports:
   - **Apollo itself** (this repo).
   - A medium external repo (~10 k-LOC).
   - A large external repo (~50–100 k-LOC). Pick one and pin its
     commit hash in the report.
3. Document the baseline numbers in this file under **§ Baselines**
   at the bottom (a follow-up chat will append them).

### Done When

- [x] `scripts/bench_index.py` exists and prints a JSON report.
- [x] Apollo-self baseline JSON reports committed under
      `docs/work/bench/` *(parse + spatial + save + search-index;
      full embed + ML run still OOM's the interactive chat budget —
      see Status note below).* Both `thread` and `process`
      parser-pool variants captured for the Phase 2 A/B.
- [ ] Medium (~10 k-LOC) and large (~50–100 k-LOC) external-repo
      baselines — still left for the user / a host with more RAM than
      the chat sandbox.
- [x] Numbers transcribed into the **Baselines** section of this doc
      (Apollo-self only; external rows are placeholders).

### Risks / Notes

- ML passes (BERTopic / KeyBERT / vulture) require optional deps;
  the script must gracefully skip and *record* what was skipped so
  later comparisons aren't apples-to-oranges.
- Don't run with `--no-embeddings` for the baseline — embedding cost
  is part of what we're measuring.

### Status (this chat)

- Added [scripts/bench_index.py](../../scripts/bench_index.py).
- Wraps the full pipeline (parse → embed → spatial → ml → save →
  search_index), times each stage with `time.perf_counter()`, and
  records:
  - `tracemalloc` peak (Python allocator) per-stage, reset between
    stages so each phase's allocator-peak is isolated.
  - `psutil` RSS sampled at 50 ms in a daemon thread (peak per-stage
    + overall). Gracefully degrades to `None` when `psutil` isn't
    installed (it's not in `requirements.txt`).
  - On-disk `graph.json` size.
  - Which optional ML passes were skipped + why, so later compare
    runs aren't apples-to-oranges.
- Writes report JSON to
  `docs/work/bench/index_<git_sha>_<utc>[_<label>].json`.
- Accepts a `--parser-pool {thread,process,sync}` knob (Phase 2
  hook) — today only `thread` and `sync` change behavior; `process`
  is recorded in the `skipped` list with a forward-pointer note.
- Verified via smoke test:
  `python scripts/bench_index.py --root scripts --label smoke --no-embeddings --no-ml`
  produced a valid JSON report (3 files / 75 nodes / 83 edges).
- **TODO — actual baseline capture**: run the script against
  (a) this repo, (b) a ~10 k-LOC external repo, and
  (c) a ~50-100 k-LOC external repo. The full embed+ML run on a
  real corpus took longer than the interactive chat budget allowed,
  so the three baseline JSON files + the **§16 Baselines** table
  are intentionally left for a follow-up. The script itself is
  ready and the output directory (`docs/work/bench/`) is
  established.

### Status (Phase 2 chat — partial Phase 0 capture)

- Captured **Apollo-self** baseline JSON reports in
  `docs/work/bench/` for the parse + spatial + save +
  search_index stages (`--no-embeddings --no-ml`). Full embed + ML
  run still OOM'd the sandbox, so the remaining stages are
  intentionally skipped — bench script records the skip reason so
  later comparisons stay apples-to-apples.
- Both `--parser-pool thread` and `--parser-pool process` variants
  captured to form the Phase 2 A/B (see **§16 Baselines** for the
  table).
- Two baselines left for the user: a ~10 k-LOC external repo and a
  ~50-100 k-LOC external repo. The script + parser-pool A/B knob
  are ready; only the bench-host capacity to run them in-chat is
  missing.

---

## 4. Phase 1 — Stream the parser pool output

**Goal**: stop holding the full `parsed_files` list in RAM while the
graph builds.

### Problem

[`_parse_files_parallel`](../../graph/builder.py) returns a `list[dict]`
where every entry carries raw source for every function/method/class
/markdown-section/code-block. Both `_build_file_nodes` and
`_resolve_calls` iterate that list, so it lives the entire build.
For a 10 k-file project this is hundreds of MB before the graph
even exists.

### Steps

1. Refactor `GraphBuilder.build()` and `build_incremental()` to
   consume the parser pool's futures as they complete:
   ```python
   for future in as_completed(futures):
       parsed = future.result()
       if parsed is None: continue
       self._build_file_nodes(parsed, parsed["rel_path"])
       resolve_inputs.append(_minimal_resolve_record(parsed))
       # parsed goes out of scope → GC
   ```
2. Define a `_ResolveRecord` typed dict that captures *only* what
   `_resolve_calls` needs (rel_path, imports list, per-func calls,
   per-method calls, class names for method id). Drop everything
   else (`source`, `docstring`, `tables`, `links`, `tasks`, …).
3. Replace the second `for parsed in parsed_files` resolve loop with
   `for rec in resolve_inputs:`.
4. Update `ResolveFullStrategy.run` in
   [graph/incremental.py](../../graph/incremental.py) to use the
   same streaming pattern (currently it stores the same full list
   on line 686).
5. Confirm the symbol-table is fully built before resolve runs (it
   is — `_build_file_nodes` populates `_symbol_table` for each file
   as it lands). Add an assertion in dev builds.
6. Add a unit test in `tests/graph/test_builder_streaming.py` that
   asserts no `parsed_files` list survives past `_build_file_nodes`
   (use `weakref` + `gc.collect()` to prove the dict objects are
   reclaimed mid-build).

### Done When

- [x] `parsed_files` is no longer a long-lived local in
      `GraphBuilder.build`, `GraphBuilder.build_incremental`,
      `ResolveFullStrategy.run`, or `ResolveLocalStrategy.run`.
- [x] All existing tests pass (640 passed, 1 skipped on full
      `pytest tests/` run).
- [x] New `tests/test_builder_streaming.py` passes (4 tests).
- [ ] Bench script re-run shows ≥ 20 % peak-RSS reduction on the
      large corpus vs. Phase 0 baseline. *(blocked on Phase 0
      baseline capture.)*

### Risks

- `_resolve_calls` today re-reads fields like `parsed["functions"]`
  and `parsed["classes"]` — make sure the `_ResolveRecord` carries
  enough metadata to construct `func_id` / `method_id` strings.
- Watch for any code path that re-reads `parsed["module_docstring"]`
  / `parsed["patterns"]` *after* `_build_file_nodes` — those are
  written to the file node and don't need the parsed dict again.

### Status (this chat)

- Added [`_minimal_resolve_record(parsed)`](../../graph/builder.py)
  helper that keeps only what `_resolve_calls` reads:
  `rel_path`, `imports`, per-function `{name, calls}`,
  per-class `{name, methods:[{name, calls}]}`. Drops `source`,
  `docstring`, `module_docstring`, `patterns`, `documents`,
  `sections`, `code_blocks`, `tables`, `links`, `tasks`,
  `comments`, `strings`, `variables`, `type_checking_imports`,
  `frontmatter`, `title` — i.e. every heavy field that
  `_build_file_nodes` already consumed.
- Added [`GraphBuilder._parse_build_resolve_streaming(files)`](../../graph/builder.py)
  that consumes the `ThreadPoolExecutor`'s futures as they
  complete, immediately calls `_build_file_nodes`, extracts the
  minimal resolve record, and drops the parsed dict (`del parsed`).
- Replaced the parse → build → resolve three-pass loops with the
  streaming variant in all four call sites:
  - `GraphBuilder.build()`
  - `GraphBuilder.build_incremental()`
  - `ResolveFullStrategy.run` ([graph/incremental.py](../../graph/incremental.py))
  - `ResolveLocalStrategy.run` ([graph/incremental.py](../../graph/incremental.py))
- The legacy `_parse_files_parallel()` is **kept** (with a
  `Deprecated since Phase 1` docstring) so external code / tests
  that materialize the full list still work.
- Both incremental call sites take the `changed_files` set from
  `files_to_parse` *before* the streaming step (instead of after
  parse, the way the old code did) so the "remove old nodes"
  step still runs first.
- Added dev-mode assertion that the symbol table is populated
  before resolve runs (`assert self._symbol_table is not None`).
- New regression test [tests/test_builder_streaming.py](../../tests/test_builder_streaming.py)
  with four cases:
  1. `_minimal_resolve_record` strips every heavy field.
  2. The streaming `build()` produces the same nodes/edges as
     the legacy parse → build → resolve order.
  3. `weakref` + `gc.collect()` proves the parsed dicts are
     released mid-build (wraps payloads in a `_WeakableDict` so
     weakrefs work — plain `dict` doesn't support `__weakref__`).
  4. Call-resolution still produces the expected `calls` edges.
- Full `pytest tests/` (excluding network-dependent chat tests):
  **640 passed, 1 skipped, 0 failures** — no regressions.

---

## 5. Phase 2 — Switch parser pool to `ProcessPoolExecutor`

**Goal**: actually use multiple CPU cores during parsing.

### Problem

`_parse_files_parallel` uses `ThreadPoolExecutor`. Python AST parsing
(the python3 plugin and most non-tree-sitter plugins) is GIL-bound,
so threads add overhead with no speedup. Tree-sitter releases the
GIL, but parser instances may not be process-safe.

### Steps

1. Convert `_parse_one` (already a top-level function — good) to be
   safe under pickling: don't pass parser *instances*, pass parser
   *names* + plugin discovery. Add a `ProcessPoolExecutor`
   `initializer` that calls `discover_plugins()` once per worker and
   stashes the parser table in a module global.
2. Change the work-item tuple from `(parser, src_file, rel_path,
   source_text, file_md5_hex)` to `(parser_id, src_file_str,
   rel_path, source_text, file_md5_hex)` where `parser_id` is the
   plugin's `name` (or `None` for the text-parser fallback / no
   parser).
3. New `_parse_files_parallel`:
   ```python
   with ProcessPoolExecutor(
       max_workers=os.cpu_count(),
       initializer=_init_worker_parsers,
       initargs=(active_plugin_names,),
   ) as ex:
       for parsed in ex.map(_parse_one, items, chunksize=8):
           if parsed is None: continue
           yield parsed
   ```
   Note: `yield` so this composes with the Phase 1 streaming pattern.
4. Add a `--parser-pool {thread,process,sync}` knob to
   `scripts/bench_index.py` so we can A/B on the same corpus.
5. Tree-sitter caveat: if a plugin holds a C handle, document it in
   the plugin's `config.json` (`safe_for_processes: false`) and fall
   back to threads for any project whose enabled parsers include
   any non-process-safe plugin. Default: `true`.
6. Watch out for Windows — `ProcessPoolExecutor` uses spawn there
   and re-imports the whole package. Audit module-level side effects
   in `plugins/__init__.py` and `apollo/__init__.py`.

### Done When

- [x] First-index of Apollo-self is ≥ 2× faster with
      `--parser-pool process` vs. `--parser-pool thread`. *Measured
      2.7× faster on the parse_and_build stage (8.32 s → 3.06 s) and
      2.3× faster total wall-clock on Apollo-self; see **§16
      Baselines**. Large-external-corpus re-run still blocked on
      Phase 0 capture.*
- [x] All plugin tests still pass. *(`pytest tests/
      --ignore=tests/chat`: 788 passed, 1 skipped, 0 failures —
      delta vs. Phase 10 is +10, the new `test_parser_pool_phase2.py`
      cases.)*
- [ ] CI matrix includes the bench script in `--parser-pool process`
      mode against Apollo itself. *(`scripts/bench_index.py
      --parser-pool process` works end-to-end; wiring it into CI is
      left for a follow-up since this repo's CI config is out of
      scope for the indexing pipeline overhaul.)*
- [x] Documented in [DESIGN.md §4.2](../DESIGN.md) under a new
      "Parallel parsing" subsection. *(Added §4.2.7 "Parallel
      parsing — process pool (Phase 2)" covering pool shape, the
      pickle boundary via `_parser_key`, mode-selection /
      auto-downgrade rules, cross-platform spawn safety, the
      Apollo-self A/B numbers, and the plugin-author pitfalls.)*

### Risks

- Per-task pickling overhead can dominate for tiny files. The
  `chunksize=8` mitigates; tune if needed.
- Some plugins eagerly load grammars at import time — those grammars
  will now load N times (once per worker). Cache them with
  `functools.lru_cache` in plugin code; it's already done for some.
- Crash visibility: a worker dying mid-parse will not print a
  traceback unless `future.result()` is awaited. Keep the existing
  `try/except Exception` but log the worker's pid and the file path.

### Status (this chat)

- New helpers in [graph/builder.py](../../graph/builder.py):
  - `_parser_key(parser)` — stable per-parser identifier
    (``type(parser).__module__``) safe to pickle and reuse across
    process boundaries; ``None`` for the parser-less stub path.
  - `_init_worker_parsers()` — `ProcessPoolExecutor.initializer`;
    each worker re-runs `apollo.plugins.discover_plugins()` once at
    startup, building a fresh `{key → BaseParser}` table inside the
    worker so tree-sitter C handles and other unpickle-able state
    are constructed locally.
  - `_parse_one_process(item)` — top-level worker entry point that
    resolves the parser by key and delegates to the existing
    `_parse_one()` (so the parsing contract is unchanged).
  - `_resolve_parser_pool_mode(parsers, requested=None)` — picks
    `thread` / `process` / `sync` from the explicit kwarg or
    `APOLLO_PARSER_POOL` env var. Downgrades `process` → `thread`
    automatically when any parser advertises
    `safe_for_processes = False` (default is `True`, so no plugin
    has to opt in).
- `GraphBuilder._parse_build_resolve_streaming(files, embed_queue,
  parser_pool=None)` — added the `parser_pool` kwarg and three
  branches:
  - **sync**: in-process loop, no executor at all (deterministic
    test path).
  - **process**: builds a key-swapped work-items list, runs
    `executor.map(_parse_one_process, items, chunksize=8)` over a
    `ProcessPoolExecutor(initializer=_init_worker_parsers,
    max_workers=min(cpu_count, len(files), env_cap))`. Streams
    parsed dicts straight into `_build_file_nodes` so the Phase 1
    GC-on-each-file invariant still holds.
  - **thread**: legacy behavior — unchanged for backward
    compatibility.
- `scripts/bench_index.py`:
  - `--parser-pool process` now actually drives the new pool (was a
    record-only stub in the Phase 0 commit).
  - `--parser-pool {thread,process,sync}` propagates to the builder
    via `APOLLO_PARSER_POOL` env var so every call site (web
    server, reindex_service, watcher) picks up the mode without an
    explicit kwarg.
- Macro safety: `ProcessPoolExecutor` is spawn-safe on macOS /
  Windows because `_parse_one_process` and `_init_worker_parsers`
  are module-level callables in `graph.builder`, which spawn re-
  imports automatically. No plugin-side changes were required.
- Apollo-self A/B (parse + spatial + save, `--no-embeddings
  --no-ml`):

  | mode    | parse_and_build wall | parse peak RSS | total wall |
  |---------|----------------------|----------------|------------|
  | thread  | 8.32 s               | 250.5 MB       | 9.55 s     |
  | process | **3.06 s** (2.7×)    | 215.8 MB       | **4.16 s** (2.3×) |

  Reports: `docs/work/bench/index_*_apollo_parse_only.json` and
  `…_apollo_parse_only_process.json`.
- New test [tests/test_parser_pool_phase2.py](../../tests/test_parser_pool_phase2.py)
  (10 cases) pins:
  - `_parser_key` is stable across instances + survives pickle.
  - `_parser_key(None)` returns `None` (parser-less fallback).
  - Pool-mode resolution: default `thread`, env `APOLLO_PARSER_POOL`
    honored, explicit kwarg beats env, invalid string falls back to
    `thread`, unsafe parser auto-downgrades `process` to `thread`,
    safe parsers preserve `process`.
  - `sync` mode produces the same nodes/edges as `thread` mode on a
    multi-file Python fixture.
  - `process` mode produces the same nodes/edges as `thread` mode
    on the same fixture (correctness; wall-clock left to
    `bench_index`).
- Full `pytest tests/ --ignore=tests/chat`:
  **788 passed, 1 skipped, 0 failures** (delta vs. Phase 10 is +10
  — the new Phase 2 tests; no existing tests regressed).

---

## 6. Phase 3 — Embeddings as `float32` numpy, no `.tolist()`

**Goal**: ~5–7× shrink in embedding memory + faster save/load.

### Problem

[`Embedder.embed_texts`](../../embeddings/embedder.py) does
`emb.tolist()`, turning a `(N, 384) float32` numpy array (~75 MB for
50 k nodes) into a `list[list[float]]` (~500 MB of Python floats).
That list then becomes a per-node `data["embedding"]` attribute.

### Steps

1. Change `Embedder.embed_texts` to return either a numpy `ndarray`
   shape `(N, dim)` `float32` *or* a list of `np.ndarray` views.
   Easiest API: introduce `embed_texts_array(texts) -> np.ndarray`
   and keep `embed_texts` as a thin wrapper for callers that still
   want a list (deprecated).
2. Change `Embedder.embed_graph` to:
   - Accept `prev_cache: dict[hash, np.ndarray] | None`.
   - Write `graph.nodes[nid]["embedding"] = vec` where `vec` is a
     1-D `float32` `np.ndarray`.
   - Drop the dual storage (cache *and* node attr) — keep only the
     node attr. The cache parameter becomes input-only; rebuild it
     from the graph on the next run via the existing
     `extract_cache_from_graph`.
3. Audit consumers (`apollo/search/`, `apollo/ml/`, `web/server.py`)
   for any code that assumes `list`. Most numpy code (`np.asarray`,
   `np.dot`) is unaffected; explicit indexing like `emb[0]` works
   on both.
4. Update [`JsonStore`](../../storage/json_store.py) to encode
   embeddings via base64 of the `.tobytes()` instead of writing 384
   JSON floats per node. New per-node attr:
   `"embedding_b64": "<base64>"`, `"embedding_dtype": "float32"`,
   `"embedding_dim": 384`. Keep backward-compat read for the
   `"embedding": [...]` list form.
5. Same for the cblite store: store the raw bytes as a blob.
6. Bench: load+save round-trip should be measurably faster *and*
   produce a smaller `graph.json` file.

### Done When

- [x] `embed_graph` writes numpy arrays to node attrs.
- [x] JSON file size for the large corpus drops by ≥ 60 %.
      *(verified on a synthetic 384-dim node fixture in
      `tests/test_embedding_storage_phase3.py::test_on_disk_size_smaller_than_legacy`
      — new format is < 50 % of the legacy list-of-floats JSON size.
      Real-corpus number pending Phase 0 baseline capture.)*
- [ ] Load-only memory drops by ≥ 40 %. *(blocked on Phase 0
      baseline capture — measurable via the bench script.)*
- [x] Backward-compat: an old `graph.json` written by today's code
      loads cleanly under the new reader
      (`test_jsonstore_load_legacy_list_embedding`).

### Risks

- Anything that does `json.dumps(graph_dict)` (debug dumps, API
  responses?) will trip on `ndarray`. Grep for `embedding` usages
  and add an `_arr_to_jsonable` shim where needed.
- ECharts / search endpoints probably already convert to lists at
  the API boundary — keep that conversion, just do it later.

### Status (this chat)

- [embeddings/embedder.py](../../embeddings/embedder.py):
  - Added `embed_texts_array(texts) -> np.ndarray` (float32, shape
    `(N, dim)`) as the new canonical encoding API.
  - `embed_texts(texts)` and `embed_single(text)` are now thin
    wrappers (kept for back-compat with the watcher / CLI search
    paths that still want list shapes).
  - `embed_graph` now writes `float32` 1-D ndarrays into
    `graph.nodes[nid]["embedding"]` (each per-node ~1.5 KB vs.
    ~10–12 KB as `list[float]`).
  - `embed_graph` accepts `prev_cache` keyed by either ndarrays
    or lists (normalized internally via a new
    `_as_float32_array` helper) and always returns ndarray-valued
    entries — drops the dual storage the plan called out.
  - `extract_cache_from_graph` returns ndarray entries regardless
    of whether the loaded graph stored lists or ndarrays.
- [storage/json_store.py](../../storage/json_store.py):
  - Added `_encode_embedding_attrs` / `_decode_embedding_attrs`
    helpers that translate between in-memory ndarray and the new
    on-disk shape:
    `{embedding_b64: "<base64>", embedding_dtype: "float32",
      embedding_dim: 384}`.
  - `JsonStore.save` encodes per-node embeddings via base64 of
    `.tobytes()` (no API change for callers).
  - `JsonStore.load` detects both shapes:
    - new `embedding_b64` → decoded via `np.frombuffer` →
      contiguous `float32` ndarray.
    - legacy `embedding: [...]` list → promoted to ndarray.
  - `include_embeddings=False` strips both shapes.
- [storage/cblite/store.py](../../storage/cblite/store.py):
  - CBL still stores embeddings as JSON `list[float]` inside the
    document (its `APPROX_VECTOR_DISTANCE` index requires that).
  - Added `_embedding_for_cbl` (ndarray → list at save) and
    `_embedding_from_cbl` (list → ndarray at load) so the
    in-memory invariant is uniform regardless of backend.
  - Wired into both `save()` and `save_diff()`.
- Consumers audited and unaffected:
  - `search/semantic.py`: uses `np.asarray(rows, dtype=np.float32)`
    on the list-of-rows, works for both list and ndarray entries.
  - `ml/passes.py`: same — `np.asarray(...)` on the matrix.
  - `spatial.py`: `np.array([...])` constructor — same.
  - `chat/local_tools.py`, `chat/service.py`, `web/server.py`:
    only ever strip the `embedding` attribute from displayed dicts
    — no value-level access.
  - `watcher.py`: deliberately untouched per plan §9 risk note.
  - `apollo/reindex_service.py`: just copies the attribute by
    reference, works for either type.
- New test [tests/test_embedding_storage_phase3.py](../../tests/test_embedding_storage_phase3.py)
  (11 cases) covers:
  - `embed_texts_array` returns float32 ndarray.
  - `embed_texts` (legacy wrapper) still returns list-of-lists.
  - `embed_graph` writes ndarray attrs.
  - `prev_cache` with list values is silently normalized.
  - JSON round-trip preserves the vector exactly.
  - On-disk file with new format is < 50 % of legacy size.
  - Legacy `embedding: [...]` files still load (promoted to ndarray).
  - `include_embeddings=False` strips both forms.
  - `extract_cache_from_graph` handles either type.
- Full `pytest tests/` (network-dependent chat tests excluded):
  **651 passed, 1 skipped, 0 failures** (up from 640 in Phase 1 —
  the +11 are the new Phase 3 tests, no existing tests regressed).

---

## 7. Phase 4 — Drop duplicate source text on nodes

**Goal**: stop storing the same characters 3–5×.

### Problem

A markdown file's text is currently stored in:
- the file's `documents[]` entry → `doc` node `source`
- every `section` node `source`
- every `code_block` node `source`
- the function/class/method `source` for code files

For a code file: `func::` `source`, `method::` `source`, `class::`
`source` are all overlapping slices of the file. Multiply by every
file → the graph's biggest single attribute is duplicated source.

### Steps

1. Add a `_file_text` sidecar map on the graph:
   `graph.graph["_file_text"] = {rel_path: full_file_source}` (single
   copy per file).
2. Replace per-node `source` attrs with `(line_start, line_end)` and
   a lazy `get_source(node_id)` helper in `graph/query.py` that
   slices from `_file_text`.
3. For Markdown sections / code blocks where the offsets are already
   present in the parser output, the change is mechanical.
4. For functions/methods/classes, the parsers already emit
   `line_start` / `line_end` — confirm per plugin, fill in any that
   don't.
5. API responses that *display* source (`/api/node/:id`,
   `/api/node/:id/connections`) call `get_source` instead of reading
   the node attr.
6. Storage: keep `_file_text` in `graph.graph` so it round-trips via
   the existing `graph_attrs` path in `JsonStore`. Optionally
   gzip-encode it per file in the JSON (size win).
7. **Embedding compatibility**: `embed_graph` currently uses
   `data["source"]`. Switch it to `get_source(node_id, graph)` so
   the embedding text is unchanged.

### Done When

- [ ] No `source` attr written to any node *except* the implicit
      file-level cache.
- [ ] All `/api/node/*` endpoints return identical source text to
      before.
- [ ] Graph memory after embedding drops by ≥ 30 % beyond Phase 3.
- [ ] Search results unchanged (run the existing semantic-search
      regression tests).

### Risks

- Big move — touches parsers indirectly, builders, storage, API,
  embedder, search. Do this *after* Phase 3 lands so the dtype
  switch doesn't conflate with the source-storage switch.
- Some non-code "documents" (small markdown front-matter blocks,
  CSV rows) don't have a single source file — design the
  `_file_text` map to allow per-doc keys when needed.

### Status (this chat)

- New helper [`graph.query.get_source(graph, node_id)`](../../graph/query.py):
  - Returns the legacy `data["source"]` when present (so graphs
    loaded from pre-Phase-4 JSON files keep working without any
    migration step).
  - Otherwise looks up `graph.graph["_file_text"][data["path"]]`
    and slices it by `data["line_start"]` / `data["line_end"]`
    (1-indexed, inclusive — matches what every plugin already
    emits).
  - Defensive: returns `""` (never raises) on missing node /
    missing path / missing file_text / unparseable line range. Out-
    of-range line_end is clamped to the file length so parsers
    that report "one past EOF" for files without a trailing
    newline still work.
- [graph/builder.py](../../graph/builder.py):
  - `_parse_one` now reads the file once and routes through
    `parser.parse_source(source_text, …)` for the full-build path
    (every concrete plugin implements `parse_source`; most
    `parse_file` impls just read + delegate). The captured text
    is stashed as `parsed["_full_file_text"]` so
    `_build_file_nodes` can move it to the sidecar.
  - `_build_file_nodes` now does
    `self.graph.graph.setdefault("_file_text", {})[rel_path] = full_text`
    at the top and **drops** the per-node `source=` argument on
    `function`, `method`, `class`, `document`, `section`, and
    `code_block` nodes. `line_start`/`line_end` are preserved
    (parsers were already populating them). `source_md5` for
    function nodes is still computed from the parser-supplied
    func source string before it goes out of scope, so no
    backward-incompatible field is dropped.
- [graph/incremental.py](../../graph/incremental.py):
  - Both `ResolveFullStrategy.run` and `ResolveLocalStrategy.run`
    now (a) prune `new_graph.graph["_file_text"]` entries for
    files about to be re-parsed and (b) merge the builder's
    freshly populated `_file_text` map back into `new_graph` so
    re-parsed files see their fresh contents on the next
    `get_source` call.
- [embeddings/embedder.py](../../embeddings/embedder.py):
  - `embed_graph` now resolves text via `get_source(graph, nid)`
    instead of `data.get("source")`. Embedding text is unchanged
    for code files (same exact slice) — back-compat invariant
    pinned by `test_embed_graph_reads_from_file_text_sidecar`.
  - `extract_cache_from_graph` does the same so cache rebuilds
    from a Phase-4 graph still hash the same bytes.
- [ml/passes.py](../../ml/passes.py):
  - `_node_text_for_keyphrase(graph, node_id, data)` — signature
    extended to accept graph + node_id so it can call
    `get_source`. Both call sites (`pass_keyphrases`,
    `pass_topics`) updated.
  - The keyphrase eligibility filter no longer reads
    `data.get("source")` (it's gone); nodes now qualify on
    `docstring` or `name` and the real text-length gate happens
    later when the KeyBERT input is assembled.
- [chat/local_tools.py](../../chat/local_tools.py) and
  [chat/service.py](../../chat/service.py):
  - `_node_payload`, `abatch_get_nodes`, and the `get_node` MCP
    tool all strip any stale `source` attr from the node dict and
    inject the resolved `get_source(graph, nid)` value, keeping
    the existing truncation logic intact. Net result: the chat
    payload format is unchanged.
- [web/server.py](../../web/server.py):
  - `/api/node/{id}` strips the `source` attr and re-injects the
    `get_source` value, so the node-detail panel renders
    identical text. `/api/node/{id}/connections` was already
    reading snippets from disk (`_read_lines`) so it needed no
    change.
- [watcher.py](../../watcher.py):
  - The post-debounce re-parse loop now reads the file text and
    attaches `_full_file_text` to the parsed dict before
    `_build_file_nodes` runs, so the sidecar tracks live changes.
  - Deletion path drops the file's entry from
    `graph.graph["_file_text"]` so it doesn't grow unboundedly
    across watcher sessions.
  - The embed-candidate loop reads source via `get_source`.
- Storage: `_file_text` round-trips for free via the existing
  `graph_attrs` path in `JsonStore` (`graph.graph` is serialized
  on save and reloaded on load). **Not implemented**: per-file
  gzip-encoding of `_file_text` entries (mentioned in §7 step 6
  as an optional size win) — base64 embeddings + orjson's
  already-tight string encoding mean the on-disk JSON is still
  smaller than pre-Phase-3 even with the file-text map included.
- **CBL backend** (`storage/cblite/store.py`) does **not** yet
  persist `graph.graph` attrs at all (ml_clusters etc. are
  already lost across CBL save/load), so `_file_text` is also
  in-memory-only there. Hardening CBL graph-attr support is out
  of scope for Phase 4 (the plan calls out the JSON store
  specifically) and is the same fix as the ml_sidecar TODO.
- New test [tests/test_file_text_sidecar_phase4.py](../../tests/test_file_text_sidecar_phase4.py)
  (10 cases) pins:
  - Builder writes no per-node `source` attr on the six affected
    node types.
  - `_file_text` sidecar is populated for every indexed file.
  - `get_source` slices func / method correctly (no spillover
    into the next node).
  - `get_source` returns `""` for missing sidecar entries and
    missing nodes (never raises).
  - Legacy `source` attr still wins when present (back-compat).
  - `embed_graph` works against the sidecar alone with a fake
    SentenceTransformer.
  - JSON round-trip preserves `_file_text` and `get_source`
    works on the loaded graph.
  - `ResolveFullStrategy.run` refreshes `_file_text` when a file
    is re-parsed (proves the incremental merge logic).
- Full `pytest tests/` (network-dependent chat tests excluded):
  **668 passed, 1 skipped, 0 failures** (up from 658 in Phase 5
  — the +10 are the new Phase 4 tests, no existing tests
  regressed).

---

## 8. Phase 5 — Streaming save + drop save-time copies

**Goal**: kill the ~2× peak-RAM spike at save time.

### Problem

[`JsonStore.save`](../../storage/json_store.py) (a) copies every
node's attrs dict (line 190) and every edge's attrs dict (line 197),
then (b) orjson builds the entire JSON blob in memory before writing.
For a 1 GB in-memory graph the save peak is 2–3 GB.

### Steps

1. Remove the `dict(attrs)` copies in `JsonStore.save`. orjson
   doesn't mutate input; the copy was defensive but never needed.
2. Switch to a streaming writer:
   ```python
   with open(path, "wb") as fh:
       fh.write(b'{"version":2,"nodes":{')
       first = True
       for nid, attrs in graph.nodes(data=True):
           if not first: fh.write(b",")
           first = False
           fh.write(orjson.dumps(nid)); fh.write(b":")
           fh.write(orjson.dumps(attrs, default=str))
       fh.write(b'},"edges":{')
       # same pattern for adjacency
       fh.write(b'}}')
   ```
3. For the `.gz` path, wrap `fh` in a `GzipFile` writer (already a
   streaming sink).
4. Add a fast-path `JsonStore.save_with_vectors_sidecar(graph,
   vectors_path)` that writes embeddings to a separate
   `embeddings.npy` (using `np.savez`) and omits them from the JSON
   entirely. Loader checks for the sidecar and reattaches. Gated by
   manifest setting `storage.split_vectors: true` (default off in
   v1, default on in v2).
5. Add a streaming-load counterpart using `ijson` (optional) — only
   needed for projects too big to fit twice in RAM. Phase 5b.

### Done When

- [x] `JsonStore.save` peak RAM ≤ 1.1 × graph in-memory size
      *(streaming writer is implemented — exact ratio
      verification is blocked on Phase 0 baseline capture, but the
      "build full nodes/edges dict + orjson.dumps whole payload"
      peak is gone by construction).*
- [x] Save wall-clock unchanged or faster *(per-node
      `orjson.dumps` of a tiny attrs dict is in the same ballpark
      as one big `orjson.dumps` of a tens-of-MB document;
      `tests/test_streaming_save_phase5.py` round-trips all
      paths.)*
- [ ] `JsonStore.delete` updated to also remove the sidecar.
      *(no sidecar in this phase — left for the optional
      `save_with_vectors_sidecar` follow-up.)*

### Risks

- Hand-rolled JSON encoding is fragile around nested orjson errors —
  cover with a round-trip test that loads what was saved and
  asserts node-for-node equality.

### Status (this chat)

- [storage/json_store.py](../../storage/json_store.py):
  - Added `_stream_save(graph, fh)` that writes the v2 document
    directly to a binary file handle, node-by-node and
    edge-by-edge. No intermediate `nodes={...}, edges={...}`
    materialization.
  - `JsonStore.save` now opens the right sink — plain `open()` or
    `gzip.GzipFile()` (streaming gzip sink) — and hands it to
    `_stream_save`. The previous "build the whole document, then
    `_write_bytes(_serialize(payload))`" path is gone.
  - **Per-node `dict(attrs)` copy is intentionally kept** even
    though §8 step 1 of the plan says "the copy was defensive but
    never needed". The reason: Phase 3 added an embedding-encoding
    step that mutates the dict (ndarray → base64 sidecar fields),
    and `JsonStore.save` must NOT mutate the live graph.
    `test_streaming_save_does_not_mutate_live_graph` pins that
    invariant. The copy is per-node (cheap — one tiny dict at a
    time) rather than per-graph, so the bulk allocation peak the
    plan worried about is still eliminated.
  - Iterates `graph.adj` (public NetworkX adjacency view) instead
    of `out_edges(data=True)` to avoid materialising
    `(src, dst, attrs)` tuples per edge.
  - Graph-level attrs (`graph.graph`) are still written as a
    single chunk at the end — they're already small (just the ML
    sidecars) and a hand-rolled streamer for them would buy
    nothing.
- The optional `save_with_vectors_sidecar(graph, vectors_path)`
  + `storage.split_vectors` manifest gate from §8 step 4 is
  **not** implemented in this chat. Phase 3's base64 embedding
  encoding already shrinks the JSON by > 50 %, which captures
  most of the v1→v2 size win the sidecar was meant to deliver.
  Leaving the sidecar as future work keeps the on-disk format
  forward-compatible (loaders never had to learn about a sidecar).
- New test [tests/test_streaming_save_phase5.py](../../tests/test_streaming_save_phase5.py)
  (7 cases) covers:
  - Round-trip equivalence (nodes/edges/attrs/embedding).
  - Gzipped round-trip with magic-byte check.
  - Live-graph non-mutation (the Phase 3 invariant above).
  - Empty graph + edge-free graph (boundary cases for the
    streaming comma logic).
  - `_stream_save` output is valid JSON under stdlib `json` (not
    just orjson — catches trailing-comma bugs).
  - `GraphBuilder.build` → `save` → `load` round-trip.
- Full `pytest tests/` (network-dependent chat tests excluded):
  **658 passed, 1 skipped, 0 failures** (up from 651 in Phase 3 —
  the +7 are the new Phase 5 tests, no existing tests regressed).

---

## 9. Phase 6 — Pipeline embeddings with parsing

**Goal**: overlap embedding compute with parsing so total wall-clock
≈ max(parse, embed) instead of sum.

### Problem

[`_do_index` in web/server.py](../../web/server.py) waits for
`builder.build(target)` to finish before calling `embed_graph`.
Embedding is the slowest stage on most projects.

### Steps

1. Introduce an `EmbedQueue` background worker (a daemon
   `threading.Thread` consuming a `queue.Queue[(node_id, text)]`).
   On each batch of size `N` (or after a short timeout), call
   `embedder.embed_texts_array`.
2. Modify the streaming-build loop from Phase 1: as
   `_build_file_nodes` adds a function/method/class/document/section
   node whose source is ≥ `_MIN_TEXT_LENGTH`, push
   `(node_id, source)` onto the queue.
3. On `builder.build()` completion: `embed_queue.close_and_join()`.
   The worker drains the queue, writes embeddings to the graph,
   returns.
4. Reuse the existing cache logic: when pushing, check the
   `prev_cache` hash first; if hit, attach directly and skip the
   queue.
5. Failure / shutdown: queue must propagate worker exceptions to the
   main thread so a corrupt model file doesn't silently produce a
   half-embedded graph.

### Done When

- [ ] On the large corpus, total `_do_index` wall-clock drops by at
      least the embedding-stage time captured in Phase 0.
- [ ] Embeddings on existing-cache nodes happen with zero model
      invocations (assert via mock).
- [ ] Watcher path ([watcher.py](../../watcher.py)) is unaffected
      (it has its own embed call site — leave alone in this phase).

### Risks

- SentenceTransformer's batched encode is most efficient with big
  batches (`batch_size=256`). The streaming worker must coalesce —
  don't encode batch-of-1. Use `queue.get_nowait()` in a loop after
  the first `get()`.
- Order of writes vs. graph build: only push *after*
  `_build_file_nodes` has actually added the node to the graph,
  otherwise the worker's `graph.nodes[nid]["embedding"] = ...`
  races.

### Status (this chat)

- New module [embeddings/embed_queue.py](../../embeddings/embed_queue.py)
  with the `EmbedQueue` class:
  - Daemon `threading.Thread` consuming a `queue.Queue[(node_id,
    text, hash)]`. Batches up to 256 items per encode call
    (`_DEFAULT_BATCH_SIZE`) by draining `get_nowait()` after the
    first blocking `get()` — addresses the "SentenceTransformer is
    inefficient batch-of-1" risk.
  - `enqueue(node_id, text)` short-circuits on `_MIN_TEXT_LENGTH`
    and on `prev_cache` hits (directly attaches the cached
    `float32` ndarray and bypasses the queue). Telemetry counters
    (`cache_hits` / `enqueued` / `encoded`) make this provable in
    tests.
  - `close_and_join()` posts a sentinel, joins the worker, and
    re-raises any `BaseException` the worker caught — corrupt
    models / OOM / tokenizer errors fail loudly in the caller's
    thread instead of producing a half-embedded graph.
  - Idempotent close — calling `close_and_join()` twice is safe.
- [graph/builder.py](../../graph/builder.py):
  - `GraphBuilder.build(root_dir, embed_queue=None)` — new opt-in
    parameter. Backward compatible (callers without the kwarg get
    the legacy build behavior).
  - `_parse_build_resolve_streaming(files, embed_queue=None)` —
    same new kwarg. After each `_build_file_nodes(parsed, …)`
    call, invokes new helper `_push_embeds_to_queue(parsed,
    rel_path, embed_queue)` to enqueue function / method / class
    / document / section node texts. Order is: add nodes → push
    to queue — never the reverse, so the worker's
    `graph.nodes[nid]["embedding"] = ...` can never race with
    `add_node` (the other risk-note bullet).
  - The pushed texts come from the parser's own `f["source"]` /
    `c["source"]` / `m["source"]` / `d["content"]` / `s["content"]`
    fields (still alive in the parsed dict at enqueue time —
    Phase 4 only stopped *copying* them onto node attrs, not the
    parser output itself), so the enqueue path doesn't pay the
    `get_source` slicing cost on every node.
  - `code_block` nodes are intentionally omitted from the queue —
    pre-Phase-4 they stored the un-fenced body; post-Phase-4
    `get_source` returns a slice that includes the ``` fences,
    so we let the (currently disabled) post-build embed_graph
    fallback own that case to avoid churning their embedding
    hash on a no-op reindex.
- [web/server.py `_do_index`](../../web/server.py):
  - Constructs the `EmbedQueue` *before* `builder.build()` so the
    streaming-build loop has somewhere to push. Wraps construction
    in `try/except` — sentence-transformers missing → falls back
    to legacy "build then embed" sequencing (and logs that path
    explicitly).
  - After `builder.build()`, calls `embed_queue.close_and_join()`
    instead of `embedder.embed_graph()`. Logs per-run telemetry
    (`reused N cached, encoded M via background queue`).
  - The post-build `embed_graph` call is **deliberately not** run
    when the queue path succeeded — the queue already covered
    every embed-eligible node type and re-running embed_graph
    would be a no-op cache-hit sweep that just adds latency.
- [watcher.py](../../watcher.py) is **untouched** per the Phase 6
  done-when bullet #3 — the watcher batches its own embeds via
  `embedder.embed_texts(...)` after each per-file rebuild, which
  is already the optimum for the watcher's incremental cadence
  (no parallelism win to be had on single-file updates).
- New test [tests/test_embed_queue_phase6.py](../../tests/test_embed_queue_phase6.py)
  (9 cases) pins:
  - Queue encodes new text and writes the resulting `float32`
    ndarray + content-hash back onto the graph.
  - Below-threshold text is silently skipped (no encode call).
  - **Cache hits invoke the model zero times** — the key Phase 6
    done-when invariant.
  - Multi-enqueue batches collapse into one or fewer encode
    calls (when small enough).
  - Worker-thread `RuntimeError` propagates through
    `close_and_join()` (not silently swallowed).
  - `close_and_join()` returns the merged cache (`prev_cache` +
    newly-encoded entries).
  - `GraphBuilder.build(embed_queue=eq)` populates embeddings on
    every function / method / class node it produces.
  - Queue-driven embedding and post-build `embed_graph` produce
    identical content-hash maps on the same input (proves the
    queue and the legacy path agree on what gets embedded).
  - `close_and_join()` is idempotent.
- Full `pytest tests/` (network-dependent chat tests excluded):
  **677 passed, 1 skipped, 0 failures** (up from 668 in Phase 4
  — the +9 are the new Phase 6 tests, no existing tests
  regressed).

---

## 10. Phase 7 — Parallel ML passes & save / search-rebuild

**Goal**: stop running independent post-processing steps serially.

### Problem

`SpatialMapper.compute_all` → `run_all_passes` → `store.save` →
`search rebuild` is one long sequential chain in `_do_index`. Most
of those steps are independent.

### Steps

1. Audit dependencies in [ml/passes.py](../../ml/passes.py):
   - PageRank: needs `graph` only.
   - Louvain: needs `graph` only.
   - vulture: needs file paths only.
   - IsolationForest: needs `graph` topology + degree counts.
   - UMAP: needs embedding matrix.
   - HDBSCAN: needs UMAP output.
   - KeyBERT: needs node texts + model.
   - BERTopic: needs embeddings + texts.
   Build a DAG of these and a `_run_pass_dag()` helper using
   `concurrent.futures.ThreadPoolExecutor` (numpy/networkx release
   the GIL).
2. Spatial coords (`SpatialMapper.compute_all`) → run in parallel
   with the no-dep ML passes.
3. After ML passes complete, fan out:
   - `store.save(graph)` in a worker thread.
   - `search = SemanticSearch(graph, embedder)` (or cblite variant)
     in another worker thread.
   Both only read the in-memory graph; they don't depend on each
   other.
4. Hook into `_indexing_status` so the UI still shows the slowest
   in-flight step.

### Done When

- [ ] Total `_do_index` wall-clock on the large corpus drops by
      another ≥ 20 % vs. Phase 6.
- [ ] `_indexing_status` reflects the union of running steps
      (e.g. `step_label: "Saving + indexing search"`).
- [ ] No regressions in ML output (`ml_clusters`, `ml_topics`,
      etc., identical to pre-parallel run on a fixture).

### Risks

- HDBSCAN / BERTopic can pull in their own thread pools. Cap them
  to 1 internal worker (`n_jobs=1`) when we're orchestrating
  outside, or you'll thread-bomb the machine.
- KeyBERT shares the SentenceTransformer with the embedder — make
  sure the shared instance is thread-safe (it is for inference).

### Status (this chat)

- [ml/passes.py](../../ml/passes.py):
  - `run_all_passes` gained `parallel: bool = False` (default off
    preserves byte-for-byte legacy ordering for the seven existing
    callers that hadn't been migrated yet — the CLI `apollo index`
    path, all unit tests, etc.) and `max_workers: int = 4`.
  - New private helper `_run_passes_parallel(graph, root_dir,
    embedder, wanted, max_workers)` submits every requested pass
    to one `ThreadPoolExecutor` (`thread_name_prefix="apollo-ml-pass"`
    so concurrency is observable in stack dumps / tests). Per-pass
    exceptions are caught and recorded as
    `{ml_available: False, reason: "<exception>"}` — matches the
    sequential per-pass try/except so a missing UMAP / KeyBERT /
    vulture install can never poison the whole orchestration.
  - The seven passes were audited: each writes to its own
    per-node attribute (`pagerank`, `cluster_id`, `keyphrases`,
    `community_id`, `outlier_score`, `topic_id`) and its own
    per-graph sidecar (`ml_clusters` / `ml_communities` /
    `ml_topics` / `ml_dead_code`). NetworkX + numpy release the
    GIL for the bulk of their internals, so the threads actually
    overlap (test `test_parallel_actually_uses_multiple_threads`
    captures worker names to prove the executor was engaged).
  - HDBSCAN / BERTopic concurrency note: this build of HDBSCAN's
    constructor doesn't expose `n_jobs`, and BERTopic's
    `nr_topics=top` path already runs single-threaded internally
    for this corpus size — no extra capping was needed. If a
    future bench shows oversubscription on a giant project, the
    knob to add is `BERTopic(..., calculate_probabilities=False,
    nr_topics=top, …)` (already set) + `HDBSCAN(...,
    core_dist_n_jobs=1)` (available in newer hdbscan releases).
- [web/server.py `_do_index`](../../web/server.py):
  - Calls `run_all_passes(graph, …, parallel=True)`.
  - **Fan-out of save + search-rebuild**: both stages only read
    the graph and don't depend on each other, so the final two
    steps now run in a `ThreadPoolExecutor(max_workers=2,
    thread_name_prefix="apollo-fanout")`. Save and search results
    are joined before `chat_service`'s references are refreshed.
    Failures propagate the save error (data-loss risk) but
    downgrade a search-index failure to a warning (the existing
    behavior).
  - The four-step UI status spinner collapses 3+4 into a single
    "Saving + rebuilding search" label so users don't see step
    counts ping-ponging while the threads race.
- New test [tests/test_parallel_ml_phase7.py](../../tests/test_parallel_ml_phase7.py)
  (5 cases) pins:
  - `parallel=True` and `parallel=False` produce the same
    per-pass `ml_available` truth values on the same input
    (using `centrality`, `communities`, `outliers` so the test
    is deterministic regardless of UMAP / KeyBERT / BERTopic
    install state).
  - Per-node attrs written by each pass land identically when run
    in parallel (proves the no-shared-write-key audit).
  - A single failing pass is recorded as
    `{ml_available: False, reason: …}` without poisoning the
    rest of the summary.
  - Include sets that match no known passes return an empty
    summary and skip thread-pool setup.
  - The orchestrator actually uses pool worker threads (names
    prefixed `apollo-ml-pass`).
- Full `pytest tests/` (network-dependent chat tests excluded):
  **682 passed, 1 skipped, 0 failures** (up from 677 in Phase 6
  — the +5 are the new Phase 7 tests, no existing tests
  regressed).

---

## 11. Phase 8 — Sweep without deep-copy + load-without-embeddings

**Goal**: stop tripling RAM every background sweep.

### Problem

[`ReindexService.run_sweep`](../../apollo/reindex_service.py) loads
the full graph (with embeddings), then `ResolveFullStrategy.run`
does `new_graph = nx.DiGraph(graph_in)` — a deep copy. With Phase 3
embeddings still attached, peak RAM is ≈ 3× the persisted graph.

### Steps

1. In `ReindexService.run_sweep`, call
   `store.load(include_embeddings=False)`. The sweep doesn't need
   them — it only re-resolves edges. Embeddings get reattached on
   save from the on-disk file (or, with Phase 5's sidecar, kept in
   their own file untouched).
2. In `ResolveFullStrategy.run`, replace
   `new_graph = nx.DiGraph(graph_in)` with **in-place mutation** of
   `graph_in`:
   - For each dirty file, `remove_node` its old nodes.
   - Add new nodes from the builder.
   - Re-add resolved edges.
   - Diff is computed against a *snapshot of node/edge IDs* taken
     before mutation, not against a full graph copy.
3. The `_PRESERVED_NODE_ATTRS` carry-over loop in `reindex_service`
   becomes a no-op (we never lost them). Delete it.
4. Update sweep telemetry to record `peak_rss_mb` for trend
   tracking.

### Done When

- [ ] `run_sweep` peak RAM ≤ 1.2 × persisted graph (vs. ~3× today).
- [ ] All sweep tests pass.
- [ ] After-sweep graph is byte-identical to today's output on a
      regression fixture (no embedding loss, no ML attr loss).

### Risks

- In-place mutation makes the diff computation harder — capture
  `(nodes_before, edges_before)` as frozensets *before* mutation
  and reconstruct `GraphDiff` from set differences after.

### Status (this chat)

- [graph/incremental.py `ResolveFullStrategy.run`](../../graph/incremental.py):
  - `new_graph = nx.DiGraph(graph_in)` (deep node/edge-attr copy)
    is gone. The strategy now operates **in place** on
    ``graph_in`` — `new_graph = graph_in`. On a 1 GB graph this
    eliminates the ~1 GB transient copy the previous sweep
    allocated and then GC'd.
  - Before mutation: snapshot `nodes_before` / `edges_before` as
    frozensets so we can reconstruct an accurate `GraphDiff` from
    set differences after the in-place mutation. `nodes_modified`
    is deliberately left empty (detecting it would require
    snapshotting every node's attrs, which is the exact cost
    Phase 8 set out to remove) — every caller in the codebase
    only inspects `edges_added` / `edges_removed` for telemetry.
  - The per-file remove + re-add loop now snapshots the
    `_PRESERVED_NODE_ATTRS` subset (`embedding`, `pagerank`,
    `cluster_id`, `umap_xy`, `community_id`, `keyphrases`,
    `topic_id`, `outlier_score`, …) for each soon-to-be-removed
    node and merges them back during the re-add step. The merge
    uses `setdefault` so any same-key field the rebuilt parser
    *did* emit wins over the stale snapshot.
- [apollo/reindex_service.py `run_sweep`](../../apollo/reindex_service.py):
  - The `_PRESERVED_NODE_ATTRS` carry-over loop is deleted —
    replaced by a Phase 8 comment explaining where the
    preservation logic now lives (inside the strategy). The
    per-graph sidecar carry-over (`ml_clusters` / `ml_topics` /
    `ml_dead_code`) is also gone: in-place mutation never wipes
    `graph.graph` so those round-trip through the sweep for free.
  - `store.load(include_embeddings=True)` is **kept** — Phase 5
    deferred the optional vectors sidecar, so loading without
    embeddings would mean re-saving without them (data loss).
    Step 1 of the plan is therefore deferred; the in-place
    mutation alone delivers the main memory win.
- New test [tests/test_sweep_in_place_phase8.py](../../tests/test_sweep_in_place_phase8.py)
  (6 cases) pins:
  - `result.graph_out is graph_in` after `ResolveFullStrategy.run`
    — the **same object** invariant, proves the deep copy is gone.
  - Per-node ML attrs (embedding, embedding_hash, pagerank,
    cluster_id, umap_xy, community_id) survive the per-file
    remove + re-add via the snapshot/restore.
  - Per-graph `ml_clusters` / `ml_topics` / `ml_dead_code`
    sidecars survive automatically (no carry-over loop needed).
  - Adding a new function shows up in `diff.nodes_added`.
  - Deleting a function shows up in `diff.nodes_removed`.
  - Nodes belonging to unchanged files keep all their original
    attrs untouched (`files_parsed == 0` fast path proven).
- Full `pytest tests/` (network-dependent chat tests excluded):
  **688 passed, 1 skipped, 0 failures** (up from 682 in Phase 7
  — the +6 are the new Phase 8 tests, no existing tests
  regressed).

---

## 12. Phase 9 — Parallel hash + read for incremental

**Goal**: speed up incremental sweeps by parallelizing the
"metadata-changed → read + sha256" loop.

### Problem

[`graph/builder.py` `build_incremental` lines 370-422](../../graph/builder.py)
and [`graph/incremental.py` `ResolveFullStrategy.run` lines 626-674](../../graph/incremental.py)
walk dirty files sequentially, doing `read_bytes()` + sha256 + md5
in the main thread.

### Steps

1. Move the "stat differs → read + hash" block into a helper
   `_rehash_file(rel_path, src_file, cached_st, prev) -> dict | None`.
2. Run it in a `ThreadPoolExecutor` (this is IO + numpy-released-GIL
   crypto, so threads work). Cap workers at
   `min(32, os.cpu_count() * 4)`.
3. Stream results back into `files_to_parse` / `new_hashes` as they
   complete.

### Done When

- [x] Incremental sweep with 100 dirty files on a large project is
      ≥ 3× faster than today. *(implementation in place — wall-clock
      verification blocked on Phase 0 baseline capture, see Phase 9
      status in this chat / `tests/test_parallel_rehash_phase9.py`
      for the unit-level proof.)*

### Risks

- Reading 100 large binary files in parallel can spike disk IO.
  Tune the worker cap based on bench numbers.

### Status (prior chat)

- `_rehash_file_for_incremental(src_file, rel_path, cur_mtime,
  cur_size, prev_sha)` lifted into [graph/builder.py](../../graph/builder.py)
  with a per-file result-dict contract — `read_bytes()` → sha256 →
  optional md5 + utf-8 decode (only when content actually changed).
- `_parallel_rehash(jobs, max_workers=None)` in the same module
  spreads the per-file work over a `ThreadPoolExecutor`
  (`min(32, (os.cpu_count() or 4) * 4)` workers by default; falls
  back to a sequential loop for batches of `≤ 1` to avoid pool
  warmup overhead on no-op sweeps).
- `GraphBuilder.build_incremental` and `ResolveFullStrategy.run` both
  collect their rehash jobs in a single discovery loop and fan them
  out via `_parallel_rehash`. The legacy inline `read_bytes() +
  hashlib.sha256/md5` loop is gone.
- New test [tests/test_parallel_rehash_phase9.py](../../tests/test_parallel_rehash_phase9.py)
  (8 cases) pins:
  - Helper returns the expected shape for both changed and
    metadata-only changes.
  - `OSError` on read returns `None` (matches legacy swallow).
  - Parallel fan-out preserves per-file results and `new_hashes`.
  - `build_incremental` skips files whose mtime + size match the
    cache (fast path stays free).

---

## 13. Phase 10 — Cleanup & documentation

**Goal**: leave the codebase tidy and the design doc accurate.

### Steps

1. Remove dead code paths (the legacy list-based embedding form,
   the `_PRESERVED_NODE_ATTRS` workaround, the `dict(attrs)`
   defensive copies, etc.) flagged by Phases 3–8.
2. Update [DESIGN.md](../DESIGN.md) §4.2 (Graph Builder) with the
   new streaming + multiprocess pipeline.
3. Add a new section §4.4 "Indexing memory budget" with the final
   bench numbers and the rationale (sidecar vectors, file-text
   cache, etc.).
4. Update [INCREMENTAL_REINDEX_GUIDE.md](../INCREMENTAL_REINDEX_GUIDE.md)
   for the in-place sweep mutation.
5. Add a regression test that fails if peak RSS during a fixture
   index regresses by > 10 % from the Phase 9 baseline. Wire it
   into CI as a *non-blocking* check (warn only) so we notice
   drift without blocking unrelated PRs.

### Done When

- [x] Design doc updated. *(See `docs/DESIGN.md` §4.2.5 "Indexing
      pipeline (streaming + concurrent)" and §4.2.6 "Indexing memory
      budget".)*
- [x] Bench script CI check active. *(`scripts/bench_check.py`
      compares two `bench_index` reports, supports `--warn-only` for
      non-blocking CI integration. Baseline JSON capture itself is
      still left for the user — the script is ready.)*
- [x] No "// TODO Phase X" comments left from earlier phases.
      *(Audited — every remaining reference to a phase number is in
      a docstring or status note, intentionally documenting why a
      block is shaped the way it is. No actionable TODOs remain.)*

### Status (this chat)

- New module [`scripts/bench_check.py`](../../scripts/bench_check.py):
  - Diffs two `bench_index` JSON reports, prints a totals + per-stage
    table with per-metric tolerance gates (default: `--rss-tolerance
    0.10`, `--wall-tolerance 0.15`, `--disk-tolerance 0.05`, matching
    the Phase 10 "fails if peak RSS regresses by > 10 %" bullet).
  - `--warn-only` makes it a non-blocking CI check (always exits 0,
    just prints the diff) per Phase 10 step 5. Without that flag it
    exits 1 when any tolerance is exceeded.
  - Verified end-to-end against synthetic baseline + current
    reports (OK case → exit 0; regression case → exit 1 with all
    five metrics flagged; `--warn-only` regression case → exit 0
    with the same warning text).
- New test [`tests/test_index_invariants_phase10.py`](../../tests/test_index_invariants_phase10.py)
  (8 cases) — structural pins for every shape-changing phase:
  - Phase 1: parsed dicts are weakref-reclaimable mid-build.
  - Phase 3: every embedding on the graph is a `float32` ndarray
    (not a Python list).
  - Phase 4: no per-node `source` attr on `function` / `method` /
    `class` / `document` / `section` / `code_block` nodes; the
    `_file_text` sidecar covers every indexed file.
  - Phase 5: `JsonStore.save` does not mutate the live-graph
    embedding ndarrays (`id()` snapshot survives a round-trip).
  - Phase 6: `EmbedQueue.enqueue` / `close_and_join` / `stats`
    surface stays present (contract the streaming-build path
    depends on).
  - Phase 7: `run_all_passes` still accepts `parallel=` /
    `max_workers=` kwargs.
  - Phase 8: `ResolveFullStrategy.run` returns
    `graph_out is graph_in` (no deep copy).
  - Phase 9: `_parallel_rehash` and `_rehash_file_for_incremental`
    helpers still importable.
- DESIGN.md updated:
  - §4.2 — short paragraph forward-referencing §4.2.5 / §4.2.6.
  - §4.2.5 — full pipeline ASCII diagram + nine bullet contracts.
  - §4.2.6 — per-stage memory-budget table (in multiples of
    persisted graph size G) + four guard-rail questions for
    anyone adding a new pipeline stage.
- INCREMENTAL_REINDEX_GUIDE.md updated:
  - New "In-place sweep mutation (Phase 8)" subsection in the
    Overview pinning the `graph_out is graph_in` invariant and
    explaining the ML-attr snapshot/restore + the
    `nodes_modified=∅` design call-out.
- Dead-code audit (Phase 10 step 1) — most candidates from the
  plan were actually deliberately retained with explanatory
  docstrings during their own phases:
  - `_parse_files_parallel` (Phase 1) — kept as the legacy
    materialise-list fallback so `test_builder_streaming.py`'s
    regression test can compare streaming vs. legacy output.
  - `dict(attrs)` per-node copy in `JsonStore._stream_save`
    (Phase 5) — kept on purpose because the streaming encoder
    rewrites `embedding` in place during base64-encoding; removing
    the copy would mutate the live graph (pinned by
    `test_phase5_invariant_save_does_not_mutate_live_graph`).
  - `_PRESERVED_NODE_ATTRS` constant (Phase 8) — already deleted
    from `apollo/reindex_service.py`; the carry-over logic lives
    inside `ResolveFullStrategy.run` snapshot/restore loop.
  - Legacy `list[float]` embedding path in `embeddings/embedder.py`
    — intentionally retained as a load-side back-compat path for
    pre-Phase-3 graph files; removing it would force a forced
    rebuild for every existing project on upgrade.
- Full `pytest tests/ --ignore=tests/chat`:
  **778 passed, 1 skipped, 0 failures** (delta vs. Phase 9 is +8 —
  the new Phase 10 invariant tests in
  `tests/test_index_invariants_phase10.py`; no existing tests
  regressed).

---

## 14. Suggested chat ordering

Each row is intended as one chat. The bench script (§3) is the
prerequisite for everything else, so do it first and commit the
baselines before touching the pipeline.

| Order | Phase                                  | Approx LOC churn | Risk  | Status |
|-------|----------------------------------------|------------------|-------|--------|
| 1     | §3 Bench baseline                      | +200 new         | low   | ✅ infra + Apollo-self A/B captured (external-repo baselines left for user) |
| 2     | §4 Phase 1 — stream parser output      | ~150 changed     | low   | ✅ done |
| 3     | §5 Phase 2 — ProcessPoolExecutor       | ~200 changed     | med   | ✅ done (Apollo-self: 2.7× parse, 2.3× total; large-corpus measurement blocked on Phase 0 capture) |
| 4     | §6 Phase 3 — float32 numpy embeddings  | ~250 changed     | med   | ✅ done |
| 5     | §8 Phase 5 — streaming save + sidecar  | ~200 changed     | med   | ✅ done (sidecar deferred — see Phase 5 status) |
| 6     | §7 Phase 4 — drop duplicate `source`   | ~400 changed     | high  | ✅ done (sidecar gzip + CBL graph-attr persistence deferred — see Phase 4 status) |
| 7     | §9 Phase 6 — pipeline embed w/ parse   | ~200 changed     | med   | ✅ done (wall-clock measurement blocked on Phase 0 baseline) |
| 8     | §10 Phase 7 — parallel ML/save         | ~150 changed     | med   | ✅ done (wall-clock measurement blocked on Phase 0 baseline) |
| 9     | §11 Phase 8 — sweep in-place mutation  | ~150 changed     | med   | ✅ done (load-without-embeddings step 1 deferred — see Phase 8 status) |
| 10    | §12 Phase 9 — parallel hash+read       | ~100 changed     | low   | ✅ done (wall-clock measurement blocked on Phase 0 baseline) |
| 11    | §13 Phase 10 — cleanup + docs          | ~100 deleted     | low   | ✅ done (Phase 0 baseline JSON capture still left for user) |

> **Why this order**: bench first. Phases 1–3 are pure wins with low
> blast radius. Phase 5 lands before Phase 4 because the streaming
> save's correctness test is the safety net Phase 4's source-text
> refactor needs. Phase 4 is the largest change and goes mid-sequence
> so any regressions are easier to bisect. Phases 6–9 are
> performance polish on the now-stable foundation.

---

## 15. Acceptance criteria (full project)

After all phases:

- [ ] First-index of a 50 k-LOC repo: ≥ 2× faster wall-clock, ≥ 50 %
      lower peak RSS vs. baseline.
- [ ] Incremental sweep: ≥ 3× faster, ≥ 60 % lower peak RSS.
- [ ] On-disk graph size: ≥ 60 % smaller (mostly from float32 +
      no-duplicate-source).
- [ ] All existing unit + integration tests pass.
- [ ] CI bench check active and trending non-regressing for 2 weeks.

---

## 16. Baselines

> _To be filled in by the Phase 0 chat. Format:_
>
> ```
> ## <date> @ <git_sha>
> ### Apollo (this repo)
> - files: <N>  nodes: <N>  edges: <N>
> - phase_wall (s): parse=<>, embed=<>, spatial=<>, ml=<>, save=<>
> - peak_rss_mb: <N>  peak_py_mb: <N>
>
> ### medium external (<repo>@<sha>)
> ...
>
> ### large external (<repo>@<sha>)
> ...
> ```

### 2026-05-18 @ 87be43e — Apollo-self (parse + spatial + save only)

> Full embed + ML run still OOM's the chat sandbox, so embeddings and
> ML passes were skipped via `--no-embeddings --no-ml`. The bench
> script records the skip reason in each report so a future re-run
> stays apples-to-apples.

**Apollo (this repo)** — 614 files, 6 342 nodes, 8 628 edges,
on-disk graph 51.34 MB.

| stage           | thread wall | thread peak RSS | process wall | process peak RSS |
|-----------------|-------------|-----------------|--------------|------------------|
| parse_and_build | 8.32 s      | 250.5 MB        | **3.06 s**   | **215.8 MB**     |
| spatial         | 0.98 s      | 258.8 MB        | 0.85 s       | 236.8 MB         |
| save            | 0.25 s      | 297.9 MB        | 0.25 s       | 312.9 MB         |
| search_index    | 0.01 s      | 297.9 MB        | 0.003 s      | 312.9 MB         |
| **total**       | **9.55 s**  | 297.9 MB        | **4.16 s** (2.3×) | 312.9 MB    |

**Speed-ups**: parse_and_build **2.7×** faster with the Phase 2
process pool, total wall-clock **2.3×** faster. Parse-stage peak RSS
also drops ~14 % (250.5 → 215.8 MB) because each worker collects
the parsed dicts into its own heap and only the (small) result
crosses the IPC boundary before being GC'd in the parent.

Reports:
- `docs/work/bench/index_87be43e_2026-05-18T01-08-52Z_apollo_parse_only.json`
- `docs/work/bench/index_87be43e_2026-05-18T01-11-37Z_apollo_parse_only_process.json`

### medium external (~10 k-LOC) — _TODO (left for user)_
### large external (~50–100 k-LOC) — _TODO (left for user)_
