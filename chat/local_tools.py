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
    """Fetch up to 20 node payloads in one call. Plan §1.1."""
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

    # Project-wide: rank all functions/methods by sort_by.
    rows = []
    for nid, data in graph.nodes(data=True):
        if data.get("type") not in ("function", "method"):
            continue
        rows.append(_row(nid, data))
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

    matches: list[dict] = []
    for nid, data in graph.nodes(data=True):
        if data.get("type") not in ("function", "method"):
            continue
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
    if name and (line_start is None or line_end is None):
        for nid, data in graph.nodes(data=True):
            if data.get("type") not in ("function", "method", "class"):
                continue
            if data.get("name") != name:
                continue
            p = data.get("path") or ""
            if p == path or p.endswith("/" + path) or path.endswith("/" + p):
                line_start = data.get("line_start") or line_start
                line_end = data.get("line_end") or line_end
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
