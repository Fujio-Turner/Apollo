# Plan: Index Freshness Indicator

Surface to the user when the on-disk codebase has drifted from the
indexed graph, so they know when to re-index. Uses a DaisyUI
`indicator` badge on the existing **Re-Index** button and a subtle dot
in the bottom status bar.

Designed so the work can be paused and resumed across multiple chat
sessions — each phase is independently shippable.

---

## 0. Background

- File / directory nodes in the graph store **relative** paths
  (e.g. `cb_python_sdk_samples/foo.py`).
- The absolute indexed root is now recorded on `dir::.` as `abs_path`
  (added when fixing file-content 404s).
- Storage backends:
  - JSON (`storage/json_store.py`) → `data/index.json`
  - Couchbase Lite (`storage/cblite/store.py`) → CBL DB with
    `nodes` and `edges` collections.
- There is currently **no place** to persist arbitrary metadata
  (e.g. `last_indexed_at`).

---

## 1. Goals

1. Persist `last_indexed_at` (and a few related fields) alongside the
   graph, in a backend-agnostic way.
2. Detect "stale" files on disk: modified, added, or deleted relative
   to the indexed snapshot.
3. Expose a `GET /api/index/status` endpoint returning a small JSON
   summary suitable for polling.
4. Render a DaisyUI indicator badge on the **Re-Index** button (and a
   subtle dot in the bottom status bar) when stale_count > 0.
5. Live-mode awareness: when the watcher is running, show a **green
   "Live"** state instead of a stale count (the watcher keeps the
   index fresh incrementally).

## 2. Non-Goals

- Auto re-indexing.
- Per-file diff UI.
- Tracking content hashes for stale detection in this iteration
  (mtime is sufficient for v1).
- Surfacing parser version mismatches (future work).

---

## 3. Design

### 3.1 Storage: generic meta API

Add two methods to **both** stores so callers don't care which backend
is in use:

```python
def save_meta(self, doc_id: str, doc: dict) -> None: ...
def get_meta(self, doc_id: str) -> dict | None: ...
```

**JSON backend** (`storage/json_store.py`):
- Sibling file: `data/_meta.json`
- Schema: `{ "<doc_id>": { ... }, ... }`
- Keeps `index.json` pure graph data — touching meta does not bloat
  the graph file or invalidate caching.

**CBL backend** (`storage/cblite/store.py`):
- Dedicated collection: `apollo_meta`
- One document per doc_id, body = the dict.
- `_create_indexes` should add a value index on `META().id`
  (already implicit, but be explicit if useful).

### 3.2 Doc IDs (constants)

Put in a new file `storage/meta_keys.py`:

```python
INDEX_META = "apollo::index_meta"
# Reserved for later phases:
# WATCH_STATE = "apollo::watch_state"
# USER_SETTINGS = "apollo::user_settings"
```

### 3.3 `apollo::index_meta` document shape

```json
{
  "last_indexed_at": "2026-04-26T09:30:12.451Z",
  "last_indexed_at_epoch": 1745659812.451,
  "indexed_root": "/Users/.../cb_python_sdk_samples",
  "file_count": 412,
  "build_kind": "full",          // "full" | "incremental"
  "parser_versions": {           // future-proof; ok to leave {}
    "python": "1.0"
  }
}
```

### 3.4 Where the meta is written

- `JsonStore.save()` / `CouchbaseLiteStore.save()` — at the end of
  every successful save call. Both full and incremental builds go
  through `save()`, so timestamps stay accurate without extra
  bookkeeping.
- Compute the value:
  - `last_indexed_at_epoch = time.time()` (UTC).
  - `last_indexed_at = datetime.utcnow().isoformat() + "Z"`.
  - `indexed_root` = `graph.nodes["dir::."].get("abs_path")`.
  - `file_count` = count of `type == "file"` nodes.
  - `build_kind` = caller-supplied flag (default `"full"`); the
    incremental builder passes `"incremental"`.

### 3.5 Stale detection helper

New module: `apollo/index_status.py` (or add to `file_inspect.py`).

```python
EXCLUDE_DIRS = {
    ".git", ".svn", ".hg",
    "venv", ".venv", "env", ".env",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    ".apollo", ".graph_search",
    "node_modules", "dist", "build",
    "_apollo_web",                     # NEW — reserved for web bookmarks
    "htmlcov", "target",
}

PARSER_EXTENSIONS = {".py"}            # extend as parsers are added

def compute_index_status(graph, store, *, max_examples: int = 20) -> dict:
    meta = store.get_meta("apollo::index_meta") or {}
    last_epoch = float(meta.get("last_indexed_at_epoch") or 0)
    root = (graph.nodes.get("dir::.") or {}).get("abs_path")
    if not root:
        return {"status": "unknown", ...}

    # Collect indexed files (relative paths) from the graph.
    indexed = {
        attrs["path"]
        for _, attrs in graph.nodes(data=True)
        if attrs.get("type") == "file" and attrs.get("path")
    }

    on_disk: dict[str, float] = {}     # rel_path -> mtime
    for dirpath, dirnames, filenames in os.walk(root):
        # in-place prune
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext not in PARSER_EXTENSIONS:
                continue
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            try:
                on_disk[rel] = os.path.getmtime(full)
            except OSError:
                continue

    new_files     = sorted(set(on_disk) - indexed)
    deleted_files = sorted(indexed - set(on_disk))
    modified_files = sorted(
        rel for rel, mtime in on_disk.items()
        if rel in indexed and mtime > last_epoch
    )

    stale = new_files + modified_files + deleted_files
    return {
        "last_indexed_at": meta.get("last_indexed_at"),
        "indexed_root": root,
        "total_indexed_files": len(indexed),
        "stale_count": len(stale),
        "new_count": len(new_files),
        "modified_count": len(modified_files),
        "deleted_count": len(deleted_files),
        "examples": stale[:max_examples],
        "watcher_active": False,        # filled in by endpoint
    }
```

Notes:
- O(n) walk; for very large repos (>50k files) we should later cache
  for 30–60s. For v1, no cache.
- mtime > last_epoch picks up out-of-band edits (git pulls etc.).
- New / deleted always count as stale regardless of mtime.

### 3.6 API endpoint

`web/server.py`:

```python
@app.get("/api/index/status")
def api_index_status():
    from apollo.index_status import compute_index_status
    res = compute_index_status(graph, store)
    res["watcher_active"] = bool(watcher and watcher.running)
    return res
```

Cheap, safe to poll every 60s. Returns 200 with `status` field even
when no index exists yet.

### 3.7 Frontend

**HTML** — wrap existing Re-Index button (find in `web/static/index.html`):

```html
<div class="indicator">
  <span id="reindex-badge"
        class="indicator-item badge badge-warning badge-xs hidden"
        title="">0</span>
  <button id="reindex-btn" class="btn btn-sm">Re-Index</button>
</div>
```

**JS** — `web/static/app.js`:

```js
async function pollIndexStatus() {
  try {
    const s = await apiFetch('/api/index/status');
    const badge = document.getElementById('reindex-badge');
    const dot   = document.getElementById('status-index-dot');
    if (s.watcher_active) {
      badge.classList.add('hidden');
      dot.className = 'w-1.5 h-1.5 rounded-full bg-success';
      dot.title = 'Live (watcher active)';
      return;
    }
    if (s.stale_count > 0) {
      badge.textContent = s.stale_count > 99 ? '99+' : String(s.stale_count);
      badge.classList.remove('hidden');
      badge.title =
        `${s.stale_count} files changed since last index ` +
        `(${s.last_indexed_at || 'never'})\n\n` +
        s.examples.slice(0, 8).join('\n');
      dot.className = 'w-1.5 h-1.5 rounded-full bg-warning';
      dot.title = badge.title;
    } else {
      badge.classList.add('hidden');
      dot.className = 'w-1.5 h-1.5 rounded-full bg-base-content/30';
      dot.title = `Index up to date (${s.last_indexed_at || 'never'})`;
    }
  } catch (e) { /* keep prior state */ }
}

// On load + every 60s + after re-index completes.
pollIndexStatus();
setInterval(pollIndexStatus, 60_000);
```

**Status bar dot** — add `<span id="status-index-dot" class="..."></span>`
near `status-nodes` element.

After a successful re-index, call `pollIndexStatus()` to clear the badge.

---

## 4. Implementation Phases

Each phase is independent and ships value. Tackle in order.

### Phase 1 — Meta storage primitive *(small)*
- [ ] Add `save_meta` / `get_meta` to `JsonStore`.
- [ ] Add `save_meta` / `get_meta` to `CouchbaseLiteStore`
      (uses `apollo_meta` collection).
- [ ] Add `storage/meta_keys.py` with `INDEX_META`.
- [ ] Unit tests: round-trip a dict for both backends.

### Phase 2 — Persist `last_indexed_at` *(small)*
- [ ] Update `JsonStore.save()` to write `INDEX_META` doc.
- [ ] Update `CouchbaseLiteStore.save()` to write same doc inside the
      transaction.
- [ ] Pass `build_kind` through builder → save (default `"full"`,
      incremental sets `"incremental"`).
- [ ] Manual verify: re-index, inspect `data/_meta.json` /
      CBL `apollo_meta` collection.

### Phase 3 — Stale-detection helper + API *(medium)*
- [ ] Implement `compute_index_status` in `apollo/index_status.py`.
- [ ] Add `/api/index/status` endpoint.
- [ ] Tests: temp dir with mix of indexed / new / deleted / unchanged
      files; assert counts.
- [ ] Verify `_apollo_web/` is excluded.

### Phase 4 — Frontend indicator *(small)*
- [ ] Wrap Re-Index button in DaisyUI `indicator`.
- [ ] Add status-bar dot.
- [ ] Wire `pollIndexStatus()` (load + 60s + post-index).
- [ ] Live mode → green dot, no badge.

### Phase 5 — Polish *(optional)*
- [ ] Tooltip lists first N stale files.
- [ ] Click badge → opens a small dropdown listing changed files.
- [ ] 30s server-side cache on `/api/index/status` for repos > 10k
      files (toggle by config).
- [ ] Surface `parser_versions` mismatch as another stale reason.

---

## 5. Edge Cases & Risks

- **No graph yet** (fresh install): endpoint returns
  `{ stale_count: 0, last_indexed_at: null, status: "unindexed" }`;
  frontend shows a neutral dot with "Not indexed" tooltip.
- **`abs_path` missing on `dir::.`** (older indexes): endpoint returns
  `status: "unknown"` and frontend hides the badge with a "Re-index
  required for status" tooltip. Re-indexing once populates it.
- **CBL transaction failure** when writing meta: should not corrupt
  the rest of the graph save — write meta as the very last step
  inside the same transaction so all-or-nothing.
- **mtime drift / clock skew**: trust local clocks; document this.
- **Huge repos**: walk on every poll could be slow. Phase 5 cache.
- **Symlinks**: `os.walk(followlinks=False)` to avoid loops.
- **Hidden files**: skipped by virtue of extension filter (only `.py`
  for now).
- **Watcher crashes silently**: `watcher_active` may say true while
  the index is actually behind. Future enhancement: ping the watcher
  for a heartbeat.

---

## 6. Files to Touch

- `storage/json_store.py` — add meta API.
- `storage/cblite/store.py` — add meta API + `apollo_meta` collection.
- `storage/meta_keys.py` — NEW.
- `graph/builder.py` — already sets `abs_path`; pass `build_kind`.
- `apollo/index_status.py` — NEW (or add to `file_inspect.py`).
- `web/server.py` — new `/api/index/status` endpoint.
- `web/static/index.html` — DaisyUI indicator wrapper, status bar dot.
- `web/static/app.js` — `pollIndexStatus()` + post-reindex refresh.
- `tests/` — tests for meta storage, stale detection, endpoint.

---

## 7. Open Questions

1. CBL collection name — `apollo_meta` vs `_meta`? **Default: `apollo_meta`** (no leading underscore so SQL++ stays simple).
2. Persist `last_indexed_at` on **every** save (incl. each watcher
   incremental tick) or only on full builds?
   **Default: every save** — the timestamp must reflect last
   write to be useful for stale detection.
3. Should stale-detection compare **content hashes** instead of
   mtime? mtime is good enough for v1; revisit if false positives are
   noisy.
4. Polling cadence — 60s ok, or expose as a setting?
