# Phase 11 — Highlights, Notes, Bookmarks & Web Content Capture

Detailed implementation plan for DESIGN.md §14.  
Each step is self-contained and can be executed in a separate chat session.

---

## Dependencies Between Steps

```
Step 1 (Schemas)
  └──► Step 2 (Annotation Store)
         ├──► Step 3 (Highlights API)
         │      └──► Step 6 (Highlights Frontend)
         ├──► Step 4 (Notes API)
         │      └──► Step 7 (Notes Frontend + Search)
         │             └──► Step 8 (Trash Can Frontend)
         └──► Step 5 (Bookmarks API)
                └──► Step 9 (Bookmarks Frontend)

Step 10 (Capture Service) ← independent, can start after Step 1
  └──► Step 11 (Capture API)
         └──► Step 12 (Capture Frontend)
               └──► Step 13 (PDF → AI Pipeline)

Step 14 (Integration Testing) ← after all steps
```

---

## Step 1 — JSON Schemas

**Goal**: Define the data contracts for highlights, notes, bookmarks, and captures following `guides/SCHEMA_DESIGN.md`.

**Files to create**:
- `schema/highlight.schema.json`
- `schema/note.schema.json`
- `schema/bookmark.schema.json`
- `schema/capture.schema.json`

**Files to edit**:
- `schema/index.html` — add the 4 new filenames to the `SCHEMA_FILES` array
- `schema/edge.schema.json` — add `annotates` and `bookmarks` to the `type` enum

**Highlight schema fields**:
| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | ✅ | `hl::<uuid>` |
| `node_id` | string | ✅ | Graph node ID this highlight is anchored to |
| `text` | string | ✅ | The selected/highlighted text |
| `start_offset` | integer | ✅ | Character offset in the node's source text |
| `end_offset` | integer | ✅ | Character offset end |
| `color` | string | ✅ | Hex color, default `#fde047` (yellow) |
| `created_at` | string | ✅ | ISO-8601 timestamp |
| `updated_at` | string | ✅ | ISO-8601 timestamp |

**Note schema fields**:
| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | ✅ | `note::<uuid>` |
| `highlight_id` | string \| null | ✅ | Link to highlight, or null for standalone |
| `node_id` | string | ✅ | Graph node this note belongs to |
| `body` | string | ✅ | Markdown note content |
| `tags` | array of string | ✅ | User tags for search (can be empty `[]`) |
| `created_at` | string | ✅ | ISO-8601 |
| `updated_at` | string | ✅ | ISO-8601 |
| `deleted_at` | string \| null | ✅ | Non-null = in trash |

**Bookmark schema fields**:
| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | ✅ | `bm::<uuid>` |
| `target_type` | string | ✅ | `node` or `note` |
| `target_id` | string | ✅ | ID of the bookmarked node or note |
| `label` | string \| null | — | Optional user-defined label |
| `created_at` | string | ✅ | ISO-8601 |

**Capture schema fields**:
| Field | Type | Required | Description |
|---|---|---|---|
| `url` | string | ✅ | Original URL |
| `slug` | string | ✅ | Folder name derived from domain + path |
| `current_md5` | string | ✅ | MD5 of latest content |
| `versions` | array | ✅ | Newest at `[0]`, see version object below |

**Capture version object**:
| Field | Type | Required | Description |
|---|---|---|---|
| `content_md5` | string | ✅ | MD5 of this version's content |
| `captured_at` | string | ✅ | ISO-8601 |
| `title` | string \| null | — | Page title extracted from HTML/PDF |
| `size_bytes` | integer | ✅ | File size |
| `content_type` | string | ✅ | MIME type (`text/html`, `application/pdf`) |

**Verification**: Open `schema/index.html` in browser → all 4 new schemas render correctly.

---

## Step 2 — Annotation Store

**Goal**: Build the persistence layer for highlights, notes, and bookmarks. Stored in `.graph_search/annotations.json`, separate from the auto-generated graph index.

**File to create**:
- `graph_search/storage/annotation_store.py`

**Design**:
```python
class AnnotationStore:
    """CRUD storage for highlights, notes, and bookmarks.
    
    Data lives in .graph_search/annotations.json, separate from the
    graph index so re-indexing never destroys user annotations.
    """
    
    def __init__(self, base_dir: str = ".graph_search"):
        ...
    
    # --- Highlights ---
    def create_highlight(self, node_id, text, start_offset, end_offset, color="#fde047") -> dict
    def get_highlights(self, node_id: str | None = None) -> list[dict]
    def update_highlight(self, hl_id: str, **updates) -> dict
    def delete_highlight(self, hl_id: str) -> None  # hard delete, cascades to notes
    
    # --- Notes ---
    def create_note(self, node_id, body, highlight_id=None, tags=None) -> dict
    def get_notes(self, node_id=None, trash=False, q=None) -> list[dict]
    def get_note(self, note_id: str) -> dict
    def update_note(self, note_id: str, **updates) -> dict
    def soft_delete_note(self, note_id: str) -> dict     # sets deleted_at
    def restore_note(self, note_id: str) -> dict          # clears deleted_at
    def purge_note(self, note_id: str) -> None            # permanent delete
    
    # --- Bookmarks ---
    def create_bookmark(self, target_type, target_id, label=None) -> dict
    def get_bookmarks(self) -> list[dict]
    def delete_bookmark(self, bm_id: str) -> None
    
    # --- Persistence ---
    def _load(self) -> dict        # reads annotations.json
    def _save(self, data) -> None  # writes annotations.json (compact separators)
```

**`annotations.json` structure**:
```json
{
  "highlights": [...],
  "notes": [...],
  "bookmarks": [...]
}
```

**Key behaviors**:
- IDs generated via `uuid.uuid4()` with prefix (`hl::`, `note::`, `bm::`)
- `created_at`/`updated_at` set automatically as ISO-8601 UTC
- `get_notes(q="smtp")` does case-insensitive substring match on `body` + `tags`
- `get_notes(trash=True)` returns only notes where `deleted_at is not None`
- `get_notes(trash=False)` (default) returns only notes where `deleted_at is None`
- Deleting a highlight cascades: soft-deletes any attached notes
- Use compact JSON serialization (`separators=(",",":")`) to match `JsonStore` convention

**Files to edit**:
- `graph_search/storage/__init__.py` — export `AnnotationStore`

**Verification**: Write a small script that creates a highlight, attaches a note, soft-deletes it, restores it, bookmarks the node. Inspect `annotations.json` after each step.

---

## Step 3 — Highlights API

**Goal**: FastAPI endpoints for highlight CRUD.

**File to edit**:
- `graph_search/web/server.py`

**Endpoints**:

| Route | Method | Request Body | Response |
|---|---|---|---|
| `/api/highlights` | GET | `?node_id=xxx` (optional) | `{ "highlights": [...] }` |
| `/api/highlights` | POST | `{ "node_id", "text", "start_offset", "end_offset", "color?" }` | `{ "highlight": {...} }` |
| `/api/highlights/{id}` | PUT | `{ "color?", "text?" }` | `{ "highlight": {...} }` |
| `/api/highlights/{id}` | DELETE | — | `{ "ok": true }` |

**Setup**:
- Instantiate `AnnotationStore` alongside existing stores in `server.py`
- Keep it as a module-level singleton (same pattern as the graph/search stores)

**Verification**: `curl` each endpoint manually. Create a highlight, list it, update color, delete it.

---

## Step 4 — Notes API

**Goal**: FastAPI endpoints for note CRUD, search, and trash management.

**File to edit**:
- `graph_search/web/server.py`

**Endpoints**:

| Route | Method | Request Body / Params | Response |
|---|---|---|---|
| `/api/notes` | GET | `?node_id=`, `?q=search`, `?trash=true` | `{ "notes": [...] }` |
| `/api/notes` | POST | `{ "node_id", "body", "highlight_id?", "tags?" }` | `{ "note": {...} }` |
| `/api/notes/{id}` | GET | — | `{ "note": {...} }` |
| `/api/notes/{id}` | PUT | `{ "body?", "tags?" }` | `{ "note": {...} }` |
| `/api/notes/{id}` | DELETE | — | `{ "note": {...} }` (soft-delete, returns updated note) |
| `/api/notes/{id}/restore` | POST | — | `{ "note": {...} }` |
| `/api/notes/{id}/purge` | DELETE | — | `{ "ok": true }` |

**Search behavior**: `GET /api/notes?q=smtp` returns notes where `body` or any `tag` contains "smtp" (case-insensitive). Only active notes unless `trash=true`.

**Verification**: Create notes with tags, search by tag, search by body text, soft-delete, list trash, restore, purge.

---

## Step 5 — Bookmarks API

**Goal**: FastAPI endpoints for bookmark CRUD.

**File to edit**:
- `graph_search/web/server.py`

**Endpoints**:

| Route | Method | Request Body | Response |
|---|---|---|---|
| `/api/bookmarks` | GET | — | `{ "bookmarks": [...] }` |
| `/api/bookmarks` | POST | `{ "target_type", "target_id", "label?" }` | `{ "bookmark": {...} }` |
| `/api/bookmarks/{id}` | DELETE | — | `{ "ok": true }` |

**Duplicate prevention**: If a bookmark already exists for the same `target_type + target_id`, return the existing one instead of creating a duplicate.

**Verification**: Bookmark a node, list bookmarks, try to bookmark the same node again (should return existing), delete bookmark.

---

## Step 6 — Highlights Frontend

**Goal**: Let users select text in the Node Detail panel and create highlights.

**Files to edit**:
- `graph_search/web/static/app.js`
- `graph_search/web/static/app.css`
- `graph_search/web/static/index.html`

**Implementation**:

1. **Selection listener**: On `mouseup` inside `#left-detail-content`, check `window.getSelection()`. If non-empty and inside the source code area:
   - Compute `start_offset` and `end_offset` relative to the node's `source` text
   - Show a floating toolbar near the selection with "📝 Add Note" button and color swatches (yellow, green, blue, orange)

2. **Offset serialization**: Map the DOM selection range back to character offsets in the raw `source` string. The source is rendered inside `<pre class="detail-code-block"><code>`, so offsets map to the text content of that element.

3. **Rendering existing highlights**: When `showDetail(data)` fires for a node, fetch `GET /api/highlights?node_id=XXX`. For each highlight, wrap the corresponding text range in a `<mark>` element with the highlight's color as background.

4. **Floating toolbar**: Absolute-positioned div near the selection. Disappears on click-away. "Add Note" opens the note editor (Step 7).

**CSS additions**:
- `.highlight-toolbar` — floating toolbar styles
- `.text-highlight` — `<mark>` element with configurable background color
- Color swatch buttons

**Verification**: Select text in the detail panel → toolbar appears → click a color → text is highlighted → refresh page → highlight persists.

---

## Step 7 — Notes Frontend + Search

**Goal**: Note creation/editing UI and a searchable Notes sidebar tab.

**Files to edit**:
- `graph_search/web/static/app.js`
- `graph_search/web/static/app.css`
- `graph_search/web/static/index.html`

**Implementation**:

1. **Note editor popover**: When "Add Note" is clicked from the highlight toolbar (or from a "+ Note" button on the detail panel for standalone notes):
   - Small popover/modal with:
     - Blockquote showing the highlighted text (if from a highlight)
     - Markdown `<textarea>` for the note body
     - Tag input (comma-separated or pill-style)
     - Save / Cancel buttons
   - On save: `POST /api/notes`

2. **Notes sidebar tab**: Add a `📝 Notes` tab to the existing sidebar tab system.
   - Search bar at the top (`<input>` that calls `GET /api/notes?q=...` with debounce)
   - List of note cards showing:
     - Highlighted text snippet (if from a highlight, truncated)
     - Note body preview (first 2 lines)
     - Node name + type badge
     - Timestamp
     - Action icons: Edit ✏️, Delete 🗑️, Bookmark ⭐
   - Click a note → navigate to the node in the graph + show its detail panel

3. **Inline note display**: In the detail panel, after the source code block, show any notes attached to this node. Each note card has Edit / Delete / Bookmark actions.

4. **Edit flow**: Click ✏️ → same popover as creation, pre-filled with existing body + tags. On save: `PUT /api/notes/{id}`.

5. **Delete flow**: Click 🗑️ → confirm dialog → `DELETE /api/notes/{id}` (soft-delete) → note disappears from active list, appears in trash.

**Verification**: Create a note from a highlight, see it in the sidebar, search for it, edit it, see updated content, delete it, confirm it's gone from active list.

---

## Step 8 — Trash Can Frontend

**Goal**: UI for viewing and managing soft-deleted notes.

**Files to edit**:
- `graph_search/web/static/app.js`
- `graph_search/web/static/app.css`
- `graph_search/web/static/index.html`

**Implementation**:

1. **Trash toggle in Notes tab**: A toggle switch or tab pair at the top of the Notes sidebar: `Active | Trash`. When "Trash" is selected, fetch `GET /api/notes?trash=true`.

2. **Trash note cards**: Same layout as active notes, but with different actions:
   - Restore ↩️ → `POST /api/notes/{id}/restore` → note moves back to active
   - Permanently Delete ❌ → confirm dialog → `DELETE /api/notes/{id}/purge` → gone forever

3. **Trash count badge**: Show count of trashed notes on the Trash toggle: `Trash (3)`.

4. **Visual distinction**: Trashed notes have a slightly dimmed/muted appearance to indicate they're pending deletion.

**Verification**: Soft-delete a note from Step 7 → switch to Trash view → see it → restore it → see it back in Active → soft-delete again → permanently delete → confirm it's gone.

---

## Step 9 — Bookmarks Frontend

**Goal**: Bookmark toggle on nodes and notes, plus a Bookmarks sidebar tab.

**Files to edit**:
- `graph_search/web/static/app.js`
- `graph_search/web/static/app.css`
- `graph_search/web/static/index.html`

**Implementation**:

1. **Star toggle on detail panel**: Add a ⭐ icon button next to the node name in `showDetail()`. Click toggles bookmark on/off.
   - On: `POST /api/bookmarks { target_type: "node", target_id: "..." }`
   - Off: `DELETE /api/bookmarks/{id}`
   - Check state on detail load: fetch bookmarks, see if this node is bookmarked

2. **Star on note cards**: Same toggle on each note card in the sidebar and detail panel.

3. **Bookmarks sidebar tab** (`⭐ Bookmarks`):
   - List of bookmarked items, grouped by type (Nodes / Notes)
   - Each item shows: name, type badge, optional label, timestamp
   - Click → navigate to the node in the graph (for node bookmarks) or show the note (for note bookmarks)
   - Remove button (unbookmark)

4. **Graph indicators**: Bookmarked nodes in the ECharts graph get a subtle visual marker (e.g., slightly larger size or a different border).

**Verification**: Bookmark a node from the detail panel, see it in the Bookmarks tab, click it to navigate, unbookmark from the tab, confirm star toggle updates.

---

## Step 10 — Web Capture Service

**Goal**: Backend service that downloads web content, converts it to Markdown, and stores it in `_apollo_web/`.

**File to create**:
- `graph_search/capture/__init__.py`
- `graph_search/capture/service.py`

**Dependencies to add to `requirements.txt`**:
- `httpx` (async HTTP client)
- `beautifulsoup4` (HTML parsing)
- `readability-lxml` (article extraction — strips nav, ads, footers)
- `markdownify` (HTML → Markdown)

**Design**:
```python
class CaptureService:
    """Download web content and store in _apollo_web/ for indexing."""
    
    def __init__(self, project_root: str):
        self._root = Path(project_root)
        self._apollo_dir = self._root / "_apollo_web"
        self._manifest_path = self._apollo_dir / "_manifest.json"
    
    async def capture(self, url: str) -> dict:
        """Download URL, convert to .md, store in _apollo_web/, return capture metadata."""
        ...
    
    async def redownload(self, capture_id: str) -> dict:
        """Re-fetch URL, version if content changed (new entry at versions[0])."""
        ...
    
    def list_captures(self) -> list[dict]:
        """Return all captures from _manifest.json."""
        ...
    
    def get_capture(self, capture_id: str) -> dict:
        """Return single capture metadata + version history from capture.json."""
        ...
    
    def delete_capture(self, capture_id: str) -> None:
        """Remove capture folder + manifest entry."""
        ...
    
    # Internal helpers
    def _url_to_slug(self, url: str) -> str
    def _detect_content_type(self, response) -> str
    def _html_to_markdown(self, html: str, url: str) -> tuple[str, str]  # (markdown, title)
    def _ensure_apollo_dir(self) -> None
    def _load_manifest(self) -> dict
    def _save_manifest(self, data: dict) -> None
```

**Slug generation**: `https://docs.python.org/3/tutorial/index.html` → `docs.python.org_3_tutorial_index.html`. Strip protocol, replace `/` with `_`, truncate to 80 chars, remove trailing underscores.

**HTML → Markdown pipeline**:
1. Fetch with `httpx` (follow redirects, 30s timeout, browser-like User-Agent)
2. Extract article content with `readability-lxml` (strips nav, sidebar, footer, ads)
3. Convert to Markdown with `markdownify` (preserves headings, links, code blocks, tables)
4. Prepend YAML frontmatter: `title`, `source_url`, `captured_at`, `content_type`
5. Save as `content.md` — the `MarkdownParser` handles it from here

**`_manifest.json` structure**:
```json
{
  "captures": [
    {
      "id": "cap::<uuid>",
      "url": "https://docs.python.org/3/tutorial/",
      "slug": "docs.python.org_3_tutorial_index.html",
      "status": "ready"
    }
  ]
}
```

Manifest is the lightweight global index. Full version history lives in each capture's `capture.json`.

**`capture.json` structure** (per earlier design):
```json
{
  "url": "https://docs.python.org/3/tutorial/",
  "slug": "docs.python.org_3_tutorial_index.html",
  "current_md5": "d4e5f6...",
  "versions": [
    {
      "content_md5": "d4e5f6...",
      "captured_at": "2026-04-25T12:00:00Z",
      "title": "Python Tutorial",
      "size_bytes": 51340,
      "content_type": "text/html"
    }
  ]
}
```

**Versioning on re-download**: MD5 the new content. If `== current_md5` → no-op, return existing. If different → overwrite `content.md`/`content.html`, insert new version at `versions[0]`, update `current_md5`.

**Verification**: Call `capture("https://example.com")` → confirm `_apollo_web/example.com/` folder created with `content.html`, `content.md`, `capture.json`. Call again → no-op (same MD5). Manually edit `content.html` and call `redownload` → confirm `versions` array now has 2 entries.

---

## Step 11 — Web Capture API

**Goal**: FastAPI endpoints for web capture CRUD.

**File to edit**:
- `graph_search/web/server.py`

**Endpoints**:

| Route | Method | Request Body / Params | Response |
|---|---|---|---|
| `/api/captures` | GET | — | `{ "captures": [...] }` from manifest |
| `/api/captures` | POST | `{ "url": "https://..." }` | `{ "capture": {...}, "status": "ready" }` |
| `/api/captures/{id}` | GET | — | `{ "capture": {...} }` with full version history |
| `/api/captures/{id}` | DELETE | — | `{ "ok": true }` |
| `/api/captures/{id}/redownload` | POST | — | `{ "capture": {...}, "changed": true/false }` |

**Auto-index trigger**: After a successful capture or redownload that produces a new version, call `build_incremental` scoped to just the new/changed `.md` file. Use the existing `run_in_executor` pattern from `POST /api/index` so it's non-blocking.

**Error handling**:
- Invalid URL → 400
- URL unreachable → 502 with error detail
- Content too large (>10MB) → 413
- Unsupported content type → 415

**Verification**: `curl -X POST /api/captures -d '{"url":"https://example.com"}'` → 200 + capture object. `GET /api/captures` → shows it. Re-index → the content appears as searchable nodes in the graph.

---

## Step 12 — Web Capture Frontend

**Goal**: UI for capturing URLs and browsing captured content.

**Files to edit**:
- `graph_search/web/static/app.js`
- `graph_search/web/static/app.css`
- `graph_search/web/static/index.html`

**Implementation**:

1. **"Capture URL" button**: Add a 🌐 button to the top bar (next to Load/Delete). Click opens a modal:
   - URL input field
   - "Capture" button
   - Status indicator: Downloading → Converting → Indexing → Ready ✅ / Error ❌
   - On success: auto-navigate to the new document node in the graph

2. **Web sidebar tab** (`🌐 Web`):
   - List of all captures from `GET /api/captures`
   - Each item shows: favicon/domain, page title, capture date, version count
   - Click → navigate to the document node in the graph
   - Actions per item: Re-download 🔄, Delete 🗑️
   - Re-download shows "Checking..." then "No changes" or "Updated to v2"

3. **Capture detail in Node Detail panel**: When a document node from `_apollo_web/` is selected, show additional metadata:
   - Source URL (clickable link to original)
   - Captured date
   - Version count
   - "Re-download" button

**Verification**: Click 🌐 button, enter a URL, see the status progress, see the capture in the Web tab, click it to see the content in the graph, re-download it.

---

## Step 13 — PDF → AI Pipeline

**Goal**: Handle PDF captures by sending content to Grok API for structured summarization.

**Files to edit**:
- `graph_search/capture/service.py`

**Design**:

1. **PDF detection**: If `content_type` from the HTTP response is `application/pdf`, save the blob as `content.pdf` in the capture folder.

2. **AI summarization**: Send the PDF content (as base64 or extracted raw text) to the Grok API with a structured prompt:
   ```
   Summarize and structure this document. Return valid JSON with:
   {
     "title": "...",
     "summary": "...",
     "sections": [
       { "heading": "...", "content": "..." }
     ],
     "key_points": ["..."],
     "tables": [
       { "headers": [...], "rows": [[...]] }
     ],
     "references": ["..."]
   }
   ```

3. **JSON → Markdown**: Convert the structured JSON response into a well-formatted `.md` file with:
   - YAML frontmatter (`title`, `source_url`, `captured_at`, `content_type: application/pdf`)
   - Summary section
   - Sections as h2 headings
   - Key points as a bullet list
   - Tables as Markdown tables
   - References as a link list

4. **Storage**: Save both `_apollo_{slug}.json` (raw AI response) and `content.md` (for indexing).

**Fallback**: If Grok API is unavailable or no API key configured, save just the PDF blob and create a minimal `content.md` with frontmatter only + a note that AI processing is pending.

**Dependencies**: No new dependencies — uses existing Grok API integration from Phase 4. PDF binary is stored as-is (no local PDF parsing library needed).

**Verification**: Capture a PDF URL → confirm `.pdf` blob saved → confirm AI summary JSON saved → confirm `content.md` generated → re-index → PDF content appears as searchable nodes.

---

## Step 14 — Integration Testing

**Goal**: End-to-end verification of all features working together.

**Test scenarios**:

1. **Highlight → Note → Search → Trash → Restore**
   - Index a project → load graph → click a node → select text → highlight it
   - Add a note to the highlight → search for the note by body text → search by tag
   - Soft-delete the note → verify it appears in Trash → restore it → verify it's back in Active
   - Permanently delete → verify it's gone everywhere

2. **Bookmark flow**
   - Bookmark a node from the detail panel → verify star is filled
   - Bookmark a note → verify it appears in Bookmarks tab
   - Click a bookmark → verify it navigates to the correct node/note
   - Unbookmark → verify star is unfilled, removed from tab

3. **Web capture — HTML**
   - Capture an HTML URL → verify `_apollo_web/` folder created
   - Verify `content.html`, `content.md`, `capture.json` exist
   - Re-index → verify document appears in graph as searchable nodes
   - Re-download same URL → verify no-op (same MD5)
   - Wait for content to change (or manually edit) → re-download → verify `versions[0]` is new

4. **Web capture — PDF**
   - Capture a PDF URL → verify `.pdf` blob saved
   - Verify AI summary JSON + `content.md` generated
   - Re-index → verify content is searchable

5. **Cross-feature**
   - Capture a web page → bookmark the resulting document node → add a note to a section
   - Search for the note → verify it links back to the captured content
   - Delete the capture → verify notes/bookmarks on that node are handled gracefully

6. **Data safety**
   - Create highlights + notes → re-index the project → verify annotations survive
   - Delete the graph index → verify `annotations.json` is untouched
   - Verify `_apollo_web/` content survives graph deletion (it's in the project root, not `.graph_search/`)

**Verification**: All 6 scenarios pass without errors. No data loss on re-index or index delete.

---

## File Index

Summary of all files created or edited across all steps.

### New Files

| File | Step | Purpose |
|---|---|---|
| `schema/highlight.schema.json` | 1 | Highlight data contract |
| `schema/note.schema.json` | 1 | Note data contract |
| `schema/bookmark.schema.json` | 1 | Bookmark data contract |
| `schema/capture.schema.json` | 1 | Web capture data contract |
| `graph_search/storage/annotation_store.py` | 2 | Annotation persistence layer |
| `graph_search/capture/__init__.py` | 10 | Capture module init |
| `graph_search/capture/service.py` | 10 | Web capture download + conversion |

### Edited Files

| File | Steps | Changes |
|---|---|---|
| `schema/index.html` | 1 | Add 4 schema filenames to `SCHEMA_FILES` |
| `schema/edge.schema.json` | 1 | Add `annotates`, `bookmarks` to type enum |
| `graph_search/storage/__init__.py` | 2 | Export `AnnotationStore` |
| `graph_search/web/server.py` | 3, 4, 5, 11 | Add all API endpoints |
| `graph_search/web/static/index.html` | 6, 7, 8, 9, 12 | Sidebar tabs, modals, toolbar HTML |
| `graph_search/web/static/app.js` | 6, 7, 8, 9, 12 | Selection handler, note UI, bookmarks, capture UI |
| `graph_search/web/static/app.css` | 6, 7, 8, 9, 12 | Highlight, note, bookmark, capture styles |
| `requirements.txt` | 10 | Add `httpx`, `beautifulsoup4`, `readability-lxml`, `markdownify` |
| `docs/DESIGN.md` | — | Phase 11 section (already added) |
