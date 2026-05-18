# SPDX-License-Identifier: BUSL-1.1
"""Index-time ML passes — Phase 1-4 of `docs/work/PLAN_ML_LIBS.md`.

Each pass writes its result onto the node payload (or onto a small
per-graph sidecar dict stored at ``graph.graph["ml_*"]``). Chat-time
tools are plain reads. No tool should ever invoke these passes during a
chat round.

Every pass is optional and graceful:

* The library import is wrapped in ``try/except``. Missing deps just
  skip the pass and emit a single warning.
* Passes that depend on embeddings short-circuit when no node has an
  ``embedding`` attribute.
* Determinism: every random-state knob is pinned to ``42`` so re-runs
  are reproducible.
"""
from __future__ import annotations

import logging
import math
import os
from collections import defaultdict
from typing import Iterable, Optional

import networkx as nx

logger = logging.getLogger(__name__)


# ─────────────────────────── helpers ───────────────────────────

def _iter_embedded_nodes(graph: nx.DiGraph) -> list[tuple[str, list[float]]]:
    """Return ``[(node_id, embedding), ...]`` for nodes that carry one.

    Skips ``directory`` and ``import`` nodes — they're structural and
    should not influence the semantic layout / clusters.
    """
    rows: list[tuple[str, list[float]]] = []
    for nid, data in graph.nodes(data=True):
        emb = data.get("embedding")
        if emb is None:
            continue
        if data.get("type") in {"directory", "import"}:
            continue
        rows.append((nid, list(emb)))
    return rows


def _node_text_for_keyphrase(graph: nx.DiGraph, node_id: str, data: dict) -> str:
    """Best-effort text blob for KeyBERT extraction.

    Combines name, docstring, and a leading slice of source so we don't
    blow KeyBERT's input budget on huge functions.

    Phase 4 of PLAN_INDEX_MEMORY_AND_CONCURRENCY: the source slice is
    now resolved via :func:`graph.query.get_source` against the file
    text sidecar instead of reading a per-node ``source`` attr (which
    no longer exists). The function falls through transparently for
    legacy graphs that still carry the old attr.
    """
    from apollo.graph.query import get_source

    parts: list[str] = []
    name = data.get("name") or ""
    if name:
        parts.append(str(name))
    doc = data.get("docstring") or ""
    if doc:
        parts.append(str(doc)[:500])
    src = get_source(graph, node_id)
    if src:
        parts.append(src[:1500])
    return "\n".join(parts).strip()


# ─────────────────────────── Phase 1.1 + 1.2 — UMAP + HDBSCAN ───────────────────────────

def pass_layout_clusters(graph: nx.DiGraph) -> dict:
    """Compute 2-D UMAP coords + HDBSCAN clusters from node embeddings.

    Writes ``umap_xy: [x, y]`` and ``cluster_id: int`` (``-1`` for
    noise) onto every embedded node. Returns a small summary dict
    ``{ml_available, computed, clusters, noise}``.
    """
    rows = _iter_embedded_nodes(graph)
    if not rows:
        return {"ml_available": False, "reason": "no embeddings on graph"}
    try:
        import numpy as np  # noqa: F401
        import umap  # type: ignore
    except Exception as e:
        logger.warning("UMAP unavailable, skipping layout pass: %s", e)
        return {"ml_available": False, "reason": f"umap-learn not installed: {e}"}
    try:
        import hdbscan  # type: ignore
    except Exception as e:
        logger.warning("hdbscan unavailable, skipping cluster pass: %s", e)
        # We still want layout even without clusters.
        hdbscan = None  # type: ignore

    import numpy as np
    ids = [r[0] for r in rows]
    mat = np.asarray([r[1] for r in rows], dtype=np.float32)
    n = len(ids)
    # UMAP needs at least n_neighbors+1 samples. For tiny graphs fall
    # back to a deterministic radial layout so the UI still gets coords.
    if n < 10:
        for i, nid in enumerate(ids):
            ang = (i / max(1, n)) * 2 * math.pi
            graph.nodes[nid]["umap_xy"] = [math.cos(ang), math.sin(ang)]
            graph.nodes[nid]["cluster_id"] = -1
        return {"ml_available": True, "computed": n, "clusters": 0,
                "noise": n, "note": "fallback layout (n<10)"}

    n_neighbors = min(15, max(2, n - 1))
    try:
        reducer = umap.UMAP(n_neighbors=n_neighbors, n_components=2,
                            min_dist=0.1, metric="cosine", random_state=42)
        coords = reducer.fit_transform(mat)
    except Exception as e:
        logger.warning("UMAP fit_transform failed (%s) — using fallback", e)
        coords = None

    if coords is None:
        # Radial fallback so the UI still has something to seed with.
        for i, nid in enumerate(ids):
            ang = (i / n) * 2 * math.pi
            graph.nodes[nid]["umap_xy"] = [math.cos(ang) * 100,
                                            math.sin(ang) * 100]
            graph.nodes[nid]["cluster_id"] = -1
        return {"ml_available": True, "computed": n, "clusters": 0,
                "noise": n, "note": "umap fit failed"}

    for i, nid in enumerate(ids):
        x, y = coords[i]
        graph.nodes[nid]["umap_xy"] = [float(x), float(y)]

    # Cluster on the 2-D coords (cheap, density-based).
    cluster_labels: list[int] = []
    if hdbscan is not None:
        try:
            min_size = max(3, min(15, n // 50))
            clusterer = hdbscan.HDBSCAN(min_cluster_size=min_size,
                                        prediction_data=False)
            cluster_labels = list(clusterer.fit_predict(coords))
        except Exception as e:
            logger.warning("HDBSCAN failed (%s) — clusters skipped", e)
            cluster_labels = [-1] * n
    else:
        cluster_labels = [-1] * n

    n_clusters = len({c for c in cluster_labels if c >= 0})
    n_noise = sum(1 for c in cluster_labels if c < 0)
    for i, nid in enumerate(ids):
        graph.nodes[nid]["cluster_id"] = int(cluster_labels[i])

    # Per-cluster summary stored on the graph object so `list_clusters`
    # can read it without rescanning every node every call.
    cluster_summary: dict[int, dict] = {}
    members: dict[int, list[str]] = defaultdict(list)
    for nid, cid in zip(ids, cluster_labels):
        members[int(cid)].append(nid)
    for cid, mids in members.items():
        # Pick a label = most common name token among members.
        token_counts: dict[str, int] = defaultdict(int)
        for mid in mids[:200]:
            name = (graph.nodes[mid].get("name") or "").lower()
            for tok in name.replace("_", " ").replace(".", " ").split():
                if len(tok) > 2:
                    token_counts[tok] += 1
        label = max(token_counts.items(), key=lambda kv: kv[1])[0] \
            if token_counts else f"cluster_{cid}"
        cluster_summary[int(cid)] = {
            "id": int(cid),
            "label": label,
            "size": len(mids),
            "representatives": mids[:5],
        }
    graph.graph["ml_clusters"] = cluster_summary

    return {"ml_available": True, "computed": n, "clusters": n_clusters,
            "noise": n_noise}


# ─────────────────────────── Phase 1.3 + 2.2 — PageRank + betweenness ───────

def pass_centrality(graph: nx.DiGraph) -> dict:
    """Write ``pagerank``, ``betweenness``, ``in_degree``, ``out_degree``.

    NetworkX ships in core deps — this pass never fails.
    """
    n = graph.number_of_nodes()
    if n == 0:
        return {"ml_available": True, "computed": 0}
    try:
        pr = nx.pagerank(graph, alpha=0.85, max_iter=100, tol=1e-6)
    except Exception as e:
        logger.warning("PageRank failed: %s — falling back to degree", e)
        pr = {nid: 0.0 for nid in graph.nodes()}
    # Betweenness on huge graphs is expensive (O(V·E)). Use the
    # k-sample approximation when we exceed 2k nodes.
    try:
        if n > 2000:
            bc = nx.betweenness_centrality(graph, k=min(500, n), seed=42,
                                            normalized=True)
        else:
            bc = nx.betweenness_centrality(graph, normalized=True)
    except Exception as e:
        logger.warning("Betweenness failed: %s", e)
        bc = {nid: 0.0 for nid in graph.nodes()}
    for nid in graph.nodes():
        graph.nodes[nid]["pagerank"] = float(pr.get(nid, 0.0))
        graph.nodes[nid]["betweenness"] = float(bc.get(nid, 0.0))
        graph.nodes[nid]["in_degree"] = int(graph.in_degree(nid))
        graph.nodes[nid]["out_degree"] = int(graph.out_degree(nid))
    return {"ml_available": True, "computed": n}


# ─────────────────────────── Phase 1.4 — KeyBERT keyphrases ───────────────────

def pass_keyphrases(graph: nx.DiGraph,
                    embedder=None,
                    top_n: int = 5,
                    max_nodes: int | None = None) -> dict:
    """Extract 3-5 keyphrases per code node using KeyBERT.

    KeyBERT reuses the existing sentence-transformer model when one is
    passed in — no extra model download. Stored as
    ``keyphrases: list[str]`` on the node.
    """
    try:
        from keybert import KeyBERT  # type: ignore
    except Exception as e:
        return {"ml_available": False, "reason": f"keybert not installed: {e}"}
    try:
        if embedder is not None and hasattr(embedder, "model"):
            kw_model = KeyBERT(model=embedder.model)
        else:
            kw_model = KeyBERT()
    except Exception as e:
        logger.warning("KeyBERT init failed: %s", e)
        return {"ml_available": False, "reason": str(e)}

    # Phase 4: eligibility check no longer reads ``data["source"]``
    # (it's gone). Nodes with a non-empty name *or* docstring qualify
    # for the first pass; the real text gating happens below where we
    # build the KeyBERT input via ``_node_text_for_keyphrase`` and
    # require ≥ 20 chars.
    eligible = [
        (nid, data) for nid, data in graph.nodes(data=True)
        if data.get("type") in {"function", "method", "class", "file"}
        and (data.get("docstring") or data.get("name"))
    ]
    if max_nodes:
        eligible = eligible[:max_nodes]

    computed = 0
    for nid, data in eligible:
        text = _node_text_for_keyphrase(graph, nid, data)
        if not text or len(text) < 20:
            continue
        try:
            phrases = kw_model.extract_keywords(
                text,
                keyphrase_ngram_range=(1, 2),
                stop_words="english",
                top_n=top_n,
            )
            graph.nodes[nid]["keyphrases"] = [
                p[0] for p in phrases if isinstance(p, tuple) and p
            ]
            computed += 1
        except Exception:
            continue
    return {"ml_available": True, "computed": computed}


# ─────────────────────────── Phase 2.1 — Louvain communities ───────────

def pass_communities(graph: nx.DiGraph) -> dict:
    """Compute Louvain (or Leiden) communities on the undirected projection.

    Stored as ``community_id: int`` on every node. Falls back to
    networkx's built-in ``greedy_modularity_communities`` if neither
    library is installed (still useful, no new dep).
    """
    if graph.number_of_nodes() == 0:
        return {"ml_available": True, "computed": 0}

    undirected = graph.to_undirected()
    partition: dict[str, int] | None = None

    # Prefer python-louvain (lightweight, fast).
    try:
        import community as community_louvain  # type: ignore
        partition = community_louvain.best_partition(undirected,
                                                      random_state=42)
    except Exception:
        partition = None

    if partition is None:
        # NetworkX-only fallback (no extra dep).
        try:
            from networkx.algorithms.community import greedy_modularity_communities
            comms = list(greedy_modularity_communities(undirected))
            partition = {}
            for cid, members in enumerate(comms):
                for nid in members:
                    partition[nid] = cid
        except Exception as e:
            logger.warning("Community detection failed: %s", e)
            return {"ml_available": False, "reason": str(e)}

    for nid in graph.nodes():
        graph.nodes[nid]["community_id"] = int(partition.get(nid, -1))
    n_communities = len({c for c in partition.values()})
    return {"ml_available": True, "computed": len(partition),
            "communities": n_communities}


# ─────────────────────────── Phase 3.1 — IsolationForest outliers ──────

def pass_outliers(graph: nx.DiGraph) -> dict:
    """Score each function node for "weirdness" via IsolationForest.

    Features: loc, complexity, param_count, in_degree, out_degree,
    embedding magnitude, pagerank. Stored as ``outlier_score`` (lower
    = more anomalous) and ``outlier_reason: str``.
    """
    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest  # type: ignore
    except Exception as e:
        return {"ml_available": False,
                "reason": f"scikit-learn not installed: {e}"}

    candidates: list[tuple[str, list[float]]] = []
    for nid, data in graph.nodes(data=True):
        if data.get("type") not in {"function", "method", "class"}:
            continue
        loc = float(data.get("loc") or 0)
        cx = float(data.get("complexity") or 0)
        params = float(data.get("param_count") or 0)
        ind = float(graph.in_degree(nid))
        outd = float(graph.out_degree(nid))
        emb = data.get("embedding")
        emag = 0.0
        if emb is not None:
            try:
                emag = float(np.linalg.norm(np.asarray(emb, dtype=np.float32)))
            except Exception:
                emag = 0.0
        pr = float(data.get("pagerank") or 0.0)
        candidates.append((nid, [loc, cx, params, ind, outd, emag, pr]))

    if len(candidates) < 10:
        return {"ml_available": True, "computed": 0,
                "note": "not enough nodes for outlier model"}

    ids = [c[0] for c in candidates]
    X = np.asarray([c[1] for c in candidates], dtype=np.float32)
    try:
        clf = IsolationForest(random_state=42, contamination="auto",
                              n_estimators=100)
        clf.fit(X)
        scores = clf.score_samples(X)  # higher = more normal
    except Exception as e:
        logger.warning("IsolationForest failed: %s", e)
        return {"ml_available": False, "reason": str(e)}

    # Compute per-feature percentile thresholds for human-readable reasons.
    medians = np.median(X, axis=0)
    for i, nid in enumerate(ids):
        graph.nodes[nid]["outlier_score"] = float(scores[i])
        loc_v, cx_v, p_v, ind_v, outd_v, _emag, _pr = X[i]
        reasons: list[str] = []
        if loc_v > medians[0] * 5 + 1:
            reasons.append(f"unusually large ({int(loc_v)} loc vs median {int(medians[0])})")
        if cx_v > medians[1] * 5 + 1:
            reasons.append(f"high complexity ({int(cx_v)} vs median {int(medians[1])})")
        if ind_v == 0 and outd_v > medians[4] * 2:
            reasons.append("orphan: 0 callers despite many callees")
        if p_v > medians[2] * 4 + 1:
            reasons.append(f"many parameters ({int(p_v)})")
        graph.nodes[nid]["outlier_reason"] = "; ".join(reasons) or "anomaly"
    return {"ml_available": True, "computed": len(ids)}


# ─────────────────────────── Phase 4.1 — BERTopic topics ───────────────

def pass_topics(graph: nx.DiGraph,
                embedder=None,
                top: int = 20) -> dict:
    """Run BERTopic over node embeddings — TF-IDF labelled topics.

    Stored on the graph object as ``graph.graph["ml_topics"]`` =
    ``{topic_id: {label, keywords, size, representatives}}``.
    """
    rows = _iter_embedded_nodes(graph)
    if len(rows) < 20:
        return {"ml_available": True, "computed": 0,
                "note": "need >=20 embedded nodes"}

    try:
        import numpy as np
        from bertopic import BERTopic  # type: ignore
    except Exception as e:
        return {"ml_available": False,
                "reason": f"bertopic not installed: {e}"}

    ids = [r[0] for r in rows]
    docs = []
    for nid in ids:
        d = graph.nodes[nid]
        # Phase 4: pass graph + node_id so _node_text_for_keyphrase
        # can resolve the source slice via the _file_text sidecar.
        text = _node_text_for_keyphrase(graph, nid, d)
        docs.append(text or (d.get("name") or nid))
    embeddings = np.asarray([r[1] for r in rows], dtype=np.float32)

    try:
        model = BERTopic(verbose=False, nr_topics=top, calculate_probabilities=False)
        topics, _probs = model.fit_transform(docs, embeddings)
    except Exception as e:
        logger.warning("BERTopic failed: %s", e)
        return {"ml_available": False, "reason": str(e)}

    info = model.get_topic_info()
    topic_summary: dict[int, dict] = {}
    members: dict[int, list[str]] = defaultdict(list)
    for nid, t in zip(ids, topics):
        members[int(t)].append(nid)
    for _, row in info.iterrows():
        tid = int(row["Topic"])
        keywords = [w for w, _ in (model.get_topic(tid) or []) if w][:8]
        topic_summary[tid] = {
            "id": tid,
            "label": (row.get("Name") or "").strip() or f"topic_{tid}",
            "keywords": keywords,
            "size": int(row.get("Count") or 0),
            "representatives": members.get(tid, [])[:5],
        }
    graph.graph["ml_topics"] = topic_summary
    # Also stamp topic_id onto each node for cheap lookup.
    for nid, t in zip(ids, topics):
        graph.nodes[nid]["topic_id"] = int(t)
    return {"ml_available": True, "computed": len(ids),
            "topics": len(topic_summary)}


# ─────────────────────────── Phase 3.2 — vulture dead code ─────────────

def pass_dead_code(graph: nx.DiGraph, root_dir: str | None) -> dict:
    """Run ``vulture`` over the project root and cross-validate against
    the graph. A node is high-confidence dead when **both**:

    * vulture flags it
    * the graph has zero ``in`` edges to it

    Result stored on ``graph.graph["ml_dead_code"]`` =
    ``[{path, line, name, kind, why}]``.
    """
    if not root_dir or not os.path.isdir(root_dir):
        return {"ml_available": False, "reason": "no root_dir"}
    try:
        import vulture  # type: ignore
    except Exception as e:
        return {"ml_available": False, "reason": f"vulture not installed: {e}"}

    try:
        v = vulture.Vulture(verbose=False)
        v.scavenge([root_dir])
    except Exception as e:
        logger.warning("vulture scavenge failed: %s", e)
        return {"ml_available": False, "reason": str(e)}

    flagged: list[dict] = []
    # Build a quick name → in_degree lookup over function/class/method.
    name_to_in: dict[str, int] = defaultdict(int)
    for nid, data in graph.nodes(data=True):
        if data.get("type") in {"function", "method", "class"}:
            nm = data.get("name") or ""
            if nm:
                name_to_in[nm] = max(name_to_in[nm],
                                       graph.in_degree(nid))

    for item in (v.get_unused_code() or []):
        nm = getattr(item, "name", "") or ""
        # vulture's `typ` ∈ {"function", "class", "variable", ...}
        kind = getattr(item, "typ", "unknown")
        path = getattr(item, "filename", "") or ""
        line = getattr(item, "first_lineno", 0) or 0
        # Cross-validate: only keep when graph has 0 in-edges.
        if name_to_in.get(nm, 0) > 0:
            continue
        try:
            rel = os.path.relpath(path, root_dir)
        except Exception:
            rel = path
        flagged.append({
            "path": rel,
            "line": int(line),
            "name": nm,
            "kind": kind,
            "why": f"vulture: {kind} unused & graph in_degree=0",
        })
    graph.graph["ml_dead_code"] = flagged
    return {"ml_available": True, "computed": len(flagged)}


# ─────────────────────────── orchestrator ───────────────────────────

def run_all_passes(graph: nx.DiGraph,
                   root_dir: str | None = None,
                   embedder=None,
                   include: Iterable[str] | None = None,
                   parallel: bool = False,
                   max_workers: int = 4) -> dict:
    """Run the index-time ML pipeline.

    ``include`` — optional whitelist of pass names. Defaults to all.
    Names: ``layout, centrality, keyphrases, communities, outliers,
    topics, dead_code``.

    Phase 7 of PLAN_INDEX_MEMORY_AND_CONCURRENCY: when ``parallel``
    is True, the seven passes are fanned out to a
    :class:`concurrent.futures.ThreadPoolExecutor` with at most
    ``max_workers`` workers. Each pass writes to its own attribute
    namespace (per-node ``pagerank`` / ``cluster_id`` / ``keyphrases``
    / ``community_id`` / ``outlier_score`` / ``topic_id`` and per-graph
    ``ml_clusters`` / ``ml_communities`` / ``ml_topics`` /
    ``ml_dead_code``) so concurrent writes don't collide. NetworkX +
    numpy release the GIL for the bulk of their internals, so threads
    are the right pool here (no pickling cost, shared graph object).

    The ``parallel=False`` default preserves byte-for-byte legacy
    behavior — every caller that hasn't opted in still gets the
    sequential ordering pinned by the existing tests.
    """
    summary: dict[str, dict] = {}
    wanted = set(include) if include else {
        "layout", "centrality", "keyphrases", "communities",
        "outliers", "topics", "dead_code",
    }

    if parallel:
        return _run_passes_parallel(
            graph, root_dir, embedder, wanted, max_workers=max_workers,
        )

    if "centrality" in wanted:
        summary["centrality"] = pass_centrality(graph)
    if "layout" in wanted:
        summary["layout"] = pass_layout_clusters(graph)
    if "keyphrases" in wanted:
        summary["keyphrases"] = pass_keyphrases(graph, embedder=embedder)
    if "communities" in wanted:
        summary["communities"] = pass_communities(graph)
    if "outliers" in wanted:
        summary["outliers"] = pass_outliers(graph)
    if "topics" in wanted:
        summary["topics"] = pass_topics(graph, embedder=embedder)
    if "dead_code" in wanted:
        summary["dead_code"] = pass_dead_code(graph, root_dir)
    return summary


def _run_passes_parallel(
    graph: nx.DiGraph,
    root_dir: str | None,
    embedder,
    wanted: set,
    max_workers: int = 4,
) -> dict:
    """Phase 7 of PLAN_INDEX_MEMORY_AND_CONCURRENCY — fanout helper.

    Submits every requested pass to a single ``ThreadPoolExecutor``.
    Passes that fail are recorded as ``{ml_available: False, reason:
    "<exception>"}`` in the returned summary rather than propagating
    (matches the sequential path's per-pass try/except behavior:
    a missing UMAP / KeyBERT / vulture installation must never break
    the whole index, regardless of orchestration mode).
    """
    from concurrent.futures import ThreadPoolExecutor

    tasks: dict[str, callable] = {}
    if "centrality" in wanted:
        tasks["centrality"] = lambda: pass_centrality(graph)
    if "layout" in wanted:
        tasks["layout"] = lambda: pass_layout_clusters(graph)
    if "keyphrases" in wanted:
        tasks["keyphrases"] = lambda: pass_keyphrases(graph, embedder=embedder)
    if "communities" in wanted:
        tasks["communities"] = lambda: pass_communities(graph)
    if "outliers" in wanted:
        tasks["outliers"] = lambda: pass_outliers(graph)
    if "topics" in wanted:
        tasks["topics"] = lambda: pass_topics(graph, embedder=embedder)
    if "dead_code" in wanted:
        tasks["dead_code"] = lambda: pass_dead_code(graph, root_dir)

    if not tasks:
        return {}

    workers = max(1, min(max_workers, len(tasks)))
    summary: dict[str, dict] = {}
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="apollo-ml-pass",
    ) as ex:
        futures = {ex.submit(fn): name for name, fn in tasks.items()}
        for fut in futures:
            name = futures[fut]
            try:
                summary[name] = fut.result()
            except Exception as e:  # noqa: BLE001
                logger.warning("ML pass %s failed: %s", name, e)
                summary[name] = {"ml_available": False, "reason": str(e)}
    return summary
