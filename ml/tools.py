# SPDX-License-Identifier: BUSL-1.1
"""Query-time chat tools — pure reads off the ML payload.

Every tool returns a TOON-friendly uniform-array payload (matching the
constraint in `PLAN_MORE_LOCAL_AI_FUNCTIONS.md` §3.1) so the LLM gets
header-once CSV-style rows.

When the underlying ML pass never ran (or its lib was missing), tools
return ``{"ml_available": false, "reason": "<why>"}`` rather than
raising. The chat agent treats that as a graceful skip.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

import networkx as nx


# ─────────────────────────── Phase 1 tools ───────────────────────────

def list_clusters(graph: nx.DiGraph, top: int = 50) -> dict:
    """List auto-discovered code clusters (HDBSCAN on UMAP coords)."""
    summary = graph.graph.get("ml_clusters")
    if not summary:
        # Fallback: rebuild from per-node `cluster_id` if the sidecar
        # was lost (e.g. an older index built before the storage layer
        # persisted graph-level attrs).
        members: dict[int, list[str]] = defaultdict(list)
        for nid, data in graph.nodes(data=True):
            cid = data.get("cluster_id")
            if cid is None:
                continue
            members[int(cid)].append(nid)
        if not members:
            return {"ml_available": False,
                    "reason": "no clusters on this index — re-run "
                              "`python main.py index <dir>` (requires "
                              "umap-learn + hdbscan)",
                    "clusters": []}
        summary = {}
        for cid, mids in members.items():
            token_counts: dict[str, int] = defaultdict(int)
            for mid in mids[:200]:
                name = (graph.nodes[mid].get("name") or "").lower()
                for tok in name.replace("_", " ").replace(".", " ").split():
                    if len(tok) > 2:
                        token_counts[tok] += 1
            label = (max(token_counts.items(), key=lambda kv: kv[1])[0]
                     if token_counts else f"cluster_{cid}")
            summary[int(cid)] = {
                "id": int(cid),
                "label": label,
                "size": len(mids),
                "representatives": mids[:5],
            }
    rows = sorted(
        (c for c in summary.values() if c.get("id", -1) >= 0),
        key=lambda c: c.get("size", 0), reverse=True,
    )[: max(1, int(top))]
    return {"ml_available": True, "clusters": rows, "total": len(summary)}


def get_cluster_members(graph: nx.DiGraph, cluster_id: int,
                        top: int = 50) -> dict:
    """Return members of a single cluster (capped at ``top``)."""
    cid = int(cluster_id)
    members: list[dict] = []
    for nid, data in graph.nodes(data=True):
        if int(data.get("cluster_id", -2)) != cid:
            continue
        members.append({
            "id": nid,
            "name": data.get("name"),
            "type": data.get("type"),
            "path": data.get("path"),
            "line_start": data.get("line_start"),
            "pagerank": data.get("pagerank"),
        })
        if len(members) >= top:
            break
    if not members:
        return {"ml_available": True, "cluster_id": cid, "members": [],
                "note": "no members or cluster_id unknown"}
    members.sort(key=lambda m: (m.get("pagerank") or 0.0), reverse=True)
    return {"ml_available": True, "cluster_id": cid,
            "members": members, "count": len(members)}


def get_node_importance(graph: nx.DiGraph, node_id: str) -> dict:
    """PageRank + betweenness + degree percentile + 1-line interpretation."""
    if node_id not in graph:
        return {"ml_available": True, "error": f"unknown node: {node_id}"}
    data = graph.nodes[node_id]
    pr = data.get("pagerank")
    bc = data.get("betweenness")
    if pr is None and bc is None:
        return {"ml_available": False,
                "reason": "centrality pass never ran on this index — "
                          "re-run `python main.py <dir>` (uses NetworkX, no extra deps)"}

    # Compute percentile of this node's pagerank vs the rest.
    pr_vals = [d.get("pagerank") or 0.0 for _, d in graph.nodes(data=True)]
    pr_vals.sort()
    pr_v = float(pr or 0.0)
    rank = sum(1 for v in pr_vals if v < pr_v)
    pct = 100.0 * rank / max(1, len(pr_vals))

    if pct >= 99:
        verdict = "top 1% — central hub"
    elif pct >= 95:
        verdict = "top 5% — important"
    elif pct >= 75:
        verdict = "top quartile — well-referenced"
    elif pct <= 5:
        verdict = "bottom 5% — rarely referenced"
    else:
        verdict = "ordinary"

    return {
        "ml_available": True,
        "node_id": node_id,
        "name": data.get("name"),
        "type": data.get("type"),
        "pagerank": float(pr or 0.0),
        "betweenness": float(bc or 0.0),
        "in_degree": int(data.get("in_degree") or graph.in_degree(node_id)),
        "out_degree": int(data.get("out_degree") or graph.out_degree(node_id)),
        "pagerank_percentile": round(pct, 1),
        "verdict": verdict,
    }


def search_graph_by_keyphrase(graph: nx.DiGraph, query: str,
                               top: int = 10) -> dict:
    """Fuzzy match against precomputed KeyBERT keyphrases per node.

    Catches synonyms (rate-limit / throttle / quota) that name-only
    text search misses.
    """
    q = (query or "").strip().lower()
    if not q:
        return {"ml_available": True, "results": [],
                "note": "empty query"}
    has_any = False
    scored: list[tuple[float, dict]] = []
    q_tokens = set(t for t in q.replace("_", " ").replace("-", " ").split()
                    if t)
    for nid, data in graph.nodes(data=True):
        phrases = data.get("keyphrases") or []
        if not phrases:
            continue
        has_any = True
        score = 0.0
        for ph in phrases:
            ph_low = str(ph).lower()
            if q == ph_low:
                score += 3.0
            elif q in ph_low:
                score += 1.5
            else:
                ph_tokens = set(ph_low.replace("_", " ")
                                       .replace("-", " ").split())
                overlap = len(q_tokens & ph_tokens)
                if overlap:
                    score += 0.5 * overlap
        if score > 0:
            scored.append((score, {
                "id": nid,
                "name": data.get("name"),
                "type": data.get("type"),
                "path": data.get("path"),
                "line_start": data.get("line_start"),
                "matched_keyphrases": [
                    p for p in phrases
                    if q in str(p).lower() or set(
                        str(p).lower().replace("_", " ")
                              .replace("-", " ").split()) & q_tokens
                ],
                "score": round(score, 3),
            }))
    if not has_any:
        return {"ml_available": False,
                "reason": "no keyphrases on this index — re-run `python main.py <dir>` "
                          "(requires keybert)",
                "results": []}
    scored.sort(key=lambda kv: kv[0], reverse=True)
    rows = [r for _, r in scored[: max(1, int(top))]]
    return {"ml_available": True, "query": query, "results": rows,
            "total": len(scored)}


# ─────────────────────────── Phase 2 tools ───────────────────────────

def get_community(graph: nx.DiGraph, node_id: str) -> dict:
    """Return Louvain community + (semantic) cluster, plus a smell flag."""
    if node_id not in graph:
        return {"ml_available": True, "error": f"unknown node: {node_id}"}
    data = graph.nodes[node_id]
    cid = data.get("community_id")
    cl = data.get("cluster_id")
    if cid is None and cl is None:
        return {"ml_available": False,
                "reason": "community / cluster passes never ran on this index — "
                          "re-run `python main.py <dir>` (community uses python-louvain "
                          "or NetworkX fallback; cluster uses umap-learn + hdbscan)"}
    # Members of the same community.
    sib_ids = [nid for nid, d in graph.nodes(data=True)
                if d.get("community_id") == cid][:50]
    notes: list[str] = []
    if cid is not None and cl is not None and cl >= 0:
        # Are all siblings in the same semantic cluster?
        sib_clusters = {graph.nodes[s].get("cluster_id") for s in sib_ids}
        if len(sib_clusters) > 1:
            notes.append(
                "community spans multiple semantic clusters → likely "
                "architectural smell"
            )
        else:
            notes.append("community + cluster agree → cohesive module")
    return {
        "ml_available": True,
        "node_id": node_id,
        "community_id": int(cid) if cid is not None else None,
        "cluster_id": int(cl) if cl is not None else None,
        "community_size": len(sib_ids),
        "members_sample": sib_ids[:10],
        "notes": notes,
    }


# ─────────────────────────── Phase 3 tools ───────────────────────────

def find_outliers(graph: nx.DiGraph, top: int = 20,
                  kind: str = "function") -> dict:
    """Return top-N most anomalous nodes by IsolationForest score."""
    rows: list[dict] = []
    valid_kinds = {kind} if kind != "any" else {"function", "method", "class"}
    has_any = False
    for nid, data in graph.nodes(data=True):
        if data.get("type") not in valid_kinds:
            continue
        if "outlier_score" not in data:
            continue
        has_any = True
        rows.append({
            "id": nid,
            "name": data.get("name"),
            "type": data.get("type"),
            "path": data.get("path"),
            "line_start": data.get("line_start"),
            "outlier_score": float(data.get("outlier_score") or 0.0),
            "reason": data.get("outlier_reason") or "",
        })
    if not has_any:
        return {"ml_available": False,
                "reason": "outlier pass never ran on this index — "
                          "re-run `python main.py <dir>` (requires scikit-learn)",
                "outliers": []}
    rows.sort(key=lambda r: r["outlier_score"])  # lowest = most anomalous
    return {"ml_available": True, "outliers": rows[: max(1, int(top))],
            "total_scored": len(rows)}


def find_dead_code(graph: nx.DiGraph,
                   kind: str = "function") -> dict:
    """High-confidence dead code: vulture flag AND zero in-edges."""
    flagged = graph.graph.get("ml_dead_code")
    if flagged is None:
        return {"ml_available": False,
                "reason": "dead-code pass never ran on this index — "
                          "re-run `python main.py <dir>` (requires vulture)",
                "dead": []}
    if kind and kind != "any":
        rows = [r for r in flagged if r.get("kind") == kind]
    else:
        rows = list(flagged)
    return {"ml_available": True, "dead": rows, "count": len(rows)}


# ─────────────────────────── Phase 4 tools ───────────────────────────

def get_topics(graph: nx.DiGraph, top: int = 20) -> dict:
    """List BERTopic topics (auto-labelled clusters) over node embeddings."""
    summary = graph.graph.get("ml_topics")
    if not summary:
        # Fallback: rebuild from per-node `topic_id` if the sidecar
        # (with auto-labels) was lost on save.
        members: dict[int, list[str]] = defaultdict(list)
        for nid, data in graph.nodes(data=True):
            tid = data.get("topic_id")
            if tid is None:
                continue
            members[int(tid)].append(nid)
        if not members:
            return {"ml_available": False,
                    "reason": "topics pass never ran on this index — "
                              "re-run `python main.py index <dir>` "
                              "(requires bertopic)",
                    "topics": []}
        summary = {}
        for tid, mids in members.items():
            token_counts: dict[str, int] = defaultdict(int)
            for mid in mids[:200]:
                name = (graph.nodes[mid].get("name") or "").lower()
                for tok in name.replace("_", " ").replace(".", " ").split():
                    if len(tok) > 2:
                        token_counts[tok] += 1
            top_kw = [w for w, _ in sorted(token_counts.items(),
                                             key=lambda kv: kv[1],
                                             reverse=True)[:8]]
            summary[int(tid)] = {
                "id": int(tid),
                "label": top_kw[0] if top_kw else f"topic_{tid}",
                "keywords": top_kw,
                "size": len(mids),
                "representatives": mids[:5],
            }
    rows = sorted(
        (t for t in summary.values() if t.get("id", -1) >= 0),
        key=lambda t: t.get("size", 0), reverse=True,
    )[: max(1, int(top))]
    return {"ml_available": True, "topics": rows, "total": len(summary)}
