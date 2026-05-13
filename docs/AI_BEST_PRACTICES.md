# AI Best Practices — Tool Catalog & Prompt Design

> Distilled from the Phase 8 §8.13 round-reduction work
> (see [`PLAN_MORE_LOCAL_AI_FUNCTIONS.md`](./work/PLAN_MORE_LOCAL_AI_FUNCTIONS.md)
> and [`BENCHMARK_ROUND_REDUCTION.md`](./work/BENCHMARK_ROUND_REDUCTION.md)).
>
> This document is the **rulebook** for adding new tools, tightening
> the system prompt, or debugging "why is the model still picking the
> wrong tool". Read this BEFORE editing
> [`ai/chat_request.json`](../ai/chat_request.json) or adding a new
> `local_tools.py` helper.

---

## TL;DR — what we learned

| # | Lesson | Cost when ignored |
|---|--------|-------------------|
| 1 | The active prompt/tool file is the one the loader actually opens — verify with logs, not assumptions. | Phase 8 tools added to `chat_request.json` were silently invisible because the loader pointed at `chat_request_v2.json`. **3 wasted iterations.** |
| 2 | Prompt-only tightenings cannot rescue a missing tool. If the LLM is choosing tool A over tool B, **first check that tool B is actually in the request payload.** | Repeated §8.13 trace failures despite "FORBIDDEN" rules in the system prompt. |
| 3 | Removing tools beats persuading models. When you can't get the model to STOP picking a bad tool, strip it from the catalog for that question shape. | Context-aware tool filter (`_select_tools` in `chat/service.py`) finally killed kitchen-sink-regex behavior. |
| 4 | Batch endpoints win rounds, not just bytes. A single `symbols=[a,b,c]` call costs less than three `symbol=a` calls because rounds dominate wall time. | One trace fanned out 8 sequential `find_symbol_usages` calls in round 1; collapsing to 1 batch call saves ~2.5 s and 1 round. |
| 5 | Honor the cheat-sheet promises in the implementation. "HTML: tag tree (head/body/script)" in the description must work, or the model wastes a round elsewhere. | `outline_file` returned `accuracy: "none"` for HTML; model fell back to `get_function_source` (which can't parse HTML) and burned a round. |
| 6 | Anti-pattern lists in the prompt are worth more than positive cheat-sheet lines. Models trained on noisy data benefit from explicit "DO NOT" rules. | Added an "Anti-patterns" block after the cheat-sheet to stop list_declarations-after-outline_file, overlapping batch+single section reads, etc. |
| 7 | Backwards-compatible parameter expansion beats new tool names. Keep `symbol` working AND add `symbols`. The model picks up the new shape from a description rewrite without a schema break. | Avoided a `find_symbol_usages_batch` tool that would have inflated the catalog and confused the cheat-sheet. |

---

## The benchmark loop

Every prompt or tool change MUST be measured against a recorded BEFORE
trace. The reference question lives in
[`BENCHMARK_ROUND_REDUCTION.md`](./work/BENCHMARK_ROUND_REDUCTION.md).

### Metrics that matter

```diagram
╭──────────────────────────╮     ╭──────────────────────╮
│ user types question      │────▶│ trace recorded       │
╰──────────────────────────╯     ╰────────────┬─────────╯
                                              │
                              ╭───────────────┴────────────────╮
                              ▼               ▼                ▼
                        ╭──────────╮   ╭────────────╮   ╭──────────╮
                        │ rounds   │   │ wall time  │   │ confidence │
                        │ (≤3 ok)  │   │ (<5 s ok)  │   │  + refs    │
                        ╰──────────╯   ╰────────────╯   ╰──────────╯
```

| Metric | Target | Why it matters |
|---|---|---|
| Rounds | 1 | Each round = 1 model latency cycle; latency dominates wall time. |
| Wall time | < 5 s | User-perceptible response budget. |
| `return_result.node_refs` count | ≥ 5 | Drives clickable chips in the UI. |
| Confidence | `high` | Avoids "—" in the trace strip. |
| `file_search` / `project_search` calls | 0 (file-named questions) | These are last-resort tools; firing them is a routing miss. |
| Sequential same-tool calls | 0 | Always means a missing batch shape or a model habit to break. |

### Trace shape evolution from Phase 8 §8.13 work

| Iteration | Rounds | Wall | Confidence | Notes |
|---|---|---|---|---|
| BEFORE baseline | 3 | 15.6 s | high | `project_search` with `cache\|Cache\|...` regex |
| §8.13 prompt-only tightening | 3 | 16.5 s | (no result) | Same shape — proved prompt edits weren't reaching a missing-from-payload toolset |
| Loader fix + context-aware filter | 3 | 16.1 s | high | All three Phase 8 tools finally invoked; no grep |
| Batch `find_symbol_usages` | 4 | 19.6 s | — | Batch picked up; new bottleneck = HTML outline gap + `get_function_source` errors on HTML |
| HTML outline regex fallback | 4 | 17.8 s | high | `outline_file` returns 216 rows for the 1.4 MB HTML file; `get_function_source` errors gone |
| Anti-patterns added | TBD | TBD | TBD | Targets `list_declarations` after `outline_file`, overlapping reads, etc. |

---

## Five rules for adding a new local tool

1. **Mirror the chat tool with an HTTP route.** Every entry in
   [`ai/chat_request.json`](../ai/chat_request.json) `tools[]` should
   have a matching `/api/...` route in
   [`web/server.py`](../web/server.py) that calls the same function in
   [`chat/local_tools.py`](../chat/local_tools.py). The HTTP route is
   the integration test; the chat tool is the consumer.

2. **Return uniform-shape rows.** Every list-returning tool must emit
   rows with the same keys (TOON's CSV-row encoding is ~3× smaller for
   uniform shapes than for ragged ones). Document this with
   `# Uniform-shape array — TOON-friendly` in the OpenAPI schema.

3. **Cap payload size at the tool, not at the schema.** Hard caps
   (200 matches, 200 KB snippet bytes, 20 batch symbols, 50 JS decls
   per `<script>`) belong in the Python helper. The schema only
   advertises the cap so the LLM picks shapes that fit.

4. **Surface every fallback explicitly.** When the AST path can't
   answer, return a regex/heuristic answer with
   `accuracy: "regex" | "graph_only" | "none"`. Never silently swap
   methods — the LLM relies on the accuracy field to pick follow-up
   tools.

5. **Backwards-compatible parameter expansion over new tool names.**
   When you need a batch shape, add `symbols` alongside `symbol` (with
   the description steering the model toward `symbols` for ≥2). A new
   tool name doubles cheat-sheet pressure and forces re-routing.

---

## Five rules for editing the system prompt

1. **Anti-patterns beat cheat-sheets for non-reasoning models.** The
   `xai/grok-4-1-fast-non-reasoning` benchmark target ignored
   "use outline_file" cheat-sheet lines until we added an explicit
   "❌ DO NOT call list_declarations after outline_file" rule.

2. **First sentence of every tool description sets routing.** Models
   anchor on the verb. `"Grep within a single file"` looks like a
   match for "find caching"; `"DO NOT call this as your first tool
   when the user names a single file"` does not. Rewrite the FIRST
   sentence when you want to demote a tool.

3. **Catalog order matters.** Phase 8 §8.13 reordered the `tools[]`
   array so `outline_file` / `list_declarations` /
   `find_symbol_usages` came first. Models that scan top-down see them
   before `file_search`.

4. **`FORBIDDEN` is rank-#5 in tool selection.** Use it only when
   ranks #1–4 (catalog shape, first-sentence verb, parameter-shape
   match, cheat-sheet entry) have been exhausted. A `FORBIDDEN` rule
   cannot rescue a tool that's missing from the request payload.

5. **Snapshot before editing.** The convention in
   [`ai/chat_request.json`](../ai/chat_request.json):
   `chat_request.json` is ACTIVE; `chat_request_v{N}.json` are
   rollback snapshots. Always copy to `chat_request_v{next}.json`
   BEFORE editing so each tuning round is reversible.

---

## Context-aware tool filtering

Some questions reliably misroute regardless of prompt edits. For those,
filter the catalog **per request** rather than trying to persuade the
model.

The first concrete instance lives in
[`chat/service.py`](../chat/service.py):

```python
def _is_file_named_question(message: str) -> bool:
    """True when the user message names a specific file."""
    return _FILE_NAMED_RE.search(message) is not None

def _select_tools(message: str) -> list[dict]:
    """Strip file_search / project_search for file-named questions."""
    if not _is_file_named_question(message):
        return TOOLS
    return [t for t in TOOLS
            if (t.get("function") or {}).get("name") not in _GREP_TOOL_NAMES]
```

**When to add a new filter:**

- The trace shows the same wrong-tool selection for the same
  question shape across ≥2 different models.
- All four prompt-shaping ranks (catalog order, first-sentence verb,
  parameter shape, cheat-sheet) have been tried.
- The HTTP endpoints for the filtered tools should remain available
  for non-LLM consumers (mark them `deprecated: true` in OpenAPI but
  do not delete the routes).

**When NOT to add a filter:**

- The misroute happens once and the model self-corrects on retry.
- The "wrong" tool is actually right in some other context (filtering
  would create a regression for a different question shape).
- A simpler description rewrite would do (try that first).

---

## Anti-pattern catalog

These are the model behaviors we've seen waste rounds. Each one is
documented in the system-prompt "Anti-patterns" block. New
anti-patterns get appended here AND to
[`ai/chat_request.json`](../ai/chat_request.json) at the same time.

| ❌ Anti-pattern | Round cost | Recorded in trace |
|----------------|-----------|-------------------|
| `list_declarations(path)` after `outline_file(path)` returned non-empty | 1 round + 0.5 s | Round 1 of the post-HTML-outline trace |
| `get_directory_tree(root: "<project root>")` returning 0 entries | 0.3 s | Round 0 of multiple traces |
| `find_symbol_usages` with generic English words after outline gave specific names | 0.3 s | Round 1 of the post-batch trace |
| `batch_file_sections` + `get_file_section` for OVERLAPPING ranges | 0.3 s + duplicate bytes | Round 2 of the post-HTML-outline trace |
| `search_graph` after `search_graph_multi` covered the same space | 0.3 s | Round 1 of multiple traces |
| Kitchen-sink regex `cache\|Cache\|store\|memo\|memory` as first call | full round | BEFORE baseline (now blocked by tool filter + system prompt) |
| `get_function_source` against an HTML file (Python AST → `invalid syntax`) | 1 round + 3 errors | Pre-HTML-outline trace (now blocked by HTML regex fallback) |

---

## Debugging checklist when a trace looks wrong

```diagram
╭───────────────────────────────────────╮
│ trace shows tool A picked over tool B │
╰────────────────┬──────────────────────╯
                 ▼
   ╭──────────────────────────────╮
   │ 1. Is tool B in the loaded   │      yes  ╭───────────────────────╮
   │    request payload?          ├──────────▶│ 2. Does tool B's first│
   │    Check the loader:         │           │    sentence match the │
   │    `_REQUEST_TEMPLATE_PATH`  │           │    user's verb?       │
   ╰──────────────┬───────────────╯           ╰──────────┬────────────╯
                  │ no                                   │ yes
                  ▼                                      ▼
        ╭─────────────────────╮                ╭─────────────────────╮
        │ FIX THE LOADER /    │                │ 3. Is tool B early  │
        │ COPY TOOL INTO      │                │    in the tools[]   │
        │ ACTIVE FILE         │                │    array (before A)?│
        ╰─────────────────────╯                ╰──────────┬──────────┘
                                                          │ yes
                                                          ▼
                                              ╭─────────────────────╮
                                              │ 4. Does tool B's    │
                                              │    parameter shape  │
                                              │    match the user's │
                                              │    inputs better    │
                                              │    than tool A's?   │
                                              ╰──────────┬──────────┘
                                                         │ yes
                                                         ▼
                                              ╭────────────────────────╮
                                              │ 5. Is tool A worth    │
                                              │    keeping at all for │
                                              │    this question      │
                                              │    shape?             │
                                              │    If no → remove it  │
                                              │    via _select_tools. │
                                              │    If yes → add an    │
                                              │    Anti-pattern line. │
                                              ╰────────────────────────╯
```

---

## Where to look in the codebase

| What | File |
|------|------|
| System prompt + tool catalog (ACTIVE) | [`ai/chat_request.json`](../ai/chat_request.json) |
| Snapshots | `ai/chat_request_v{1,2,3}.json` |
| Tool dispatcher | [`chat/service.py::_exec_tool`](../chat/service.py) |
| Context-aware tool filter | [`chat/service.py::_select_tools`](../chat/service.py) |
| Tool template loader | [`chat/service.py::_load_request_template`](../chat/service.py) |
| Local-tool implementations | [`chat/local_tools.py`](../chat/local_tools.py) |
| HTTP routes that mirror chat tools | [`web/server.py`](../web/server.py) |
| OpenAPI definitions | [`docs/openapi.yaml`](./openapi.yaml) |
| Markdown reference (with HTTP examples) | [`docs/API.md`](./API.md) |
| Plan / rationale doc | [`docs/work/PLAN_MORE_LOCAL_AI_FUNCTIONS.md`](./work/PLAN_MORE_LOCAL_AI_FUNCTIONS.md) |
| Benchmark traces (BEFORE/AFTER) | [`docs/work/BENCHMARK_ROUND_REDUCTION.md`](./work/BENCHMARK_ROUND_REDUCTION.md) |

---

## Process: recording a benchmark trace

1. Open the test project in the Apollo web UI (the §8.13 reference is
   `cb_completed_request`).
2. Wait for the file tree + search index to finish loading.
3. Open a fresh chat with no history. Paste the test question
   verbatim from
   [`BENCHMARK_ROUND_REDUCTION.md`](./work/BENCHMARK_ROUND_REDUCTION.md).
4. Click the **▸ Trace** strip under the assistant's response and copy
   every line starting with `➤ / ↻ / 🔧 / ↩ / ✓ / ●`.
5. Paste into the matching `<paste trace here>` block in the benchmark
   doc and fill in the metrics table.
6. Cross-check against `apollo.log` by grepping the `id=…` from the
   trace's first line:
   ```bash
   grep 'id=<rid>' .apollo/logs/apollo.log
   ```
7. Note provider/model from the model selector — comparisons across
   models are invalid.

A phase is shipped when:

- [ ] Trace is pasted into the matching AFTER section.
- [ ] Final answer is factually equivalent to BEFORE.
- [ ] Total wall time monotonically decreases (or a clear reason is
      documented for why it doesn't).
- [ ] No regression in confidence (`high → high` or better).
- [ ] No new `[ERROR]` step events in the trace.
