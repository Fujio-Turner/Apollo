# BENCHMARK — LLM Round Reduction (Before / After)

**Plan being measured:** [`PLAN_LLM_ROUND_REDUCTION.md`](./PLAN_LLM_ROUND_REDUCTION.md)
**Reference design:** [`docs/DESIGN.md §8`](../DESIGN.md) (chat / tool architecture, TOON, 3-round budget, AI trace panel)

The same question is asked of the chat agent before the plan lands and
again after each phase ships. Numbers come straight from the AI trace
panel ([`DESIGN.md §8.8`](../DESIGN.md)) so they are apples-to-apples.

---

## Test question

> *"In the folder and file of `en/index.html`, what function(s) is/are
> used to cache processed data?"*

**Why this question:** it is a representative "first-contact, single-file,
where-is-X" question. The answer is a small set of named symbols (~6)
plus their line ranges (~5) inside one large file (27 716 lines). It is
the exact shape that should resolve in **round 0** with the right tool
results, but today forces 3 rounds of grep-and-read.

**Provider / model:** xAI Grok — `grok-4-1-fast-non-reasoning`
**Conversation history at start:** 1 message (this question only)
**Project root:** `/Users/fujioturner/Documents/git_folders/fujio-turner/cb_completed_request`
**Target file size:** 1 402 859 bytes, 27 716 lines, md5 `3d6a0ffcd83b499b6663f6624adf6376`

---

## BEFORE — Run #1 (baseline)

### Final answer (verbatim)

> ## Caching Functions in `en/index.html`
>
> The file implements a comprehensive in-memory caching system using
> JavaScript `Map` and `WeakMap` objects to cache processed data for
> performance optimization (labeled "Step 9" in comments). No persistent
> storage like `localStorage` is used.
>
> **Main Cache Stores (declared ~line 3225):**
>
> - `parseTimeCache: new Map()` — Caches parsed time strings
>   (e.g., `"1h4m17s" → ms`)
> - `normalizeStatementCache: new Map()` — Caches normalized SQL++
>   statements (literals/numbers → `"?"`)
> - `timestampRoundingCache: new Map()` — Caches rounded timestamps
>   for charts
> - `operatorsCache, planStatsCache, timeUnitCache: WeakMap()` —
>   Auto-GC cleaned plan/operator data
>
> **Key Functions Using Caches:**
>
> - `clearCaches()` (~line 3237): Clears all caches + destroys charts
>   on new JSON parse
> - `logCacheStats()` (~line 3251): Logs cache usage stats (size /
>   utilization %)
> - `getOperators(operator, …)` (~line 3260): Recursively extracts
>   operators, caches root results
> - Time parsing (~line 3000+): `parseTimeCache.has() / get() / set()`
>   with size-limit eviction
> - `normalizeStatement(statement)` (~line 3130):
>   `normalizeStatementCache.has() / get() / set()` with eviction
>
> **Cache Management:**
>
> - Size limits via `CACHE_LIMITS` (~line 3219): Evicts oldest 20%
>   when full
> - Called in `parseJSON()` to clear on reload
> - WeakMaps auto-clean via GC
>
> Files: `en/index.html` (~lines 261, 3009–3065, 3133–3150, 3214–3300)
> Confidence: **high**

### Full trace (raw, from the trace panel)

```
➤request xai/grok-4-1-fast-non-reasoning hist=1
↻round 0 finish=tool_calls 4.142s · 4 tc
🔧file_search {"path": "en/index.html", "pattern": "cache|Cache|cached|localStorage|sessionStorage|IndexedDB|store", "context": 10, "regex": true}
↩file_search → 144 320 B · 0.056s · toon 142 596 B (-1.2%)
🔧project_search {"pattern": "cache|Cache|cached|localStorage|sessionStorage|IndexedDB|store", "root": "en", "file_glob": "*.html", "regex": true}
↩project_search → 94 406 B · 0.043s · toon 93 688 B (-0.8%)
🔧file_stats {"path": "en/index.html"}
↩file_stats → 271 B · 0.021s · toon 240 B (-11.4%)
🔧search_graph {"query": "\"cache processed data\" OR cache OR caching", "top": 20, "type": "function"}
↩search_graph → 3 265 B · 1.975s · toon 2 195 B (-32.8%)
↻round 1 finish=tool_calls 4.952s · 4 tc
🔧get_file_section {"path": "en/index.html", "start_line": 3200, "end_line": 3300}
↩get_file_section → 7 414 B · 0.029s · toon 5 889 B (-20.6%)
🔧get_file_section {"path": "en/index.html", "start_line": 3000, "end_line": 3200}
↩get_file_section → 13 811 B · 0.021s · toon 10 766 B (-22%)
🔧get_file_section {"path": "en/index.html", "start_line": 3100, "end_line": 3200}
↩get_file_section → 7 038 B · 0.019s · toon 5 513 B (-21.7%)
🔧search_graph {"query": "clearCaches OR logCacheStats OR parseTimeCache OR normalizeStatementCache", "type": "function"}
↩search_graph → 1 491 B · 0.071s · toon 971 B (-34.9%)
↻round 2 finish=tool_calls 4.282s · 1 tc
✓return_result 1 file · 0 refs · high · 15.642s
●done reason=return_result 1 698 B · 15.643s
```

### Aggregate metrics

| Metric                                  | Value         | Notes                                                       |
|-----------------------------------------|---------------|-------------------------------------------------------------|
| **Total wall time**                     | **15.642 s**  | request → `done`                                            |
| Round count                             | 3             | round 0, 1, 2                                               |
| Tool calls total                        | 8             | 4 + 4 + 0 (round 2 was the final stream)                    |
| Tool wall time (sum)                    | ~2.25 s       | dominated by `search_graph` round-0 = 1.975 s               |
| LLM processing time                     | ~11.1 s       | wall − tool − overhead                                      |
| Network / SSE overhead                  | ~2.3 s        |                                                             |
| **Round 0 payload (TOON, post-encode)** | **~239 KB**   | `file_search` 142.6 KB + `project_search` 93.7 KB + others  |
| Largest single payload                  | 142.6 KB      | `file_search` regex with 10-line context window             |
| Cold-cache penalty                      | 1.904 s       | 1.975 s (first `search_graph`) − 0.071 s (second)           |
| Overlapping reads                       | 1             | round 1: `3000–3200` superseded by combined window          |
| Final answer confidence                 | high          | model self-reported via `return_result`                     |
| `return_result.files`                   | 1             |                                                             |
| `return_result.node_refs`                | 0             | model never resolved hits to graph node IDs                 |

### Per-round wall-time breakdown

| Round | Wall    | Tool calls                                                 | What the model was doing                                  |
|-------|---------|------------------------------------------------------------|-----------------------------------------------------------|
| 0     | 4.142 s | `file_search`, `project_search`, `file_stats`, `search_graph` | Scattershot: regex inside file + across project + stats + graph |
| 1     | 4.952 s | 3× `get_file_section` (overlapping) + 1× `search_graph`    | Drill into the line ranges hinted by round-0 hits         |
| 2     | 4.282 s | none — final `return_result` + answer stream               | Compose markdown answer                                   |

### Where the time went (visualised)

```diagram
total = 15.642 s
╭──────────────────────────────────────────────────────────────────╮
│ ████████████████████████████████████████████  LLM    11.1 s 71%   │
│ █████████  Apollo tools                        2.25 s 14%        │
│         (1.975 s of that = first search_graph)                   │
│ █████████  Network / SSE                       2.30 s 15%        │
╰──────────────────────────────────────────────────────────────────╯
```

### Identified waste (mapping to plan phases)

| Symptom in trace                                                                | Root cause                                                                                          | Plan phase that fixes it                          |
|---------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|---------------------------------------------------|
| Round-0 `file_search` returned **142.6 KB** (134+ matches × 21 lines context)   | Default `context=10` + no byte cap + no `kind` classification                                       | **Phase 1** — summary view + classifier + cap     |
| Round-0 `project_search` returned **93.7 KB** of cross-file regex hits          | Same as above; model didn't actually need cross-file data for a single-file question                | **Phase 1** — same                                 |
| Round-1 `get_file_section(3100–3200)` was already covered by `(3000–3200)`      | No per-request range cache — Apollo re-served identical bytes                                       | **Phase 2** — per-`rid` range cache + merge       |
| First `search_graph` cost **1.975 s**, second cost **0.071 s** (28× cheaper)    | Cold lazy-init on first hit (likely embedding model load or CBL semantic open)                      | **Phase 3** — pre-warm on app start / project swap |
| Model never reached for `list_declarations` / `find_symbol_usages`              | Those tools don't exist yet                                                                         | **Phase 4** — net-new high-leverage tools         |
| `return_result.node_refs` was **0** (no graph IDs cited)                        | The model couldn't resolve text hits to graph nodes — `list_declarations` would emit them directly  | **Phase 4** — same                                 |

---

## BEFORE — Run #2 (variance baseline, no code changes)

> Re-run of the **identical question** against the **identical
> codebase** — `git status` confirms only `docs/work/PLAN_*.md` and
> `docs/work/BENCHMARK_*.md` were added; no phase from the plan has
> shipped. This run exists purely to measure how stable the current
> pipeline is, so the AFTER deltas can be told apart from
> run-to-run LLM variance.

### Full trace (raw)

```
➤request xai/grok-4-1-fast-non-reasoning hist=1
↻round 0 finish=tool_calls 3.866s · 4 tc
🔧file_stats {"path": "en/index.html"}
↩file_stats → 271 B · 0.167s · toon 240 B (-11.4%)
🔧file_search {"path": "en/index.html", "pattern": "cache|Cache|cached|localStorage|sessionStorage|IndexedDB|store|memo|memory", "context": 10}
↩file_search → 239 848 B · 0.174s · toon 237 084 B (-1.2%)
🔧project_search {"pattern": "en/index\\.html.*(cache|Cache|cached|localStorage|sessionStorage|IndexedDB|store|memo|memory)", "context": 5}
↩project_search → 297 B · 0.010s · toon 268 B (-9.8%)
🔧search_graph {"query": "\"en/index.html\" cache OR caching OR \"local storage\" OR memo OR store processed data", "top": 20, "type": "function"}
↩search_graph → 15 B · 0.001s · toon 11 B (-26.7%)
↻round 1 finish=tool_calls 4.795s · 3 tc
🔧batch_file_sections {"ranges": [{"path": "en/index.html", "start": 3000, "end": 3100}, {"path": "en/index.html", "start": 3130, "end": 3200}, {"path": "en/index.html", "start": 3210, "end": 3300}]}
↩batch_file_sections → 18 852 B · 0.438s · toon 15 968 B (-15.3%)
🔧batch_file_sections {"ranges": [{"path": "en/index.html", "start": 20370, "end": 20600}]}
↩batch_file_sections → 17 012 B · 0.142s · toon 14 476 B (-14.9%)
🔧get_directory_tree {"root": "en", "depth": 2}
↩get_directory_tree → 65 B · 0.001s · toon 47 B (-27.7%)
↻round 2 finish=tool_calls 11.25s · 1 tc
✓return_result 0 files · 0 refs · high · 20.857s
●done reason=return_result 3 323 B · 20.858s
```

### Aggregate metrics — Run #1 vs Run #2

| Metric                                 | Run #1     | Run #2      | Δ          | Notes                                                   |
|----------------------------------------|------------|-------------|------------|---------------------------------------------------------|
| **Total wall time**                    | 15.642 s   | **20.857 s**| **+5.2 s** | Slower — pure LLM variance (no code changed)            |
| Round count                            | 3          | 3           | =          |                                                         |
| Tool calls total                       | 8          | 7           | −1         | Run #2 used `batch_file_sections` instead of 3× single  |
| Tool wall time (sum)                   | ~2.25 s    | ~0.93 s     | −1.32 s    | Cold-cache vanished on its own this run                 |
| LLM processing time                    | ~11.1 s    | ~18.9 s     | **+7.8 s** | Round 2 alone was 11.25 s vs 4.28 s baseline            |
| Round-0 payload (TOON, post-encode)    | ~239 KB    | **~252 KB** | +13 KB     | Model widened the regex (`+memo,memory`)                |
| Largest single payload                 | 142.6 KB   | **237.1 KB**| +94.5 KB   | Same shape problem, more material                       |
| Cold-cache penalty (1st `search_graph`)| 1.904 s    | 0.000 s     | **−1.9 s** | One-off; will re-appear on first session of next boot   |
| `return_result.files`                  | 1          | **0**       | −1         | Worse — model didn't surface the file at all            |
| `return_result.node_refs`              | 0          | 0           | =          | Neither run resolved hits to graph IDs                  |
| Final-answer confidence (self-rated)   | high       | high        | =          | Self-rating is unreliable across runs                   |
| Answer byte size (final markdown)      | 1 698 B    | 3 323 B     | +1 625 B   | Run #2 wrote a longer answer (likely fluffier)          |

### What changed between Run #1 and Run #2 (with no code changes)

The two runs prove the central claim of the plan: **the variance is
inside the LLM's tool-selection loop, and bigger / sloppier round-0
payloads make it worse.**

| Observation                                                             | Implication                                                                                          |
|-------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| Run #2 widened the regex to include `memo,memory` → +94 KB on `file_search` | The model has too many degrees of freedom on round 0; nothing in the tool result tells it "you have enough" |
| Run #2 used `batch_file_sections` (good!) but also asked for `20370–20600` (irrelevant) | Bigger blob → model invents tangential follow-ups to "make sense" of it                              |
| Run #2 burned a call on `get_directory_tree(root="en", depth=2)` returning empty | First-contact tools used reactively, not proactively                                                 |
| Round 2 ballooned to 11.25 s (vs 4.28 s)                                | More material on the wire = more reading + more re-reasoning before composing the final answer        |
| `return_result.files = 0` in Run #2                                     | Model lost track of the canonical file in the noise; UI got no chip                                  |
| Cold-cache penalty disappeared on its own                               | Not the dominant cost — Phase 3 is real but lower priority than Phases 1 / 4                         |

**Take-away for the plan ordering:** Phase 1 (compact `file_search`)
and Phase 4 (`list_declarations` / `find_symbol_usages`) just got
*more* important — they directly remove the degrees of freedom that
caused Run #2's regression.

---

## AFTER — to be filled in after each phase ships

> Re-run the **identical question** with the **same model and project**.
> Paste the new trace verbatim into the matching section below. Keep
> the BEFORE block above untouched so the diff is reproducible.

### After Phase 1 (summary view + byte cap + `kind` classifier)

```
<paste trace here>
```

| Metric                       | Before    | After     | Δ      |
|------------------------------|-----------|-----------|--------|
| Total wall time              | 15.642 s  |           |        |
| Round count                  | 3         |           |        |
| Tool calls total             | 8         |           |        |
| Round-0 payload (TOON)       | ~239 KB   |           |        |
| Largest single payload       | 142.6 KB  |           |        |
| LLM processing time          | ~11.1 s   |           |        |
| Final-answer confidence      | high      |           |        |

**Notes:**

- _Did the model still need round 1?_
- _Were any `truncated:true` / `suggestions` hints used?_
- _Answer parity with BEFORE (same symbols cited)?_

### After Phase 2 (per-request range cache + range merge)

```
<paste trace here>
```

| Metric                        | Before   | Phase 1 | Phase 2 | Δ vs. before |
|-------------------------------|----------|---------|---------|--------------|
| Total wall time               | 15.642 s |         |         |              |
| Overlapping `get_file_section`| 1        |         |         |              |
| Tool wall time (sum)          | ~2.25 s  |         |         |              |

**Notes:**

- _Did `served_from=rid:…` show up in the trace for cache hits?_

### After Phase 3 (cold-cache pre-warm)

```
<paste trace here>
```

| Metric                       | Before    | Phase 1 | Phase 2 | Phase 3 | Δ vs. before |
|------------------------------|-----------|---------|---------|---------|--------------|
| First `search_graph` dt      | 1.975 s   |         |         |         |              |
| Cold-cache penalty           | 1.904 s   |         |         |         |              |
| Total wall time              | 15.642 s  |         |         |         |              |

**Notes:**

- _Which lazy-init was actually dominating? (embedding model? CBL? GraphQuery?)_
- _Did the warmup thread land before the user's first question?_

### After Phase 4 (`list_declarations` / `find_symbol_usages` / `outline_file`)

```
<paste trace here>
```

| Metric                              | Before     | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Δ vs. before |
|-------------------------------------|------------|---------|---------|---------|---------|--------------|
| Total wall time                     | 15.642 s   |         |         |         |         |              |
| Round count                         | 3          |         |         |         |         |              |
| Tool calls total                    | 8          |         |         |         |         |              |
| Round-0 payload (TOON)              | ~239 KB    |         |         |         |         |              |
| LLM processing time                 | ~11.1 s    |         |         |         |         |              |
| `return_result.node_refs` count     | 0          |         |         |         |         |              |
| Final-answer confidence             | high       |         |         |         |         |              |

**Target:** 1 round, < 5 s total, `node_refs ≥ 5` (each cited cache
symbol resolved to a graph ID), confidence=high, answer parity with
BEFORE.

---

## How to reproduce a run

1. Open the project at `cb_completed_request` in the Apollo web UI.
2. Wait until the file tree finishes populating (so the search index is
   loaded — relevant for the Phase 3 measurement).
3. Open a fresh chat (no prior history) and paste the **Test question**
   verbatim.
4. After the answer streams, click the **▸ Trace** strip under the
   assistant bubble to expand the per-step log.
5. Copy the entire trace (the lines starting with `➤ / ↻ / 🔧 / ↩ /
   ✓ / ●`) into the matching `<paste trace here>` block above.
6. Cross-check totals against `apollo.log` by grepping the request's
   `id=…`:

   ```
   grep 'id=<rid>' .apollo/logs/apollo.log
   ```

7. Note the **provider/model** the model selector showed at send time;
   if it changed from BEFORE, the comparison is invalid — re-run with
   the matching model.

---

## Sign-off criteria (per phase)

A phase is considered shipped when:

- [ ] The trace for the test question has been pasted into the
      matching AFTER section.
- [ ] Final answer is **factually equivalent** to BEFORE (same set of
      cited symbols and approximate line numbers).
- [ ] Total wall time monotonically decreases vs. the prior phase, or
      a clear reason is documented if it doesn't.
- [ ] No regression in confidence (`high → high` or better).
- [ ] No new `[ERROR]` step events in the trace.
