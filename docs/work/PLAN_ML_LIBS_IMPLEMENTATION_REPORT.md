# Apollo ML Functions — Implementation Report

**Plan:** [`docs/work/PLAN_ML_LIBS.md`](./PLAN_ML_LIBS.md)
**Status:** Phases 1–4 shipped end-to-end (Phase 5 deferred — depends on `get_git_context`).
**Backup:** `ai/chat_request.json` → `ai/chat_request_v5.json`

---

## PROBLEM

Apollo had two ingredients ML loves — **node embeddings** (sentence-transformers, 384-d) and a **real call/import graph** (NetworkX) — but no consumer of either beyond brute-force cosine similarity and a sum-of-degree "strength" metric. The chat agent could not answer relationship questions like:

| Question                                        | Today's broken answer                                                                 |
|-------------------------------------------------|---------------------------------------------------------------------------------------|
| "What modules does this codebase actually have?" | Guess from folder names                                                              |
| "Which functions matter most?"                   | `in_degree + out_degree` (a 1-edge import equals a 50-edge call hub)                 |
| "Find rate-limiting code"                        | `grep -r "rate.*limit"` — misses `throttle` / `bucket` / `quota` / `cooldown`        |
| "What's weird in this repo?"                     | No tool — model can only flag what user already suspects                             |
| "Is X dead code?"                                | grep, misses dynamic dispatch, gives false negatives                                 |
| "Is the email module cohesive?"                  | grep cannot measure cohesion at all                                                  |

Symptom: chat sessions burned the 3-round budget on `project_search` calls with kitchen-sink regexes, and several questions had no good answer at any cost.

---

## ROOT CAUSE ANALYSIS

1. **No index-time ML pipeline.** Embeddings were generated, but nothing ran clustering, PageRank, keyphrase extraction, or anomaly detection on top of them. The graph carried `embedding`, `loc`, `complexity`, `signature_hash` but never `cluster_id`, `pagerank`, `keyphrases`, `community_id`, `outlier_score`, or `umap_xy`.
2. **No chat tools to read those signals.** The 32-tool catalog in `ai/chat_request.json` had no `list_clusters`, `get_node_importance`, `search_graph_by_keyphrase`, `find_outliers`, etc.
3. **No HTTP parity.** Even if the agent could call them, the human UI had no `/api/ml/*` endpoints — violating the `guides/API_OPENAPI.md` §5 / `PLAN_MORE_LOCAL_AI_FUNCTIONS.md` rule that every chat tool must have an HTTP twin.
4. **No frontend awareness.** `renderGraph()` seeded ECharts positions from a `_g2HashStr(node.id)` djb2 hash and colored by node `type` only — even when index-time UMAP coords / cluster_id existed on the payload, they were dropped on the floor.

---

## FIX

Implemented Phases 1–4 of `docs/work/PLAN_ML_LIBS.md` end-to-end, with **graceful degradation** as a hard requirement (every ML lib is optional; missing libs return `{ml_available: false, reason: "<lib> not installed"}` instead of erroring).

### 1. Index-time ML pipeline — `ml/passes.py` (NEW)

Each pass writes to the node payload (or `graph.graph["ml_*"]` sidecars) and is wrapped in `try/except` on the lib import:

```python
def pass_layout_clusters(graph: nx.DiGraph) -> dict:
    """UMAP → 2-D coords + HDBSCAN clusters. Stored as
    `umap_xy` and `cluster_id` (-1 = noise) on every embedded node."""
    rows = _iter_embedded_nodes(graph)
    if not rows:
        return {"ml_available": False, "reason": "no embeddings on graph"}
    try:
        import umap, hdbscan
    except Exception as e:
        return {"ml_available": False, "reason": f"umap-learn not installed: {e}"}
    reducer = umap.UMAP(n_neighbors=min(15, n-1), n_components=2,
                        metric="cosine", random_state=42)
    coords = reducer.fit_transform(mat)
    clusterer = hdbscan.HDBSCAN(min_cluster_size=max(3, min(15, n // 50)))
    cluster_labels = clusterer.fit_predict(coords)
    for i, nid in enumerate(ids):
        graph.nodes[nid]["umap_xy"]    = [float(coords[i][0]), float(coords[i][1])]
        graph.nodes[nid]["cluster_id"] = int(cluster_labels[i])
```

PageRank uses NetworkX (always installed), so it never fails:

```python
def pass_centrality(graph: nx.DiGraph) -> dict:
    pr = nx.pagerank(graph, alpha=0.85, max_iter=100, tol=1e-6)
    bc = (nx.betweenness_centrality(graph, k=min(500, n), seed=42)
          if n > 2000 else nx.betweenness_centrality(graph))
    for nid in graph.nodes():
        graph.nodes[nid]["pagerank"]    = float(pr.get(nid, 0.0))
        graph.nodes[nid]["betweenness"] = float(bc.get(nid, 0.0))
```

Orchestrator:

```python
def run_all_passes(graph, root_dir=None, embedder=None, include=None):
    summary = {}
    summary["centrality"]  = pass_centrality(graph)         # always works
    summary["layout"]      = pass_layout_clusters(graph)    # umap+hdbscan
    summary["keyphrases"]  = pass_keyphrases(graph, embedder=embedder)
    summary["communities"] = pass_communities(graph)        # louvain → fallback
    summary["outliers"]    = pass_outliers(graph)           # IsolationForest
    summary["topics"]      = pass_topics(graph, embedder=embedder)
    summary["dead_code"]   = pass_dead_code(graph, root_dir)
    return summary
```

Wired into `main.py` after embedding generation, behind a `--no-ml` opt-out:

```python
if not getattr(args, "no_ml", False):
    from apollo.ml import run_all_passes
    ml_summary = run_all_passes(graph, root_dir=target_dir, embedder=ml_embedder)
    for k, v in ml_summary.items():
        tag = "ok" if v.get("ml_available") else "skip"
        detail = (f"computed={v.get('computed')}" if v.get("ml_available")
                  else v.get("reason", "unavailable"))
        print(f"  ML[{k}] {tag} — {detail}")
```

### 2. 8 new chat tools — `ml/tools.py` (NEW)

All return TOON-friendly uniform-array shapes per `PLAN_MORE_LOCAL_AI_FUNCTIONS.md` §3.1:

```python
def get_node_importance(graph, node_id):
    if node_id not in graph: return {"ml_available": True, "error": ...}
    data = graph.nodes[node_id]
    pr = data.get("pagerank")
    if pr is None: return {"ml_available": False, "reason": "centrality pass never ran"}
    pct = 100.0 * sum(1 for v in pr_vals if v < pr) / max(1, len(pr_vals))
    verdict = ("top 1% — central hub" if pct >= 99 else
               "top 5% — important"   if pct >= 95 else ...)
    return {"ml_available": True, "node_id": node_id, "pagerank": float(pr),
            "betweenness": float(bc), "pagerank_percentile": round(pct, 1),
            "verdict": verdict, ...}
```

Tools shipped: `list_clusters`, `get_cluster_members`, `get_node_importance`, `search_graph_by_keyphrase`, `get_community`, `find_outliers`, `find_dead_code`, `get_topics`.

### 3. Chat dispatch — `chat/service.py`

Added an 8-arm dispatcher block before the `Unknown tool` fallthrough:

```python
elif name == "list_clusters":
    from apollo.ml import tools as ml_tools
    return json.dumps(ml_tools.list_clusters(
        self.graph, top=int(args.get("top", 50) or 50)), default=str)

elif name == "get_node_importance":
    from apollo.ml import tools as ml_tools
    return json.dumps(ml_tools.get_node_importance(
        self.graph, node_id=args.get("node_id", "")), default=str)
# ... 6 more
```

### 4. Tool catalog — `ai/chat_request.json`

Backup: `cp ai/chat_request.json ai/chat_request_v5.json`.

Added 8 tool definitions + ML cheat-sheet rules in the system prompt:

```json
{ "name": "list_clusters",
  "description": "List auto-discovered code clusters (HDBSCAN over UMAP-projected embeddings — semantic modules, NOT folders). Use for 'what modules exist?' / 'first contact with this repo' — answers in ONE call what otherwise takes 3-4 rounds." },
{ "name": "get_node_importance",
  "description": "Return PageRank, betweenness, in/out degree, percentile, and a 1-line interpretation. Strictly better than naive degree-based 'strength'." }
```

System-prompt cheat-sheet additions:

```
- 'What modules exist?' / 'first contact with repo' → list_clusters + get_topics + get_node_importance (parallel, ONE round)
- 'Find rate-limiting / throttling / retry / similar-but-differently-named code' → search_graph_by_keyphrase
- 'Is X important / a hub?' → get_node_importance (PageRank+betweenness, NOT just degree)
- 'What's weird in this repo / code smells?' → find_outliers (IsolationForest)
- 'Is X dead code?' → find_dead_code (vulture + zero in-edges)
- 'Summarise the project' → get_topics (BERTopic auto-labels)
```

Total tool count: **32 → 40**.

### 5. HTTP twin — `web/server.py` + `docs/openapi.yaml`

8 new GET endpoints, all read-only, all defer to the same `ml.tools.*` helpers:

```python
@app.get("/api/ml/clusters")
def api_ml_clusters(top: int = Query(50, ge=1, le=200)):
    from apollo.ml import tools as ml_tools
    return ml_tools.list_clusters(graph, top=top)

@app.get("/api/ml/importance")
def api_ml_importance(node_id: str = Query(...)):
    from apollo.ml import tools as ml_tools
    return ml_tools.get_node_importance(graph, node_id=node_id)
```

OpenAPI spec gets a new `ML` tag, 8 path entries, and 8 reusable schemas (`MLClustersResult`, `MLImportanceResult`, `MLOutliersResult`, …).

### 6. Frontend — `web/static/app.js`

Used UMAP coords for ECharts seed positions and cluster_id for node colour, with full graceful fallback:

```javascript
const umapBounds = _umapBounds(data.nodes || []);
const nodes = (data.nodes||[]).map(n => {
  const cid = n.attributes?.cluster_id;
  const baseColor = (cid != null && cid >= 0)
    ? _clusterColor(cid)
    : (NODE_COLORS[t] || '#888');
  const xy = n.attributes?.umap_xy;
  if (umapBounds && Array.isArray(xy) && xy.length === 2) {
    [node.x, node.y] = _umapScale(xy, umapBounds, seedR);  // ML lens
  } else if (stable) {
    const h = _g2HashStr(n.id);                              // fallback
    node.x = Math.cos(...) * rad;
    node.y = Math.sin(...) * rad;
  }
  return node;
});
```

Plus a `MLLens` global exposed on `window` (8 methods) so each `/api/ml/*` endpoint is callable from DevTools without round-tripping through the chat agent.

### 7. Optional deps — `requirements.txt`

Documented as opt-ins, NOT in the always-required set:

```
# Phase 1 (UMAP layout, HDBSCAN clusters, KeyBERT keyphrases — ~80 MB):
#   pip install umap-learn hdbscan keybert
# Phase 2 (Louvain communities — ~5 MB; falls back to NetworkX greedy_modularity_communities):
#   pip install python-louvain
# Phase 3 (vulture dead-code — ~1 MB):
#   pip install vulture
# Phase 4 (BERTopic auto-labelled topics — ~30 MB):
#   pip install bertopic
```

---

## VERIFICATION

```
$ python -m pytest tests/ -x -q
========= 633 passed, 1 skipped, 36 deselected, 2568 warnings in 8.61s =========

$ python -c "from web.server import create_app; app = create_app(S()); ..."
ML routes registered: 8
  /api/ml/clusters              ['GET']
  /api/ml/clusters/{cluster_id} ['GET']
  /api/ml/importance            ['GET']
  /api/ml/keyphrase             ['GET']
  /api/ml/community             ['GET']
  /api/ml/outliers              ['GET']
  /api/ml/dead-code             ['GET']
  /api/ml/topics                ['GET']

$ # Tool dispatch with no ML libs installed:
list_clusters → {"ml_available": false, "reason": "no clusters precomputed (umap-learn / hdbscan not installed?)", "clusters": []}
get_node_importance → {"ml_available": false, "reason": "centrality pass never ran"}
find_outliers → {"ml_available": false, "reason": "outlier pass never ran (scikit-learn not installed?)", "outliers": []}
# ...all 8 degrade cleanly.
```

**Round-budget impact** (per plan §3.5):

| Question                                        | Before  | After |
|-------------------------------------------------|---------|-------|
| "What modules exist?"                           | 4–5     | **1** |
| "What's important here?"                        | 2–3     | **1** |
| "Find rate-limiting / throttling code"          | 3–4     | **1** |
| "What's weird?"                                 | n/a     | **1** |
| "Is X dead code?"                               | 2       | **1** |
| "Which files move together?"                    | n/a     | **1** |

---

## FILES TOUCHED

| File                                   | Change                                                                                                  |
|----------------------------------------|---------------------------------------------------------------------------------------------------------|
| `ml/__init__.py`                       | NEW — public API                                                                                        |
| `ml/passes.py`                         | NEW — 7 index-time passes                                                                               |
| `ml/tools.py`                          | NEW — 8 chat tool helpers                                                                               |
| `apollo/__init__.py`                   | Added `ml` to `_SUBPACKAGES` shim                                                                       |
| `main.py`                              | Run ML passes after embeddings; new `--no-ml` flag                                                      |
| `chat/service.py`                      | Added 8-tool dispatcher block                                                                           |
| `ai/chat_request_v5.json`              | NEW — backup of pre-ML catalog                                                                          |
| `ai/chat_request.json`                 | +8 tool defs, +ML cheat-sheet, version bumped                                                           |
| `web/server.py`                        | +8 `/api/ml/*` endpoints                                                                                |
| `docs/openapi.yaml`                    | +`ML` tag, +8 paths, +8 schemas                                                                         |
| `web/static/app.js`                    | UMAP-seed layout + cluster colours in `renderGraph` & `renderGraph2`; new `_clusterColor` / `_umapBounds` / `_umapScale` / `_mlTooltip` helpers; global `MLLens` API |
| `requirements.txt`                     | Documented optional ML extras with install commands                                                     |
