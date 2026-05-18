# Hybrid Graph + Spatial Retrieval

**A consensus-based re-ranking model for code knowledge graphs**

> Companion to [`docs/DESIGN.md` §16](DESIGN.md#16-hybrid-graph--spatial-re-ranking).
> Reference implementation lives in [`spatial.py`](../spatial.py).

---

**Author:** Fujio Turner ([github.com/fujio-turner](https://github.com/fujio-turner))
**First publication / disclosure date:** 2026-05-18
**Canonical URL:** https://github.com/fujio-turner/apollo/blob/main/docs/HYBRID_GRAPH_SPATIAL.md
**Version:** 1.0

**Copyright © 2026 Fujio Turner. All rights reserved in the text and figures.**

**Licence — text, figures, and code listings in this document:**
[Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
You are free to share and adapt this work for any purpose, including
commercial use, provided you give appropriate credit, link to the
licence, and indicate if changes were made. The reference
implementation in [`spatial.py`](../spatial.py) is licensed
separately under the repository licence (see
[`licenses/BSL-1.1.txt`](../licenses/BSL-1.1.txt)).

**Defensive publication notice.** This document is published as
*prior art* with the timestamp above. The hybrid operator
`H(s, c)`, the 4-D engineered spatial view `(x, y, z, face)`, and
the 2×2 disagreement matrix (`CONFIRMED` / `STRUCTURAL_ONLY` /
`HIDDEN_SIMILAR` / `UNRELATED`) described herein are disclosed
publicly on the date above and form prior art against any
subsequent patent claim by third parties.

**Suggested citation:**

```bibtex
@techreport{turner2026hybrid,
  author      = {Fujio Turner},
  title       = {Hybrid Graph + Spatial Retrieval:
                 A Consensus-Based Re-ranking Model
                 for Code Knowledge Graphs},
  institution = {Apollo Project},
  year        = {2026},
  month       = {may},
  type        = {White Paper},
  number      = {v1.0},
  url         = {https://github.com/fujio-turner/apollo/blob/main/docs/HYBRID_GRAPH_SPATIAL.md},
  note        = {Licensed CC BY 4.0. Published as defensive prior art.}
}
```

Plain-text citation:

> Turner, F. (2026). *Hybrid Graph + Spatial Retrieval: A
> Consensus-Based Re-ranking Model for Code Knowledge Graphs*
> (Apollo Project White Paper v1.0). Retrieved from
> https://github.com/fujio-turner/apollo/blob/main/docs/HYBRID_GRAPH_SPATIAL.md

---

## Abstract

Apollo indexes a codebase along two orthogonal axes:

1. A **discrete graph** of parser-derived relationships
   (`calls`, `imports`, `defines`, `inherits`, `contains`,
   `references`) produced by the parser plugins, and
2. A **continuous spatial embedding** `(x, y, z, face)` produced by
   [`SpatialMapper.compute_all`](../spatial.py) where `x` is a 1-D
   semantic projection of the OpenAI-style embedding via UMAP/PCA,
   `y` is normalised call-depth from entry points, `z` is normalised
   PageRank, and `face` is a categorical role bucket.

Each axis is individually useful but **independently lossy**: the
graph omits anything the parser cannot see (dynamic dispatch,
templated code, polyglot calls, comment-level intent), and the
spatial embedding compresses away the precise edge structure.

This paper proposes a single consensus operator,
**`H(s, c) = Σᵢ wᵢ · aᵢ(s, c)`**, that combines the two views into a
ranked relevance score, and a 2 × 2 disagreement table that turns the
*difference* between the two views into a first-class signal
(structural-only edges → audit candidates; spatially-close non-edges
→ discovery candidates).

We give:

- A worked example where the hybrid view answers a question neither
  axis can answer alone (§3.1, "Find the dead refactor").
- Three worked **failure modes** where naïvely combining the two
  *produces* erroneous results (§4: boilerplate collapse, layered-
  architecture compression, polyglot blind spots).
- A pure-functional formulation in Python (§5) that fits in ~40 lines
  and is independent of any storage backend.
- The underlying mathematics (§6) and a discussion of when the linear
  combination is justified vs. when a probabilistic / rank-fusion
  alternative is more appropriate.

---

## 0. Intuition for the non-mathematical reader

If §3–§6 read like dense notation, the entire idea reduces to one
analogy and one rule of thumb.

### 0.1 The two-witnesses analogy

Imagine you are trying to figure out which of your coworkers are
*actually* working on the same project as Alice.

- **Witness 1 — the org chart.** Tells you who *reports* to whom,
  who is on whose team, who shares a manager. Exact. Discrete.
  But it cannot see that Bob and Carol, in different departments,
  happen to be collaborating on a side project.
- **Witness 2 — the office floor plan.** Tells you who *sits* near
  whom, who eats lunch with whom, who is in the same Slack
  channels. Fuzzy. Continuous. Tells you about real
  collaboration patterns the org chart can't see, but also tells
  you "Eve sits near Alice" — which is just a seating
  coincidence.

Neither witness alone is reliable. But if **both witnesses agree**
that someone is close to Alice, you have high confidence. And —
critically — when the **two witnesses disagree**, the disagreement
itself is the most interesting signal:

- Same team on the org chart, but never seen together in the floor
  plan → maybe a *reporting line that doesn't reflect real work*.
- Never on the same team, but always seen together in the floor
  plan → maybe a *real working relationship the org chart doesn't
  know about yet*.

Apollo plays the exact same game on a codebase. The **graph** is
the org chart (precise, parser-derived). The **spatial view** is
the floor plan (fuzzy, learned from embeddings + structural
properties). The hybrid operator is "ask both witnesses, then
look at where they agree *and* where they disagree."

### 0.2 What the four spatial axes "feel like"

| Axis | What it really measures | Floor-plan analogy |
|---|---|---|
| `x` (semantic) | Does this code talk about the same topic? | Sit in the same office? |
| `y` (call-depth) | Is this code at the same architectural layer? | Same floor of the building? |
| `z` (PageRank) | Is this code an important hub? | Are they a VP that everyone meets with? |
| `face` (role) | What kind of code is this — test, config, util, storage…? | What department are they in — HR, eng, sales? |

When all four agree with the seed, you have a confident match.
When they disagree, the failure-mode catalogue in §4 tells you
*why* — and that "why" is usually a real finding about the
codebase, not noise.

### 0.3 The one-sentence summary

> **Graph view tells you what is connected. Spatial view tells you
> what is similar. Hybrid retrieval is the systematic study of
> when those two answers agree, and what to do when they don't.**

---

## 1. Motivation

Two questions a developer routinely asks Apollo today:

> *"What does `emails()` do, and what is related to it?"*

> *"What other code in this repo does the same thing as `emails()` —
> even if it isn't linked?"*

The first question is a graph traversal. The second is a vector
search. Today they are different commands, return different shapes,
and never check each other's work.

When the two are consulted together informally — by a human reading
both result lists side-by-side — the *agreement* between them is
recognisable as a strong relevance signal, and the *disagreement* is
recognisable as either a parser miss or a stale edge. This paper
formalises that intuition into a single ranking function.

---

## 1.5 Position vs. related work, and what is genuinely novel

Hybrid graph-plus-vector retrieval has become a popular topic in the
2024–2025 RAG (retrieval-augmented generation) literature under names
like **HybridRAG**, **GraphRAG + VectorRAG**, and "fusion retrieval."
A reader familiar with that body of work will reasonably ask: *what
specifically is new here?*

The honest, narrow claims are:

1. **The 4-D engineered spatial view is novel.** Most hybrid systems
   pair a knowledge graph with raw chunk/document embeddings. Apollo
   instead projects every node into four *separately interpretable*
   axes — `x` (semantic topic via UMAP/PCA), `y` (architectural layer
   via call-depth BFS), `z` (importance via PageRank), `face` (role
   bucket). Each axis is independently meaningful, independently
   tunable, and independently auditable. Raw-embedding hybrids cannot
   answer "they agree on topic but disagree on layer" — Apollo can.

2. **The convex-combination consensus operator at query time, with
   pre-computed payloads, is novel.** Existing hybrids either
   (a) concatenate retrieved contexts and rely on the LLM to sort it
   out, (b) use Reciprocal Rank Fusion at the *rank* level, or (c) run
   a late-interaction cross-encoder. None expose a small set of
   per-axis weights `(w_g, w_x, w_y, w_f, w_z)` that a developer can
   reweight per query intent. The operator runs in `O(|C|)` over the
   BFS candidate set with no additional model calls.

3. **The 2 × 2 disagreement matrix as a *first-class output* is
   novel.** RAG hybrids treat the second retriever as a recall booster.
   Apollo treats disagreement between the two retrievers as the *whole
   point*: `STRUCTURAL_ONLY` becomes an audit surface (leaky
   abstractions, stale edges), `HIDDEN_SIMILAR` becomes a discovery
   surface (parallel implementations, missing links). Obsidian's
   "unlinked mentions" panel is the closest analogue and it is
   informal, single-view, and document-level.

4. **The failure-mode catalogue is novel for code-graph hybrids.**
   §4 enumerates five specific ways naïve combination *degrades*
   results (boilerplate embedding collapse, deep-stack `y`
   compression, polyglot parser blind spots, seed-as-outlier,
   `w_z` inflation) with mitigations. RAG-hybrid papers focus on
   end-to-end Q&A accuracy on documents/finance transcripts and do
   not surface these code-specific failure modes.

What is **not** novel, and we should not claim it is:

- The high-level idea "combine a graph view with a vector view."
  That is the whole HybridRAG literature.
- Combining ranked lists from two retrievers. RRF (Cormack et al.,
  2009) is the standard reference and §6.4 explicitly recommends
  RRF as the fallback when calibration is impractical.
- Per-language code embeddings (CodeBERT, GraphCodeBERT, UniXcoder).
  These produce the embeddings; Apollo's contribution sits *above*
  whichever embedding model you choose.

The right one-line positioning is:

> *"While hybrid graph-vector retrieval is widely studied for
> document RAG, Apollo contributes a lightweight, query-time
> consensus operator with per-axis weights that makes the two views
> inspectable, reweightable per developer intent, and — most
> importantly — turns disagreement between them into first-class
> code-quality signals."*

A small ablation (graph-only vs. spatial-only vs. hybrid; with and
without the disagreement-matrix surface) on this repo and on a
larger polyglot repo would substantiate every one of the claims
above; see §8.

**Patent-landscape note.** The closest commercial competitor in the
code-intelligence space, Sourcegraph, holds a single relevant U.S.
patent — **US 9,753,723 B2** (Slack & Liu, granted 2017) — which
covers *index construction* (generating, linking, and presenting
language-independent code representations from diverse sources),
not retrieval or re-ranking. Apollo's contribution composes *above*
any such index and falls outside the scope of that patent. See §9.3
for the full analysis. No prior published patent (Sourcegraph,
Meta/Glean, Google/Kythe, Microsoft/LSIF) claims a hybrid
graph-plus-spatial consensus operator or a 2 × 2 disagreement
matrix between two code-retrieval views as described in this paper.

---

## 2. Background — the two views

### 2.1 Graph view

The graph view is exact, sparse, and high-precision. Edges are
emitted by the parser plugins. The cost of a missed edge is silent:
the consumer never knows it asked an incomplete question.

```
mailer.py ──defines──▶ emails()
            ─imports─▶ smtplib
            
emails()  ──calls────▶ smtplib.SMTP
          ──references▶ SMTP_HOST
          
send_report() ──calls──▶ emails()
```

For a seed `s`, the **graph proximity** of `c` is naturally captured
by hop distance under whatever edge subset the query specifies:

```
g(s, c) = 1 / (1 + hops(s, c))     ∈ (0, 1]
```

### 2.2 Spatial view

[`SpatialMapper`](../spatial.py) projects every node into four
dimensions:

| Axis    | Computed from                                  | Interpretation                       | Range          |
|---------|------------------------------------------------|--------------------------------------|----------------|
| `x`     | 1-D UMAP (or PCA fallback) of node embedding   | semantic neighbourhood (topic)       | 0 – 360°       |
| `y`     | BFS depth from no-incoming-call entry points   | architectural layer                  | 0 – 360°       |
| `z`     | normalised PageRank                            | importance / hubness                 | 0 – 1          |
| `face`  | rule-based bucket (test/config/util/storage/…) | role                                 | {±1 … ±6}      |

For a seed `s`, four per-axis **spatial agreements** with `c`:

```
aₓ (s, c) = 1 − min(|Δx|, 360 − |Δx|) / 180          # 0..1, wraparound aware
a_y(s, c) = 1 − |Δy| / 360                            # 0..1
a_f(s, c) = 𝟙[face(c) == face(s)]                    # {0, 1}
a_z(c)    = z(c)                                      # 0..1, prior (importance)
```

`x` is treated as circular because UMAP→[0,360°] in `compute_x` has no
natural origin; `y` is linear because BFS-depth has an origin (entry
points are layer 0).

---

## 3. The hybrid operator

### 3.1 Definition

For seed `s` and candidate `c`:

```
H(s, c) = w_g · g(s, c)
        + w_x · aₓ(s, c)
        + w_y · a_y(s, c)
        + w_f · a_f(s, c)
        + w_z · a_z(c)
```

with weights summing to 1 and a candidate set `C(s, N)` produced by an
N-hop BFS from `s` under the user-selected edge subset.

The "3-of-5 converge" intuition is recovered by substitution:
candidates whose `(x, y, face)` cluster near `s` get a high
`a_x + a_y + a_f` contribution; outliers get a low one and sink to
the bottom of the ranking.

### 3.2 The disagreement matrix

| `g(s,c) > τ_g` | `spatial(s,c) > τ_s` | Quadrant label             | Action                                              |
|----------------|----------------------|----------------------------|-----------------------------------------------------|
| ✅              | ✅                    | **CONFIRMED**              | Promote — first-class result                        |
| ✅              | ❌                    | **STRUCTURAL-ONLY**        | Investigate — leaky abstraction, glue, or stale edge |
| ❌              | ✅                    | **HIDDEN-SIMILAR**         | Suggest as discovery / candidate missing link        |
| ❌              | ❌                    | UNRELATED                  | Drop                                                |

`spatial(s, c) = (a_x + a_y + a_f) / 3` and `τ_g`, `τ_s` are tunable
thresholds (default `τ_g = 0.25`, i.e. ≤ 3 hops; `τ_s = 0.7`).

### 3.3 Worked example — "Find the dead refactor"

**The situation.** Six months ago a team started migrating email
handling out of an old top-level function into a new service class:

- Old: `mailer.py::emails()` — one big function. Still called from a
  handful of legacy code paths.
- New: `notifications/email_service.py::EmailService.send()` — a
  class with a cleaner interface. Called from rewritten flows.

The migration was never finished. Nobody on the team remembers there
are *two* implementations. Both still ship. The old one occasionally
gets bugs filed against it ("emails not landing"); fixes go in, and
then weeks later the same fix has to be re-applied to the new one,
or worse — only the new one gets fixed and the old one keeps
silently misbehaving.

A new engineer asks the chatbot: *"How do we send emails in this
codebase?"*

**What each view says, on its own.**

| View | Top results | Misses |
|---|---|---|
| Graph traversal — callers of `emails()` | `send_report()`, `nightly_digest()`, `legacy/cron.py::run()` | Never surfaces `EmailService.send()` because there is no edge between them. |
| Semantic vector search — top-k near `emails()` | `EmailService.send()`, `EmailService.queue()`, `MailgunAdapter.deliver()` | Never surfaces the actual *callers* of `emails()`, because callers don't mention email at the token level. |

So a developer using *only* the graph view believes `emails()` is the
canonical implementation. A developer using *only* the vector view
believes `EmailService.send()` is. Both are wrong: the codebase has
**both**, and that's the actual story.

**The hybrid trace, with numbers.**

Assume (made up but representative) spatial coordinates:

| Node | x (semantic) | y (layer) | face | z (PR) |
|---|---:|---:|---:|---:|
| `emails()` *(seed)* | 47° | 210° | 2 (callable, has incoming+outgoing) | 0.42 |
| `send_report()` | 51° | 180° | 1 (entry-point-ish) | 0.10 |
| `nightly_digest()` | 49° | 175° | 1 | 0.08 |
| `legacy/cron.py::run()` | 44° | 160° | 1 | 0.06 |
| `EmailService.send()` | 53° | 215° | 2 | 0.31 |
| `EmailService.queue()` | 56° | 215° | 2 | 0.18 |

For `EmailService.send()` vs. the seed:

- `g(s, c) = 1/(1+∞) = 0.0` — there is no graph path.
- `a_x = 1 − |47 − 53|/180 = 1 − 0.033 = 0.967` — very close on topic.
- `a_y = 1 − |210 − 215|/360 = 0.986` — same layer.
- `a_f = 𝟙[2 == 2] = 1.0` — same role.
- `a_z = 0.31`.

`spatial(s, c) = (0.967 + 0.986 + 1.0)/3 = 0.984`, and `g(s, c) = 0`.

That places the pair squarely in the `HIDDEN_SIMILAR` quadrant
(graph says "no link," spatial says "same thing"). With default
thresholds `τ_g = 0.25, τ_s = 0.70`, the operator emits:

```
HIDDEN_SIMILAR: emails()  ↔  EmailService.send()      score: spatial=0.98, graph=0.00
```

**What the user actually sees in the chat panel.**

```
How do we send emails in this codebase?

  Direct (graph) — these call emails() today:
    • send_report()                  hops=1   confirmed
    • nightly_digest()               hops=1   confirmed
    • legacy/cron.py::run()          hops=2   confirmed

  Possible parallel implementation (no graph link, high spatial agreement):
    ⚠ EmailService.send()            spatial=0.98
    ⚠ EmailService.queue()           spatial=0.93

  These look like they do the same job as emails() but neither calls
  the other. Consider consolidating or documenting which one is
  canonical.
```

**Why this matters in plain language.** Neither single retriever can
tell the user "there are two of these, only one of which is being
modernised." The graph view doesn't *know* about `EmailService`
because nothing links them. The vector view doesn't *know* about
`send_report()` because legacy callers don't mention emails. Only
the *disagreement* between the two views surfaces the dead-refactor —
and the disagreement is exactly what the `HIDDEN_SIMILAR` quadrant
labels.

### 3.4 Worked example — "Catch the leaky abstraction"

**The situation.** `utils/logging.py::format_log()` is one of those
helpers everyone reaches for. It started small ("format a log line
with a timestamp"). Over two years it accreted special cases:
HTML-escape for the web log dashboard, JSON-pretty-print for the
storage layer's debug mode, ANSI-colourise for the CLI, gzip for
high-volume parser logs.

Today it is imported by 47 files and called ~600 times. By every
graph metric it looks like a healthy, central utility. By every
single-view test, it's fine.

**What the graph view says.** `format_log()` is a hub: in-degree 47,
PageRank in the top 3 %. Healthy.

**What the spatial view says.** Its 47 callers, when projected onto
`x` and `face`, do **not** cluster:

| Cluster | Caller `x` band | Caller `face` | # of callers |
|---|---:|---:|---:|
| Web/HTTP | 12°–25° | 4 (util/format) | 11 |
| Storage / DB | 188°–204° | 3 (storage/db) | 14 |
| Parser plugins | 91°–105° | 2 (functions) | 16 |
| CLI / scripts | 312°–330° | 1 (entry-point-ish) | 6 |

For each of the 47 caller pairs `(format_log, caller)`:

- `g = 1/(1+1) = 0.5` — direct callers, so graph proximity is high
  for *all* of them.
- `spatial` (avg of `a_x + a_y + a_f`) lands between **0.31 and 0.55**
  depending on which cluster the caller is in.

The seed `format_log()` itself sits at `x = 270°, face = 4` (a
util). Caller-by-caller, the spatial agreement is mediocre across
the board: every caller has a high graph score, but **no caller has
a high spatial score**. The aggregate over all 47 pairs falls in the
`STRUCTURAL_ONLY` quadrant.

**What the operator emits.**

```
STRUCTURAL_ONLY hub detected:
  utils/logging.py::format_log()
    in-degree: 47    PageRank: 0.81    role-face: 4 (util)
    caller spatial-spread:  σ_x = 112°   distinct faces in callers: 4

  Interpretation: callers are structurally tied to this symbol but
  semantically unrelated to it and to each other. This is a hub
  that has grown to serve multiple unrelated subsystems.

  Suggested action: review whether the four caller clusters
  (web, storage, parser, cli) should each have their own thin
  wrapper, with format_log() reduced to a shared primitive.
```

**Why this matters in plain language.** The graph view *can't* catch
this because being a popular utility is, in the graph, exactly the
same shape as being a useful utility. The vector view *can't* catch
this because `format_log()`'s own embedding is fine — its problem is
about *its callers*, not itself. Only the disagreement between
"everything is tightly connected" (graph) and "nothing here is
about the same topic" (spatial) reveals the smell.

This is the canonical refactoring trigger: a hub doing too many
jobs. Apollo names it.

### 3.5 Worked example — "Refactor the API surface"

**The situation.** `User.from_dict` is the constructor that builds
a `User` model from a JSON payload. It is called from request
handlers, from the test suite, from migration scripts, and from a
background job that re-hydrates users from an audit log. A staff
engineer is about to change its signature (`role` will become a
required field). She needs the **complete** set of callers, ranked
by how disruptive the change will be — not by how *similar* the
callers are.

This is the *opposite* of §3.3: she does **not** want the spatial
view promoted. She wants the structural view dominant.

**The weight choice.** The JSON DSL exposes a `rerank` knob:

```json
{
  "find":    { "name": "from_dict", "type": "method", "class": "User" },
  "traverse":{ "direction": "in", "edge": "calls", "depth": 4 },
  "rerank":  "structural"
}
```

`"structural"` is shorthand for the weight tuple
`Weights(g=0.70, x=0.05, y=0.05, f=0.05, z=0.15)` — `w_g` dominates,
`w_z` ranks the survivors by importance, and the spatial axes are
deliberately damped.

**Trace on three representative callers.**

| Caller | hops | `a_x` | `a_y` | `a_f` | `z` | `H(s,c)` under `structural` | `H(s,c)` under `semantic` |
|---|---:|---:|---:|---:|---:|---:|---:|
| `POST /users handler` | 1 | 0.92 | 0.81 | 1.0 | 0.34 | **0.452** | **0.711** |
| `tests/test_user.py::test_round_trip` | 1 | 0.78 | 0.40 | 0.0 | 0.05 | 0.379 | 0.355 |
| `scripts/migrate_audit.py::rehydrate` | 1 | 0.32 | 0.10 | 0.0 | 0.02 | **0.366** | 0.143 |

Under **semantic** weighting (`Weights(g=0.10, x=0.45, y=0.15, f=0.20, z=0.10)`),
the `migrate_audit` script — the one that is *most* unlike a normal
caller — sinks to the bottom and the engineer **misses it**.

Under **structural** weighting, all three callers stay near the top
of the ranking, and `migrate_audit` is preserved with a score
*comparable* to the request handler. That is exactly what the
refactor needs: the cross-cutting, semantically-unusual caller is
the one most likely to break, and the operator is reweighted so it
doesn't get drowned out.

**Why this matters in plain language.** The same data, the same
operator, two different weight tuples → two opposite rankings. The
hybrid framing makes the trade-off **explicit** and **per-query**,
rather than baked into the retriever.

### 3.6 Worked example — "Why is this test failing in CI but not locally?"

**The situation.** A test in `tests/test_pipeline.py` passes on
every developer's laptop and fails in CI roughly 1 % of the time.
The failure message points at `pipeline.run()` returning `None`.

A naïve search starts at `pipeline.run` and walks `calls` edges. It
finds the immediate callees: `_load_input`, `_transform`,
`_write_output`. None of them obviously misbehave.

**What the hybrid view adds.** Re-rank under the **discovery**
intent — `Weights(g=0.05, x=0.40, y=0.10, f=0.10, z=0.05)` plus
*inverted* `w_g` (penalise nodes with a graph edge to the seed).
This deliberately surfaces nodes that look semantically related
to `pipeline.run` but have **no** graph edge to it:

```
HIDDEN_SIMILAR around pipeline.run():
  • storage/lockfile.py::_acquire()       spatial=0.81   no edge
  • tests/conftest.py::tmp_workspace()    spatial=0.74   no edge
  • ci/setup_runner.sh wrapper            spatial=0.69   non-Python (text parser only)
```

The first two are unsurprising. The third is the lead: the CI
wrapper writes a different temp path, and `pipeline.run` reads
`os.environ["WORKSPACE"]` *through* a helper that the graph parser
didn't track (it's a dynamic `getattr`). The graph couldn't find
this. The vector view alone, without the `HIDDEN_SIMILAR` framing,
would have buried the CI wrapper in the noise.

**Why this matters in plain language.** Flaky bugs almost always
live *outside* the call graph the parser can see — environment,
filesystem, time, network. The discovery quadrant is the cheapest
way to surface "things the static graph doesn't know about that
nevertheless look related."

### 3.7 Worked example — "Onboarding: explain this codebase to me"

**The situation.** A new hire opens Apollo for the first time. They
type into the chat: *"I just joined the team. Where should I start
reading?"*

**A pure-graph answer.** Returns the nodes with the highest in-
degree: probably the logger, the config loader, and `__init__.py`.
Useless for onboarding.

**A pure-vector answer.** Returns nodes whose embedding is closest
to the literal string "where should I start reading." Returns
nothing useful — there are no comments in production code that say
"start here."

**The hybrid answer, with `discovery` weighting biased toward
`face`-cluster diversity.** Walks the top of the `z` (PageRank)
distribution, then within that walks down by `face` so the result
set has one representative from each role bucket:

```
A good 30-minute reading tour of this codebase:

  Entry points        face=1     main.py::cli_main, web/server.py::create_app
  Hubs                face=2     ProjectManager.reprocess, GraphBuilder.build
  Storage layer       face=3     storage/cblite_backend.py::save_diff
  Utilities           face=4     spatial.py::SpatialMapper.compute_all
  Configuration       face=5     cblite_config.json + apollo/settings.py
  Tests (smoke)       face=6     tests/test_end_to_end.py

  Read in that order — the file tree and call graph will make
  sense by the time you hit storage.
```

**Why this matters in plain language.** Onboarding is the canonical
query that *no single retriever* answers, because the question is
implicitly "give me one of each *kind* of thing." The `face` axis
is what makes that a one-line operator: bucket the top of the
PageRank distribution by role, return one per bucket. Neither raw
graph nor raw vectors can do this — they don't know about roles.

---

## 4. Failure modes — when combining the two **degrades** results

The operator is not a free lunch. The following cases produce
*worse* answers than either single view alone if used unchecked.

### 4.1 Embedding collapse near boilerplate

Auto-generated code (`migrations/`, protobuf stubs, `__init__.py`
re-exports, licence headers) clusters in a small region of `x` because
the embedding model has nothing distinctive to encode. Empirically, a
repo with 2 000 protobuf-generated files can place 30 % of all nodes
within a 10° arc on `x`.

**Failure mode:** every candidate appears spatially close to every
other candidate, so `a_x → 1` for *all* `c`. The hybrid score
collapses onto `w_g · g(s, c)` — i.e. graph-only — and the extra
weight budget spent on the spatial terms is wasted (worse: it adds a
near-constant offset that dampens the discriminative power of `g`).

**Mitigation:** compute a per-node `x_variance` over its
embedding-K-nearest-neighbours at index time; if `x_variance < ε`,
flag the node as "generic" and zero out `w_x` for that pair.

### 4.2 Layer-depth compression in deep architectures

`compute_y` normalises BFS depth to `[0, 360]` by dividing by
`max_depth`. In a flat script (`max_depth = 3`), one architectural
layer ≈ 120° of `y`. In a hexagonal architecture
(`max_depth = 12`), one layer ≈ 30°.

**Failure mode:** in deep stacks, `a_y` falsely reports "same layer"
for nodes that are actually 2–3 layers apart, because |Δy|/360 stays
small relative to the threshold. The operator over-promotes cross-
layer calls (e.g. a controller calling a repository directly,
bypassing the service layer — exactly the bug you want to *catch*,
not hide).

**Mitigation:** keep `compute_y` un-normalised internally and only
normalise at the consumer; use **layer-equality** (`⌊y · L⌋ == ⌊y_s · L⌋`
for `L` = inferred layer count) instead of distance for `a_y` in deep
repos.

### 4.3 Polyglot parser blind spots

Apollo's parser plugins are per-language. A TypeScript file that
calls a Python FastAPI endpoint produces no `calls` edge in the
graph. The spatial view, however, sees the two as semantically close
(they handle the same domain). The disagreement matrix dutifully
labels the pair as `HIDDEN-SIMILAR` and offers it as a discovery
suggestion.

**Failure mode:** the user is told "these are unconnected but
similar — consider linking them" when in fact they *are* linked, just
through a transport the parser cannot follow.

**Mitigation:** before reporting a `HIDDEN-SIMILAR` pair, check
whether the two nodes live in *different language plugins*. If so,
downgrade the suggestion from "missing link" to "cross-language
candidate; verify manually." Long-term: add an HTTP-route plugin that
emits synthetic edges across language boundaries.

### 4.4 The seed-is-an-outlier trap

If the seed itself is in a sparse spatial region (e.g. the only
Markdown file in a Python repo), every candidate is spatially
"distant" from it. `a_x`, `a_y`, `a_f` all collapse to ≈ 0 and the
ranking falls back onto `g` and `z`. This *isn't* a bug per se, but
it silently changes the operator's behaviour — users may believe
they are getting hybrid results when they are getting graph + prior.

**Mitigation:** report the effective weights `(w_g · ḡ, w_x · āₓ,
…)` averaged over the result set; surface the seed-is-outlier case
explicitly in the API response.

### 4.5 Score inflation under high `w_z`

`a_z(c) = z(c)` is a **prior**, not an agreement — it doesn't depend
on `s` at all. Setting `w_z` too high causes the same set of
high-PageRank hubs (config loader, logger, `__init__`) to dominate
*every* query.

**Mitigation:** cap `w_z ≤ 0.15` by default; document that `w_z` is a
tie-breaker among otherwise-equivalent candidates, not a primary
signal.

---

## 5. Functional-programming formulation

The operator factors cleanly into pure functions with no shared
state. Everything below operates on the in-memory `nx.DiGraph` that
`compute_all` already annotates with `spatial` payloads.

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Iterable
import networkx as nx


# ─── primitives ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Weights:
    g: float = 0.40   # graph proximity
    x: float = 0.20   # semantic
    y: float = 0.15   # architectural layer
    f: float = 0.15   # role
    z: float = 0.10   # importance prior

    def normalised(self) -> "Weights":
        s = self.g + self.x + self.y + self.f + self.z
        return Weights(self.g/s, self.x/s, self.y/s, self.f/s, self.z/s)


def circular_agreement(a: float, b: float, period: float = 360.0) -> float:
    """1.0 when identical, 0.0 when opposite — wraparound aware."""
    d = abs(a - b) % period
    d = min(d, period - d)
    return 1.0 - d / (period / 2)


def linear_agreement(a: float, b: float, span: float = 360.0) -> float:
    return max(0.0, 1.0 - abs(a - b) / span)


# ─── per-axis pure scorers ───────────────────────────────────────────

def graph_proximity(hops: int) -> float:
    return 1.0 / (1.0 + hops)


def axis_x(s: dict, c: dict) -> float:
    return circular_agreement(s["x"], c["x"])

def axis_y(s: dict, c: dict) -> float:
    return linear_agreement(s["y"], c["y"])

def axis_face(s: dict, c: dict) -> float:
    return 1.0 if s["face"] == c["face"] else 0.0

def axis_z(c: dict) -> float:
    return c["z"]


# ─── composition ─────────────────────────────────────────────────────

def hybrid_score(
    seed_spatial: dict,
    cand_spatial: dict,
    hops: int,
    w: Weights,
) -> float:
    w = w.normalised()
    return (
        w.g * graph_proximity(hops)
      + w.x * axis_x(seed_spatial, cand_spatial)
      + w.y * axis_y(seed_spatial, cand_spatial)
      + w.f * axis_face(seed_spatial, cand_spatial)
      + w.z * axis_z(cand_spatial)
    )


# ─── pipeline ────────────────────────────────────────────────────────

def candidates(graph: nx.DiGraph, seed: str, max_hops: int,
               edge_types: set[str]) -> Iterable[tuple[str, int]]:
    """BFS yielding (node_id, hop_distance) under an edge-type filter."""
    seen = {seed: 0}
    frontier = [seed]
    yield seed, 0
    for depth in range(1, max_hops + 1):
        nxt: list[str] = []
        for n in frontier:
            for succ in graph.successors(n):
                if graph.edges[n, succ].get("type") not in edge_types:
                    continue
                if succ in seen:
                    continue
                seen[succ] = depth
                nxt.append(succ)
                yield succ, depth
        frontier = nxt


def rank(
    graph: nx.DiGraph,
    seed: str,
    *,
    max_hops: int = 3,
    edge_types: set[str] = frozenset({"calls", "imports", "defines"}),
    weights: Weights = Weights(),
) -> list[tuple[str, float]]:
    seed_sp = graph.nodes[seed]["spatial"]
    scored = [
        (c, hybrid_score(seed_sp, graph.nodes[c]["spatial"], hops, weights))
        for c, hops in candidates(graph, seed, max_hops, edge_types)
        if c != seed
    ]
    return sorted(scored, key=lambda t: t[1], reverse=True)


# ─── disagreement matrix as a separate pure function ─────────────────

def quadrant(g_score: float, s_score: float,
             tau_g: float = 0.25, tau_s: float = 0.70) -> str:
    return {
        (True,  True):  "CONFIRMED",
        (True,  False): "STRUCTURAL_ONLY",
        (False, True):  "HIDDEN_SIMILAR",
        (False, False): "UNRELATED",
    }[(g_score >= tau_g, s_score >= tau_s)]
```

**Why this shape**

- **No mutation** — every function returns a new value; the graph is
  read-only input. Makes the operator trivially memoizable and
  testable.
- **Per-axis scorers are first-class.** A user who wants to disable
  the role axis just passes `Weights(f=0)`. A user who wants to add a
  new axis (e.g. recency) writes a new pure scorer and a new weight
  field — no other code changes.
- **Pipeline is a generator.** `candidates(...)` streams; `rank(...)`
  only materialises the BFS frontier. The hot path on a 200 k-node
  graph is bounded by the user's `max_hops`, not by graph size.
- **Quadrant labelling is orthogonal to ranking.** The disagreement
  matrix is its own function, callable independently for the
  discovery / audit surfaces.

---

## 6. Mathematical formulation

### 6.1 The combined score as a convex combination

Let `A = {g, x, y, f, z}` be the axis set. For seed `s` and candidate
`c`, each axis defines an agreement function

```
aᵢ : V × V → [0, 1]    for i ∈ A
```

with weights `wᵢ ≥ 0`, `Σᵢ wᵢ = 1`. The hybrid score is the convex
combination

```
H(s, c) = Σᵢ∈A wᵢ · aᵢ(s, c)            ∈ [0, 1]
```

Because each `aᵢ` is bounded in `[0, 1]` and the weights form a
simplex, `H` is itself bounded in `[0, 1]` and is a *proper convex
combination*, which gives two useful guarantees:

1. **Monotonicity.** If `aⱼ(s, c) ≥ aⱼ(s, c′)` for all `j` then
   `H(s, c) ≥ H(s, c′)`. The ranking respects per-axis improvement.
2. **Interpretability of weights.** `wᵢ` is the fraction of the total
   score budget allocated to axis `i`. A user can read off the
   weighting and reason about it.

### 6.2 Per-axis definitions (formal)

Let `(x_v, y_v, z_v, f_v) = spatial(v)` for `v ∈ V`. Let `d_G(s, c)`
denote shortest-hop distance in the directed subgraph induced by the
selected edge types, with `d_G(s, c) = ∞` if no path exists.

```
g(s, c) =  1 / (1 + d_G(s, c))                          ∈ (0, 1]

aₓ(s, c) = 1 − min(|x_s − x_c|, 360 − |x_s − x_c|) / 180   ∈ [0, 1]

a_y(s, c) = max(0, 1 − |y_s − y_c| / 360)                   ∈ [0, 1]

a_f(s, c) = δ(f_s, f_c)                                     ∈ {0, 1}

a_z(c)    = z_c                                             ∈ [0, 1]
```

where δ is the Kronecker delta.

### 6.3 The disagreement matrix as a joint indicator

Let

```
S(s, c) = (aₓ(s, c) + a_y(s, c) + a_f(s, c)) / 3        # spatial consensus
```

and let `τ_g, τ_s ∈ [0, 1]` be thresholds. The disagreement label is

```
Q(s, c) = (𝟙[g(s, c) ≥ τ_g], 𝟙[S(s, c) ≥ τ_s])
        ∈ { (1,1), (1,0), (0,1), (0,0) }
```

mapped to {CONFIRMED, STRUCTURAL_ONLY, HIDDEN_SIMILAR, UNRELATED}.

### 6.4 Why linear combination is justified — and when it isn't

Linear combination is the right choice when:

- The axes are **conditionally independent given relevance** (a
  reasonable approximation here — parser edges and embeddings are
  produced by independent pipelines).
- The user is willing to **express trade-offs as weights**. The
  intent-based weighting in §16.3 of `DESIGN.md` is exactly this.
- The result is a **ranked list**, not a calibrated probability.
  Linear-combination scores are *ordinally* meaningful but not
  *probabilistically* meaningful.

It is **not** the right choice when:

- Axes are **strongly correlated** (e.g. `g` and `a_y` both reflect
  layered structure in a strictly-layered codebase). In that case the
  weighted sum double-counts the shared signal.
- A **calibrated probability** of relevance is required (e.g. to
  threshold for an alert). Use logistic regression on per-axis
  features with labelled training data instead.
- **Rank fusion** is more appropriate — e.g. Reciprocal Rank Fusion
  (Cormack et al., 2009):
  ```
  RRF(c) = Σ_view 1 / (k + rank_view(c))
  ```
  RRF is robust to score-scale differences across views and is the
  recommended fallback when calibration is impractical.

### 6.5 Cost analysis

For a query from seed `s` with hop budget `N` and average branching
factor `b`:

| Component         | Cost                                |
|-------------------|-------------------------------------|
| BFS               | `O(b^N)` time, `O(b^N)` space       |
| Per-axis scoring  | `O(1)` per candidate (5 axes)       |
| Ranking sort      | `O(|C| log |C|)`                    |
| `compute_all`     | `O(|V| · d_emb)` at *index* time, not query time |

The hybrid operator adds **no query-time overhead beyond the BFS
itself**, because all spatial values are precomputed in `spatial`
payloads.

---

## 7. Threshold selection and weight tuning

Two cheap calibration loops are sufficient:

1. **Threshold sweep on a labelled set.** Pick 20 seed-candidate
   pairs with hand-labelled `RELEVANT / IRRELEVANT`. For each
   `(τ_g, τ_s)` in a 10×10 grid, compute the F1 of `CONFIRMED ∪
   HIDDEN_SIMILAR` against the labels. Pick the F1-maximising
   thresholds. Re-run quarterly.
2. **Weight sweep by intent.** For each of the three named intents
   (`semantic`, `structural`, `discovery`), define 5 representative
   queries with hand-picked top-10 results. Grid-search the 4-simplex
   `(w_g, w_x, w_y, w_f)` (with `w_z` fixed at 0.1) at granularity
   0.1; pick the weight tuple that maximises NDCG@10. The three
   resulting weight tuples become the named presets the JSON DSL
   exposes.

Total labelling effort: ~60 pairs, one afternoon. Re-tune when the
embedding model changes or when a major new language plugin lands.

---

## 8. Evaluation plan

A faithful evaluation requires holding the graph and the embeddings
*fixed* and varying only the ranking function. Proposed metrics:

| Metric              | Measures                                                       | Baseline                |
|---------------------|----------------------------------------------------------------|-------------------------|
| NDCG@10             | Ranking quality vs. labelled relevance                         | Graph-only, semantic-only |
| HIDDEN_SIMILAR precision@k | How many flagged pairs are real missing links             | n/a (new surface)       |
| STRUCTURAL_ONLY recall on injected bugs | Inject stale edges → does the operator flag them | n/a (new surface)       |
| Query-time p50/p99  | Latency overhead vs. graph-only                                | Graph-only              |

Datasets: this repo (~50 k nodes), the cpython repo (~400 k nodes as
a polyglot stress test for §4.3), and a synthetic boilerplate-heavy
repo (10 k protobuf-generated nodes) to exercise §4.1.

### 8.1 Concrete ablation plan

The novelty claims in §1.5 are testable. A minimum-viable ablation
should hold the candidate set fixed and vary only the ranking
function:

| Configuration | Operator | What it isolates |
|---|---|---|
| **A — graph-only** | `H = g(s, c)` | Baseline; pure BFS by hop distance. |
| **B — spatial-only** | `H = (a_x + a_y + a_f) / 3` | Baseline; pure embedding-style retrieval. |
| **C — RRF fusion** | `RRF(A, B)` per §6.4 | Generic fusion strawman from the RAG literature. |
| **D — hybrid (this paper)** | Full `H(s, c)` with default weights | The contribution. |
| **E — hybrid + intent presets** | D with `semantic` / `structural` / `discovery` presets | Tests whether per-intent reweighting actually helps. |
| **F — hybrid + disagreement** | D plus surfacing `STRUCTURAL_ONLY` and `HIDDEN_SIMILAR` quadrants | Tests whether disagreement-as-output is independently valuable. |

For each configuration, measure NDCG@10 on the labelled query set,
plus the §4.x failure-mode-specific harnesses (boilerplate-collapse,
deep-stack compression). The four claims in §1.5 each correspond
to a specific A↔B / C↔D / D↔E / D↔F comparison; the table above is
designed so every claim has its own falsifier.

---

## 9. Related work

Hybrid graph-plus-vector retrieval is an active area in 2024–2025
RAG research. This section maps the most directly comparable work
and is candid about what Apollo borrows, what it differs on, and
what (per §1.5) is genuinely new.

### 9.1 RAG hybrids (closest in spirit, different domain)

- **HybridRAG** (Sarmah et al., arXiv:2408.04948, 2024) and the
  larger **GraphRAG / VectorRAG** family. Combine a knowledge graph
  with a vector index, retrieve from both, concatenate or rank-fuse
  the contexts before handing them to an LLM. Domain is typically
  enterprise documents, finance transcripts, or general Q&A.
  **Difference:** Apollo applies the same family of ideas to
  *parser-derived code knowledge graphs*, exposes a per-axis
  weighted operator at query time (not a black-box concatenation),
  and treats disagreement as a first-class output rather than just
  better recall.
- **Reciprocal Rank Fusion** (Cormack, Clarke, Büttcher, 2009).
  The rank-only alternative to score combination. §6.4 names RRF as
  the principled fallback when calibration is impractical; Apollo
  uses the score-combination form because the per-axis scores are
  bounded in `[0, 1]` and conditionally near-independent, which is
  exactly when linear combination is appropriate.

### 9.2 Code-specific graph + embedding work

- **CodeBERT, GraphCodeBERT, UniXcoder.** Combine token-level and
  data-flow signals at *embedding training time*. Produce a single
  embedding that bakes both signals together.
  **Difference:** these models *make* embeddings; Apollo composes
  *on top of* whichever embedding model is in use. The per-axis
  decomposition lets a developer reweight contributions per query
  — something a baked-in embedding cannot do.
- **Caller-graph GCNs, hyperbolic code embeddings.** Use graph
  neural networks over call graphs to produce semantic
  representations. **Difference:** they produce one richer
  embedding; they do not formalise the disagreement between graph
  and embedding as a signal.

### 9.3 Code-intelligence systems

- **Sourcegraph code intelligence** (LSIF / SCIP + fuzzy search).
  Maintains a precise structural index alongside a fuzzy search
  layer; the user picks one or the other.
  **Difference:** the split is real, but there is no explicit
  consensus operator, no per-axis decomposition of the fuzzy side,
  and no first-class disagreement matrix. A `STRUCTURAL_ONLY` hub or
  a `HIDDEN_SIMILAR` pair is not a Sourcegraph concept.

  **Patent landscape (as of 2026-05).** Sourcegraph holds one U.S.
  patent in the code-intelligence space:

  > **US 9,753,723 B2** — *"Systems and methods for generating,
  > linking, and presenting computer code representations from
  > diverse sources"* (Slack & Liu; assigned to Sourcegraph, Inc.;
  > filed 2014-09-05, granted 2017-09-05; priority 2013-09-06).

  Per its abstract, the claims cover the *index-construction*
  pipeline: building a language-specific representation of code
  structure, augmenting it with inferred information, and mapping
  the language-specific components into a language-independent
  representation. This is the conceptual ancestor of the LSIF and
  SCIP indexing formats.

  **What that patent does *not* cover, and why it does not affect
  the contribution in this paper:** the patent is about *building
  an index*. It does not claim a hybrid retrieval operator, a
  weighted multi-axis consensus score, embeddings or PageRank on
  code, or a disagreement matrix between two retrieval views.
  Apollo's contribution sits one layer above any precise index —
  it would compose equally well over an LSIF/SCIP backend, a
  custom AST graph, or any other source of `calls`/`imports`
  edges. The two artefacts are orthogonal: Sourcegraph's patent is
  *what you index with*; this paper is *how you re-rank and
  cross-check what came out of the index*.

  The SCIP format itself is published as open Protobuf under the
  Apache 2.0 licence ([github.com/sourcegraph/scip](https://github.com/sourcegraph/scip)),
  which carries an implicit patent grant for essential claims in
  the contributed implementation — separate from US 9,753,723's
  scope, but useful context for downstream adopters.

- **Glean (Meta), Kythe (Google).** Internal code-intelligence
  graphs at large scale. No published patents on the
  hybrid-retrieval problem this paper addresses. Same structural
  observation as Sourcegraph: precise graphs without a paired
  continuous view, no consensus operator.

### 9.4 Knowledge-management tools

- **Obsidian's graph view.** Surfaces hand-authored links and an
  "unlinked mentions" panel for documents that mention a note's
  title but don't link to it. **The unlinked-mentions panel is the
  closest analogue to the `HIDDEN_SIMILAR` quadrant** in any
  shipping tool the author is aware of, but it is informal,
  document-level (not code-graph), and based on string matching
  rather than a learned spatial view.

### 9.5 Late-interaction re-rankers

- **ColBERT, MonoT5, cross-encoder rerankers.** Re-rank a candidate
  set with a heavier model.
  **Difference:** these add a model call per candidate and produce
  a single opaque score. The Apollo operator is `O(|C|)` over
  pre-computed payloads, runs with zero model calls at query time,
  and produces an inspectable decomposition `(w_g · g, w_x · a_x,
  …)` that a user can debug.

### 9.6 Summary table

| System / paper | Two views? | Per-axis weights at query time? | Disagreement as first-class output? | Code-graph specific? | Published patent on *this* layer? |
|---|:---:|:---:|:---:|:---:|:---:|
| HybridRAG (2024) | yes | no (concat / RRF) | no | no | none found |
| RRF (Cormack 2009) | yes | rank-only | no | no | n/a (academic) |
| GraphCodeBERT | baked in | n/a | no | yes | none found |
| Sourcegraph | yes | no | no | yes | US 9,753,723 — *index construction only*, not retrieval/re-ranking |
| Glean / Kythe | yes (graph only) | no | no | yes | none found |
| Obsidian | yes (informal) | no | partial (unlinked mentions) | no | none found |
| ColBERT / cross-encoders | no | n/a | no | no | n/a (academic) |
| **Apollo (this paper)** | **yes** | **yes (5 axes)** | **yes (2×2 quadrants)** | **yes** | none — published as defensive prior art (see header) |

---

## 10. Summary

The graph and spatial views in Apollo are already produced and
already stored. The contribution of this paper is not a new index or
a new model — it is the recognition that

> *agreement between the two existing views is itself a signal,
> and disagreement between them is itself a signal of a different kind.*

A single pure function (`hybrid_score`) and a single pure labelling
function (`quadrant`) capture both signals, fit in one file, add no
query-time overhead beyond the existing BFS, and expose a small,
honest set of failure modes (§4) that callers can defend against.

The recommended landing sequence is:

1. Land `hybrid_score` / `quadrant` as a pure module behind the
   existing `search_graph_by_keyphrase` and `find_outliers` tools,
   gated by a `rerank` parameter that defaults to `"hybrid"`.
2. Add the `HIDDEN_SIMILAR` quadrant to the chat retrieval surface as
   an explicit "you might also want to look at…" list.
3. Run the §7 calibration loop on this repo before turning the
   `discovery` intent preset on by default.
4. Revisit §4.3 mitigation once a second non-Python plugin lands and
   the polyglot blind spot becomes measurable.
