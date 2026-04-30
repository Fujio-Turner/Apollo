# API & OpenAPI Guide — Apollo

> Spec format: [OpenAPI 3.1.0](https://spec.openapis.org/oas/latest.html)
>
> Live docs:
> - [`/api-docs`](http://localhost:8080/api-docs) — Swagger UI **rendering the hand-maintained `docs/openapi.yaml`** (curated descriptions, examples, schemas)
> - [`/openapi.yaml`](http://localhost:8080/openapi.yaml) — raw YAML spec, served verbatim for external clients & codegen
> - [`/docs`](http://localhost:8080/docs) (Swagger UI) and [`/redoc`](http://localhost:8080/redoc) — **FastAPI auto-generated** views derived from `server.py`

---

## 1. What Is OpenAPI?

OpenAPI is a **YAML/JSON specification** that describes a REST API in a machine-readable format. It is **not** an HTML page — it's a structured data file that tools render into interactive documentation, client SDKs, and test suites.

Our spec lives at `docs/openapi.yaml`. The human-friendly markdown summary lives at `docs/API.md`.

### What We Ship

| Artifact | Path | Purpose |
|---|---|---|
| OpenAPI spec | `docs/openapi.yaml` | Machine-readable, source of truth |
| Markdown reference | `docs/API.md` | Quick-reference for developers |
| Swagger UI | `/docs` (live) | Interactive explorer (auto-served by FastAPI) |
| Redoc | `/redoc` (live) | Clean read-only docs (auto-served by FastAPI) |

FastAPI auto-generates Swagger UI and Redoc from the route definitions in `server.py`. The `openapi.yaml` file is our **hand-maintained** spec that includes richer descriptions, examples, and reusable schemas.

---

## 2. Project Structure

```
docs/
├── openapi.yaml          # OpenAPI 3.1 spec — ALL endpoints
├── API.md                 # Markdown quick reference
└── DESIGN.md              # Links to both

apollo/web/
└── server.py              # FastAPI app — route definitions + error handlers
```

---

## 3. How Errors Work

All API errors return a consistent JSON shape. This is enforced by three global exception handlers registered in `create_app()` inside `server.py`.

### Error Response Shape

```json
{
  "status_code": 400,
  "error": "Bad Request",
  "detail": "Not a directory: /foo"
}
```

| Field | Type | Description |
|---|---|---|
| `status_code` | `int` | HTTP status code (matches the response status) |
| `error` | `string` | Human-readable error category (from `HTTPStatus.phrase`) |
| `detail` | `string \| object \| array` | Specific error message or validation payload |

### Exception Handlers

The three handlers in `server.py` catch everything:

```
HTTPException          →  { status_code, HTTPStatus.phrase, exc.detail }
RequestValidationError →  { 422, "Validation Error", exc.errors() }
Exception (catch-all)  →  { 500, "Internal Server Error", "An unexpected error occurred" }
```

### How to Raise Errors in Endpoint Code

Use FastAPI's built-in `HTTPException`. The global handler normalizes it automatically:

```python
from fastapi import HTTPException

# In an endpoint:
raise HTTPException(status_code=404, detail="Node not found: func::main.py::foo")
raise HTTPException(status_code=400, detail="Not a directory: /tmp/bad")
raise HTTPException(status_code=503, detail="Chat not available. Set XAI_API_KEY.")
```

**Do not** return error dicts manually with 200 status codes. Always `raise HTTPException(...)` so the response flows through the global handler and gets the standard shape.

### Reusable Error Responses in openapi.yaml

Six reusable error responses are defined under `components/responses/`:

| Ref Name | Code | When to use |
|---|---|---|
| `BadRequest` | 400 | Invalid input, bad path, missing required field |
| `NotFound` | 404 | Node, thread, or resource not found |
| `ValidationError` | 422 | Pydantic / query-param validation failures |
| `ServiceUnavailable` | 503 | AI chat or image generation not configured |
| `UnsupportedOperation` | 501 | Feature unavailable in current environment (e.g. Docker) |
| `InternalServerError` | 500 | Unexpected server error |

Reference them in path definitions with `$ref`:

```yaml
"400":
  $ref: "#/components/responses/BadRequest"
```

---

## 4. Tags

Every endpoint belongs to exactly one tag. Tags group endpoints in the docs UI and in `openapi.yaml`.

| Tag | Prefix | Description |
|---|---|---|
| System | `/api/env`, `/api/version` | Runtime environment flags and backend version |
| Filesystem | `/api/browse-*`, `/api/tree` | Local directory browsing |
| Indexing | `/api/index`, `/api/indexing-status` | Create, delete, monitor indexes |
| Reindex | `/api/index/sweep`, `/api/index/history`, `/api/index/last`, `/api/index/summary`, `/api/index/config` | Phase 9 incremental reindex telemetry & config |
| Graph | `/api/graph`, `/api/node`, `/api/neighbors`, `/api/wordcloud`, `/api/stats` | Graph data retrieval |
| Search | `/api/search`, `/api/search/multi` | Semantic and keyword search |
| Files | `/api/file/*`, `/api/project/search` | Read-only file inspection |
| Projects | `/api/projects/*` | Project manifests, bootstrap wizard, reprocess, leave |
| Annotations | `/api/annotations*` | User-authored highlights, **notes**, **bookmarks**, tags & collections (Notes & Bookmarks tab) |
| Settings | `/api/settings`, `/api/logging/info` | API keys, chat config, plugin config, logging snapshot |
| Chat | `/api/chat*` | AI chat, threads, history |
| Images | `/api/image/*` | Image generation |
| Watch | `/api/watch/*` | File watcher status and control |
| Realtime | `/ws` | WebSocket channel for live graph updates |

When adding a new endpoint, assign it to an existing tag. If none fit, add a new tag entry to both the `tags:` list in `openapi.yaml` and the table above.

---

## 5. Adding a New Endpoint

### Step 1 — Write the Route in `server.py`

Add your endpoint inside `create_app()`, grouped with related routes. Follow the existing pattern:

```python
@app.post("/api/bookmarks")
async def create_bookmark(request: Request):
    body = await request.json()
    title = body.get("title", "")
    node_id = body.get("node_id")
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")
    # ... create bookmark ...
    return {"id": bookmark_id, "title": title, "node_id": node_id}
```

**Conventions:**
- All API routes start with `/api/`.
- Use `async def` for endpoints that do I/O or call `await`.
- Parse JSON bodies with `request.json()` (no Pydantic models yet — we may adopt them later).
- Return plain dicts — FastAPI serializes them to JSON.
- Raise `HTTPException` for errors — never return error dicts with 200.
- Place the route near related endpoints (look for the comment banners like `# ---- Chat --`).

### Step 2 — Add to `docs/openapi.yaml`

Add a new path entry under `paths:`. Follow this template:

```yaml
  /api/bookmarks:
    post:
      operationId: createBookmark        # camelCase, unique across all endpoints
      tags: [Graph]                       # pick from existing tags
      summary: Create a bookmark          # one line, shown in docs sidebar
      description: >                      # optional, longer explanation
        Save a bookmark for a graph node so you can find it later.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [node_id]
              properties:
                node_id:
                  type: string
                title:
                  type: string
                  default: ""
      responses:
        "200":
          description: Created bookmark
          content:
            application/json:
              schema:
                type: object
                required: [id, title, node_id]
                properties:
                  id:
                    type: string
                  title:
                    type: string
                  node_id:
                    type: string
        "400":
          $ref: "#/components/responses/BadRequest"
        "500":
          $ref: "#/components/responses/InternalServerError"
```

**Rules:**
- Every endpoint needs an `operationId` — use camelCase, keep it unique.
- Always include at least one success response and relevant error responses.
- For reusable schemas, add them under `components/schemas/` and `$ref` them.
- For error responses, always use the reusable `$ref` — don't inline error schemas.

### Step 3 — Add to `docs/API.md`

Add a section under the appropriate tag heading:

```markdown
### `POST /api/bookmarks`

Create a bookmark for a graph node.

**Request Body**
| Field     | Type   | Required | Description         |
|-----------|--------|----------|---------------------|
| `node_id` | string | yes      | Node to bookmark    |
| `title`   | string | no       | Display name        |

**Response**
\```json
{ "id": "bm_123", "title": "Main entry", "node_id": "func::main.py::main" }
\```
```

### Step 4 — Verify

1. Start the server: `python main.py serve`
2. Open `http://localhost:8080/docs` — confirm your endpoint shows up in Swagger UI
3. Test the endpoint with the "Try it out" button
4. Test error cases — confirm they return the standard `{status_code, error, detail}` shape

---

## 6. Updating an Existing Endpoint

### Changed Parameters or Response Shape

1. **Update `server.py`** — modify the route code.
2. **Update `docs/openapi.yaml`** — change the parameter list, schema properties, or add new response codes.
3. **Update `docs/API.md`** — adjust the param table or response example.

### Renamed Endpoint

1. **Update `server.py`** — change the `@app.get("/api/old")` decorator path.
2. **Update `docs/openapi.yaml`** — move the path entry to the new key.
3. **Update `docs/API.md`** — update the heading and any prose.
4. **Update frontend** — search `app.js` for the old URL and replace it.

### Added a New Query Parameter

```python
# server.py — add the parameter
@app.get("/api/search")
def search_nodes(
    q_text: str = Query(..., alias="q"),
    top: int = Query(10),
    type_filter: Optional[str] = Query(None, alias="type"),
    lang: Optional[str] = Query(None),          # ← new
):
```

```yaml
# openapi.yaml — add the parameter entry
- name: lang
  in: query
  required: false
  schema:
    type: string
  description: Filter results by programming language.
```

```markdown
<!-- API.md — add to the param table -->
| `lang` | string | no      | Filter by language |
```

---

## 7. Deleting an Endpoint

1. **Remove the route** from `server.py`.
2. **Remove the path entry** from `docs/openapi.yaml`.
3. **Remove the section** from `docs/API.md`.
4. **Remove any schemas** in `openapi.yaml` `components/schemas/` that are no longer referenced by any path.
5. **Search the frontend** (`app.js`, `index.html`) for calls to the deleted URL and remove them.
6. **Check `DESIGN.md`** — if the endpoint was mentioned in a phase checklist, update or remove the reference.

### Checking for Orphaned Schemas

After removing a path, grep the spec for any schema that's no longer referenced:

```bash
# List all schema names
grep -E '^\s{4}\w+:$' docs/openapi.yaml | sed 's/://;s/^ *//'

# For each name, check if it's still $ref'd
grep -c 'SomeSchemaName' docs/openapi.yaml
```

If a schema's only reference was the deleted path, remove it from `components/schemas/`.

---

## 8. Adding a New Reusable Schema

When multiple endpoints share the same request or response shape, define it once:

```yaml
components:
  schemas:
    Bookmark:
      type: object
      required: [id, title, node_id, created_at]
      properties:
        id:
          type: string
        title:
          type: string
        node_id:
          type: string
        created_at:
          type: string
          format: date-time
```

Then reference it from path definitions:

```yaml
schema:
  $ref: "#/components/schemas/Bookmark"
```

**Naming conventions:**
- PascalCase for schema names (e.g. `GraphNode`, `ChatRequest`, `ErrorResponse`).
- Group related schemas together with YAML comment banners (`# ── Chat ──`).

---

## 9. SSE Streaming Endpoints

The `POST /api/chat` endpoint returns Server-Sent Events (`text/event-stream`), not JSON. Special rules apply:

- **Pre-stream errors** (empty message, service unavailable) → raise `HTTPException` as normal. The global handler returns JSON.
- **Mid-stream errors** → once streaming has started, JSON error responses are impossible. Emit an SSE error frame instead:
  ```
  data: [ERROR] Something went wrong
  ```
- **End-of-stream** → emit `data: [DONE]\n\n`.
- **In `openapi.yaml`** → document the response as `text/event-stream` with a `string` schema and an example showing the frame format.

---

## 10. Checklist

Use this checklist when touching any API endpoint:

- [ ] Route added/modified in `server.py`
- [ ] Errors use `raise HTTPException(...)` (not manual error dicts)
- [ ] Path entry added/updated in `docs/openapi.yaml`
- [ ] `operationId` is set and unique
- [ ] Correct tag assigned
- [ ] All error responses use `$ref` to reusable responses
- [ ] Section added/updated in `docs/API.md`
- [ ] Tested via `/docs` Swagger UI "Try it out"
- [ ] Error responses return `{status_code, error, detail}` shape
