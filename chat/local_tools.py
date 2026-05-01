"""
Local tool helpers for the chat agent (PLAN_MORE_LOCAL_AI_FUNCTIONS phases 1-4).

Pure functions that take the graph (and, where needed, the indexed `root_dir`)
and return *uniform-shape* dicts. Each helper is reused by:

  - `chat/service.py:_exec_tool_impl` (the AI tool dispatcher)
  - `web/server.py` HTTP endpoints (`/api/nodes/batch`, `/api/files/sections`,
    `/api/paths`, …)

Read-only — never writes, never shells out except for `get_git_context` which
runs `git log` / `git blame` with a hard timeout and returns
`{"git_available": false}` on any failure.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import re
import subprocess
import time
from collections import deque, defaultdict
from pathlib import Path

import networkx as nx

logger = logging.getLogger(__name__)


# ─────────────────────────── shared helpers ───────────────────────────

# Per-range cap for batch_file_sections (plan §1.2).
_BATCH_RANGE_MAX_LINES = 400


def _node_payload(graph: nx.DiGraph, node_id: str,
                  include_source: bool = True,
                  include_edges: bool = True,
                  source_max_chars: int = 2000) -> dict:
    """Build the full node-detail payload (mirrors chat.service get_node)."""
    if node_id not in graph:
        return {"id": node_id, "error": "not_found"}
    data = {k: v for k, v in graph.nodes[node_id].items() if k != "embedding"}
    if not include_source and "source" in data:
        data = {k: v for k, v in data.items() if k != "source"}
    elif include_source and "source" in data:
        src = data.get("source") or ""
        if isinstance(src, str) and len(src) > source_max_chars:
            data = dict(data)
            data["source"] = src[:source_max_chars] + "\n... (truncated)"
    out = {"id": node_id, **data}
    if include_edges:
        edges_in = []
        for pred in graph.predecessors(node_id):
            edata = dict(graph.edges[pred, node_id])
            edges_in.append({"source": pred, "type": edata.get("type", "")})
        edges_out = []
        for succ in graph.successors(node_id):
            edata = dict(graph.edges[node_id, succ])
            edges_out.append({"target": succ, "type": edata.get("type", "")})
        out["edges_in"] = edges_in
        out["edges_out"] = edges_out
    return out


# ─────────────────────────── Phase 1 ───────────────────────────

def batch_get_nodes(graph: nx.DiGraph, node_ids: list[str],
                    include_source: bool = True,
                    include_edges: bool = True) -> dict:
    """Fetch up to 20 node payloads in one call. Plan §1.1.

    Synchronous, in-memory NetworkX path. For an async parallel path
    that reads each node document from Couchbase Lite via
    ``asyncio.to_thread`` + ``asyncio.gather``, see
    :func:`abatch_get_nodes`.
    """
    ids = list(node_ids or [])[:20]
    nodes: list[dict] = []
    missing: list[str] = []
    for nid in ids:
        if nid in graph:
            nodes.append(_node_payload(graph, nid,
                                       include_source=include_source,
                                       include_edges=include_edges))
        else:
            missing.append(nid)
    return {"nodes": nodes, "missing": missing, "requested": len(ids)}


async def abatch_get_nodes(graph: nx.DiGraph, node_ids: list[str],
                           include_source: bool = True,
                           include_edges: bool = True,
                           cbl_store=None) -> dict:
    """Async variant of :func:`batch_get_nodes`.

    When ``cbl_store`` is a :class:`CouchbaseLiteStore`, fetches each
    node's document from CBL in parallel via
    :meth:`CouchbaseLiteStore.aget_node_docs` (which uses
    ``asyncio.to_thread`` + ``asyncio.gather``). Edge topology and any
    fields not present on the CBL doc fall back to the in-memory
    NetworkX graph.

    Without a ``cbl_store`` this is just an awaitable wrapper around
    the synchronous in-memory path so callers can keep one code path.
    """
    ids = list(node_ids or [])[:20]
    if cbl_store is None or not hasattr(cbl_store, "aget_node_docs"):
        return batch_get_nodes(graph, ids,
                               include_source=include_source,
                               include_edges=include_edges)

    cbl_docs = await cbl_store.aget_node_docs(ids)

    nodes: list[dict] = []
    missing: list[str] = []
    for nid in ids:
        attrs = cbl_docs.get(nid)
        if attrs is None and nid not in graph:
            missing.append(nid)
            continue
        if attrs is not None:
            data = {k: v for k, v in attrs.items() if k != "embedding"}
            if not include_source and "source" in data:
                data.pop("source", None)
            elif include_source and isinstance(data.get("source"), str) \
                    and len(data["source"]) > 2000:
                data["source"] = data["source"][:2000] + "\n... (truncated)"
            payload = {"id": nid, **data}
            if include_edges and nid in graph:
                edges_in = [
                    {"source": p, "type": graph.edges[p, nid].get("type", "")}
                    for p in graph.predecessors(nid)
                ]
                edges_out = [
                    {"target": s, "type": graph.edges[nid, s].get("type", "")}
                    for s in graph.successors(nid)
                ]
                payload["edges_in"] = edges_in
                payload["edges_out"] = edges_out
            nodes.append(payload)
        else:
            # CBL miss but graph has it → fall back to graph attrs.
            nodes.append(_node_payload(graph, nid,
                                       include_source=include_source,
                                       include_edges=include_edges))
    return {"nodes": nodes, "missing": missing, "requested": len(ids)}


def batch_file_sections(graph: nx.DiGraph, root_dir: str | None,
                        ranges: list[dict]) -> dict:
    """Read up to 10 (path, start, end) ranges in one call. Plan §1.2."""
    from apollo import file_inspect
    capped = list(ranges or [])[:10]
    sections: list[dict] = []
    for r in capped:
        path = r.get("path")
        try:
            start = int(r.get("start"))
            end = int(r.get("end"))
        except (TypeError, ValueError):
            sections.append({"path": path, "error": "invalid start/end"})
            continue
        if not path or start < 1 or end < start:
            sections.append({"path": path, "start": start, "end": end,
                             "error": "invalid range"})
            continue
        # Per-range cap (400 lines) per the plan.
        if end - start + 1 > _BATCH_RANGE_MAX_LINES:
            end = start + _BATCH_RANGE_MAX_LINES - 1
        try:
            sec = file_inspect.get_file_section(graph, root_dir, path, start, end)
            sections.append({
                "path": sec.get("path"),
                "start": sec.get("start_line"),
                "end": sec.get("end_line"),
                "md5": sec.get("md5"),
                "lines": sec.get("lines", []),
                "truncated": sec.get("truncated", False),
            })
        except file_inspect.FileChangedError as e:
            sections.append({"path": path, "start": start, "end": end,
                             "error": str(e), "expected_md5": e.expected,
                             "actual_md5": e.actual})
        except file_inspect.FileAccessError as e:
            sections.append({"path": path, "start": start, "end": end,
                             "error": str(e)})
        except Exception as e:  # pragma: no cover — defensive
            logger.exception("batch_file_sections: failed on %s [%d-%d]",
                             path, start, end)
            sections.append({"path": path, "start": start, "end": end,
                             "error": f"{type(e).__name__}: {e}"})
    return {"sections": sections, "requested": len(ranges or []),
            "served": len(sections)}


def get_directory_tree(graph: nx.DiGraph, root: str = ".", depth: int = 3,
                       glob: str | None = None,
                       include_dirs: bool = True) -> dict:
    """Flat directory listing with depth and node-counts. Plan §1.3.

    Walks `contains` edges from `root` (relative path). Returns one uniform
    array of {path, kind, depth, size_bytes, lang, node_count} entries so
    TOON encoding collapses it to one header row.
    """
    depth = max(0, min(int(depth), 6))
    # Resolve start node id. The graph uses `dir::<rel-path>`, with `dir::.`
    # for the project root.
    if root in (None, "", ".", "./"):
        start_id = "dir::."
    else:
        start_id = f"dir::{root.lstrip('./')}"
    if start_id not in graph:
        # Fallback: try matching by `path` attribute for top-level dirs.
        candidate = None
        for nid, data in graph.nodes(data=True):
            if data.get("type") == "directory" and data.get("path") in (root, root.rstrip("/")):
                candidate = nid
                break
        if candidate is None:
            return {"entries": [], "error": f"directory not found: {root}",
                    "root": root}
        start_id = candidate

    # Pre-compute file → child-node count from the graph (defines/contains).
    file_node_counts: dict[str, int] = defaultdict(int)
    for src, dst, edata in graph.edges(data=True):
        if edata.get("type") in ("contains", "defines"):
            src_data = graph.nodes.get(src, {})
            if src_data.get("type") == "file":
                file_node_counts[src] += 1

    # BFS over contains edges up to `depth`.
    entries: list[dict] = []
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_id, 0)])
    while queue:
        nid, d = queue.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        data = graph.nodes.get(nid, {})
        ntype = data.get("type")
        path = data.get("path", "")
        is_dir = ntype == "directory"
        if d > 0 or nid == start_id:
            # Skip the start entry itself for cleaner output.
            if nid != start_id:
                if is_dir and not include_dirs:
                    pass
                elif not is_dir and glob and not fnmatch.fnmatch(path, glob):
                    pass
                else:
                    entries.append({
                        "path": path,
                        "kind": "dir" if is_dir else "file",
                        "depth": d,
                        "size_bytes": int(data.get("size_bytes") or 0),
                        "lang": data.get("language") or "",
                        "node_count": int(file_node_counts.get(nid, 0)) if not is_dir else 0,
                    })
        if d >= depth:
            continue
        # Walk only `contains` children.
        for succ in graph.successors(nid):
            edata = graph.edges[nid, succ]
            if edata.get("type") != "contains":
                continue
            stype = graph.nodes.get(succ, {}).get("type")
            if stype not in ("file", "directory"):
                continue
            queue.append((succ, d + 1))

    entries.sort(key=lambda e: (e["depth"], e["kind"] != "dir", e["path"]))
    return {"entries": entries, "root": root, "depth": depth,
            "glob": glob or "", "count": len(entries)}


def project_stats_detailed(graph: nx.DiGraph, top_n: int = 20,
                           group: str = "dir") -> dict:
    """Deeper aggregation over the existing graph. Plan §1.4."""
    top_n = max(1, min(int(top_n), 50))
    group = group if group in ("dir", "lang", "ext") else "dir"

    # Group counts.
    group_counts: dict[str, int] = defaultdict(int)
    # Top files by node_count (children defined / contained).
    file_node_counts: dict[str, int] = defaultdict(int)
    for src, dst, edata in graph.edges(data=True):
        if edata.get("type") in ("contains", "defines"):
            sd = graph.nodes.get(src, {})
            if sd.get("type") == "file":
                file_node_counts[src] += 1

    file_paths: dict[str, str] = {}
    for nid, data in graph.nodes(data=True):
        ntype = data.get("type")
        if ntype == "file":
            path = data.get("path") or ""
            file_paths[nid] = path
            if group == "dir":
                key = os.path.dirname(path) or "."
            elif group == "lang":
                key = data.get("language") or "unknown"
            else:  # ext
                _, e = os.path.splitext(path)
                key = (e or "").lstrip(".") or "noext"
            group_counts[key] = group_counts.get(key, 0) + 1
        # group all nodes when grouping by lang? No — keep file-centric.

    # Top files by node count.
    top_files = sorted(file_node_counts.items(), key=lambda kv: kv[1],
                       reverse=True)[:top_n]
    top_files_out = [
        {"file": file_paths.get(fid, fid), "node_id": fid, "node_count": cnt}
        for fid, cnt in top_files
    ]

    # Top connected nodes by total degree, excluding file/directory hubs.
    skip_types = {"file", "directory", "import"}
    degree_rows: list[tuple[str, int]] = []
    for nid in graph.nodes():
        nt = graph.nodes[nid].get("type", "")
        if nt in skip_types:
            continue
        try:
            degree_rows.append((nid, graph.degree(nid)))
        except Exception:
            continue
    degree_rows.sort(key=lambda kv: kv[1], reverse=True)
    top_connected = []
    for nid, deg in degree_rows[:top_n]:
        d = graph.nodes[nid]
        top_connected.append({
            "id": nid,
            "name": d.get("name", ""),
            "type": d.get("type", ""),
            "path": d.get("path", ""),
            "degree": int(deg),
        })

    # Group rows.
    group_rows = sorted(
        ({"key": k, "file_count": v} for k, v in group_counts.items()),
        key=lambda r: r["file_count"], reverse=True,
    )[:top_n]

    return {
        "group": group,
        "top_n": top_n,
        "groups": group_rows,
        "top_files": top_files_out,
        "top_connected": top_connected,
    }


# ─────────────────────────── Phase 2 ───────────────────────────

def get_paths_between(graph: nx.DiGraph, start_node_id: str, end_node_id: str,
                      max_length: int = 5, max_paths: int = 5,
                      edge_types: list[str] | None = None,
                      shortest_only: bool = False) -> dict:
    """Find paths between two nodes. Plan §2.1."""
    max_length = max(1, min(int(max_length), 8))
    max_paths = max(1, min(int(max_paths), 20))
    if start_node_id not in graph:
        return {"error": f"start node not found: {start_node_id}"}
    if end_node_id not in graph:
        return {"error": f"end node not found: {end_node_id}"}

    # Build an undirected view filtered by edge_types.
    if edge_types:
        et = set(edge_types)
        keep_edges = [(u, v) for u, v, d in graph.edges(data=True)
                      if d.get("type") in et]
        sub = nx.DiGraph()
        sub.add_nodes_from(graph.nodes(data=True))
        for u, v in keep_edges:
            sub.add_edge(u, v, **graph.edges[u, v])
        view = sub.to_undirected(as_view=False)
    else:
        view = graph.to_undirected(as_view=False)

    paths: list[list[str]] = []
    try:
        if shortest_only:
            try:
                p = nx.shortest_path(view, start_node_id, end_node_id)
                if len(p) - 1 <= max_length:
                    paths = [p]
            except nx.NetworkXNoPath:
                paths = []
        else:
            for p in nx.all_simple_paths(view, start_node_id, end_node_id,
                                         cutoff=max_length):
                paths.append(p)
                if len(paths) >= max_paths:
                    break
    except nx.NodeNotFound as e:
        return {"error": str(e)}

    out_paths = []
    for p in paths:
        edges = []
        for a, b in zip(p[:-1], p[1:]):
            if graph.has_edge(a, b):
                etype = graph.edges[a, b].get("type", "")
                direction = "out"
            elif graph.has_edge(b, a):
                etype = graph.edges[b, a].get("type", "")
                direction = "in"
            else:
                etype = ""
                direction = ""
            edges.append({"from": a, "to": b, "type": etype,
                          "direction": direction})
        out_paths.append({"length": len(p) - 1, "node_ids": p, "edges": edges})

    return {"start": start_node_id, "end": end_node_id, "paths": out_paths,
            "count": len(out_paths)}


def get_subgraph(graph: nx.DiGraph, seed_node_ids: list[str], depth: int = 1,
                 edge_types: list[str] | None = None,
                 max_nodes: int = 200) -> dict:
    """Subgraph induced by seeds plus depth-N neighbours. Plan §2.2."""
    depth = max(0, min(int(depth), 3))
    max_nodes = max(1, min(int(max_nodes), 500))
    et = set(edge_types) if edge_types else None

    seeds = [s for s in (seed_node_ids or [])[:10] if s in graph]
    if not seeds:
        return {"error": "no valid seeds", "nodes": [], "edges": []}

    visited: dict[str, int] = {s: 0 for s in seeds}
    queue: deque[tuple[str, int]] = deque([(s, 0) for s in seeds])
    while queue:
        nid, d = queue.popleft()
        if d >= depth:
            continue
        for neigh, _, edata in list(graph.out_edges(nid, data=True)) + \
                              [(p, nid, graph.edges[p, nid]) for p in graph.predecessors(nid)]:
            other = neigh if neigh != nid else _
            if et and edata.get("type") not in et:
                continue
            if other not in visited:
                visited[other] = d + 1
                queue.append((other, d + 1))

    # Cap by degree (prefer hubs) if too many.
    if len(visited) > max_nodes:
        ranked = sorted(visited.keys(),
                        key=lambda n: graph.degree(n), reverse=True)[:max_nodes]
        visited = {n: visited[n] for n in ranked}

    node_set = set(visited.keys())
    nodes = []
    for nid in node_set:
        d = graph.nodes[nid]
        nodes.append({
            "id": nid,
            "name": d.get("name", ""),
            "type": d.get("type", ""),
            "path": d.get("path", ""),
            "depth": visited[nid],
        })
    edges = []
    for u, v, edata in graph.edges(data=True):
        if u in node_set and v in node_set:
            if et and edata.get("type") not in et:
                continue
            edges.append({"source": u, "target": v,
                          "type": edata.get("type", "")})
    return {"seeds": seeds, "depth": depth, "nodes": nodes, "edges": edges,
            "node_count": len(nodes), "edge_count": len(edges)}


def get_inheritance_tree(graph: nx.DiGraph, class_node_id: str,
                         include_methods: bool = False) -> dict:
    """Transitive closure on `inherits` edges. Plan §2.3."""
    if class_node_id not in graph:
        return {"error": f"node not found: {class_node_id}"}

    def _walk(start: str, direction: str) -> list[dict]:
        # direction: 'up' = follow inherits OUT from start (ancestors),
        # 'down'    = follow inherits IN  to start (descendants).
        seen: set[str] = set()
        out: list[dict] = []
        q: deque[tuple[str, int]] = deque([(start, 0)])
        while q:
            nid, d = q.popleft()
            if nid in seen:
                continue
            seen.add(nid)
            if nid != start:
                data = graph.nodes[nid]
                out.append({"id": nid, "name": data.get("name", ""),
                            "path": data.get("path", ""), "depth": d})
            if direction == "up":
                for succ in graph.successors(nid):
                    if graph.edges[nid, succ].get("type") == "inherits":
                        q.append((succ, d + 1))
            else:
                for pred in graph.predecessors(nid):
                    if graph.edges[pred, nid].get("type") == "inherits":
                        q.append((pred, d + 1))
        return out

    ancestors = _walk(class_node_id, "up")
    descendants = _walk(class_node_id, "down")

    methods: list[dict] = []
    if include_methods:
        related_class_ids = {class_node_id} | {a["id"] for a in ancestors} | {d["id"] for d in descendants}
        for cid in related_class_ids:
            for succ in graph.successors(cid):
                if graph.edges[cid, succ].get("type") in ("defines", "has_method"):
                    sd = graph.nodes[succ]
                    if sd.get("type") in ("method", "function"):
                        methods.append({
                            "class_id": cid,
                            "id": succ,
                            "name": sd.get("name", ""),
                            "path": sd.get("path", ""),
                            "line_start": sd.get("line_start"),
                        })

    return {
        "class_id": class_node_id,
        "ancestors": ancestors,
        "descendants": descendants,
        "methods": methods,
    }


def get_transitive_imports(graph: nx.DiGraph, file_node_id: str,
                           direction: str = "in",
                           max_depth: int = 5) -> dict:
    """BFS on `imports` edges. Plan §2.4."""
    if file_node_id not in graph:
        return {"error": f"node not found: {file_node_id}"}
    direction = direction if direction in ("in", "out", "both") else "in"
    max_depth = max(1, min(int(max_depth), 10))

    visited: dict[str, int] = {file_node_id: 0}
    q: deque[tuple[str, int]] = deque([(file_node_id, 0)])
    while q:
        nid, d = q.popleft()
        if d >= max_depth:
            continue
        # OUT direction = what this file imports (this → succ via imports).
        if direction in ("out", "both"):
            for succ in graph.successors(nid):
                if graph.edges[nid, succ].get("type") == "imports":
                    if succ not in visited:
                        visited[succ] = d + 1
                        q.append((succ, d + 1))
        # IN direction = what depends on this file (pred → this via imports).
        if direction in ("in", "both"):
            for pred in graph.predecessors(nid):
                if graph.edges[pred, nid].get("type") == "imports":
                    if pred not in visited:
                        visited[pred] = d + 1
                        q.append((pred, d + 1))

    rows: list[dict] = []
    for nid, d in visited.items():
        if nid == file_node_id:
            continue
        data = graph.nodes[nid]
        rows.append({"id": nid, "name": data.get("name", ""),
                     "path": data.get("path", ""), "type": data.get("type", ""),
                     "depth": d})
    rows.sort(key=lambda r: (r["depth"], r["path"]))
    return {"file_node_id": file_node_id, "direction": direction,
            "max_depth": max_depth, "imports": rows, "count": len(rows)}


# ─────────────────────────── Phase 3 ───────────────────────────

def get_code_metrics(graph: nx.DiGraph, node_ids: list[str] | None = None,
                     top_n: int = 20, sort_by: str = "complexity") -> dict:
    """LOC / complexity / param-count metrics. Plan §3.1."""
    top_n = max(1, min(int(top_n), 100))
    sort_by = sort_by if sort_by in ("complexity", "loc", "param_count") else "complexity"

    def _row(nid: str, data: dict) -> dict:
        return {
            "id": nid,
            "name": data.get("name", ""),
            "type": data.get("type", ""),
            "path": data.get("path", ""),
            "line_start": data.get("line_start"),
            "loc": int(data.get("loc") or data.get("line_count") or 0),
            "complexity": int(data.get("complexity") or data.get("cyclomatic_complexity") or 0),
            "param_count": int(data.get("param_count") or len(data.get("params") or [])),
            "signature_hash": data.get("signature_hash") or "",
        }

    if node_ids:
        ids = list(node_ids)[:50]
        rows = [_row(nid, graph.nodes[nid]) for nid in ids if nid in graph]
        missing = [nid for nid in ids if nid not in graph]
        return {"metrics": rows, "missing": missing, "scope": "explicit"}

    # Project-wide: rank all functions/methods by sort_by. Use the
    # cached type index so the chat tool doesn't pay an O(N) walk —
    # for typical projects the function/method bucket is a small
    # fraction of total nodes.
    from apollo.graph.indices import get_indices
    by_type = get_indices(graph).by_type()
    rows = []
    for ntype in ("function", "method"):
        for nid in by_type.get(ntype, ()):
            rows.append(_row(nid, graph.nodes[nid]))
    rows.sort(key=lambda r: r.get(sort_by) or 0, reverse=True)
    return {"metrics": rows[:top_n], "scope": "top_n", "sort_by": sort_by,
            "top_n": top_n, "total_eligible": len(rows)}


def search_graph_by_signature(graph: nx.DiGraph,
                              param_names: list[str] | None = None,
                              param_annotations: list[str] | None = None,
                              signature_hash: str | None = None,
                              fuzzy: bool = False, top: int = 20) -> dict:
    """Find functions matching a parameter pattern. Plan §3.2."""
    top = max(1, min(int(top), 50))
    if not (param_names or signature_hash or param_annotations):
        return {"error": "provide param_names, param_annotations, or signature_hash"}

    target_names = list(param_names) if param_names else None
    target_anns = list(param_annotations) if param_annotations else None

    # Restrict iteration to the function/method buckets via the cached
    # type index — avoids walking variable / import / comment nodes
    # entirely. The index is shared across chat-tool calls.
    from apollo.graph.indices import get_indices
    by_type = get_indices(graph).by_type()
    candidates: list[str] = []
    for ntype in ("function", "method"):
        candidates.extend(by_type.get(ntype, ()))

    matches: list[dict] = []
    for nid in candidates:
        data = graph.nodes[nid]
        if signature_hash and data.get("signature_hash") != signature_hash:
            continue
        params = data.get("params") or []
        # Normalise: params may be list[str] or list[dict].
        names: list[str] = []
        anns: list[str] = []
        for p in params:
            if isinstance(p, str):
                names.append(p)
                anns.append("")
            elif isinstance(p, dict):
                names.append(p.get("name") or "")
                anns.append(p.get("annotation") or p.get("type") or "")
        if target_names is not None:
            if fuzzy:
                if names[: len(target_names)] != target_names:
                    continue
            else:
                if names != target_names:
                    continue
        if target_anns is not None:
            if anns[: len(target_anns)] != target_anns:
                continue
        matches.append({
            "id": nid,
            "name": data.get("name", ""),
            "type": data.get("type", ""),
            "path": data.get("path", ""),
            "line_start": data.get("line_start"),
            "params": names,
            "signature_hash": data.get("signature_hash") or "",
        })
    return {"matches": matches[:top], "count": len(matches),
            "fuzzy": bool(fuzzy)}


def find_test_correspondents(graph: nx.DiGraph, node_id: str,
                             include_heuristic: bool = True) -> dict:
    """Find tests covering a node. Plan §3.3."""
    if node_id not in graph:
        return {"error": f"node not found: {node_id}"}
    data = graph.nodes[node_id]
    name = data.get("name") or ""

    explicit: list[dict] = []
    for u, v, edata in graph.edges(data=True):
        if edata.get("type") == "tests" and v == node_id:
            d = graph.nodes[u]
            explicit.append({"id": u, "name": d.get("name", ""),
                             "path": d.get("path", ""),
                             "line_start": d.get("line_start")})
        elif edata.get("type") == "tests" and u == node_id:
            d = graph.nodes[v]
            explicit.append({"id": v, "name": d.get("name", ""),
                             "path": d.get("path", ""),
                             "line_start": d.get("line_start")})

    heuristic: list[dict] = []
    if include_heuristic and name:
        candidate_names = {f"test_{name}", f"test{name}", f"Test{name}",
                           f"{name}_test"}
        for nid, d in graph.nodes(data=True):
            if d.get("type") not in ("function", "method", "class"):
                continue
            if not d.get("is_test"):
                # Fallback: file under tests/ or starting with test_
                p = d.get("path") or ""
                if "tests/" not in p and "/test_" not in p and not os.path.basename(p).startswith("test_"):
                    continue
            if d.get("name") in candidate_names:
                heuristic.append({"id": nid, "name": d.get("name", ""),
                                  "path": d.get("path", ""),
                                  "line_start": d.get("line_start")})

    return {"node_id": node_id, "explicit": explicit, "heuristic": heuristic,
            "count": len(explicit) + len(heuristic)}


def detect_entry_points(graph: nx.DiGraph,
                        kinds: list[str] | None = None) -> dict:
    """Find probable entry points. Plan §3.4."""
    kinds_set = set(kinds) if kinds else None

    rows: list[dict] = []
    cli_decorators = {"click.command", "click.group", "typer.command",
                      "app.command"}
    route_decorators = {"app.get", "app.post", "app.put", "app.delete",
                        "app.patch", "app.route", "router.get", "router.post",
                        "router.put", "router.delete", "router.patch"}

    def _add(kind: str, nid: str, data: dict, extra: dict | None = None):
        if kinds_set and kind not in kinds_set:
            return
        row = {"kind": kind, "id": nid, "name": data.get("name", ""),
               "path": data.get("path", ""),
               "line": data.get("line_start") or 0}
        if extra:
            row.update(extra)
        rows.append(row)

    for nid, data in graph.nodes(data=True):
        ntype = data.get("type")
        path = data.get("path") or ""
        name = data.get("name") or ""
        decorators = data.get("decorators") or []

        # __main__ marker on file nodes.
        if ntype == "file":
            patterns = data.get("patterns") or []
            if "main_block" in patterns or "if_name_main" in patterns:
                _add("main", nid, data)
            base = os.path.basename(path)
            if base in ("main.py", "cli.py", "manage.py", "__main__.py", "app.py"):
                _add("script", nid, data, {"basename": base})
            # FastAPI app instantiation hint.
            if "fastapi_app" in patterns:
                _add("fastapi_app", nid, data)

        if ntype in ("function", "method"):
            # CLI decorator detection.
            for dec in decorators:
                dec_str = dec if isinstance(dec, str) else str(dec)
                low = dec_str.replace(" ", "")
                if any(c in low for c in cli_decorators):
                    _add("cli", nid, data, {"decorator": dec_str})
                    break
                if any(r in low for r in route_decorators):
                    method = ""
                    for r in route_decorators:
                        if r in low and r.split(".")[-1] in ("get", "post",
                                                              "put", "delete",
                                                              "patch"):
                            method = r.split(".")[-1].upper()
                            break
                    _add("http_route", nid, data,
                         {"decorator": dec_str, "method": method or "*"})
                    break
            if name == "main" and not data.get("class"):
                _add("main_func", nid, data)
            if data.get("is_pytest_fixture"):
                _add("pytest_fixture", nid, data)

    return {"entry_points": rows, "count": len(rows)}


# ─────────────────────────── Phase 4 ───────────────────────────

def get_git_context(graph: nx.DiGraph, root_dir: str | None, path: str,
                    name: str | None = None,
                    line_start: int | None = None,
                    line_end: int | None = None,
                    limit: int = 10) -> dict:
    """git log / git blame for a file or line range. Plan §4.1.

    Returns `{git_available: false}` cleanly on:
    - non-git roots
    - missing `git` binary
    - any subprocess failure
    """
    limit = max(1, min(int(limit), 30))

    # Resolve repo root: prefer indexed root, fall back to cwd.
    repo_root: Path | None = None
    if root_dir:
        repo_root = Path(root_dir).expanduser().resolve(strict=False)
    else:
        # Try graph's `dir::.` abs_path.
        root_node = graph.nodes.get("dir::.") if graph else None
        if root_node and root_node.get("abs_path"):
            repo_root = Path(root_node["abs_path"]).expanduser().resolve(strict=False)
    if repo_root is None:
        return {"git_available": False, "reason": "no repo root"}

    if not (repo_root / ".git").exists():
        return {"git_available": False, "reason": "not a git repo",
                "repo_root": str(repo_root)}

    # Resolve `name` to a line range via the graph if possible.
    # Iterate only function/method/class buckets via the cached type
    # index instead of scanning the whole graph for one symbol.
    if name and (line_start is None or line_end is None):
        from apollo.graph.indices import get_indices
        by_type = get_indices(graph).by_type()
        for ntype in ("function", "method", "class"):
            for nid in by_type.get(ntype, ()):
                data = graph.nodes[nid]
                if data.get("name") != name:
                    continue
                p = data.get("path") or ""
                if p == path or p.endswith("/" + path) or path.endswith("/" + p):
                    line_start = data.get("line_start") or line_start
                    line_end = data.get("line_end") or line_end
                    break
            if line_start is not None and line_end is not None:
                break

    def _run(cmd: list[str]) -> tuple[bool, str]:
        try:
            r = subprocess.run(cmd, cwd=str(repo_root), capture_output=True,
                               text=True, timeout=5, check=False)
            return r.returncode == 0, r.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired,
                subprocess.SubprocessError) as e:
            # Do not raise into the chat stream — degrade silently. The
            # repo-level diagnostic still goes to the log so operators can
            # tell whether `git` is missing vs. timing out.
            logger.debug("get_git_context: %s failed (%s)", cmd[0], e)
            return False, ""

    # Recent commits touching the file.
    log_cmd = ["git", "log", f"-n{limit}",
               "--pretty=format:%H|%an|%ad|%s", "--date=iso", "--", path]
    ok, log_out = _run(log_cmd)
    if not ok:
        return {"git_available": False, "reason": "git log failed",
                "repo_root": str(repo_root)}
    commits = []
    for line in (log_out or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({"sha": parts[0][:12], "author": parts[1],
                            "date": parts[2], "summary": parts[3]})

    blame_rows = []
    if line_start is not None and line_end is not None and line_end >= line_start:
        blame_cmd = ["git", "blame", "--line-porcelain",
                     "-L", f"{line_start},{line_end}", "--", path]
        ok2, blame_out = _run(blame_cmd)
        if ok2 and blame_out:
            cur: dict = {}
            for ln in blame_out.splitlines():
                if ln.startswith("\t"):
                    if cur:
                        blame_rows.append(cur)
                        cur = {}
                    continue
                if not ln:
                    continue
                if " " in ln and len(ln.split(" ")[0]) == 40:
                    cur = {"sha": ln.split(" ")[0][:12]}
                elif ln.startswith("author "):
                    cur["author"] = ln[len("author "):]
                elif ln.startswith("author-time "):
                    try:
                        cur["date"] = time.strftime(
                            "%Y-%m-%d",
                            time.gmtime(int(ln[len("author-time "):])),
                        )
                    except Exception:
                        pass
                elif ln.startswith("summary "):
                    cur["summary"] = ln[len("summary "):]

    return {
        "git_available": True,
        "path": path,
        "line_start": line_start,
        "line_end": line_end,
        "commits": commits,
        "blame": blame_rows,
    }


def search_notes_fulltext(annotation_manager, query: str,
                          type_filter: str | None = None,
                          top: int = 10) -> dict:
    """Substring search across all annotations. Plan §4.2."""
    top = max(1, min(int(top), 50))
    if not query:
        return {"error": "`query` is required", "results": []}
    if annotation_manager is None:
        return {"results": [], "note": "no project open"}
    try:
        items = annotation_manager.list_all()
    except Exception as e:
        return {"error": f"annotation lookup failed: {e}", "results": []}
    if type_filter:
        items = [a for a in items if getattr(a, "type", None) == type_filter]
    needle = query.lower()
    needle_tokens = [t for t in re.split(r"\s+", needle) if t]
    scored: list[tuple[float, dict]] = []
    for a in items:
        d = a.to_dict() if hasattr(a, "to_dict") else dict(a)
        hay = " ".join(str(d.get(k, "")) for k in ("content", "tags", "target",
                                                    "title")).lower()
        if not hay:
            continue
        score = 0.0
        if needle in hay:
            score += 2.0
        for t in needle_tokens:
            if t in hay:
                score += 1.0
        if score > 0:
            scored.append((score, {
                "id": d.get("id"),
                "type": d.get("type"),
                "target": d.get("target"),
                "content": (d.get("content") or "")[:400],
                "tags": d.get("tags") or [],
                "score": score,
            }))
    scored.sort(key=lambda kv: kv[0], reverse=True)
    return {"results": [r for _, r in scored[:top]],
            "count": len(scored), "query": query}


# ─────────────────────────── Phase 8 (§8.3) ───────────────────────────
#
# File-shaped tools whose names + first-sentence descriptions exactly
# match the noun phrases users put in their questions ("what's in this
# file", "where is X declared / used in file Y"). These shift the model's
# rational tool pick away from `file_search` (regex grep) and toward a
# resolved-fact answer in round 0.

# Map graph node `type` → declaration `kind` reported by list_declarations
# and outline_file. We deliberately keep the surface narrow — the schema
# enum in ai/chat_request.json is the source of truth for valid values.
_DECL_KIND_FROM_NODE_TYPE = {
    "function": "function",
    "class": "class",
    "method": "method",
    "variable": "var",
    "section": "section",   # markdown headings
    "code_block": "code_block",
    "table": "table",
    "link": "link",
    "task_item": "task_item",
}

# Patterns used when the file came in via `text_parser` fallback (no AST).
# Match on a full source line; the first match wins.
_FALLBACK_DECL_PATTERNS = [
    ("function", re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(")),
    ("function", re.compile(r"^\s*(?:export\s+(?:default\s+)?)?function\s*\*?\s*(\w+)\s*\(")),
    ("class",    re.compile(r"^\s*(?:export\s+(?:default\s+)?)?class\s+(\w+)\b")),
    ("weakmap_decl", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*new\s+WeakMap\s*\(")),
    ("map_decl",     re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*new\s+Map\s*\(")),
    ("weakset_decl", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*new\s+WeakSet\s*\(")),
    ("set_decl",     re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*new\s+Set\s*\(")),
    ("const",        re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=")),
    ("let",          re.compile(r"^\s*(?:export\s+)?let\s+(\w+)\s*=")),
    ("var",          re.compile(r"^\s*(?:export\s+)?var\s+(\w+)\s*=")),
]

# All schema-permitted kinds (mirrors chat_request.json enum exactly).
_DECL_KIND_ENUM = {
    "function", "class", "method", "const", "let", "var", "def",
    "map_decl", "weakmap_decl", "set_decl", "weakset_decl",
    # Markdown / outline-only kinds (returned by outline_file but valid
    # in list_declarations too):
    "section", "code_block", "table", "link", "task_item",
}


def _resolve_file_node(graph: nx.DiGraph, path: str) -> tuple[str | None, dict]:
    """Map a path → (file_node_id, node_data). Tolerates absolute or relative
    paths and graphs that store either form on the node. Returns (None, {})
    when no file node matches."""
    if not path:
        return None, {}
    candidates = {
        path,
        path.lstrip("./"),
        os.path.basename(path),
    }
    # Direct ID hits first.
    for cand in (f"file::{path}", f"file::{path.lstrip('./')}"):
        if cand in graph:
            return cand, graph.nodes[cand]
    # Walk only ``file`` nodes via the cached type index so we don't
    # pay an O(N) graph scan to resolve a single path. For projects
    # of any real size most nodes are *not* file nodes.
    from apollo.graph.indices import get_indices
    by_type = get_indices(graph).by_type()
    for nid in by_type.get("file", ()):
        data = graph.nodes[nid]
        p = data.get("path") or ""
        if not p:
            continue
        if p in candidates or path in (p, os.path.abspath(p)):
            return nid, data
        # Match by suffix (handles "en/index.html" against any deeper path).
        if p.endswith("/" + path) or path.endswith("/" + p):
            return nid, data
    return None, {}


def _is_exported_decl(node_data: dict, source_line: str | None) -> bool:
    """Best-effort 'is this top-level / exported?' classification.

    Source files vary wildly. Python defines exports by absence of a leading
    underscore; JS/TS uses an explicit `export` keyword we can sniff on the
    declaration line. Anything ambiguous returns False.
    """
    name = node_data.get("name") or ""
    if name and name.startswith("_") and not name.startswith("__"):
        return False
    if source_line and re.match(r"^\s*export\b", source_line):
        return True
    # Python convention: any non-underscore top-level def is "exported".
    if name and not name.startswith("_"):
        return True
    return False


def _read_file_lines(graph: nx.DiGraph, root_dir: str | None,
                     path: str) -> tuple[Path, list[str]]:
    """Resolve and read a file under the indexed sandbox.

    Wraps file_inspect.safe_path so we keep one place that decides what
    "inside the project" means.
    """
    from apollo import file_inspect
    p = file_inspect.safe_path(path, graph, root_dir)
    if not p.is_file():
        raise file_inspect.FileAccessError(f"Not a file: {p}", status_code=404)
    with open(p, encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\n") for ln in f]
    return p, lines


def list_declarations(graph: nx.DiGraph, root_dir: str | None,
                      path: str,
                      kinds: list[str] | None = None,
                      limit: int = 200) -> dict:
    """List every top-level declaration in `path`. Plan §8.3.1.

    Reads the parser-built `defines` edges off the file node. Falls back
    to a single regex pass when the file was indexed by `text_parser`
    (no AST nodes attached); in that case `accuracy` is `"regex"`.
    """
    limit = max(1, min(int(limit), 500))
    requested = set(kinds or []) if kinds else None
    if requested:
        requested = {k for k in requested if k in _DECL_KIND_ENUM}

    file_id, file_data = _resolve_file_node(graph, path)
    rows: list[dict] = []
    accuracy = "ast"

    # Read file source ONCE — needed for both the "is_exported" sniff
    # (for graph-derived declarations) and the regex fallback.
    p_path: Path | None = None
    file_lines: list[str] = []
    try:
        p_path, file_lines = _read_file_lines(graph, root_dir, path)
    except Exception:
        # Sandbox or read failure — return whatever the graph already knows.
        accuracy = "graph_only"

    graph_rows_added = 0
    if file_id is not None:
        for _src, succ, edata in graph.out_edges(file_id, data=True):
            if edata.get("type") != "defines":
                continue
            ndata = graph.nodes.get(succ, {})
            ntype = ndata.get("type")
            kind = _DECL_KIND_FROM_NODE_TYPE.get(ntype)
            if kind is None:
                continue
            if requested and kind not in requested:
                continue
            ls = ndata.get("line_start") or ndata.get("line") or 0
            le = ndata.get("line_end") or ls
            src_line = ""
            if file_lines and 1 <= ls <= len(file_lines):
                src_line = file_lines[ls - 1]
            rows.append({
                "name": ndata.get("name", ""),
                "kind": kind,
                "line_start": int(ls or 0),
                "line_end": int(le or 0),
                "is_exported": _is_exported_decl(ndata, src_line),
                "parent": ndata.get("parent_class") or "",
            })
            graph_rows_added += 1
            # Methods are pulled in via the class node; iterate explicitly
            # so we surface them in `list_declarations` even though the
            # `defines` edge from the FILE node only points at the class.
            if ntype == "class":
                for _c, m_succ, m_edata in graph.out_edges(succ, data=True):
                    if m_edata.get("type") != "defines":
                        continue
                    mdata = graph.nodes.get(m_succ, {})
                    if mdata.get("type") != "method":
                        continue
                    if requested and "method" not in requested:
                        continue
                    mls = mdata.get("line_start") or 0
                    mle = mdata.get("line_end") or mls
                    rows.append({
                        "name": mdata.get("name", ""),
                        "kind": "method",
                        "line_start": int(mls or 0),
                        "line_end": int(mle or 0),
                        "is_exported": _is_exported_decl(mdata, ""),
                        "parent": mdata.get("parent_class") or ndata.get("name", ""),
                    })
                    graph_rows_added += 1

    # When the graph either had no entry for this file OR had a file
    # node but zero defines edges, fall back to a full regex pass so the
    # tool still returns something useful (this is the common case for
    # files indexed by `text_parser` and for fixtures with stub graphs).
    if graph_rows_added == 0 and accuracy != "graph_only":
        accuracy = "regex"

    # Regex fallback: catch new Map() / WeakMap() / Set() / WeakSet()
    # bindings and any def/class/function/const/let/var the parser missed
    # (text_parser fallback, or unknown extensions).
    seen_keys = {(r["name"], r["line_start"], r["kind"]) for r in rows}
    if file_lines:
        # If we already have AST rows for this file we still scan for
        # map/weakmap bindings because the standard variable parser does
        # NOT distinguish those kinds.
        scan_full = (file_id is None) or accuracy == "regex"
        for i, line in enumerate(file_lines, 1):
            for kind, rx in _FALLBACK_DECL_PATTERNS:
                if not scan_full and not kind.endswith("_decl"):
                    continue
                if requested and kind not in requested:
                    continue
                m = rx.match(line)
                if not m:
                    continue
                name = m.group(1)
                key = (name, i, kind)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                rows.append({
                    "name": name,
                    "kind": kind,
                    "line_start": i,
                    "line_end": i,
                    "is_exported": bool(re.match(r"^\s*export\b", line)),
                    "parent": "",
                })
                break  # first matching pattern wins for this line

    rows.sort(key=lambda r: (r["line_start"], r["name"]))
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {
        "path": str(p_path) if p_path else path,
        "declarations": rows,
        "count": len(rows),
        "truncated": truncated,
        "accuracy": accuracy,
    }


# Cap on how many symbols can be looked up in a single batch call.
# Keeps the response payload bounded and avoids accidental DoS-via-fanout
# (each symbol still triggers a per-line classifier pass).
_USAGES_BATCH_MAX_SYMBOLS = 20


def find_symbol_usages(graph: nx.DiGraph, root_dir: str | None,
                       path: str,
                       symbol: str | None = None,
                       symbols: list[str] | None = None,
                       kinds: list[str] | None = None) -> dict:
    """Every line in `path` that mentions a symbol (or symbols), classified.

    Plan §8.3.2 + Phase 8 §8.13 batch follow-up.

    Two input shapes:

    * ``symbol="foo"`` — single-symbol mode (legacy). Response keeps the
      flat ``{symbol, usages[], count}`` shape so existing HTTP consumers
      and the Phase 8 unit tests don't change.
    * ``symbols=["foo", "bar"]`` — batch mode. Reads the file ONCE,
      classifies every line against every requested symbol, and returns
      ``{results: [{symbol, usages[], count}, …], total}``. Cuts a
      fan-out of N sequential ``find_symbol_usages`` calls (which the
      benchmark trace showed the model issuing 8× in round 1) down to
      a single tool call — and a single file read for HTML/JS files
      that are megabytes in size.

    Uses the shared text classifier (``file_inspect._classify_hit``) so
    ``file_search`` and the Phase 1 round-reduction work agree on what
    counts as a declaration / read / write / call. Returns the trimmed
    line itself — no surrounding context — to keep payloads ~10× smaller
    than file_search.
    """
    from apollo import file_inspect

    # Normalise input into a non-empty `wanted` list. The flag below
    # decides which response shape to emit so single-symbol callers
    # keep the legacy contract.
    batch_mode = symbols is not None
    if batch_mode:
        wanted = [s for s in (symbols or []) if s]
    elif symbol:
        wanted = [symbol]
    else:
        wanted = []

    if not wanted:
        if batch_mode:
            return {"path": path, "results": [], "total": 0,
                    "error": "`symbols` must be a non-empty list"}
        return {"path": path, "symbol": symbol, "usages": [], "count": 0,
                "error": "`symbol` is required"}

    # De-dupe while preserving order and enforce the per-call cap.
    seen: set[str] = set()
    deduped: list[str] = []
    for s in wanted:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    deduped = deduped[:_USAGES_BATCH_MAX_SYMBOLS]

    requested = {k for k in (kinds or [])
                 if k in {"declaration", "read", "write", "call",
                          "comment", "string"}}

    # Single file read covers every requested symbol — this is the
    # whole point of the batch path. For a 1.4 MB HTML file with 8
    # symbols (the §8.13 trace) we go from 8 file reads to 1.
    p_path, lines = _read_file_lines(graph, root_dir, path)

    # One combined regex acts as a cheap pre-filter so we only run the
    # per-symbol classifier on lines that contain at least one of the
    # requested symbols. Then per-symbol regexes resolve which symbols
    # actually appear on the line (and which kind they are).
    combined = re.compile(
        r"\b(?:" + "|".join(re.escape(s) for s in deduped) + r")\b"
    )
    per_symbol_needles = {s: re.compile(r"\b" + re.escape(s) + r"\b")
                          for s in deduped}
    per_symbol_out: dict[str, list[dict]] = {s: [] for s in deduped}

    for i, line in enumerate(lines, 1):
        if not combined.search(line):
            continue
        for s in deduped:
            if not per_symbol_needles[s].search(line):
                continue
            kind = file_inspect._classify_hit(line, s)
            if requested and kind not in requested:
                continue
            per_symbol_out[s].append({
                "line_no": i,
                "kind": kind,
                "text": line.strip()[:240],
            })

    md5 = file_inspect.file_md5(p_path)
    accuracy = "text"  # heuristic; AST-resolved variant could land later.

    if not batch_mode:
        # Preserve the single-symbol response shape for backward compat.
        s = deduped[0]
        return {
            "path": str(p_path),
            "symbol": s,
            "md5": md5,
            "usages": per_symbol_out[s],
            "count": len(per_symbol_out[s]),
            "accuracy": accuracy,
        }

    results = [
        {
            "symbol": s,
            "usages": per_symbol_out[s],
            "count": len(per_symbol_out[s]),
        }
        for s in deduped
    ]
    return {
        "path": str(p_path),
        "md5": md5,
        "results": results,
        "total": sum(r["count"] for r in results),
        "accuracy": accuracy,
    }


# ── HTML outline regex helpers (Phase 8 §8.3.3 — round-2 follow-up) ───
#
# The benchmark trace in PLAN_MORE_LOCAL_AI_FUNCTIONS.md §8.13 showed
# `outline_file` returning `accuracy: "none"` for `en/index.html`
# because the file was indexed by `text_parser` (no `defines` edges),
# even though §8.3.3 of the plan promised a "tag tree (head/body/script)"
# fallback for HTML. That gap forced the model into round 2 with three
# wasted `get_function_source` calls (Python AST against HTML →
# `invalid syntax`) before it found the right ranges via
# `batch_file_sections`. This regex fallback closes the gap.

# Top-level structural / landmark tags whose opening line we surface.
# Order matters only for the regex compile cache.
_HTML_LANDMARK_TAGS = (
    "html", "head", "body", "header", "nav", "main", "section", "article",
    "aside", "footer", "script", "style", "template",
    "h1", "h2", "h3", "h4", "h5", "h6",
)
# Open-tag detector: `<tag …>` with optional attrs, captured greedily so
# we can pull `id="…"` / `name="…"` for naming the row. Self-closing
# tags (e.g. <meta/>) are intentionally excluded — we only care about
# block-level landmarks.
_HTML_OPEN_RE = re.compile(
    r"<(" + "|".join(_HTML_LANDMARK_TAGS) + r")(\s[^>]*)?>",
    re.IGNORECASE,
)
# Close-tag detector for the same set. Used to compute line ranges.
_HTML_CLOSE_RE = re.compile(
    r"</(" + "|".join(_HTML_LANDMARK_TAGS) + r")\s*>",
    re.IGNORECASE,
)
# Pull `id="foo"` (or single-quoted) out of an attribute blob.
_HTML_ID_RE = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# JS top-level decls inside <script> blocks. Matches:
#
#   function NAME(...)
#   class NAME ...
#   const|let|var NAME = ...   (any RHS — `new Map()`, arrow fn, literal, …)
#
# Any `const NAME =` is intentionally surfaced (not just function/arrow
# RHS) because the §8.13 use case was the model asking "what caches are
# defined in this HTML file?" — `const operatorsCache = new Map()` and
# friends must appear in the outline.
_JS_DECL_IN_SCRIPT_RE = re.compile(
    r"^\s*(?:export\s+)?"
    r"(?:async\s+)?"
    r"(?:"
    r"function\s+([A-Za-z_$][\w$]*)"
    r"|class\s+([A-Za-z_$][\w$]*)"
    r"|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="
    r")",
    re.MULTILINE,
)


def _html_outline_from_text(lines: list[str], depth: int) -> list[dict]:
    """Build an HTML outline by scanning open/close tags + script bodies.

    Strategy:

    1. Walk every line once. When a landmark open-tag fires, push onto a
       stack with its starting line. When the matching close-tag fires,
       pop and emit a row with `line_start`/`line_end`. Mismatched /
       missing closers fall back to "the next sibling open tag's line"
       so we still emit a usable range for the model.
    2. For each emitted `<script>` row, scan its body for JS function /
       class / arrow-function-const declarations and emit them as nested
       `depth=2` rows.
    3. Headings (`<h1>`-`<h6>`) are surfaced as single-line rows because
       the closer is almost always on the same line.

    All rows use the same uniform shape as the AST path so the LLM gets
    a TOON-friendly response either way.
    """
    rows: list[dict] = []
    # Stack entries: (tag_lower, attrs_blob, line_start_1based)
    stack: list[tuple[str, str, int]] = []

    def _row_name(tag: str, attrs: str) -> str:
        m = _HTML_ID_RE.search(attrs or "")
        if m:
            return f"<{tag} #{m.group(1)}>"
        return f"<{tag}>"

    for i, line in enumerate(lines, 1):
        # Find every open and close on this line — a single line can
        # contain both for short tags like <h2>Title</h2>.
        for m in _HTML_OPEN_RE.finditer(line):
            tag = m.group(1).lower()
            attrs = m.group(2) or ""
            # Heading shortcut: emit immediately, no nesting needed.
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                rows.append({
                    "kind": "heading",
                    "name": _row_name(tag, attrs),
                    "line_start": i,
                    "line_end": i,
                    "depth": 1,
                })
                continue
            stack.append((tag, attrs, i))
        for m in _HTML_CLOSE_RE.finditer(line):
            tag = m.group(1).lower()
            # Pop the most recent matching open tag.
            for j in range(len(stack) - 1, -1, -1):
                if stack[j][0] == tag:
                    open_tag, attrs, ls = stack.pop(j)
                    # Outermost stack depth dictates row depth (1-based).
                    row_depth = max(1, len(stack) + 1)
                    if row_depth > depth:
                        # User asked for shallower output — skip.
                        break
                    kind = "script" if open_tag == "script" else "tag"
                    rows.append({
                        "kind": kind,
                        "name": _row_name(open_tag, attrs),
                        "line_start": ls,
                        "line_end": i,
                        "depth": row_depth,
                    })
                    break

    # Anything left on the stack never closed — emit with line_end = EOF
    # so the model still sees the section.
    eof = len(lines)
    while stack:
        tag, attrs, ls = stack.pop(0)
        row_depth = max(1, len(stack) + 1)
        if row_depth > depth:
            continue
        kind = "script" if tag == "script" else "tag"
        rows.append({
            "kind": kind,
            "name": _row_name(tag, attrs),
            "line_start": ls,
            "line_end": eof,
            "depth": row_depth,
        })

    # For each <script> row, surface its inner JS top-level decls.
    # Always emit when depth >= 2: the `depth` parameter caps HTML tag
    # nesting, but JS decls inside a <script> are *content* of that
    # script row, not extra HTML nesting — and surfacing them is the
    # whole point of the HTML outline (§8.13 fixed the case where the
    # model otherwise reached for `get_function_source`, which can't
    # parse HTML, and burned a round). Capped at 50 per script block.
    if depth >= 2:
        nested: list[dict] = []
        for r in rows:
            if r["kind"] != "script":
                continue
            ls, le = r["line_start"], r["line_end"]
            if le <= ls:
                continue
            body = "\n".join(lines[ls:le - 1])  # body is between tags
            count = 0
            for m in _JS_DECL_IN_SCRIPT_RE.finditer(body):
                if count >= 50:
                    break
                name = m.group(1) or m.group(2) or m.group(3) or ""
                if not name:
                    continue
                # Translate body offset → file line.
                rel_line = body.count("\n", 0, m.start()) + 1
                file_line = ls + rel_line  # body starts on ls + 1
                kind = "function" if m.group(1) else (
                    "class" if m.group(2) else "const")
                nested.append({
                    "kind": kind,
                    "name": name[:120],
                    "line_start": file_line,
                    "line_end": file_line,
                    "depth": r["depth"] + 1,
                })
                count += 1
        rows.extend(nested)

    rows.sort(key=lambda r: (r["line_start"], r["depth"], r["name"]))
    return rows


def outline_file(graph: nx.DiGraph, root_dir: str | None,
                 path: str, depth: int = 2) -> dict:
    """Sub-second outline of a file — uniform `outline[]` shape. Plan §8.3.3.

    Reads off the parser-produced graph payload first. For HTML files
    (and any file outside the graph) falls back to a regex-based scan
    that emits a tag tree of landmark elements (`head`, `body`, `script`,
    `style`, `<h1..h6>`, `<section>`, …) plus the JS function / class /
    arrow-const declarations found inside each `<script>` block. For
    every other file type that has no graph entry, returns
    `outline: []` and `accuracy: "none"` rather than guessing.

    `accuracy` enum:

    * ``ast``    — rows came from the parser graph.
    * ``regex``  — HTML / no-graph fallback.
    * ``none``   — file is not HTML and has no graph entry; nothing to show.
    """
    depth = max(0, min(int(depth), 6))
    file_id, _file_data = _resolve_file_node(graph, path)
    rows: list[dict] = []

    # Graph path — preferred, exact line ranges from the parser.
    if file_id is not None:
        for _src, succ, edata in graph.out_edges(file_id, data=True):
            if edata.get("type") != "defines":
                continue
            ndata = graph.nodes.get(succ, {})
            ntype = ndata.get("type")
            kind = _DECL_KIND_FROM_NODE_TYPE.get(ntype)
            if kind is None:
                continue
            ls = ndata.get("line_start") or ndata.get("line") or 0
            le = ndata.get("line_end") or ls
            rows.append({
                "kind": kind,
                "name": ndata.get("name", "")[:120],
                "line_start": int(ls or 0),
                "line_end": int(le or 0),
                "depth": 1,
            })
            if ntype == "class" and depth >= 2:
                for _c, m_succ, m_edata in graph.out_edges(succ, data=True):
                    if m_edata.get("type") != "defines":
                        continue
                    mdata = graph.nodes.get(m_succ, {})
                    if mdata.get("type") != "method":
                        continue
                    mls = mdata.get("line_start") or 0
                    mle = mdata.get("line_end") or mls
                    rows.append({
                        "kind": "method",
                        "name": mdata.get("name", "")[:120],
                        "line_start": int(mls or 0),
                        "line_end": int(mle or 0),
                        "depth": 2,
                    })

    if rows:
        rows.sort(key=lambda r: (r["line_start"], r["depth"], r["name"]))
        return {
            "path": path,
            "outline": rows,
            "count": len(rows),
            "depth": depth,
            "accuracy": "ast",
        }

    # Fallback path — only fires for HTML (and HTM). Other no-graph
    # files keep the documented `accuracy: "none"` contract so the LLM
    # can tell the difference between "nothing here" and "scan limited".
    ext = (Path(path).suffix or "").lower()
    if ext in {".html", ".htm"}:
        try:
            _p, lines = _read_file_lines(graph, root_dir, path)
        except Exception:
            return {"path": path, "outline": [], "count": 0,
                    "depth": depth, "accuracy": "none"}
        html_rows = _html_outline_from_text(lines, depth)
        return {
            "path": path,
            "outline": html_rows,
            "count": len(html_rows),
            "depth": depth,
            "accuracy": "regex",
        }

    return {"path": path, "outline": [], "count": 0, "depth": depth,
            "accuracy": "none"}
