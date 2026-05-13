# PLAN — Python ML Libraries for Indexing, Query & UI

**Owner:** indexing / search / chat / web
**Status:** Not started — design + sequencing locked
**Sibling plan:** [`PLAN_MORE_LOCAL_AI_FUNCTIONS.md`](./PLAN_MORE_LOCAL_AI_FUNCTIONS.md)
**Reference:** [`docs/DESIGN.md §4.4`](../DESIGN.md) (embedding generation),
[`§7.4`](../DESIGN.md) (Idea Cloud strength metric), [`§9`](../DESIGN.md)
(structured indexing)

---

## 0. Goal

Apollo already has the two ingredients ML loves: **node embeddings** and a
**real graph**. This plan adds a small, opinionated set of Python ML
libraries that turn those existing artefacts into:

- **Better layout** — semantically related code clusters visibly in the
  graph, instead of arbitrary spring positions.
- **Better signal** — node centrality, community membership, keyphrases
  and outlier scores stored on the node payload at index time.
- **New chat tools** — `get_cluster`, `get_community`,
  `get_node_importance`, `find_outliers`, `find_dead_code`,
  `search_graph_by_keyphrase`, `get_topics`, `get_complexity_history`,
  `find_co_changed_files`.

All work happens **at index time** and is cached on the node payload.
Query-time calls are plain reads. **No new background services**, no
GPU dependencies, no online inference per chat turn.

All new tools follow the constraints from
[`PLAN_MORE_LOCAL_AI_FUNCTIONS.md`](./PLAN_MORE_LOCAL_AI_FUNCTIONS.md) §0:
read-only, TOON-friendly uniform-array shapes, hard caps, fits the
3-round chat budget.

---

## 0.1 Why this beats brute-force `grep` (and current heuristics)

The same framing as the sibling plan: `grep` finds **strings**; the
graph already knows **what each string means**. ML extends that one
step further — it knows **what each node is *about* and how it sits
relative to everything else** without anyone having to grep for it.

Concretely, today the model has no good answer for:

| Question                                        | Today's brute-force answer                                                                 |
|-------------------------------------------------|--------------------------------------------------------------------------------------------|
| "What modules does this codebase actually have?" | `project_search` for likely keywords, hope folder names match meaning.                    |
| "Which functions matter most?"                   | Sum of in+out degree (current `strength`). Equates a 1-edge import with a 50-edge call hub.|
| "Find code that does rate-limiting."             | `grep -r "rate.*limit"`, miss `throttle`/`bucket`/`quota`/`cool[_ ]?down`.                 |
| "What's weird in this repo?"                     | No tool. The model can only flag what the user already suspects.                           |
| "Is this dead code?"                             | `grep` for the name; misses dynamic dispatch, gives false negatives on truly-unused code.  |
| "Which files are coupled but not via imports?"   | Impossible without git history + co-change analysis.                                       |

Each ML library below answers one of those questions in **one call**,
because the answer is precomputed and stored on the node.

### End-to-end worked example — codebase first-contact

> User (new to the repo): *"What does this project do, what are the
> main parts, and what should I look at first?"*

**Without these libs (today, ~5 rounds, low confidence):**

1. `get_directory_tree` → folder shape, model guesses purpose from names.
2. `get_wordcloud(strong)` → top names, but every project's top names
   are `__init__`, `get`, `name`, `data`. Noise.
3. `get_stats` → totals, no semantic grouping.
4. `project_search` for `README` keywords.
5. `return_result(confidence=med)` — "looks like it's about X based
   on filenames."

**With Phase 1 of this plan (~1 round, high confidence):**

1. *Parallel in one round:* `get_topics()`
   + `get_node_importance(top=10)`
   + `get_clusters()`.
2. `return_result(confidence=high)` — "5 clusters: **Email Sending**
   (mailer.py + 3 helpers), **Auth** (auth/ + middleware), **Indexing
   pipeline** (parser/ + graph/), **Web UI** (web/static + apollo/api),
   **Chat** (chat/ + ai/). Highest-PageRank entry points: `main.py`,
   `apollo.api.server.app`, `chat.service.chat_stream`."

The win is the same as the sibling plan: **fewer rounds, higher
confidence, no grep noise**, and answers to questions that grep simply
cannot answer correctly at all.

---

## 1. What's already in Apollo (do NOT re-implement)

Cross-checked against [`search/semantic.py`](../../search/semantic.py),
[`graph/builder.py`](../../graph/builder.py), and the existing
`/api/wordcloud` strength metric:

| Capability                                   | Lives in                                          |
|----------------------------------------------|---------------------------------------------------|
| Sentence-transformer embeddings (384-d)      | `search/semantic.py`                              |
| Brute-force cosine similarity                | `search/semantic.py`                              |
| Couchbase Lite vector index (Phase 5)        | `search/cblite_semantic.py`                       |
| Sum-of-degree "strength" centrality          | `/api/wordcloud` aggregation                      |
| Per-function `complexity`, `loc`, `signature_hash` | `parser/python3` → builder writes node payload |
| `is_test`, `tests` edges                     | `parser/python3` → builder                        |
| File-level + function-level MD5 hashing      | `graph/builder.py`, `graph/incremental.py`        |

Anything below is net-new.

---

## 2. Phasing — 5 batches, ordered by ROI per shared dependency

**Dep stack reuse is the sequencing rule.** Phase 1 pulls in
`umap-learn`, `hdbscan`, `scikit-learn` and `keybert` once and reuses
them across four features. Later phases add one or two libs each.

### Phase 1 — Embeddings → Layout, Clusters, Centrality, Keyphrases  ☑ ship together

**Shared deps:** `umap-learn`, `hdbscan`, `scikit-learn`, `keybert`,
plus the existing `sentence-transformers`. Single `pip install`,
single index-time pass.

#### 1.1 UMAP — semantic graph layout

**Question it answers:** *"Where, visually, do related parts of my
codebase live?"*
**Brute-force baseline:** ECharts force layout pushes nodes apart by
edge density only — semantically identical files end up wherever the
springs settle, often opposite corners.
**With UMAP:**
- At index time, project the 384-d embeddings to 2-d.
- Store `(x, y)` on each node payload.
- The web UI seeds ECharts node positions with these coords; force
  layout still runs but starts from a meaningful spot.
- Result: auth code clusters together, mailer code clusters together,
  parser plumbing clusters together — **visible at a glance, before
  the user clicks anything**.

**No new chat tool needed** — this is pure UI.

**Cost:** seconds for ~10k nodes; cached, only recomputed on full
re-index (incremental re-index reuses old coords for unchanged nodes).

#### 1.2 HDBSCAN — auto-discovered code clusters

**Question it answers:** *"What are the natural modules of this
codebase, regardless of folder layout?"*
**Brute-force baseline:** the model guesses from folder names —
fails completely for misnamed folders, monorepos, or refactor-in-progress
codebases.
**With HDBSCAN:**
- Run on the UMAP coords (cheap, density-based, no `k` to tune).
- Each node gets a `cluster_id` (or `-1` for noise).
- Cluster colour drives node colour in the UI.

**New tools:**
```json
{ "name": "list_clusters",
  "description": "List auto-discovered code clusters (semantic modules). Each cluster has id, label, member count, and a few representative node IDs." }

{ "name": "get_cluster_members",
  "parameters": { "properties": {
    "cluster_id": { "type": "integer" },
    "top": { "type": "integer", "default": 50 }
  } } }
```

**Worked example — relationship investigation:**

> User: *"I keep seeing `bcc` and `recipient` and `smtp` scattered
> around — is there really one email module here or is it tangled?"*

| Path                  | Steps                                                                                       |
|-----------------------|---------------------------------------------------------------------------------------------|
| **`grep` baseline**   | 3 separate `project_search` calls, model reads each match, tries to mentally cluster.       |
| **HDBSCAN cluster**   | One `list_clusters` → cluster 4 ("Email"). One `get_cluster_members(4)` → 11 functions across 4 files. Answer: "yes, it's one logical module spread across `mailer.py`, `alerts/notify.py`, `reports/daily.py`, and `tests/test_mailer.py`." |

#### 1.3 PageRank — upgrade the strength metric

**Question it answers:** *"Which symbols actually matter here?"*
(better than today's sum-of-degree).
**Brute-force baseline:** current `strength = in_degree + out_degree`
treats a one-edge utility import the same as a 50-edge call hub.
Over-promotes module-level imports of common stdlib symbols.
**With PageRank:**
- Compute `nx.pagerank(G)` once at index time.
- Store `pagerank` on every node.
- Word-cloud `strong` mode switches from sum-of-degree to PageRank
  percentile (still TOON-friendly, same shape).

**New tool:**
```json
{ "name": "get_node_importance",
  "description": "Return PageRank, betweenness, and degree percentile for a node, plus a 1-line interpretation ('top 1% — central hub' / 'rarely referenced').",
  "parameters": { "properties": {
    "node_id": { "type": "string" }
  } } }
```

**Worked example:**

> User: *"Is `MailService` actually a hub or is it just a class
> nobody uses?"*

| Path                       | Steps                                                                                          |
|----------------------------|------------------------------------------------------------------------------------------------|
| **`grep` baseline**        | `grep -rn MailService .` → 23 hits, half are docstrings/strings, model has to read each.       |
| **`get_node_importance`**  | One call → `pagerank=0.0042 (top 2%), betweenness=0.18 (top 1%), in_degree=12`. Verdict: hub.  |

#### 1.4 KeyBERT — keyphrases per node

**Question it answers:** *"What is this function/file actually about,
in human words?"*
**Brute-force baseline:** the model reads the source and summarises —
costs tokens every time it's asked.
**With KeyBERT:**
- At index time, extract 3–5 keyphrases per function/file using the
  embedding model already loaded (no extra model download).
- Store as `keyphrases: [str]` on the node payload.

**New tool:**
```json
{ "name": "search_graph_by_keyphrase",
  "description": "Find nodes whose precomputed keyphrases match a query. Different from search_graph: keyphrases are extracted once at index time, so this catches synonyms (rate-limit / throttle / quota) that text search misses.",
  "parameters": { "properties": {
    "query": { "type": "string" },
    "top":   { "type": "integer", "default": 10 }
  } } }
```

**Worked example:**

> User: *"Find any rate-limiting code."*

| Path                                   | Steps                                                                                            |
|----------------------------------------|--------------------------------------------------------------------------------------------------|
| **`grep` baseline**                    | `project_search "rate.*limit"` misses functions named `throttle`, `bucket`, `quota`, `cooldown`. |
| **`search_graph_by_keyphrase`**        | One call returns nodes whose KeyBERT keyphrases include any of {rate-limit, throttle, request-quota, cooldown}. Hits all 4 implementations in one shot. |

**Phase 1 done-when:**
- [ ] `requirements.txt` adds `umap-learn`, `hdbscan`, `keybert`
- [ ] `graph/builder.py` invokes the four passes after embeddings, with
      a single `--no-ml` flag for users who want to skip
- [ ] Node payload schema gains `umap_xy`, `cluster_id`, `pagerank`,
      `betweenness`, `keyphrases`
- [ ] Three new tools registered in `ai/chat_request.json` +
      `chat/service.py`
- [ ] `web/static/app.js` honours `umap_xy` and `cluster_id` for layout
      and colour
- [ ] Tests in `tests/ml/test_phase1.py` cover: deterministic seeds,
      graceful skip when libs not installed, incremental reuse of
      cached coords for unchanged nodes
- [ ] Sibling plan §3.3 cheat-sheet gets new bullets:
      *"What modules exist? → `list_clusters`."*
      *"Find synonyms of X? → `search_graph_by_keyphrase`."*
      *"Is X important? → `get_node_importance`."*

---

### Phase 2 — Graph-native community + structural similarity

Adds two more lenses that complement Phase 1: clustering by
**connectivity** instead of meaning, and embeddings that combine the
two.

#### 2.1 Louvain / Leiden — connectivity communities

**Library:** `python-louvain` (simple) or `leidenalg` + `igraph` (better
quality, modest install).

**Question it answers:** *"Which functions actually call each other,
regardless of what they're 'about'?"*

This is the **structural twin** of HDBSCAN. HDBSCAN finds nodes that
*read alike*; Louvain finds nodes that *call each other*. Where the
two agree → real cohesive module. Where they disagree → architectural
smell (semantically related code that doesn't talk, or tightly-coupled
code with no shared meaning).

**New tool:**
```json
{ "name": "get_community",
  "description": "Return the connectivity community for a node (Louvain), plus the cluster (HDBSCAN, semantic). When community and cluster disagree, the node is a likely architectural smell — flagged in `notes`.",
  "parameters": { "properties": {
    "node_id": { "type": "string" }
  } } }
```

**Worked example — relationship investigation:**

> User: *"Is the email code well-organised or is it scattered?"*

| Path                  | Steps                                                                                                  |
|-----------------------|--------------------------------------------------------------------------------------------------------|
| **`grep` baseline**   | Impossible — grep can't measure cohesion.                                                              |
| **`get_community`**   | Returns `community=7 (mailer.py + alerts.py + reports/daily.py)` and `cluster=4 (Email)`. **Agree** → cohesive module. If they disagreed it would say so explicitly. |

#### 2.2 Betweenness centrality — bottleneck detection

Already a NetworkX one-liner once we own the precompute pass. Stored as
`betweenness` on the node (Phase 1 did this when computing PageRank;
Phase 2 just exposes it).

**Question it answers:** *"Which functions, if I broke them, would
fragment the codebase the most?"*

**Surfaced via:** `get_node_importance` (already added in Phase 1.3) —
this phase just teaches the chat system prompt to mention betweenness
when the user asks about refactoring risk.

#### 2.3 node2vec / GraphSAGE — graph embeddings (optional)

**Library:** `karateclub` (lightweight, scikit-learn-style API) or
`pytorch-geometric` (heavier, do not pick this unless someone asks).

**Question it answers:** *"Find functions structurally similar to this
one — same role in the call graph, even if differently named."*

Today's `find_similar` uses text embeddings only. Two functions with
identical names but different call patterns get the same vector.
node2vec embeddings encode neighbourhood structure → "this is another
factory function," "this is another retry wrapper."

**New tool:**
```json
{ "name": "find_structurally_similar",
  "parameters": { "properties": {
    "node_id": { "type": "string" },
    "top":     { "type": "integer", "default": 10 }
  } } }
```

**Worked example:**

> User: *"I wrote `retry_smtp_send` — has anyone else written
> retry-around-IO patterns I should reuse?"*

| Path                              | Steps                                                                                  |
|-----------------------------------|----------------------------------------------------------------------------------------|
| **`grep` baseline**               | `project_search "retry"` — finds the word, misses untagged retry loops; floods results with comments. |
| **`find_structurally_similar`**   | Returns the 5 functions whose call-graph neighbourhood looks like `retry_smtp_send`'s — including ones not named `retry_*`. |

**Phase 2 done-when:**
- [ ] `python-louvain` (and/or `leidenalg`) added; `karateclub`
      optional behind a config flag
- [ ] Node payload gains `community_id`
- [ ] `get_community` and (optional) `find_structurally_similar` tools
      registered
- [ ] System prompt cheat-sheet: *"Cohesion check / 'is this
      scattered?' → `get_community`."*

---

### Phase 3 — Anomaly & dead code

#### 3.1 IsolationForest — outlier detection

**Library:** `scikit-learn` (already in Phase 1's dep set).

**Features per function:** `loc`, `complexity`, `param_count`,
`in_degree`, `out_degree`, embedding magnitude, optionally PageRank.

**Question it answers:** *"What's weird in this repo?"* — god-functions,
orphan helpers, copy-pasta, abnormal complexity-vs-LOC ratios.

**New tool:**
```json
{ "name": "find_outliers",
  "description": "Return the most anomalous nodes by IsolationForest score, with a per-node reason (`unusually large`, `high complexity / low caller count`, `orphan helper`, etc.).",
  "parameters": { "properties": {
    "top":   { "type": "integer", "default": 20 },
    "kind":  { "type": "string", "enum": ["function", "class", "file"], "default": "function" }
  } } }
```

**Worked example — relationship investigation:**

> User: *"Where should I look first for code smells?"*

| Path                | Steps                                                                                                |
|---------------------|------------------------------------------------------------------------------------------------------|
| **`grep` baseline** | Impossible — grep cannot rank by abnormality.                                                        |
| **`find_outliers`** | One call returns 20 ranked functions with reasons: "complexity 47 vs project median 4," "1200 LOC vs median 32," "orphan: 0 callers despite 8 callees." |

#### 3.2 vulture + graph cross-validation — high-confidence dead code

**Library:** `vulture` (very small).

Run `vulture` *and* check the graph: a node is high-confidence dead
when **both** vulture flags it **and** `in_degree == 0`. Cuts the
classic vulture false-positive rate (decorator-registered handlers,
plugin entry points) because those *do* have graph edges.

**New tool:**
```json
{ "name": "find_dead_code",
  "description": "Cross-validated dead code: flagged by vulture AND with zero in-edges in the graph. Returns path, line, name, why.",
  "parameters": { "properties": {
    "kind": { "type": "string", "enum": ["function", "class", "variable"], "default": "function" }
  } } }
```

**Worked example:**

> User: *"Is `legacy_send_v1` actually used anywhere?"*

| Path                 | Steps                                                                                                |
|----------------------|------------------------------------------------------------------------------------------------------|
| **`grep` baseline**  | `grep -rn legacy_send_v1 .` → 1 hit (its own definition). But misses dynamic dispatch.               |
| **`find_dead_code`** | Cross-checks: vulture flags it AND graph in_degree=0 AND no string literal mentions. **High-confidence dead**. |

**Phase 3 done-when:**
- [ ] `vulture` added to `requirements.txt`
- [ ] Node payload gains `outlier_score`, `outlier_reason`
- [ ] Two new tools registered
- [ ] System prompt cheat-sheet: *"Find smells / dead code → `find_outliers` / `find_dead_code`."*

---

### Phase 4 — Topics + multi-language metrics

#### 4.1 BERTopic — auto-labelled topics

**Library:** `bertopic` (depends on `umap-learn` + `hdbscan` already
present).

**Question it answers:** *"Summarise what each part of the codebase
does, in a sentence."*

BERTopic combines UMAP + HDBSCAN + class-based TF-IDF to produce a
human-readable label per cluster. Apollo can use the same UMAP +
HDBSCAN already computed in Phase 1, so the marginal cost is just the
TF-IDF labelling step.

**New tool:**
```json
{ "name": "get_topics",
  "description": "List auto-labelled topics in the codebase. Each topic has id, label (e.g. 'Email Sending'), keywords, member node count, and representative node IDs.",
  "parameters": { "properties": {
    "top":  { "type": "integer", "default": 20 }
  } } }
```

**Worked example — relationship investigation:**

> User: *"Give me a one-paragraph summary of this project."*

| Path                | Steps                                                                                                |
|---------------------|------------------------------------------------------------------------------------------------------|
| **`grep` baseline** | Read README; if missing/stale, grep for `def `, summarise. ~6 rounds, low confidence.                |
| **`get_topics`**    | One call returns "1. Indexing pipeline (parser, builder, mistune, AST). 2. Web UI (ECharts, FastAPI, SSE). 3. Chat (Grok API, TOON, tool-calling). 4. Embeddings (sentence-transformers, cosine). 5. Notes (annotations, FTS)." |

#### 4.2 Lizard — multi-language metrics in one call

**Library:** `lizard` (zero dependencies, supports Python, JS, TS, Go,
Java, C, C++, Rust, Swift, Ruby, PHP, ...).

**Question it answers:** *"Give me complexity / LOC / param count for
non-Python plugins, the same as Python."*

This unblocks Phase 6 metrics for *every* future language plugin
without each plugin re-implementing complexity. Drop into
`parser/base.py` as a default `compute_metrics()` implementation.

**Not a new chat tool** — it's an indexing-layer upgrade that makes
the existing `get_code_metrics` tool (sibling plan Phase 3.1) work
across languages.

**Phase 4 done-when:**
- [ ] `bertopic`, `lizard` added to requirements
- [ ] Topic table stored separately (it's small, doesn't bloat node payload)
- [ ] `get_topics` tool registered
- [ ] `parser/base.py` falls back to `lizard` when a plugin doesn't
      provide its own metrics
- [ ] At least one non-Python plugin (HTML5 or markdown_gfm) gets
      complexity/LOC for free

---

### Phase 5 — Time-aware insights (depends on `get_git_context`)

Build only after sibling plan Phase 4.1 (`get_git_context`) lands. These
need git history.

#### 5.1 ruptures — change-point detection

**Library:** `ruptures`.

**Question it answers:** *"When did this file go bad?"*

For each file, build a per-commit time series of `complexity` and
`loc`. Apply change-point detection. Flag the commit where the file
"broke."

**New tool:**
```json
{ "name": "get_complexity_history",
  "description": "Per-commit history of complexity & LOC for a file, plus detected change-points (when did the file's metrics start to spike).",
  "parameters": { "properties": {
    "path": { "type": "string" }
  } } }
```

**Worked example:**

> User: *"`mailer.py` feels gnarly — when did it get this bad?"*

| Path                          | Steps                                                                                              |
|-------------------------------|----------------------------------------------------------------------------------------------------|
| **`grep` baseline**           | Impossible.                                                                                        |
| **`get_complexity_history`**  | Returns per-commit metrics + "complexity stable at 4–6 until commit `abc123` (2024-08-12), jumped to 18 then 27 over 3 PRs adding multi-tenant support." |

#### 5.2 implicit — co-change recommendation

**Library:** `implicit` (fast collaborative filtering).

**Question it answers:** *"What other files are usually edited
together with this one?"*

Catches **implicit coupling** the call graph misses entirely:
schema + migration, config + docs, route + handler + test, when none
of those import each other.

**New tool:**
```json
{ "name": "find_co_changed_files",
  "description": "Files most often committed together with the given file (collaborative filtering on git history). Reveals coupling the call graph cannot see.",
  "parameters": { "properties": {
    "path": { "type": "string" },
    "top":  { "type": "integer", "default": 10 }
  } } }
```

**Worked example:**

> User: *"I'm changing `schema/notes.sql` — what else will probably
> need to move with it?"*

| Path                          | Steps                                                                                              |
|-------------------------------|----------------------------------------------------------------------------------------------------|
| **`grep` baseline**           | `project_search "notes"` — floods on the word; misses the migration script and the API route.     |
| **`find_co_changed_files`**   | Returns: `apollo/api/routes/notes.py` (87% co-change), `tests/api/test_notes.py` (81%), `web/static/app.js` (34%), `docs/API.md` (28%). Precise blast-radius list. |

**Phase 5 done-when:**
- [ ] `ruptures`, `implicit` added (gated behind sibling plan Phase 4.1)
- [ ] `get_complexity_history`, `find_co_changed_files` tools registered
- [ ] Skip cleanly when not in a git repo (mirror `get_git_context`'s
      `git_available` pattern)

---

## 3. Cross-cutting concerns

### 3.1 Compute at index time, query at read time

Every ML pass writes its result onto the node payload (or a small
sidecar table for topics / co-change matrices). Chat-time tools are
**plain reads**. No tool should ever invoke UMAP, HDBSCAN, or training
during a chat round — the round budget would blow instantly.

### 3.2 Graceful degradation

Every ML lib is **optional**. If `umap-learn` isn't installed, the
indexer skips the layout pass and the UI falls back to spring layout.
If `vulture` isn't installed, `find_dead_code` returns
`{ml_available: false}`. Same pattern as `get_git_context`'s
`git_available: false` in the sibling plan.

### 3.3 Incremental re-index reuse

Phase 6 of `DESIGN.md` already guarantees per-function MD5 hashing.
The ML passes piggyback on this:

- Unchanged node → reuse cached `umap_xy`, `cluster_id`, `pagerank`,
  `keyphrases`.
- Changed node → recompute keyphrases (fast); flag for next batch
  re-layout (deferred until ≥5% of nodes changed, then UMAP runs once).

This keeps incremental re-index proportional to what actually changed,
matching the rest of Apollo's behaviour.

### 3.4 TOON shape audit

Every new tool returns a uniform-array payload exactly as required by
`PLAN_MORE_LOCAL_AI_FUNCTIONS.md` §3.1. `list_clusters`,
`get_topics`, `find_outliers`, `find_dead_code`,
`find_co_changed_files`, `find_structurally_similar`, and
`search_graph_by_keyphrase` all return one homogeneous list →
header-once TOON.

### 3.5 Round-budget impact

| Question                                                  | Today  | After Phase 1–3 |
|-----------------------------------------------------------|--------|-----------------|
| "What modules exist?"                                     | 4–5    | 1               |
| "What's important here?"                                  | 2–3    | 1               |
| "Find rate-limiting / retry / throttling code."           | 3–4    | 1               |
| "What's weird?"                                           | n/a    | 1               |
| "Is X dead code?"                                         | 2      | 1               |
| "Which files move together?"                              | n/a    | 1               |

### 3.6 Dependency cost

Cumulative install size if every phase ships:

| Phase | New deps                                          | ~MB |
|-------|---------------------------------------------------|-----|
| 1     | `umap-learn`, `hdbscan`, `keybert` (sklearn already used) | ~80 |
| 2     | `python-louvain` (`leidenalg` optional)           | ~5  |
| 3     | `vulture`                                         | ~1  |
| 4     | `bertopic`, `lizard`                              | ~30 |
| 5     | `ruptures`, `implicit`                            | ~25 |

All pure-Python or pre-built wheels. No CUDA, no compilers, no
external services.

---

## 4. Sequencing summary

| Phase | Libraries                                         | Risk   | Est. PRs | Depends on                        |
|-------|---------------------------------------------------|--------|----------|-----------------------------------|
| 1     | umap-learn, hdbscan, keybert                      | low    | 1        | —                                 |
| 2     | python-louvain (+ optional karateclub)            | low    | 1        | Phase 1 (PageRank precompute)     |
| 3     | vulture                                           | low    | 1        | Phase 1 (outlier features)        |
| 4     | bertopic, lizard                                  | medium | 1        | Phase 1 (UMAP+HDBSCAN reuse)      |
| 5     | ruptures, implicit                                | medium | 1        | sibling plan Phase 4.1 (`get_git_context`) |

Ship Phase 1 first — single dep stack, four user-visible wins (layout,
clusters, importance, keyphrases) for the cost of one indexing pass.

---

## 5. Take-away

Same shape as the sibling plan: **the indexer is doing the hard work,
these libraries make the result legible.** Wherever the model would
otherwise reach for `project_search` — or worse, ask the user for
context — a precomputed ML signal answers the question in one tool
call. And several of the questions here (*"what modules exist?"*,
*"what's weird?"*, *"what moves together?"*) cannot be answered by
`grep` at all, no matter how many rounds you spend.

That is the only test that should be applied to any future ML
addition: **does it answer a relationship question that grep
fundamentally cannot?** If yes, ship it. If it just reformats
something `grep` already does, skip it.
