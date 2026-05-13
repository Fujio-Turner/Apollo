# PLAN — Cut LLM Round Cost by Delivering Better Tool Results

**Owner:** chat / file_inspect / search
**Status:** Not started — design + sequencing locked
**Sibling plans:** [`PLAN_MORE_LOCAL_AI_FUNCTIONS.md`](./PLAN_MORE_LOCAL_AI_FUNCTIONS.md), [`PLAN_ML_LIBS.md`](./PLAN_ML_LIBS.md)
**Reference:** [`docs/DESIGN.md §8`](../DESIGN.md) (chat / tool architecture, TOON re-encoding, 3-round budget, AI trace panel)

---

## 0. Goal

Apollo's tool layer is fast on the wire (~14% of total turn time), but
the **shape** of what those tools return forces the LLM into extra
rounds of fetching and re-fetching. The 3-round budget rule
([`DESIGN.md §8`](../DESIGN.md)) was designed around this — but inside a
round, the model still wastes wall-time digesting bloated payloads or
issuing overlapping follow-up reads.

This plan attacks the **per-round** cost, not the round budget itself.

### 0.1 Reference trace (single user question)

The user asked the model: *"in folder/file en/index.html, what
function(s) are used to cache processed data?"*

Recorded by the trace panel ([§8.8](../DESIGN.md)) and reproduced from
the apollo log:

| Phase                          | Wall  | Notes                                              |
|--------------------------------|------:|----------------------------------------------------|
| Apollo tools (8 calls)         | ~2.25s | One outlier: `search_graph` round-0 = **1.975s**  |
| LLM processing (3 rounds)      | ~11.1s | 71% of total                                       |
| Network / SSE overhead         | ~2.3s  | 15%                                                |
| **Total**                      | **15.642s** | Final answer was correct, confidence=high     |

### 0.2 Where the rounds went

- **Round 0** returned ~237 KB total (`file_search` 144 KB + `project_search`
  94 KB). The model had to read all of it just to locate ~6 symbols
  and ~5 line ranges in one file.
- **Round 1** issued **overlapping** reads:
  `get_file_section(3000–3200)` then `(3100–3200)` then
  `(3200–3300)`. The second call was already covered by the first.
- **Round 2** was pure formatting — no new information needed.
- The first `search_graph` call took **1.975 s** while the second took
  **0.071 s** — likely a cold-cache / lazy-init hit.

### 0.3 Target outcome

For questions of this class ("where in this file is X declared / used /
cached"), we want **1 round, < 5 s total**. The same answer, ~3× faster,
with no LLM-budget changes and no model swap.

The single biggest lever is making round 0 small and self-sufficient,
so the model doesn't need round 1 at all.

---

## 1. What's already in Apollo (do NOT re-implement)

Cross-checked against [`file_inspect.py`](../../file_inspect.py),
[`chat/local_tools.py`](../../chat/local_tools.py),
[`chat/service.py`](../../chat/service.py),
[`ai/chat_request.json`](../../ai/chat_request.json):

| Capability                                          | Lives in                                  |
|-----------------------------------------------------|-------------------------------------------|
| TOON re-encoding of every tool result               | `chat/service.py::_to_toon_for_llm`       |
| 3-round chat budget + cheat-sheet                   | `ai/chat_request.json` system prompt      |
| Per-file `file_stats` (md5, lines, function counts) | `file_inspect.py:170`                     |
| `file_search` regex + N-line context windows        | `file_inspect.py:456`                     |
| `project_search` cross-file grep with byte cap      | `file_inspect.py:510`                     |
| `get_file_section(start, end)` slicing              | `file_inspect.py:335`                     |
| `batch_file_sections` (multi-range fetch)           | `chat/local_tools.py:86`                  |
| MAX_FILE_SEARCH_MATCHES / MAX_PROJECT_SNIPPET_BYTES caps | `file_inspect.py`                    |
| AI trace panel + per-step bytes/dt                  | `web/static/app.js`, `chat/service.py`    |

What is **not** there yet:

- No way to ask "list all `Map()` / `class` / `def` declarations in
  this file" — the model gets there with a regex `file_search` and a
  144 KB result.
- No server-side dedup of overlapping `get_file_section` ranges.
- No latency budget / pre-warm for the first `search_graph` call.
- No "summary view" for `file_search` / `project_search` when the raw
  hit count would balloon the payload past N KB.

---

## 2. Phasing — 4 batches, ordered by ROI and risk

### Phase 1 — Compact `file_search` / `project_search` payloads ☑ ship first

**Lever:** This is the single biggest win in the trace — round 0
shipped 237 KB to answer a question whose final cited material is
under 2 KB. Cutting that payload makes round 0 self-sufficient so the
LLM skips round 1 entirely.

#### 1.1 Add a `summary` mode to `file_search` and `project_search`

Today every match carries `context_before` + `context_after` (default
**5 lines each side**, so 11 lines per hit). A regex like
`cache|Cache|cached|localStorage|...` against a 27 716-line file
returned 134+ matches × ~11 lines × ~80 chars ≈ 144 KB.

New parameter `view: "summary" | "full"` (default `summary`):

| Field                    | `summary` mode (default)                                                          | `full` mode (today's behaviour)        |
|--------------------------|-----------------------------------------------------------------------------------|----------------------------------------|
| `matches[].line_no`      | yes                                                                               | yes                                    |
| `matches[].text`         | trimmed to 200 chars                                                              | full line                              |
| `matches[].context_before/after` | **omitted**                                                               | N lines each side                      |
| `matches[].kind`         | `"declaration" \| "assignment" \| "call" \| "comment" \| "string" \| "other"`     | same (new field, see §1.3)             |
| `groups[]`               | hits clustered by nearby line ranges (gap > 20 lines opens a new group)           | omitted                                |
| `bytes_saved`            | reported back so the LLM trusts the trim                                          | n/a                                    |

The model gets the **map** of where caching lives, not the source. If
it actually needs surrounding lines, it issues `get_file_section` for
the *exact* range — which is what the existing tools are good at.

#### 1.2 Hard byte-cap with explicit "narrow your scope" hint

When the unfiltered match list would exceed `MAX_FILE_SEARCH_BYTES`
(target: **16 KB** in summary mode, **48 KB** in full mode):

- Truncate to the byte budget.
- Set `truncated: true` and `truncate_reason: "byte_cap"`.
- Append a `suggestions` block: top 3 line-range buckets with the
  densest hits, plus the 5 most common matched substrings, so the LLM
  has a concrete narrowing path instead of guessing.

The system prompt already nudges toward `return_result` with `med`
confidence rather than re-grepping; this gives the model the data to
do that one round earlier.

#### 1.3 Classify each hit (cheap, no extra deps)

A 5-line regex pass per hit tags it with `kind`:

```python
DECL_RX = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var|def)\s+(\w+)")
ASSIGN_RX = re.compile(r"^\s*(\w+)\s*=\s*new\s+(?:Map|WeakMap|Set|WeakSet)\b")
COMMENT_RX = re.compile(r"^\s*(?://|#|/\*|\*)")
```

For the trace's question, this alone collapses 134 generic
`cache|Cache|...` hits to ~12 declaration/assignment hits + a count of
the rest. The model answers in round 0.

**Phase 1 done-when:**

- [ ] `file_inspect.file_search` and `project_search` accept `view`
      param (default `summary`)
- [ ] Hits carry `kind` from the lightweight classifier
- [ ] Byte-cap enforced; `truncate_reason` + `suggestions` returned
- [ ] `ai/chat_request.json` tool schema documents both modes; cheat-sheet
      adds: *"Default to `view='summary'`. Only ask for `full` when you
      already know the file and need surrounding code."*
- [ ] Tests: re-run the reference trace; round-0 payload < 16 KB,
      total turn < 5 s

---

### Phase 2 — Eliminate redundant work inside a round

**Lever:** Round 1 of the reference trace did 3 `get_file_section`
calls whose ranges overlapped (`3000–3200`, `3100–3200`, `3200–3300`).
Apollo paid the read+TOON-encode cost three times for material it
already returned once.

#### 2.1 Per-request range cache for `get_file_section`

Inside a single chat turn (keyed by the request's `rid`, see
[`DESIGN.md §8.8`](../DESIGN.md)), keep the last N served
`(path, md5, start, end)` ranges. On a new call:

- If the requested range is **fully contained** in a prior range,
  return the cached slice and add `served_from: "rid:<rid>"` so the
  trace panel makes the dedup visible.
- If it **partially overlaps**, return only the new lines plus a
  pointer to the prior range: `{"prior_range": [3000, 3200], "new_lines": [3201, 3300]}`.

This costs one dict per request and disappears when the SSE stream
closes.

#### 2.2 Range-merge in `batch_file_sections`

`chat/local_tools.batch_file_sections` already accepts multiple
ranges. Add a merge pass:

- Sort ranges within a path.
- Coalesce ranges where `gap < 20 lines` (the same gap rule used in
  Phase 1.1 grouping).
- Return one merged segment per coalesced cluster, with the original
  request ranges echoed back so the LLM knows which one it asked for.

This is purely server-side; no schema change for the model.

**Phase 2 done-when:**

- [ ] `chat/service.py` threads `rid` into the file-inspect
      dispatcher, instantiates a per-request range cache
- [ ] `batch_file_sections` coalesces near-adjacent ranges
- [ ] Trace panel shows `served_from=rid:…` for cache hits
- [ ] Test: replay a transcript with 3 overlapping `get_file_section`
      calls; only the first hits disk

---

### Phase 3 — Fix first-call cold-cache on `search_graph`

**Lever:** The first `search_graph` of the trace cost **1.975 s**; the
second cost **0.071 s** — a 28× gap. Whatever lazy init runs on first
hit (TF-IDF index build? CBL semantic warm-up? embedding model load?)
should run **before** the user's first question.

#### 3.1 Profile the cold path

Add timing breadcrumbs around each lazy-init in:

- `search/cblite_semantic.py` (Couchbase Lite vector index open)
- `search/semantic.py` (sentence-transformer model load — should
  already be lazy)
- `chat/service.py::_query` lazy `GraphQuery` build (see
  [`DESIGN.md §4.2.2`](../DESIGN.md))

Emit one log line per lazy-init: `lazy.init name=… dt=…`. Grep
`apollo.log` for which one dominates.

#### 3.2 Pre-warm on app start (non-blocking)

After `web.server.create_app()` finishes wiring, kick off a background
thread that:

1. Issues a no-op `search.search("warmup", top=1)` against the active
   store.
2. Builds the lazy `GraphQuery` once.
3. Logs `lazy.warmup elapsed=…`.

Project-switch hooks ([`DESIGN.md §4.2.2`](../DESIGN.md)) already
reload the search instance — the same hook re-fires the warmup. This
is the single project-switch invariant rule (#3) in `DESIGN.md` —
piggyback, don't duplicate.

#### 3.3 Belt-and-braces: a `"warmup": true` ping in `/api/chat`

When the chat UI mounts, it can `POST /api/chat?warmup=true` (no
message, no history). The endpoint runs the same warmup path and
returns immediately. Useful when the user opens a project and
*then* the search index is reloaded — round 0 of their first real
question is no longer cold.

**Phase 3 done-when:**

- [ ] Lazy-init breadcrumbs land in `apollo.log`
- [ ] Background warmup thread runs after `create_app()` and after
      `_swap_to_project_store`
- [ ] `/api/chat?warmup=true` no-op is wired and the UI calls it on
      project open
- [ ] Reference trace re-run: first `search_graph` < 0.3 s

---

### Phase 4 — Net-new high-leverage tools for "where is X declared / used in this file?"

**Lever:** The reference question would be one tool call against a
properly-scoped tool, not a regex grep + 3 file slices.

These tools follow the
[`PLAN_MORE_LOCAL_AI_FUNCTIONS.md`](./PLAN_MORE_LOCAL_AI_FUNCTIONS.md)
constraints: read-only, TOON-friendly uniform-array shape, fits the
3-round budget, paired HTTP endpoint.

#### 4.1 `list_declarations(path, kinds?)`

Returns every top-level declaration in a file: functions, classes,
methods, module/file-level `const | let | var | def | new Map() | new
WeakMap() | new Set() | new WeakSet()`. One row per declaration:

```toon
declarations[N,]{name,kind,line_start,line_end,is_exported,parent}:
  parseTimeCache,map_decl,3225,3225,false,
  normalizeStatementCache,map_decl,3226,3226,false,
  clearCaches,function,3237,3249,true,
  logCacheStats,function,3251,3258,true,
  getOperators,function,3260,3320,true,
```

For the trace's question, *one* call — `list_declarations("en/index.html",
kinds=["map_decl","weakmap_decl","function"])` — returns the exact
answer in < 1 KB. Round 0, done.

Implementation: lean on the existing `parser/` plugins. Python plugin
already produces functions/classes/variables — surface them. For HTML
and JS, lean on the tree-sitter backend
([`parser/treesitter_parser.py`](../../parser/treesitter_parser.py))
to extract `lexical_declaration` / `function_declaration` /
`class_declaration` nodes.

#### 4.2 `find_symbol_usages(path, symbol, kind?)`

For a known symbol in a file, return all line-numbers + 1-line snippets
where it's referenced (decl + reads + writes + calls), classified by
the same `kind` taxonomy from Phase 1.3. No surrounding context.

```toon
usages[N,]{line_no,kind,text}:
  3225,declaration,"const parseTimeCache = new Map();"
  3009,read,"if (parseTimeCache.has(s)) return parseTimeCache.get(s);"
  3015,write,"parseTimeCache.set(s, ms);"
  3239,call,"parseTimeCache.clear();"
```

This is the Phase 1.3 classifier upgraded from "regex pass over
file_search hits" to "AST-aware pass over a single symbol". For
languages with parser-level identifier resolution (Python AST, JS
tree-sitter), it's exact; for fallback `text_parser` files, it's
text-search with the classifier.

#### 4.3 `outline_file(path, depth?)` (cheap quick-look)

Sub-second outline of a file:

- For source files: top-level decl names + line ranges (basically a
  thinned `list_declarations` capped at `depth=1`).
- For markdown/notes: heading tree.
- For HTML: tag tree at `depth=2` (`<head>`, `<body>`, `<script id=…>`,
  …).

This is the file-shaped analogue of `get_directory_tree`. The model
should reach for it on first contact with any unfamiliar file —
cheaper and more accurate than `file_search`-with-context.

**Phase 4 done-when:**

- [ ] Three new tools registered in `ai/chat_request.json` and
      dispatched in `chat/service.py`
- [ ] Matching HTTP endpoints (`GET /api/files/declarations`,
      `GET /api/files/usages`, `GET /api/files/outline`) per
      `PLAN_MORE_LOCAL_AI_FUNCTIONS.md` parity rule
- [ ] System prompt cheat-sheet adds: *"`In file X, where is Y
      defined/used?`        → `find_symbol_usages` (NOT `file_search`)"*
      and *"`What's in file X?`               → `outline_file` /
      `list_declarations` (NOT `file_search` for `def\|class`)"*
- [ ] Reference trace re-run: 1 round, < 5 s, confidence=high

---

## 3. Cross-cutting concerns

### 3.1 TOON shape audit

Every new payload above (`declarations`, `usages`, `outline`,
`matches[]` with `kind`, the Phase 2 cache-hit row) is a uniform-shape
array. They will collapse to header-once TOON exactly as
[`DESIGN.md §8.7`](../DESIGN.md) describes — the byte-savings ratio
should match or beat `search_graph_multi`'s reported 30–50%.

### 3.2 Graceful degradation

Mirror the `git_available: false` pattern from `get_git_context`:

- If the tree-sitter grammar for a file's language isn't installed,
  `list_declarations` falls back to the regex classifier from
  Phase 1.3 and tags `accuracy: "regex"` in the response.
- If `find_symbol_usages` can't resolve a symbol via the parser,
  it returns text-match results with `accuracy: "text"`.

The model should treat `accuracy: "text"` as a hint to verify with
`get_file_section`, the same way it treats `confidence: "med"` today.

### 3.3 Round-budget impact

| Question                                                    | Today  | After this plan |
|-------------------------------------------------------------|--------|-----------------|
| "Where is X cached / declared in this file?"                | 3      | 1               |
| "What's in this file?" (cold first-contact)                 | 2–3    | 1               |
| "Show me the body of function Y in file X"                  | 1–2    | 1               |
| "Where do we use X across the project?"                     | 2–3    | 1–2             |
| "Cold-start: first user question after project open"        | +1.9 s | + < 0.3 s       |

### 3.4 Plan independence

Phase 1 and Phase 2 are **pure server-side** — no schema change, no
prompt change, can ship as bug-fixes without touching the chat agent
contract. Phase 3 is observability + a startup hook. Phase 4 is the
only one that needs new tool registration; ship it last so we can
measure how much the first three already moved the needle.

### 3.5 Interaction with sibling plans

- [`PLAN_MORE_LOCAL_AI_FUNCTIONS.md`](./PLAN_MORE_LOCAL_AI_FUNCTIONS.md)
  — Phase 4 here is the natural successor to its `batch_file_sections`
  / `get_directory_tree` family. The cheat-sheet section in that plan
  should be extended in lock-step.
- [`PLAN_ML_LIBS.md`](./PLAN_ML_LIBS.md) — KeyBERT keyphrases (Phase
  1.4 there) make `find_symbol_usages` smarter on synonyms; not a
  blocker.
- [`docs/DESIGN.md §4.2.2`](../DESIGN.md) project-switch invariants —
  Phase 3 warmup MUST go through `_swap_to_project_store`, not bind
  to a startup-only reference. Same regression class as the rules
  documented there.

---

## 4. Sequencing summary

| Phase | Scope                                              | Risk   | Est. PRs | Depends on             |
|-------|----------------------------------------------------|--------|----------|------------------------|
| 1     | `file_search` / `project_search` summary mode      | low    | 1        | —                      |
| 2     | per-request range cache + range merge              | low    | 1        | —                      |
| 3     | profile + warmup `search_graph` cold path          | low    | 1        | —                      |
| 4     | `list_declarations`, `find_symbol_usages`, `outline_file` | medium | 1   | Phase 1 classifier reuse |

Ship Phase 1 first — single PR, no schema change, biggest measured
impact on the reference trace.

---

## 5. Take-away

The reference trace's bottleneck was never Apollo's tool latency — it
was that **round 0 returned 237 KB to answer a 2 KB question**, which
forced round 1 of follow-up reads, which overlapped each other, which
forced round 2 of formatting. The fix is the same shape as the sibling
plans: **make the tool answer the *resolved* question, not deliver raw
material the LLM has to grind through.**

Test for any future tool addition: *can the model finish the user's
question in round 0 from this payload alone?* If yes, ship it. If not,
the tool is doing too much (return less) or too little (return a more
resolved fact).
