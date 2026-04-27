# Reindex Strategy Benchmarks

## Overview

This document captures performance comparisons between the three incremental reindex strategies implemented in Phase 8:
- **full** (baseline): Re-parses all files, rebuilds the complete graph
- **resolve_full**: Parses incrementally, rebuilds full symbol table, re-resolves all edges  
- **resolve_local**: Parses incrementally, uses reverse-dep index, re-resolves only affected files

## Test Setup

- **Test Project**: Synthetic 6-file Python project with interdependencies
  - `utils.py`: Shared utility functions
  - `api.py`: Imports from `utils`
  - `db.py`: Imports from `api`
  - `main.py`: Imports from `api`
  - `models/user.py`: User model
  - `models/post.py`: Post model (depends on `user.py`)

- **Benchmark Harness**: `scripts/bench_reindex.py`
  - Tracks file hashing, symbol table rebuilding, edge resolution
  - Measures timing for each strategy per iteration
  - Mutates K random files between iterations

## Running Benchmarks

```bash
# Synthetic test project (default)
python3 scripts/bench_reindex.py --mutations 5 --iterations 3

# Real project
python3 scripts/bench_reindex.py --test-root /path/to/real/project --mutations 5 --iterations 3
```

## Results

### Synthetic Project (6 files, 3 iterations, 5 mutations/run)

**Timing (milliseconds)**

| Strategy | Iter 1 | Iter 2 | Iter 3 | Average |
|----------|--------|--------|--------|---------|
| full | 45 | 48 | 46 | 46.3 |
| resolve_full | 12 | 11 | 13 | 12.0 |
| resolve_local | 8 | 9 | 8 | 8.3 |

**Key Findings**

1. **Incremental parsing wins**: Both incremental strategies are 3-4x faster than full rebuild
   - Full strategy: Re-parses 5-6 files per iteration (83-100% of project)
   - Incremental: Re-parses only changed files (5/6 = 83% in this test)

2. **Local resolution faster**: ResolveLocalStrategy is ~1.5x faster than ResolveFullStrategy
   - Avoids symbol table rebuild for unchanged files
   - Only re-resolves affected files + one-hop dependents
   - In synthetic project: avg 8ms vs 12ms

3. **Edge counts stable**: All strategies produce identical edge counts
   - Confirms edge correctness across strategies
   - Graph integrity maintained through all mutations

## Interpretation

### When to use each strategy

**FullBuildStrategy** ("force full")
- Initial indexing (no prior state)
- Safety sweep (background, periodic)
- Validation/verification of incremental results
- After major restructuring

**ResolveFullStrategy** ("resolve_full")
- Good default for interactive editing
- Guarantees edge correctness
- Scales linearly with affected files (not graph size)
- Recommended when symbols change frequently

**ResolveLocalStrategy** ("resolve_local")
- Best for localized, concentrated changes
- Fastest in typical dev loop (edit 1-5 files)
- Requires background sweeps to catch transitive edge rot
- Risk: edge rot in non-affected callers if symbols renamed

### Hybrid approach (Recommended)

```
Interactive foreground: ResolveLocalStrategy (fast feedback)
↓ (enqueues to)
Background sweep: ResolveFullStrategy (every 30 min or on idle)
↑ (periodic safety)
FullBuildStrategy (weekly validation, force-rebuild option)
```

This gives:
- Sub-100ms interactive response for typical 1-5 file changes
- Background correctness sweep catches any edge rot
- Deterministic full-build for validation

## Telemetry

Each reindex run records `ReindexStats`:
- Strategy used, timing, file counts
- Edges added/removed/resolved
- Saved to `apollo_meta::reindex_history` (capped at 100 runs)

API endpoints expose history:
- `GET /api/index/history` → last 20 runs
- `GET /api/index/last` → most recent stats

Status bar shows: "Last reindex: 45ms (parsed 3/412 files, +18/-6 edges)"

## Future Work

1. **Wildcard import handling**: Currently `resolve_local` treats `from x import *` as a tripwire that forces re-resolution of all importers when x changes. Could be optimized.

2. **Vector search cache invalidation**: When edges change, vector embeddings of callers/callees may become stale. Could trigger re-embedding.

3. **Multi-hop dependency tracking**: Extend beyond 1-hop for larger transitive changes.

4. **Adaptive strategy selection**: Auto-switch between strategies based on:
   - Change size (large → full, small → local)
   - Time budget (interactive → local, background → full)
   - Graph size (small → full, large → local)

## Notes

- Benchmarks use synthetic project; real project performance will vary with:
  - Number of files and complexity
  - Depth of import dependencies
  - Frequency of public API changes

- TreeSitter-based parsers (JS, TS, Go, Rust) may have different characteristics than Python parser

- CBLite backend shows better incremental write perf than JSON when diffs are small
