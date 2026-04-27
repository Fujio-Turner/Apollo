# Plan: Apollo Project Bootstrap & Management

When a user opens Apollo's **My Files** view and selects a folder to
work on, Apollo must decide whether the folder is *new to Apollo*
(needs a first-time bootstrap wizard) or *already an Apollo project*
(open it normally, optionally re-index). This document specifies the
bootstrap detection, the on-disk metadata layout (`_apollo/`), the
wizard UX, and the management actions ("Reprocess", "Leave / Remove
Apollo Files").

Related docs:
- `docs/DESIGN.md` — overall Apollo design (esp. §4 Storage, §9 UI,
  §13 Indexing)
- `guides/SCHEMA_DESIGN.md` — JSON Schema conventions; this plan adds
  `apollo-project.schema.json`
- `guides/STYLE_HTML_CSS.md` — DaisyUI + Tailwind, Heroicons-only,
  `data-theme="dark"` default
- `docs/work/PLAN_INCREMENTAL_REINDEX.md` — what "Reprocess" actually
  invokes under the hood

---

## 1. Goals

1. **Detect** whether the picked folder is a new or existing Apollo
   project by looking for `<picked_folder>/_apollo/apollo.json`.
2. **Bootstrap** new projects with a wizard overlay that lets the user
   either "Process All" or pick "Custom Filters" (folder checkboxes,
   excluded file patterns, included doc-type tagify lists).
3. **Persist** project-level config (version, ignore lists, doc types,
   first-created and last-indexed timestamps, completion flag) in
   `_apollo/apollo.json` so subsequent opens are instant and
   reproducible.
4. **Manage** the project lifecycle from the same overlay later:
   `Reprocess`, `Edit Filters`, and `Leave (Remove Apollo
   Files/Folders)`.
5. Use **DaisyUI** (modal, steps, tabs, toggle, file-input, badge,
   btn-primary/btn-error) per `guides/STYLE_HTML_CSS.md`; **no HTML
   emojis** — Heroicons only.
6. All on-disk state is **JSON Schema-validated** per
   `guides/SCHEMA_DESIGN.md`.

## 2. Non-Goals

- Multi-root projects (one picked folder = one Apollo project for now).
- Cloud sync of `_apollo/`.
- Editing the wizard's results during indexing — wizard closes, then
  the indexing modal (Phase 8) takes over.
- Migrating older `data/` / `.apollo/` / `.graph_search/` storage
  layouts; this plan creates the new in-folder `_apollo/` location.
  A separate migration step is out of scope here but a stub is noted
  in §10.

---

## 3. On-Disk Layout

Every Apollo-managed folder gets a single hidden-ish subfolder at its
root:

```
<picked_folder>/
└── _apollo/
    ├── apollo.json                  ← project manifest (this plan)
    ├── graph.json                   ← knowledge graph (existing, moved here)
    ├── embeddings.npy               ← vector index (existing, moved here)
    ├── annotations.json             ← highlights / notes / bookmarks (Phase 11)
    ├── chat/                        ← saved chat threads (existing)
    └── _meta.json                   ← reindex telemetry (PLAN_INCREMENTAL_REINDEX.md)
```

Why a per-project folder (vs. a global app DB)?

- Mirrors the Obsidian model — the project is *self-describing* and
  portable. Copy/move/zip the folder and Apollo state goes with it.
- Survives Apollo upgrades and reinstalls.
- Easy to remove: one `rm -rf _apollo/`. The wizard's
  **Leave (Remove Apollo Files)** button does exactly that, with a
  confirmation modal.
- Mirrors the existing `_apollo_web/` capture folder (DESIGN §14.3),
  keeping all Apollo-owned, project-scoped state under an `_apollo*`
  prefix.

### 3.1 `apollo.json` — the project manifest

```json
{
  "$schema": "https://apollo.local/schema/apollo-project.schema.json",
  "project_id": "ap::01J9Q3...",
  "root_dir": "/Users/me/code/myapp",
  "created_at": "2026-04-26T14:00:00Z",
  "created_by_version": "0.7.2",
  "last_opened_at": "2026-04-26T14:00:00Z",
  "last_opened_by_version": "0.7.2",
  "last_indexed_at": null,
  "last_indexed_by_version": null,
  "initial_index_completed": false,
  "filters": {
    "mode": "custom",
    "include_dirs": [
      "src", "apollo", "docs"
    ],
    "exclude_dirs": [
      "venv", ".venv", "node_modules", "target", "build", "dist",
      "htmlcov", ".pytest_cache"
    ],
    "exclude_file_globs": [
      "*.min.js", "*.lock", "package-lock.json", "*.pyc"
    ],
    "include_doc_types": [
      "py", "md", "json", "yaml", "toml", "html", "css", "js", "ts"
    ]
  },
  "stats": {
    "files_indexed": 0,
    "nodes": 0,
    "edges": 0,
    "elapsed_seconds": 0
  }
}
```

Field notes:

- `project_id` — ULID prefixed with `ap::`; lets us identify the
  project even if the folder is moved.
- `created_by_version` / `last_*_by_version` — Apollo version strings
  (`apollo.__version__`). Used to gate future schema migrations.
- `initial_index_completed` — `false` while the wizard's first run is
  pending or in-flight; flipped to `true` only after the first
  successful index. If a user closes the app mid-bootstrap we re-open
  the wizard at the **Resume** step on next open.
- `filters.mode` — `"all"` | `"custom"`. `"all"` ignores
  `include_dirs` and applies only the built-in skip list (DESIGN §9
  `_SKIP_DIRS`).
- `filters.include_dirs` / `exclude_dirs` — relative to `root_dir`.
  `exclude_dirs` is the user's list **plus** the built-in skip list
  (the built-ins are merged at runtime, not persisted, so upgrading
  Apollo's blocklist takes effect for free).
- `filters.exclude_file_globs` — glob patterns matched against the
  file's path relative to `root_dir`. Tagify input.
- `filters.include_doc_types` — extension whitelist (no leading dot).
  Tagify input. If empty → fall back to all known plugin types.
- `stats` — last completed index summary, kept here so the next
  "Open" can show counts immediately without reading the graph file.

### 3.2 `apollo-project.schema.json`

A new entry for `schema/`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://apollo.local/schema/apollo-project.schema.json",
  "title": "Apollo Project Manifest",
  "type": "object",
  "required": [
    "project_id", "root_dir", "created_at", "created_by_version",
    "initial_index_completed", "filters"
  ],
  "properties": {
    "project_id":            { "type": "string", "pattern": "^ap::" },
    "root_dir":              { "type": "string" },
    "created_at":            { "type": "string", "format": "date-time" },
    "created_by_version":    { "type": "string" },
    "last_opened_at":        { "type": ["string","null"], "format": "date-time" },
    "last_opened_by_version":{ "type": ["string","null"] },
    "last_indexed_at":       { "type": ["string","null"], "format": "date-time" },
    "last_indexed_by_version":{ "type": ["string","null"] },
    "initial_index_completed": { "type": "boolean" },
    "filters": {
      "type": "object",
      "required": ["mode", "exclude_dirs", "exclude_file_globs", "include_doc_types"],
      "properties": {
        "mode":               { "enum": ["all", "custom"] },
        "include_dirs":       { "type": "array", "items": {"type":"string"} },
        "exclude_dirs":       { "type": "array", "items": {"type":"string"} },
        "exclude_file_globs": { "type": "array", "items": {"type":"string"} },
        "include_doc_types":  { "type": "array", "items": {"type":"string"} }
      }
    },
    "stats": {
      "type": "object",
      "properties": {
        "files_indexed":     { "type": "integer", "minimum": 0 },
        "nodes":             { "type": "integer", "minimum": 0 },
        "edges":             { "type": "integer", "minimum": 0 },
        "elapsed_seconds":   { "type": "number",  "minimum": 0 }
      }
    }
  }
}
```

This is added to `schema/` and registered in `schema/index.html` per
`guides/SCHEMA_DESIGN.md` §"Adding a New Schema".

---

## 4. Detection Flow ("Open Folder" → bootstrap or open)

The user picks a folder via the **My Files** view (an existing
DaisyUI tree / file-picker). The single decision point:

```
PICK FOLDER  ─►  POST /api/projects/open  { path: "/abs/path" }
                              │
        ┌─────────────────────┴─────────────────────┐
        ▼                                           ▼
  _apollo/apollo.json EXISTS               _apollo/ MISSING
        │                                           │
        ▼                                           ▼
  read + validate                       create empty `_apollo/`
        │                                           │
   initial_index_                       generate apollo.json with
   completed?                           defaults (mode = "all"),
        │                               initial_index_completed = false
   ┌────┴────┐                                       │
   │         │                                       │
  YES        NO                                      │
   │         │                                       │
   ▼         └──────────────┐                        │
  open       ◄──────────────┴────────────────────────┘
  normally,                  show BOOTSTRAP WIZARD
  show stats                 (modal overlay, see §5)
  in dev
  toolbar
```

Endpoint contract:

```
POST /api/projects/open          { path }            → ProjectInfo
POST /api/projects/init          { path, filters }   → ProjectInfo
PUT  /api/projects/filters       { filters }         → ProjectInfo
POST /api/projects/reprocess     { mode }            → IndexJob
POST /api/projects/leave         { confirm: true }   → { removed: [...] }
GET  /api/projects/current                           → ProjectInfo | null
GET  /api/projects/tree?depth=N                      → DirTree (for the wizard)
```

`ProjectInfo` mirrors `apollo.json` plus a derived
`needs_bootstrap: bool` flag (= `!initial_index_completed`).

`POST /api/projects/leave` is destructive — it deletes
`<root>/_apollo/` and `<root>/_apollo_web/` (if present) and clears
the in-memory graph; the UI must use a DaisyUI confirm modal with a
typed-in folder name to enable the **Remove** button (same UX as the
existing index-delete dialog, per DESIGN §9.6).

---

## 5. Bootstrap Wizard UX

A DaisyUI **`modal modal-open`** overlay (full-screen on mobile,
centered card on desktop). All copy uses Inter 14–16 px per the
typography guide. Heroicons only — no emoji.

### 5.1 States

```
       ┌─ Step 1 — Welcome / mode pick ──────────────────────┐
       │   [Process All]   [Custom Filters]   [Cancel]       │
       └────────┬────────────────────┬────────────────────────┘
                │                    │
        click "Process All"   click "Custom Filters"
                │                    │
                ▼                    ▼
       ┌─ Step 2-A — Confirm All ─┐  ┌─ Step 2-B — Custom Filters ──────┐
       │ "Apollo will scan every  │  │ Tabs: [Folders] [Files] [Types]  │
       │  file under <root>       │  │ ┌────────────────────────────┐   │
       │  except built-in skips   │  │ │ Folders pane: tree w/      │   │
       │  (venv, node_modules…)." │  │ │ checkboxes, "Select all" / │   │
       │ [Back]   [Process]       │  │ │ "Unselect all" buttons     │   │
       └──────────┬───────────────┘  │ └────────────────────────────┘   │
                  │                  │ ┌────────────────────────────┐   │
                  │                  │ │ Files pane: Tagify input   │   │
                  │                  │ │ for `exclude_file_globs`   │   │
                  │                  │ │ (chip per glob)            │   │
                  │                  │ └────────────────────────────┘   │
                  │                  │ ┌────────────────────────────┐   │
                  │                  │ │ Types pane: Tagify input   │   │
                  │                  │ │ for `include_doc_types`    │   │
                  │                  │ │ (suggestions from plugins) │   │
                  │                  │ └────────────────────────────┘   │
                  │                  │ [Back] [Process] [Cancel]        │
                  │                  └──────────┬───────────────────────┘
                  ▼                             ▼
       ┌─ Step 3 — Indexing Progress (existing Phase 8 modal) ─┐
       │ steps steps-vertical, polling /api/indexing-status     │
       └─────────────────────────┬──────────────────────────────┘
                                 ▼
       ┌─ Step 4 — Done ────────────────────────────────────────┐
       │ "Indexed N files, M nodes, K edges in T s."             │
       │ [Open Project]                                          │
       └─────────────────────────────────────────────────────────┘
```

### 5.2 Re-entry / management mode

If the user opens **an existing project** and clicks the
`Project Settings` button (gear icon in the dev toolbar, DESIGN §9.6),
the same modal opens directly at Step 2-B with current filters
pre-loaded, plus three extra buttons in the footer:

```
[Reprocess (Incremental)]  [Reprocess (Full)]  [Leave Project]
```

- `Reprocess (Incremental)` → `POST /api/projects/reprocess
  { mode: "incremental" }` (uses
  `PLAN_INCREMENTAL_REINDEX.md` Option chosen at runtime).
- `Reprocess (Full)` → `POST /api/projects/reprocess { mode: "full"
  }` — wipes the graph + embeddings (preserves `apollo.json`,
  `annotations.json`, `chat/`) and runs from scratch.
- `Leave Project` → DaisyUI confirm modal → `POST /api/projects/leave`
  → returns the user to **My Files**.

### 5.3 Folder tree pane (Step 2-B)

Source: `GET /api/projects/tree?depth=3` returns a JSON tree
descending up to 3 levels deep from the picked root, with file/dir
counts per directory:

```json
{
  "name": "myapp",
  "path": ".",
  "type": "dir",
  "child_dir_count": 5,
  "child_file_count": 12,
  "children": [
    { "name": "src", "path": "src", "type": "dir", "...": "..." },
    { "name": "docs", "path": "docs", "type": "dir", "...": "..." }
  ]
}
```

Render with DaisyUI `collapse collapse-arrow` rows; each row has a
DaisyUI `checkbox checkbox-primary checkbox-sm`. Top of pane:

- `Select all`  (DaisyUI `btn btn-xs btn-ghost`)
- `Unselect all`
- `Use built-in skips` (toggle — DaisyUI `toggle toggle-primary`)
  defaults ON; when ON the user can't uncheck `_SKIP_DIRS` entries
  but they're rendered as disabled, struck-through rows so the user
  understands what's being excluded.

A node check state of `false` writes the path into
`filters.exclude_dirs`. A node `true` writes it into
`filters.include_dirs` *only if* its parent is excluded — otherwise
it's implicit (everything under an included dir is included unless
explicitly excluded). This keeps the persisted JSON minimal and
diff-friendly.

### 5.4 Files pane — `exclude_file_globs`

Tagify input pre-seeded with sensible defaults
(`*.min.js`, `*.lock`, `package-lock.json`, `*.pyc`, `*.so`,
`*.dylib`). Each chip removable with `×`. Free-text entry creates a
new chip. Validation: warn (not block) if the pattern doesn't compile
as a glob.

### 5.5 Types pane — `include_doc_types`

Tagify input with **suggestions** sourced from
`GET /api/plugins` (existing plugin discovery — DESIGN §4.1.1). Each
plugin advertises the extensions it handles; the suggestions
dropdown shows them as a checklist:

```
[ x ] py    Python 3
[ x ] md    Markdown (GFM)
[   ] go    Go (planned)
[ x ] json  Generic text
...
```

Empty list = "all known plugin types" (i.e., wildcard).

---

## 6. Backend changes

New module: **`apollo/projects/manager.py`**

```
class ProjectManager:
    def open(self, path: str) -> ProjectInfo
    def init(self, path: str, filters: Filters) -> ProjectInfo
    def update_filters(self, filters: Filters) -> ProjectInfo
    def reprocess(self, mode: Literal['incremental','full']) -> IndexJob
    def leave(self) -> list[str]                # paths removed
    def current(self) -> ProjectInfo | None
    def tree(self, depth: int = 3) -> DirNode
```

Implementation sketch:

- `open()` resolves `path/_apollo/apollo.json`. If missing, returns a
  `ProjectInfo` with `needs_bootstrap=True` and a stub manifest in
  memory (not yet written). Always writes `last_opened_at`/version
  if a manifest already exists.
- `init()` performs schema validation (`jsonschema.validate`),
  ensures `_apollo/` exists, writes `apollo.json` with
  `initial_index_completed=false`, then enqueues an indexing job
  (existing `POST /api/index` plumbing — Phase 8).
- The indexing job, on success, sets `initial_index_completed=true`
  and updates `last_indexed_at` + `stats`.
- `reprocess()` reuses the same indexing pipeline; `mode="full"`
  deletes `graph.json`/`embeddings.npy` first.
- `leave()` deletes `_apollo/` and `_apollo_web/` recursively, after
  asserting `confirm=true` from the API caller.

The existing `graph/builder.py` already supports a skip list
(DESIGN §9 `_SKIP_DIRS`). Extend it to accept the user's
`exclude_dirs` (merged with built-ins) and `exclude_file_globs`
(checked via `fnmatch.fnmatch`) and an `include_doc_types`
allowlist applied before plugin dispatch.

### 6.1 Settings flow

`data/settings.json` (already in the repo) gains a
`recent_projects: [{path, project_id, last_opened_at}]` array of up
to 10 entries so My Files can surface recently used folders.

### 6.2 Couchbase Lite — per-project database lifecycle

When `settings.json` selects `backend: "cblite"` (DESIGN §4.3, Phase 5),
**each Apollo project gets its own Couchbase Lite database** rooted
inside that project's `_apollo/` folder. Mixing projects into one
shared CBL would break isolation, make `Leave Project` impossible to
implement cleanly, and entangle indexes/embeddings/SQL++ scopes that
should be per-project.

#### 6.2.1 On-disk layout & DB naming

Each project's CBL bundle is named
**`apollo_<md5(abspath)>.cblite2`** — a fixed `apollo_` prefix
followed by the lowercase hex MD5 of the project's absolute root
path at creation time. The prefix makes the bundles instantly
recognizable on disk; underscores are safe on macOS (APFS/HFS+),
Windows (NTFS/exFAT), and Linux. This guarantees:

- **Globally unique names.** Two unrelated projects on the same
  machine (`/Users/alice/code/foo` and `/srv/work/foo`) get
  different bundles even though their basenames collide.
- **Cross-platform stability.** The exact same project copied to
  two locations (`C:\Users\bob.smith\Documents\git_hub\my_project`
  vs. `/Users/bob/git/my_project`) produces *different* hashes —
  i.e., they're treated as distinct CBL databases by design,
  because they're different working copies.
- **A flat, predictable registry.** All CBL bundles can live side
  by side under one directory and still be uniquely identifiable
  from the file system alone.

Hash computation:

```
db_hash = md5(os.path.abspath(root_dir).encode("utf-8")).hexdigest()
db_name = f"apollo_{db_hash}.cblite2"
```

The hash is **computed once at `init()` time** and persisted as
`storage.db_hash` and `storage.db_relpath` in `apollo.json`. After
that the name is fixed even if the user moves the folder — moves
are handled by §6.2.6 "Rebind on Move" below, not by recomputing
the hash on the fly.

##### Storage location

Two valid placements; we support both via a settings flag
`cblite_storage_root`:

```
1) Per-project (default — keeps the project portable):

   <picked_folder>/_apollo/cblite/apollo_<md5>.cblite2/
       ├── db.sqlite3
       ├── db.sqlite3-wal
       ├── db.sqlite3-shm
       └── Attachments/

2) Global registry (opt-in — useful for CBL on a fast SSD scratch
   disk while the project lives on a slow network share):

   ~/.apollo/cblite/apollo_<md5>.cblite2/
```

Mode (1) is the default because it preserves the "self-contained
folder" model from §3 — copy the folder, the DB goes too.
Mode (2) is selected per-project and recorded as
`storage.location_mode: "global"` in the manifest so the right
path can be reconstructed on every open.

When the JSON backend is selected the `cblite/` folder is simply
absent.

`storage/cblite/store.py` already takes a `db_path` and creates
parent dirs (see `CouchbaseLiteStore.__init__` → `_open` →
`Path(...).parent.mkdir`), so all we need to compute is the right
hashed path per project.

#### 6.2.2 Manifest additions

Extend `apollo.json` (and the schema in §3.2) with a `storage` block:

```json
{
  "storage": {
    "backend": "cblite",
    "db_hash": "9a4f1c6e2b8d7a30c5e0f1a2b3c4d5e6",
    "db_name": "apollo_9a4f1c6e2b8d7a30c5e0f1a2b3c4d5e6.cblite2",
    "location_mode": "project",
    "db_relpath": "cblite/apollo_9a4f1c6e2b8d7a30c5e0f1a2b3c4d5e6.cblite2",
    "origin_abspath": "/Users/bob/git/my_project",
    "cblite_version": "3.2.0",
    "schema_version": 1
  }
}
```

- `backend` — `"json"` | `"cblite"`. Defaults to whatever
  `settings.json` says at init time but is **frozen** at project
  creation. Switching backends after init requires the explicit
  **Convert Storage** flow (§6.2.7).
- `db_hash` — `md5(origin_abspath)`; the canonical project ID for
  CBL purposes. Never recomputed.
- `db_name` — `apollo_<db_hash>.cblite2`. Stored explicitly so a human can
  locate the bundle on disk by reading the manifest.
- `location_mode` — `"project"` (default, bundle inside `_apollo/`)
  or `"global"` (bundle under `~/.apollo/cblite/`).
- `db_relpath` — only set when `location_mode == "project"`;
  relative to `_apollo/`. Lets the project folder be moved with the
  DB inside it.
- `origin_abspath` — the path used to compute `db_hash`. Surfaced in
  the management modal so the user can see "this DB was created for
  `/Users/bob/git/my_project`" — important when the folder has been
  moved and we're about to rebind (§6.2.6).
- `cblite_version` — `libcblite` version string captured at create
  time, for migration gating.
- `schema_version` — Apollo's own CBL schema version (collections,
  indexes); bumped when we change the document layout.

#### 6.2.3 Manager API additions

`apollo/projects/manager.py` owns the CBL handle for the *currently
open* project. There is **at most one open CBL handle per process**:

```
class ProjectManager:
    _store: BaseStore | None       # JsonStore or CouchbaseLiteStore
    _project: ProjectInfo | None

    def open(self, path) -> ProjectInfo:
        # ... read manifest ...
        self._close_existing()                       # release prior project's CBL
        self._store = open_store(
            backend  = manifest.storage.backend,
            location = self._resolve_storage_path(manifest),
        )

    def init(self, path, filters, *, backend=None) -> ProjectInfo:
        backend = backend or settings.default_backend
        self._mkdir_apollo(path)
        if backend == "cblite":
            self._mkdir(path / "_apollo" / "cblite")
        # write manifest with storage block, then open_store(...)

    def reprocess(self, mode):
        if mode == "full" and manifest.storage.backend == "cblite":
            self._store.delete()                     # rm -rf apollo_<hash>.cblite2/
            self._store = open_store("cblite", db_path)  # fresh DB
        # ... run indexer ...

    def leave(self) -> list[str]:
        self._close_existing()
        shutil.rmtree(_apollo)                       # CBL is inside, gone with it
```

`_close_existing()` calls `CouchbaseLiteStore.close()` which already
exists. Skipping this on project-switch leaks the SQLite handle and
on Windows blocks the directory from being deleted by `leave()`.

#### 6.2.4 Lifecycle events ↔ CBL operations

| Lifecycle event           | CBL action                                                    |
|---------------------------|---------------------------------------------------------------|
| `init` (cblite backend)   | `mkdir _apollo/cblite/` then lazy-open on first `save()`.     |
| First successful index    | `_create_indexes()` (existing) — value index on `type`, vector index on `embedding`. Only runs once per DB; safe to re-call. |
| Open existing project     | `open_store(...)` — opens the bundle, no index recreate.      |
| Reprocess (Incremental)   | Reuse open handle. Builder writes deltas via the diff-based persistence path from `PLAN_INCREMENTAL_REINDEX.md`. |
| Reprocess (Full)          | `store.delete()` (closes + `rm -rf` bundle) → re-open empty → re-index. Keeps `apollo.json`, `annotations.json`, `chat/`. |
| Switch to another project | `_close_existing()` on old store, then `open_store()` for the new project's path. |
| Leave Project             | `_close_existing()` then `rm -rf _apollo/` (and `_apollo_web/`). |
| Process exit / SIGTERM    | `atexit` hook calls `_close_existing()` for clean shutdown.   |
| Crash mid-write           | CBL's SQLite WAL handles atomicity; on next open a recovery run replays the WAL. We add a `db.sqlite3-wal` size check to telemetry. |

#### 6.2.5 Concurrency & locking

CBL holds a SQLite file lock on the bundle. Two Apollo processes
opening the same project will fight; the second one's `open()` raises
`CBLError(BUSY)`. We surface this as a DaisyUI `alert alert-warning`
in the wizard ("This project is already open in another Apollo
window") and refuse to proceed. The lock check is cheap because we
attempt `open_store()` eagerly inside `ProjectManager.open()`.

#### 6.2.6 Rebind on Move (folder moved, hash now stale)

The `db_hash` is intentionally **not** recomputed every time a project
opens — that would orphan the DB on every move/rename. Instead, on
`open()`:

1. Compute `current_hash = md5(os.path.abspath(picked_folder))`.
2. Read `manifest.storage.db_hash`.
3. If they match → normal open.
4. If they differ → the folder was moved or renamed. Show a
   DaisyUI `alert alert-info` modal:

   > "This project was created at
   > `manifest.storage.origin_abspath`.
   > It now lives at `<current_path>`.
   > [Keep existing DB] [Rebind to new path] [Open Read-Only]"

   - **Keep existing DB** (default): no change. We still find the
     bundle via `db_relpath` (project mode) or via the stored
     `db_name` (global mode); only the *hash-of-current-path*
     differs from the stored one. Recommended for everyday moves.
   - **Rebind to new path**: rename the bundle to
     `apollo_<new_hash>.cblite2`, update `db_hash`, `db_name`,
     `db_relpath`, and `origin_abspath` in the manifest. Used when
     the project's identity *should* change (e.g., a user renames
     a fork).
   - **Open Read-Only**: open the bundle without writing; useful
     when the user is just inspecting an archive.

This keeps the simple "MD5 of abspath" naming rule the user
requested while not punishing legitimate moves with a forced
re-index.

#### 6.2.7 Backend conversion (post-init)

Out of scope for the bootstrap wizard but recorded here so the
schema accommodates it:

- `POST /api/projects/convert-storage { to: "cblite" | "json" }` —
  reads from current store, writes into a sibling temp store, swaps
  paths atomically, deletes old store. Updates
  `apollo.json.storage.backend` and `cblite_version`.
- A `Convert Storage` button lives in the management modal's footer
  alongside the Reprocess buttons; disabled with tooltip if libcblite
  isn't installed.

#### 6.2.8 Telemetry

Per-index telemetry in `_apollo/_meta.json` already records timing
and bytes written. For CBL we add:

- `db_size_bytes` (sum of `db.sqlite3*` + `Attachments/`)
- `wal_size_bytes` (canary for un-checkpointed writes)
- `cblite_version`
- A weekly `compact` action: call `cbl.compact()` opportunistically
  when `db_size_bytes / live_doc_bytes > 1.5`. Logged in
  `_meta.json`.

#### 6.2.9 Acceptance criteria additions

- [ ] Two distinct project folders create two distinct
      `_apollo/cblite/apollo_<md5>.cblite2/` bundles whose names are the
      MD5 of each project's absolute path; their graphs do not
      cross-contaminate.
- [ ] `apollo.json.storage.db_hash` matches
      `md5(apollo.json.storage.origin_abspath)` exactly at create
      time.
- [ ] Moving a project folder triggers the rebind dialog (§6.2.6);
      choosing **Keep existing DB** opens the project without
      renaming the bundle, and choosing **Rebind** updates all four
      `db_*` fields and renames the bundle on disk.
- [ ] `Leave Project` closes the CBL handle before `rm -rf` and
      succeeds on Windows where open handles would otherwise block
      deletion.
- [ ] Switching from project A → project B in the same Apollo
      session closes A's handle (no growing fd count over repeated
      switches).
- [ ] `Reprocess (Full)` on a CBL project yields an empty bundle of
      a few KB before the indexer fills it back up, proving the
      delete actually ran.
- [ ] Opening a project whose `_apollo/cblite/apollo_<md5>.cblite2/` is
      already in use by another Apollo process surfaces a
      DaisyUI `alert-warning` and does not crash.
- [ ] `apollo.json.storage.backend` is honored on subsequent opens
      regardless of the current `settings.json` default.

---

## 7. Frontend changes

Files (`apollo/web/static/`, per `STYLE_HTML_CSS.md`):

- `index.html` — add the bootstrap modal markup (`<dialog id="bootstrap-modal" class="modal">`), the folder-tree template, the Tagify inputs, and a gear-icon button in the dev toolbar to re-open it for management. **No `<style>` or `<script>` blocks**, no emoji.
- `app.css` — minor additions: tree-row indentation, disabled-row strikethrough for built-in skips, custom Tagify pill colors that match `--p` / `--er`.
- `app.js` — new `bootstrap.js`-style functions added to the existing file (per the guide's "do not create additional files unless clearly scoped"):
  - `openProject(path)` — calls `/api/projects/open`, then either opens the wizard or loads the graph.
  - `renderWizard(state, projectInfo)` — single function rendering Steps 1 → 4 by toggling DaisyUI modal sections.
  - `loadProjectTree()` / `serializeFilters()` / `submitInit(filters)`.
  - `submitReprocess(mode)`, `submitLeave()`.
  - Tagify init for the two text inputs (already loaded from CDN per Phase 12).
  - Heroicon SVGs inlined per the style guide rule (Heroicons outline 24×24, `stroke-width="1.5"`).

The wizard reuses the **Phase 8 indexing-status modal** for Step 3 —
no duplication. It just programmatically advances to that modal and
listens for completion.

---

## 8. Edge Cases & Resume Behavior

| Scenario | Behavior |
|---|---|
| User cancels Step 1/2 of wizard | `_apollo/` is **not** created; nothing persists. |
| User cancels mid-indexing | `initial_index_completed` stays `false`. Next open of this folder re-opens wizard at "Resume?" step (offers `[Continue]`/`[Edit Filters]`). |
| `apollo.json` exists but fails schema validation | Treat as new project; rename old file to `apollo.json.bak.<timestamp>` and start fresh wizard. |
| `apollo.json` written by a newer Apollo version | Show a banner "This project was created with Apollo X.Y; opening in read-only mode." Disable Reprocess until upgrade. |
| Folder is itself inside another `_apollo/` project (nested) | Refuse with a modal: "Pick a parent folder, or open the existing project at `<parent>` instead." |
| User picks the same folder twice in My Files | `recent_projects` deduplicates by `project_id`. |
| Disk full / no write permission on `_apollo/` | Wizard surfaces a DaisyUI `alert alert-error` with the OS error and the **Process** button stays disabled. |
| Project-relative paths contain symlinks pointing outside `root_dir` | `_safe_path` (DESIGN §12.3) is reused — out-of-root targets are excluded with a warning chip in the wizard summary. |

---

## 9. Telemetry & Observability

- Each successful index updates `apollo.json.stats` AND appends a row
  to `_apollo/_meta.json` (already used by
  `PLAN_INCREMENTAL_REINDEX.md`).
- The dev toolbar (DESIGN §9.6) gains a tooltip on the node-count
  badge showing `last_indexed_at` and Apollo version used.
- Errors during init/reprocess go to the existing notifications area
  and to `_apollo/_meta.json` under a `last_error` field.

---

## 10. Migration & Rollout

1. **Phase A (this plan)** — Implement `_apollo/` layout, manifest,
   wizard, and `/api/projects/*` endpoints. New projects only.
2. **Phase B** — One-shot migration helper that reads the legacy
   `data/` / `.apollo/` / `.graph_search/` paths and offers to move
   them into the user's selected project's `_apollo/` folder. Skipped
   by this plan; a `# TODO migrate` placeholder lives in
   `apollo/projects/manager.py`.
3. **Phase C** — Surface multiple recent projects in the My Files
   view, switch active project without restart.

---

## 11. Acceptance Criteria

- [ ] `schema/apollo-project.schema.json` exists, is registered in
      `schema/index.html`, and `apollo.json` written by the wizard
      validates against it.
- [ ] Picking a fresh folder triggers the wizard; picking the same
      folder again after a successful index opens straight into the
      graph view (no wizard).
- [ ] **Process All** vs **Custom Filters** both end at the
      Phase 8 indexing modal and produce a graph that reflects the
      chosen filters.
- [ ] **Custom Filters** persists `include_dirs`, `exclude_dirs`,
      `exclude_file_globs`, `include_doc_types` exactly as the user
      configured them; re-opening the wizard pre-loads them.
- [ ] **Reprocess (Incremental)** completes without losing user
      annotations/chat/bookmarks; **Reprocess (Full)** rebuilds the
      graph from scratch but still preserves them.
- [ ] **Leave Project** deletes `_apollo/` and `_apollo_web/`,
      removes the project from `recent_projects`, and returns the
      user to My Files.
- [ ] Closing the app mid-bootstrap leaves
      `initial_index_completed=false`; next open offers a Resume
      step.
- [ ] No HTML emojis introduced; all icons are inline Heroicons
      outline 24×24 `stroke-width="1.5"` per
      `guides/STYLE_HTML_CSS.md`.
- [ ] All new user-visible chrome uses DaisyUI components
      (`modal`, `tabs`, `tab`, `collapse`, `checkbox`, `toggle`,
      `btn`, `alert`, `badge`) — no raw CSS where DaisyUI suffices.
- [ ] All new persisted JSON files validate against their schema in
      a unit test under `tests/test_project_manifest.py`.
