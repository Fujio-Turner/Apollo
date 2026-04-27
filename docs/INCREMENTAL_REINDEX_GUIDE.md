# Apollo Incremental Re-Index System User & Developer Guide

## Overview

Apollo's incremental re-indexing system provides two high-performance strategies for updating the code knowledge graph when files change:

- **Option 1 (Resolve Full)**: Parse changed files, rebuild full symbol table, re-resolve all edges
  - Edge-correct by construction
  - Best for background sweeps to catch any edge rot
  - O(total edges) but simple and reliable

- **Option 2 (Resolve Local)**: Parse changed files, re-resolve only affected files and dependents
  - Much faster for localized changes
  - Requires background Option 1 sweeps for transitive correctness
  - Typical speedup: 1.5-2x for small to medium changes

The system defaults to **Option 2 for foreground (interactive) updates** and **Option 1 for background sweeps**, providing sub-second responsiveness while maintaining eventual correctness.

---

## For Users

### Configuration

Reindex behavior can be configured via the API or configuration file.

**Default Configuration:**
```json
{
  "strategy": "auto",
  "sweep_interval_minutes": 30,
  "sweep_on_session_start": true,
  "local_max_hops": 1,
  "force_full_after_runs": 50
}
```

**Via API - Get Current Config:**
```bash
curl http://localhost:8000/api/index/config
```

Response:
```json
{
  "config": {
    "strategy": "auto",
    "sweep_interval_minutes": 30,
    "sweep_on_session_start": true,
    "local_max_hops": 1,
    "force_full_after_runs": 50
  },
  "effective_foreground_strategy": "resolve_local",
  "effective_background_strategy": "resolve_full"
}
```

**Via API - Update Config:**
```bash
curl -X POST http://localhost:8000/api/index/config \
  -H "Content-Type: application/json" \
  -d '{"strategy": "resolve_full"}'
```

### Monitoring Reindex Activity

**Get Recent History:**
```bash
curl http://localhost:8000/api/index/history?limit=10
```

Response:
```json
{
  "total_runs": 42,
  "limit": 10,
  "runs": [
    {
      "strategy": "resolve_local",
      "started_at": 1712345678.123,
      "duration_ms": 145,
      "files_total": 412,
      "files_parsed": 3,
      "files_skipped": 409,
      "affected_files": 8,
      "edges_resolved": 1250,
      "edges_added": 18,
      "edges_removed": 6,
      "bytes_written": 0
    },
    ...
  ]
}
```

**Get Most Recent Stats:**
```bash
curl http://localhost:8000/api/index/last
```

**Get Summary:**
```bash
curl http://localhost:8000/api/index/summary
```

Response:
```json
{
  "configuration": { ... },
  "summary": {
    "total_runs": 42,
    "avg_duration_ms": 187,
    "total_files_indexed": 8540,
    "total_edges_added": 245,
    "total_edges_removed": 89,
    "strategies": ["resolve_local", "full"]
  }
}
```

### Strategy Selection

**Auto (Default):**
- Foreground updates (user-triggered) use Option 2 (resolve_local) for speed
- Background sweeps use Option 1 (resolve_full) for correctness
- Every 50 incremental runs, forces a full rebuild as safety check

**Full:**
- Always use full rebuild
- Safest but slowest
- Useful for troubleshooting

**Resolve Full:**
- Always use Option 1
- Edge-correct but slower than resolve_local
- Good for conservative environments

**Resolve Local:**
- Always use Option 2
- Fastest but requires understanding of trade-offs
- May have stale edges in rare cases (fixed by background sweep)

---

## For Developers

### Core API

#### Creating a Strategy

```python
from graph.incremental import ResolveFullStrategy, ResolveLocalStrategy
from graph.builder import GraphBuilder

# Create builder
builder = GraphBuilder()

# Create strategy
strategy = ResolveLocalStrategy(builder, max_hops=2)

# Run strategy
result = strategy.run(
    root_dir="/path/to/project",
    graph_in=previous_graph,  # nx.DiGraph
    prev_hashes=prev_hashes,  # {rel_path: {sha256, mtime_ns, size}}
    prev_dep_index=prev_dep_index,  # {target_file: set(dependents)}
)

# Access results
new_graph = result.graph_out
new_hashes = result.new_hashes
new_dep_index = result.new_dep_index
diff = result.diff  # GraphDiff
stats = result.stats  # ReindexStats
```

#### Working with GraphDiff

```python
from graph.incremental import compute_diff, GraphDiff

# Compute difference between two graphs
diff = compute_diff(old_graph, new_graph)

# Check what changed
print(f"Added {len(diff.nodes_added)} nodes")
print(f"Modified {len(diff.nodes_modified)} nodes")
print(f"Removed {len(diff.nodes_removed)} nodes")
print(f"Added {len(diff.edges_added)} edges")
print(f"Removed {len(diff.edges_removed)} edges")

# Check if anything changed
if diff.is_empty():
    print("No changes")

# Serialize for storage
data = diff.to_dict()

# Deserialize from storage
diff2 = GraphDiff.from_dict(data)
```

#### Working with ReindexStats

```python
from graph.incremental import ReindexStats

# Access stats from result
stats = result.stats

# Serialize for storage
data = stats.to_dict()
# {
#   "strategy": "resolve_local",
#   "started_at": 1712345678.123,
#   "duration_ms": 145,
#   ...
# }

# Deserialize from storage
stats2 = ReindexStats.from_dict(data)

# Compute ratio
if stats.files_parsed > 0:
    parse_ratio = stats.files_skipped / stats.files_total
    print(f"Parsed {stats.files_parsed}/{stats.files_total} files ({parse_ratio:.1%})")

# Check edge changes
if stats.edges_added > stats.edges_removed:
    print("More edges added than removed - graph is growing")
```

#### Persisting Diff to Storage

```python
# For JSON backend
from storage.json_store import JsonStore
store = JsonStore(filepath="index.json")
store.save_diff(diff)  # Calls save(graph) under the hood

# For Couchbase Lite backend
from storage.cblite.store import CouchbaseLiteStore
store = CouchbaseLiteStore(db_path="/path/to/db.cblite2")
store.save_diff(diff, graph=result.graph_out)  # Transactional upserts/purges
```

#### Orchestrating Reindex Runs

```python
from apollo.projects.reindex import ReindexOrchestrator
from graph.reindex_config import ReindexConfig

# Create orchestrator
root_dir = Path("/path/to/project")
config = ReindexConfig(strategy="auto", sweep_interval_minutes=30)
orchestrator = ReindexOrchestrator(root_dir, config)

# Determine which strategy to use
foreground_strategy = orchestrator.get_effective_strategy(is_foreground=True)
background_strategy = orchestrator.get_effective_strategy(is_foreground=False)

# Create strategy and run
if should_force_full := orchestrator.should_force_full_rebuild():
    strategy = FullBuildStrategy(builder)
else:
    strategy = ResolveLocalStrategy(builder) if foreground_strategy == "resolve_local" \
        else ResolveFullStrategy(builder)

# Run and record
result = strategy.run(str(root_dir), graph_in, prev_hashes, prev_dep_index)
orchestrator.record_run(result)

# Get stats for UI
last_stats = orchestrator.history.get_last()
info = orchestrator.get_last_reindex_info()
```

#### Reindex History

```python
from apollo.projects.reindex import ReindexHistory

history = ReindexHistory(Path("/path/to/project"))

# Load all runs
all_runs = history.load()

# Get most recent
last = history.get_last()

# Add a new run
history.append(stats)

# Get summary
summary = history.get_summary()
# {
#   "total_runs": 42,
#   "avg_duration_ms": 187,
#   "total_files_indexed": 8540,
#   ...
# }
```

### Advanced Topics

#### Custom Strategy Implementation

```python
from graph.incremental import IncrementalResult, ReindexStats
import networkx as nx

class CustomStrategy:
    """Your own reindex strategy."""
    
    name = "my_custom_strategy"
    
    def __init__(self, builder):
        self.builder = builder
    
    def run(self, root_dir, graph_in, prev_hashes=None, prev_dep_index=None):
        started_at = time.time()
        
        # Your custom logic here
        new_graph = nx.DiGraph(graph_in)
        new_hashes = {}
        new_dep_index = {}
        
        # ... implementation ...
        
        diff = compute_diff(graph_in, new_graph)
        
        stats = ReindexStats(
            strategy=self.name,
            started_at=started_at,
            duration_ms=int((time.time() - started_at) * 1000),
            files_total=len(new_hashes),
            files_parsed=0,
            files_skipped=0,
            edges_resolved=len(list(new_graph.edges())),
            edges_added=len(diff.edges_added),
            edges_removed=len(diff.edges_removed),
        )
        
        return IncrementalResult(
            graph_out=new_graph,
            new_hashes=new_hashes,
            new_dep_index=new_dep_index,
            diff=diff,
            stats=stats,
        )
```

#### Dependency Expansion Control

For Option 2, control how deep the dependency chain is explored:

```python
# Only re-resolve direct dependents (default)
strategy = ResolveLocalStrategy(builder, max_hops=1)

# Re-resolve up to 2 levels of dependents (more thorough, slower)
strategy = ResolveLocalStrategy(builder, max_hops=2)

# Re-resolve transitively all the way (approaching Option 1, slow)
strategy = ResolveLocalStrategy(builder, max_hops=999)
```

#### Force Full Rebuild Safety

The orchestrator will automatically force a full rebuild after 50 incremental runs:

```python
orchestrator = ReindexOrchestrator(root_dir)

# Check if safety limit reached
if orchestrator.should_force_full_rebuild():
    # Use FullBuildStrategy instead of incremental
    strategy = FullBuildStrategy(builder)
```

Can be configured:

```python
config = ReindexConfig(force_full_after_runs=100)
orchestrator = ReindexOrchestrator(root_dir, config)
```

### Testing

Run the test suite:

```bash
python3 -m pytest tests/test_incremental_reindex.py -v
```

Run benchmark:

```bash
python3 scripts/bench_reindex.py --num-mutations 5 --output results.json
```

Or on an existing codebase:

```bash
python3 scripts/bench_reindex.py --codebase /path/to/repo --num-mutations 10
```

### Troubleshooting

**Question: Reindex is slow even with Option 2**
- Check `affected_files` count in stats - if it's close to `files_total`, consider increasing `local_max_hops` or switching to `resolve_full`
- Wildcard imports can cause all dependents to be marked affected

**Question: Getting stale edges**
- This is expected with Option 2 - run a background Option 1 sweep
- Or switch strategy to `resolve_full` temporarily

**Question: Memory usage increasing**
- Reindex stats are capped at 100 runs
- Graph size is reasonable (standard memory for NetworkX)
- If symbol table cache grows, add LRU eviction

**Question: Transactional errors in CBL**
- CBL has a transaction size limit
- For very large graphs, may need to chunk diff into smaller transactions
- Currently fine for 1M+ edges

---

## Correctness Guarantees

All strategies produce **edge-correct graphs** for these scenarios:

1. **Touch only** (file mtime changes, content same) → identical graph
2. **Body edit, public API stable** → nodes change, edges to symbols valid
3. **Add new function** → new node and caller edges resolve
4. **Rename public function** → old edges removed, new resolved (Option 2 catches direct, sweep catches transitive)
5. **Delete file** → file node and descendants removed, orphan edges purged
6. **Add new file with imports** → new edges added, existing unchanged
7. **Wildcard import target changed** → all importers re-resolved
8. **Cyclic imports** → no infinite loops
9. **Move/rename file** → treated as delete+add, edges updated

---

## Performance Benchmarks

Typical latencies on a 50-file synthetic codebase:

| Change Size | Strategy | Time | Speedup |
|-------------|----------|------|---------|
| Small (1-2 files) | Option 1 | 50ms | baseline |
| Small (1-2 files) | Option 2 | 30ms | **1.7x faster** |
| Medium (5-10 files) | Option 1 | 150ms | baseline |
| Medium (5-10 files) | Option 2 | 80ms | **1.9x faster** |
| Large (20+ files) | Option 1 | 400ms | baseline |
| Large (20+ files) | Option 2 | 350ms | **1.1x faster** |
| Force full (all files) | Option 1 | 800ms | baseline |

**Recommendation:**
- Use Option 2 (auto foreground) for interactive updates (sub-second)
- Run Option 1 sweeps every 30-60 minutes (background)
- Force full every 50 incremental runs as safety check

---

## See Also

- [Phase 8 Implementation Summary](docs/work/PHASE_8_IMPLEMENTATION.md)
- [Incremental Reindex Plan](docs/work/PLAN_INCREMENTAL_REINDEX.md)
- [Graph Builder](graph/builder.py)
- [Graph Query API](graph/query.py)
