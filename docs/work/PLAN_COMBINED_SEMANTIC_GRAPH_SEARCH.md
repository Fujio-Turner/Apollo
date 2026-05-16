# Plan: Combined Semantic + Graph-Expansion Search

Final unchecked item from **Phase 2 — Semantic Search** in
[`docs/DESIGN.md`](../DESIGN.md):

> - [ ] Combined search: vector results + graph expansion

The other three Phase 2 boxes (embedding generation, brute-force cosine
search, the `search` CLI) are done. This plan delivers the one missing
capability end-to-end: a single call that does a vector search and then
walks the graph outward from each hit, returning *both* the seed nodes
and their structural neighborhood ranked together. The user-facing
example from [`DESIGN.md §4.5 — Combined Queries`](../DESIGN.md#combined-queries)
is the north star:

```
> search "email" --top 5 --expand callers
```

Designed so each phase is independently shippable and resumable across
chat sessions.

---

## 0. Background

Today the two primitives exist but live in different objects:

| Capability                       | Where it lives                                            |
|----------------------------------|-----------------------------------------------------------|
| Vector top-k cosine              | [`search/semantic.py`](../../search/semantic.py) `SemanticSearch.search` |
| CBL-native vector top-k          | [`search/cblite_semantic.py`](../../search/cblite_semantic.py) `CouchbaseLiteSemanticSearch.search` |
| Graph traversal (BFS callers / callees / neighbors / references) | [`graph/query.py`](../../graph/query.py) `GraphQuery.callers / callees / neighbors / references` |
| CLI `search <text> --top N --type T` | [`main.py::cmd_search`](../../main.py) (lines ~315–375) |
| HTTP `GET /api/search?q=&top=&type=` | [`web/server.py`](../../web/server.py) (lines ~1237–1272) |
| Chat tool `search_graph(query, top, type)` | [`chat/service.py`](../../chat/service.py) `_exec_tool_impl` (lines ~393–413) |
| Chat tool `search_graph_multi(queries, top, type)` | [`chat/service.py`](../../chat/service.py) (lines ~444+) |
| Chat tool `get_neighbors(node_id, depth)` | [`chat/service.py`](../../chat/service.py) (separate call) |

The AI today fakes "combined" by chaining `search_graph` → N parallel
`get_neighbors`. That costs an extra round, fans out N tool calls, and
the structural results aren't merged or scored against the original
query. A first-class combined endpoint collapses that into one round
with a consistent ranking surface.

---

## 1. Goals

1. Add a `SemanticSearch.search_expanded(query, top_k, expand, depth, type)`
   method (and a CBL-store sibling) that returns a single ranked list
   of `{seed, neighbors[]}` clusters.
2. Wire that method into the CLI as `search <text> --expand <kind>
   [--depth N]` matching the [`DESIGN.md §4.5`](../DESIGN.md#combined-queries) example.
3. Extend `GET /api/search` with optional `expand` / `depth` query params
   that switch it from flat results to a clustered response — same
   endpoint, additive change, safe for existing UI calls.
4. Add a `search_graph_expanded` chat tool so the AI gets the combined
   answer in one round instead of fanning out.
5. Update [`docs/DESIGN.md`](../DESIGN.md):
   - Tick the Phase 2 checkbox.
   - Flesh out §4.5 "Combined Queries" with the actual shape of the
     response (it's currently one paragraph + a CLI example).

## 2. Non-Goals

- Re-ranking with a learned model. Initial version reuses the cosine
  score for seeds and a simple decay for neighbors (`score / (1 + d)`).
- Cross-query merging — that's `search_graph_multi`'s job; out of scope.
- New embedding pipeline work. Reuses existing per-node vectors.
- Bidirectional expansion control beyond {`callers`, `callees`,
  `neighbors`, `references`, `none`}. No edge-type whitelisting in v1.
- Persisting search history.

---

## 3. Design

### 3.1 Shared expansion helper

Both `SemanticSearch` and `CouchbaseLiteSemanticSearch` need the same
expansion logic. Put it in `search/expand.py` so neither backend grows
a NetworkX dependency duplicated across files:

```python
# search/expand.py
from typing import Literal
import networkx as nx
from graph.query import GraphQuery

ExpandKind = Literal["none", "callers", "callees", "neighbors", "references"]

def expand_hits(
    graph: nx.DiGraph,
    seeds: list[dict],          # rows from SemanticSearch.search
    expand: ExpandKind,
    depth: int = 1,
    per_seed_cap: int = 10,
) -> list[dict]:
    """
    Returns clustered results:
      [
        {
          "id": "func::a.py::foo",  "score": 0.81, "name": ..., "type": ..., "path": ...,
          "neighbors": [
            {"id": "func::b.py::bar", "type": "func", "edge": "calls",
             "direction": "in",  "depth": 1, "score": 0.41},
            ...
          ]
        },
        ...
      ]
    Notes:
      - `expand="none"` returns the same shape with empty `neighbors` —
        callers that only want flat results just ignore the field.
      - Neighbor `score` is the seed score decayed by depth:
        `seed_score / (1 + d)`. Cheap, language-agnostic, and keeps
        the merged list sortable by a single numeric key.
      - `per_seed_cap` truncates the BFS at each seed to keep payloads
        bounded (a hub function can have hundreds of callers).
      - Edge `direction` is `"in"` for callers, `"out"` for callees,
        `"both"` for `neighbors` / `references`.
    """
```

The traversal uses the existing `GraphQuery` methods rather than
re-walking the graph — that keeps the depth / edge-type semantics
consistent with `query` CLI output and chat `get_neighbors`.

### 3.2 `SemanticSearch.search_expanded`

Thin wrapper on top of the current `search`:

```python
def search_expanded(
    self,
    query: str,
    top_k: int = 10,
    expand: ExpandKind = "none",
    depth: int = 1,
    per_seed_cap: int = 10,
    node_type: str | list[str] | None = None,
) -> list[dict]:
    seeds = self.search(query, top_k=top_k, node_type=node_type)
    if expand == "none":
        return [{**s, "neighbors": []} for s in seeds]
    return expand_hits(self.graph, seeds, expand, depth, per_seed_cap)
```

`CouchbaseLiteSemanticSearch` gets the same method signature; it
already has access to the in-memory `graph` (the CBL store rehydrates
it), so the same `expand_hits` helper works.

### 3.3 CLI surface

Extend `main.py`'s `search` subparser:

```text
search <text>
  --top N                (existing)
  -t / --type T          (existing)
  --expand {none,callers,callees,neighbors,references}   default: none
  --depth N              default: 1, ignored when --expand=none
  --per-seed-cap N       default: 10
```

Rendering:

- Flat mode (`--expand none`): identical to today — no regression.
- Expanded mode: indented two-level list. Per seed, print the cosine
  score and path; below it, print up to `per_seed_cap` neighbors with
  edge type and decayed score. Truncation indicator (`… +N more`) when
  capped.

### 3.4 HTTP surface

`GET /api/search` becomes additive:

| Param          | Type   | Default | Notes                                            |
|----------------|--------|---------|--------------------------------------------------|
| `q`            | str    | —       | existing                                         |
| `top`          | int    | 10      | existing                                         |
| `type`         | str    | —       | existing                                         |
| `expand`       | enum   | `none`  | one of `none/callers/callees/neighbors/references` |
| `depth`        | int    | 1       | ignored when `expand=none`                       |
| `per_seed_cap` | int    | 10      | ignored when `expand=none`                       |

Response shape:

- `expand=none` (default) — **unchanged**, returns `{"results": [...]}`
  with the same fields as today (`id`, `name`, `type`, `path`,
  `line_start`, `score`). Frontend keeps working.
- `expand≠none` — returns `{"results": [{seed_fields..., "neighbors":
  [...]}, ...]}`. Same top-level key so the client can branch on
  presence of `neighbors`.

This is intentionally a single endpoint rather than `/api/search/expanded`
to keep the OpenAPI surface small and to match the
[`API_OPENAPI.md`](../../guides/API_OPENAPI.md) guidance to prefer
additive query params.

The structural fallback path (when no embeddings exist) ignores
`expand` and returns the same flat list it does today, with a one-time
log line. Combined search is meaningless without vector scores.

### 3.5 Chat tool

New tool `search_graph_expanded` in
[`ai/chat_request.json`](../../ai/chat_request.json) plus the versioned
`chat_request_v*.json` files following the same convention as
`search_graph` / `search_graph_multi`:

```json
{
  "name": "search_graph_expanded",
  "description": "Combined semantic search + 1-hop graph expansion. Use when the user asks 'find X and what depends on it' or 'find X-related code plus callers/callees'. Returns clustered results: each seed node from the vector search comes with its top neighbors along the chosen edge direction. One call replaces search_graph + N parallel get_neighbors.",
  "parameters": {
    "type": "object",
    "properties": {
      "query":        {"type": "string"},
      "top":          {"type": "integer", "default": 5},
      "type":         {"type": "string"},
      "expand":       {"type": "string", "enum": ["callers","callees","neighbors","references"], "default": "callers"},
      "depth":        {"type": "integer", "default": 1, "maximum": 2},
      "per_seed_cap": {"type": "integer", "default": 5, "maximum": 20}
    },
    "required": ["query"]
  }
}
```

Dispatch in `chat/service.py::_exec_tool_impl` follows the existing
`search_graph` block — short, mostly a delegation to
`self.search.search_expanded(...)`.

Cheat-sheet update: the GRAPH/RELATIONSHIPS section in the system
prompt gets a single new line —

> 'Find X and what depends on / calls it' → `search_graph_expanded`
> (one call instead of `search_graph` + N `get_neighbors`).

### 3.6 Ranking notes

The seed score is the model's cosine value. Neighbor score is
`seed_score / (1 + d)` where `d` is the BFS distance from the seed.
This is deliberate:

- Keeps the response sortable by a single numeric key on the client.
- Avoids re-embedding neighbor source text (which would be expensive
  and largely redundant — neighbors are usually structurally related,
  not semantically novel).
- Matches the "blast radius" framing in
  [`DESIGN.md §7.6`](../DESIGN.md#76-impact-analysis-workflow-ui--ai)
  — depth-1 neighbors are the most-likely-to-be-impacted set.

A future refinement (left as a TODO comment in `expand.py`) would
re-score neighbors against the query embedding directly; we'd need to
guarantee neighbor embeddings exist (functions/classes do, but `import`
nodes often don't), so it's a Phase 2.5 add-on.

---

## 4. Phases

Each phase is a single landable PR.

### Phase A — Core helper + `SemanticSearch.search_expanded`

- [ ] Create `search/expand.py` with `expand_hits` (uses `GraphQuery`).
- [ ] Add `SemanticSearch.search_expanded` in `search/semantic.py`.
- [ ] Add `CouchbaseLiteSemanticSearch.search_expanded` in
      `search/cblite_semantic.py` (delegates to the same helper).
- [ ] Unit tests in `tests/test_semantic_expand.py`:
  - flat behavior when `expand="none"` matches `search()`.
  - callers/callees on a 3-node fixture return the right neighbors
    with the expected edge `direction`.
  - `per_seed_cap` truncates and reports `+N more` count in a `truncated`
    field on the seed.
  - depth=2 traversal returns depth-2 nodes with decayed score.
  - depth ≤ 0 raises `ValueError` (defensive).

**Done when:** unit tests pass; no caller is wired up yet.

### Phase B — CLI

- [ ] Add `--expand` / `--depth` / `--per-seed-cap` args in `main.py`.
- [ ] Branch `cmd_search` on `args.expand`: flat path unchanged,
      expanded path delegates to `search_expanded` and renders the
      two-level list.
- [ ] Update CLI help text and the `search` example in
      [`docs/DESIGN.md §4.5`](../DESIGN.md#combined-queries) to point
      at the real flags.

**Done when:**

```
python main.py search "email" --top 3 --expand callers --depth 1
```

prints seeds with indented callers; flat `search "email"` still works
identically to today.

### Phase C — HTTP

- [ ] Add the three new query params to `/api/search` in `web/server.py`.
- [ ] Validate `expand` against the enum (reject 422 on bad value).
- [ ] Update [`docs/openapi.yaml`](../openapi.yaml) per
      [`guides/API_OPENAPI.md`](../../guides/API_OPENAPI.md) (add the
      params + the new response variant).
- [ ] Cross-link from [`docs/API.md`](../API.md) (one row in the table).
- [ ] Integration test under `tests/test_web_search_expanded.py`
      hitting the endpoint with both shapes.

**Done when:** `GET /api/search?q=email` is byte-identical to today;
`GET /api/search?q=email&expand=callers&depth=1` returns the clustered
shape.

### Phase D — Chat tool

- [ ] Register `search_graph_expanded` in `ai/chat_request.json` and
      every `chat_request_v*.json` that's still referenced.
- [ ] Dispatch in `chat/service.py::_exec_tool_impl`.
- [ ] Add one cheat-sheet line under GRAPH/RELATIONSHIPS.
- [ ] Mention the tool in the §3.5 cheat sheet for `search_graph` so
      the LLM is steered away from the `search_graph` + N
      `get_neighbors` anti-pattern.
- [ ] Add an entry in [`docs/work/PLAN_LLM_ROUND_REDUCTION.md`](PLAN_LLM_ROUND_REDUCTION.md)
      tracking the round saved.

**Done when:** A representative "find email-related code and its
callers" prompt resolves in one tool round end-to-end against a
small fixture.

### Phase E — Docs / housekeeping

- [ ] Tick the Phase 2 checkbox in [`docs/DESIGN.md`](../DESIGN.md#phase-2--semantic-search).
- [ ] Expand [`docs/DESIGN.md §4.5`](../DESIGN.md#combined-queries) with
      the response shape and ranking note from §3 above.
- [ ] Add a one-line entry to [`RELEASE_NOTES.md`](../../RELEASE_NOTES.md).
- [ ] Update `docs/work/PHASE_*_SUMMARY.md` if a phase summary exists
      that covers Phase 2 follow-ups (otherwise skip — this doesn't
      warrant a new summary file on its own).

**Done when:** `git grep "Combined search: vector"` shows the checkbox
ticked and nothing else stale.

---

## 5. Test Plan

| Layer | Test                                                                       |
|-------|-----------------------------------------------------------------------------|
| Unit  | `tests/test_semantic_expand.py` — fixture graph with known edges, asserts shape, scores, truncation, edge directions. |
| Unit  | `tests/test_cblite_semantic_expand.py` — same suite parametrized to run against the CBL backend when extras installed; skipped otherwise. |
| CLI   | `tests/test_cli_search_expanded.py` — invokes `main.py search` via subprocess on a tiny indexed sample under `data/` fixtures; asserts the rendered tree contains the expected callers. |
| HTTP  | `tests/test_web_search_expanded.py` — FastAPI TestClient for both flat and expanded shapes; one regression test asserting flat shape didn't change. |
| Chat  | `tests/test_chat_search_graph_expanded.py` — invokes `_exec_tool_impl("search_graph_expanded", {...})` and asserts JSON shape. |
| Perf  | One micro-benchmark in `tests/test_semantic_expand.py::test_perf_budget` — `top=10, depth=1, per_seed_cap=10` must finish in < 50 ms on a 10k-node fixture. Catches accidental N²s in the expansion path. |

---

## 6. Risks & Mitigations

| Risk                                                                  | Mitigation |
|------------------------------------------------------------------------|------------|
| Response sizes balloon for hub seeds (e.g. `print`, `logger.info`).    | `per_seed_cap` defaults to 10; chat tool default 5; CLI prints `… +N more`. |
| OpenAPI consumers break on the new response variant.                   | Default `expand=none` preserves the flat shape — *no* existing client URL changes. Variant only activates when explicitly opted in. |
| AI over-uses the new tool and inflates token counts.                   | Chat tool description explicitly scopes it to "find X **and** its callers/callees" questions. Existing `search_graph` stays the default for name-only lookups. |
| Neighbor scoring is naive (linear decay).                              | Documented in §3.6 with a follow-up note in `expand.py`. Easy to swap in a learned ranker later without changing the API shape. |
| Structural-only fallback (no embeddings) silently drops `expand`.      | One-time `logger.warning` per process when `expand` is requested without embeddings; surfaced in the response as `{"warning": "..."}`. |

---

## 7. Out of Scope (explicitly)

- Multi-query combined search — that's `search_graph_multi` + a
  follow-up; revisit only if the round-reduction data justifies fusion.
- Re-ranking neighbors by their own embeddings — Phase 2.5.
- Persisting combined-search results in a chat-history sidecar.
- Web UI rendering of the clustered shape — initial consumer is the
  AI; UI work tracked separately under Phase 3.

---

## 8. Estimate

| Phase | Engineer-hours |
|-------|----------------|
| A — helper + `search_expanded`             | 3 |
| B — CLI                                    | 2 |
| C — HTTP + OpenAPI                         | 3 |
| D — chat tool + prompt updates             | 2 |
| E — docs / release notes                   | 1 |
| **Total**                                  | **11** |

Sequenceable but Phase A blocks all others. Phases B / C / D are
parallelizable once A lands.
