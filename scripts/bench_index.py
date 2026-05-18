#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Benchmark harness for the full indexing pipeline.

Captures wall-clock + memory metrics for each pipeline stage
(parse → embed → spatial → ml → save → search-index) so we can
measure the impact of every phase in
``docs/work/PLAN_INDEX_MEMORY_AND_CONCURRENCY.md``.

Usage::

    python scripts/bench_index.py --root /path/to/project
    python scripts/bench_index.py --root . --out-dir docs/work/bench
    python scripts/bench_index.py --root . --parser-pool sync

Writes a JSON report to
``docs/work/bench/index_<git_sha>_<utc>.json`` containing per-phase
timings plus peak RSS (psutil, if installed) and peak Python-allocator
usage (``tracemalloc``).

This script is the **Phase 0** prerequisite: capture baseline numbers
*before* starting Phase 1 of the plan, then re-run it after every later
phase to confirm regressions / improvements.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import threading
import time
import tracemalloc
from pathlib import Path

# Ensure the repo root is on sys.path so ``import apollo`` works when
# this script is invoked from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ─────────────────────────────────────────────────────────────────────
# RSS sampler — runs in a daemon thread so we capture true peak RSS
# during the indexing run. ``psutil`` is optional; without it we record
# ``None`` for the RSS fields so reports stay machine-parseable.
# ─────────────────────────────────────────────────────────────────────
try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except Exception:  # pragma: no cover — optional dep
    psutil = None  # type: ignore
    _HAS_PSUTIL = False


class _RSSSampler:
    """Background thread that samples ``psutil`` RSS every ``interval_s``."""

    def __init__(self, interval_s: float = 0.05):
        self.interval_s = interval_s
        self.peak_bytes = 0
        self.samples = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc = psutil.Process(os.getpid()) if _HAS_PSUTIL else None

    def _run(self) -> None:
        assert self._proc is not None
        while not self._stop.is_set():
            try:
                rss = self._proc.memory_info().rss
            except Exception:
                break
            if rss > self.peak_bytes:
                self.peak_bytes = rss
            self.samples += 1
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        if not _HAS_PSUTIL:
            return
        # Seed peak with current RSS so a no-op stage still reports
        # something sensible.
        try:
            assert self._proc is not None
            self.peak_bytes = self._proc.memory_info().rss
        except Exception:
            pass
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="bench-rss-sampler")
        self._thread.start()

    def stop(self) -> None:
        if not _HAS_PSUTIL or self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._thread = None

    @property
    def peak_mb(self) -> float | None:
        if not _HAS_PSUTIL:
            return None
        return round(self.peak_bytes / (1024 * 1024), 2)


# ─────────────────────────────────────────────────────────────────────
# Stage timing — context manager that records {wall_s, peak_py_mb,
# peak_rss_mb} per stage. ``tracemalloc`` peak is reset per stage so we
# get isolated allocator-peak per phase.
# ─────────────────────────────────────────────────────────────────────
class _StageTimer:
    """Collects per-stage timing + memory measurements."""

    def __init__(self, sampler: _RSSSampler):
        self.sampler = sampler
        self.stages: dict[str, dict] = {}
        self._stack: list[tuple[str, float, int]] = []

    def __call__(self, name: str):
        return self._ctx(name)

    def _ctx(self, name: str):
        outer = self

        class _Ctx:
            def __enter__(self_inner):
                # Reset tracemalloc peak so this stage's peak is isolated
                # from earlier stages while still accumulating allocations
                # held by the long-lived graph.
                try:
                    tracemalloc.reset_peak()
                except Exception:
                    pass
                outer._stack.append(
                    (name, time.perf_counter(), outer.sampler.peak_bytes)
                )
                return self_inner

            def __exit__(self_inner, *exc):
                _name, t0, rss_at_start = outer._stack.pop()
                wall = time.perf_counter() - t0
                try:
                    _cur, peak_py = tracemalloc.get_traced_memory()
                except Exception:
                    peak_py = 0
                peak_rss = outer.sampler.peak_bytes
                outer.stages[_name] = {
                    "wall_s": round(wall, 4),
                    "peak_py_mb": round(peak_py / (1024 * 1024), 2),
                    "peak_rss_mb": (round(peak_rss / (1024 * 1024), 2)
                                    if _HAS_PSUTIL else None),
                    "rss_delta_mb": (
                        round((peak_rss - rss_at_start) / (1024 * 1024), 2)
                        if _HAS_PSUTIL else None
                    ),
                }
                return False

        return _Ctx()


# ─────────────────────────────────────────────────────────────────────
# Pipeline runner — mirrors web/server.py::_do_index but stripped to
# just the heavy stages. Each stage records what (if anything) was
# skipped so reports remain comparable across boxes.
# ─────────────────────────────────────────────────────────────────────
def _git_sha(root: Path) -> str:
    """Best-effort short git SHA of the *repo containing this script*."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode().strip() or "unknown"
    except Exception:
        return "unknown"


def _run_index(root_dir: str, parser_pool: str, no_embeddings: bool,
               no_ml: bool, store_dir: str) -> dict:
    """Run the full pipeline against ``root_dir`` and return a report dict."""
    skipped: list[str] = []
    sampler = _RSSSampler()
    sampler.start()
    tracemalloc.start()
    timer = _StageTimer(sampler)

    try:
        # ── Stage 1: parse + build graph ─────────────────────────────
        with timer("parse_and_build"):
            from apollo.graph import GraphBuilder

            # Phase 2 of PLAN_INDEX_MEMORY_AND_CONCURRENCY shipped the
            # ``process`` / ``sync`` parser-pool modes — propagate the
            # requested mode to the builder via the
            # ``APOLLO_PARSER_POOL`` env var so the streaming-build
            # path picks it up without an explicit kwarg through every
            # call site (web/server, reindex_service, watcher, …).
            if parser_pool in ("thread", "process", "sync"):
                os.environ["APOLLO_PARSER_POOL"] = parser_pool

            builder = GraphBuilder()
            graph = builder.build(root_dir)

        n_nodes = graph.number_of_nodes()
        n_edges = graph.number_of_edges()
        n_files = sum(
            1 for _, d in graph.nodes(data=True) if d.get("type") == "file"
        )

        # ── Stage 2: embeddings ──────────────────────────────────────
        if no_embeddings:
            skipped.append("embed (disabled via --no-embeddings)")
        else:
            with timer("embed"):
                try:
                    from apollo.embeddings.embedder import get_shared_embedder
                    emb = get_shared_embedder()
                    emb.embed_graph(graph)
                except Exception as e:
                    skipped.append(f"embed: {type(e).__name__}: {e}")

        # ── Stage 3: spatial coords ──────────────────────────────────
        with timer("spatial"):
            try:
                from apollo.spatial import SpatialMapper
                SpatialMapper().compute_all(graph)
            except Exception as e:
                skipped.append(f"spatial: {type(e).__name__}: {e}")

        # ── Stage 4: ML passes ───────────────────────────────────────
        if no_ml:
            skipped.append("ml (disabled via --no-ml)")
        else:
            with timer("ml"):
                try:
                    from apollo.ml import run_all_passes
                    ml_embedder = None
                    try:
                        from apollo.embeddings.embedder import (
                            get_shared_embedder as _shared,
                        )
                        ml_embedder = _shared()
                    except Exception:
                        ml_embedder = None
                    ml_summary = run_all_passes(
                        graph, root_dir=root_dir, embedder=ml_embedder,
                    ) or {}
                    # Record which passes ran vs were skipped so later
                    # comparisons aren't apples-to-oranges.
                    for k, v in ml_summary.items():
                        if not v.get("ml_available"):
                            skipped.append(
                                f"ml.{k}: {v.get('reason', 'unavailable')}"
                            )
                except Exception as e:
                    skipped.append(f"ml: {type(e).__name__}: {e}")

        # ── Stage 5: save graph ──────────────────────────────────────
        with timer("save"):
            try:
                from apollo.storage import open_store
                save_path = os.path.join(store_dir, "graph.json")
                os.makedirs(store_dir, exist_ok=True)
                store = open_store("json", save_path)
                store.save(graph)
                try:
                    store.close()
                except Exception:
                    pass
            except Exception as e:
                skipped.append(f"save: {type(e).__name__}: {e}")

        # ── Stage 6: build search index ──────────────────────────────
        with timer("search_index"):
            try:
                from apollo.embeddings.embedder import (
                    get_shared_embedder as _shared,
                )
                from apollo.search.semantic import SemanticSearch
                SemanticSearch(graph, _shared())
            except Exception as e:
                skipped.append(f"search_index: {type(e).__name__}: {e}")

    finally:
        try:
            tracemalloc.stop()
        except Exception:
            pass
        sampler.stop()

    # Build report ──────────────────────────────────────────────────
    total_wall = sum(s["wall_s"] for s in timer.stages.values())
    on_disk_bytes: int | None = None
    try:
        on_disk_bytes = os.path.getsize(
            os.path.join(store_dir, "graph.json")
        )
    except OSError:
        on_disk_bytes = None

    report = {
        "schema_version": 1,
        "git_sha": _git_sha(_REPO_ROOT),
        "utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": os.path.abspath(root_dir),
        "parser_pool": parser_pool,
        "no_embeddings": bool(no_embeddings),
        "no_ml": bool(no_ml),
        "psutil_available": _HAS_PSUTIL,
        "totals": {
            "wall_s": round(total_wall, 4),
            "files": n_files,
            "nodes": n_nodes,
            "edges": n_edges,
            "on_disk_graph_bytes": on_disk_bytes,
            "on_disk_graph_mb": (round(on_disk_bytes / (1024 * 1024), 2)
                                 if on_disk_bytes else None),
            "peak_rss_mb": sampler.peak_mb,
        },
        "stages": timer.stages,
        "skipped": skipped,
    }
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--root", required=True,
        help="Path to the project root to index.",
    )
    p.add_argument(
        "--out-dir",
        default=str(_REPO_ROOT / "docs" / "work" / "bench"),
        help="Directory to write the JSON report into.",
    )
    p.add_argument(
        "--parser-pool",
        choices=("thread", "process", "sync"),
        default="thread",
        help=("Parser-pool mode. ``thread`` (default) = legacy "
              "ThreadPoolExecutor; ``process`` = Phase 2 "
              "ProcessPoolExecutor (multi-core, spawn-safe); ``sync`` = "
              "single-threaded for deterministic / debug runs."),
    )
    p.add_argument(
        "--no-embeddings", action="store_true",
        help="Skip embedding generation (NOT recommended for baseline runs).",
    )
    p.add_argument(
        "--no-ml", action="store_true",
        help="Skip ML passes (UMAP/HDBSCAN/PageRank/...).",
    )
    p.add_argument(
        "--store-dir",
        default=None,
        help=("Where to write the graph.json under test. Defaults to "
              "a fresh temp dir per run (no impact on the user's "
              "_apollo/ store)."),
    )
    p.add_argument(
        "--label",
        default=None,
        help="Optional label suffix on the report filename for A/B runs.",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress the human-readable summary; only write JSON.",
    )
    args = p.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    # Use a per-run scratch dir for the under-test store unless caller
    # pinned --store-dir. Keeps the user's real _apollo/ untouched and
    # gives us an isolated on-disk-size measurement.
    import tempfile
    store_dir = args.store_dir or tempfile.mkdtemp(prefix="apollo_bench_")
    cleanup_store_dir = args.store_dir is None

    if not args.quiet:
        print(f"bench_index: root={root}")
        print(f"bench_index: parser_pool={args.parser_pool} "
              f"no_embeddings={args.no_embeddings} no_ml={args.no_ml}")
        print(f"bench_index: psutil available: {_HAS_PSUTIL}")

    try:
        report = _run_index(
            root_dir=root,
            parser_pool=args.parser_pool,
            no_embeddings=args.no_embeddings,
            no_ml=args.no_ml,
            store_dir=store_dir,
        )
    finally:
        if cleanup_store_dir:
            try:
                import shutil
                shutil.rmtree(store_dir, ignore_errors=True)
            except Exception:
                pass

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    label = f"_{args.label}" if args.label else ""
    fname = f"index_{report['git_sha']}_{report['utc'].replace(':', '-')}{label}.json"
    out_path = out_dir / fname
    out_path.write_text(json.dumps(report, indent=2, default=str))

    if not args.quiet:
        print()
        print(f"── Report ────────────────────────────────────────────")
        print(f"  files:   {report['totals']['files']}")
        print(f"  nodes:   {report['totals']['nodes']}")
        print(f"  edges:   {report['totals']['edges']}")
        print(f"  on-disk: {report['totals']['on_disk_graph_mb']} MB")
        print(f"  peakRSS: {report['totals']['peak_rss_mb']} MB")
        print(f"  total:   {report['totals']['wall_s']}s")
        for name, s in report["stages"].items():
            rss_part = (f" peak_rss={s['peak_rss_mb']}MB"
                        if s['peak_rss_mb'] is not None else "")
            print(f"    {name:18s} wall={s['wall_s']}s "
                  f"peak_py={s['peak_py_mb']}MB{rss_part}")
        if report["skipped"]:
            print("  skipped:")
            for s in report["skipped"]:
                print(f"    - {s}")
        print()
        print(f"wrote: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
