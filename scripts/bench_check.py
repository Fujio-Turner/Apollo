#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Compare two ``bench_index`` JSON reports and flag regressions.

Phase 10 of ``docs/work/PLAN_INDEX_MEMORY_AND_CONCURRENCY.md`` calls for
a *non-blocking* CI hook that warns when a fixture-index run drifts
slower or fattier than a checked-in baseline. This script is that hook:
it diffs two report files produced by :mod:`scripts.bench_index` and
prints a human-readable summary plus a machine-readable exit code.

Exit codes::

    0   No regression beyond the configured tolerance.
    1   At least one metric regressed beyond tolerance.
    2   Usage / IO error (couldn't read one of the reports).

Default tolerances mirror the Phase 10 plan bullet "fails if peak RSS
during a fixture index regresses by > 10 %":

    --rss-tolerance     0.10   (10 % peak RSS regression)
    --wall-tolerance    0.15   (15 % wall-clock regression)
    --disk-tolerance    0.05   (5 %  on-disk graph size regression)

Usage::

    # Compare two captures
    python scripts/bench_check.py --baseline old.json --current new.json

    # CI-friendly (non-blocking): always exit 0, just print warnings
    python scripts/bench_check.py --baseline old.json --current new.json --warn-only

    # Tighter tolerances for a release gate
    python scripts/bench_check.py --baseline old.json --current new.json \
        --rss-tolerance 0.05 --wall-tolerance 0.10
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        print(f"error: report not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    except json.JSONDecodeError as e:
        print(f"error: {path} is not valid JSON: {e}", file=sys.stderr)
        raise SystemExit(2)


def _ratio(current: float | None, baseline: float | None) -> float | None:
    """Return ``(current / baseline) - 1`` or ``None`` if either side is missing.

    A positive ratio means *worse* (slower / more memory / bigger). A
    negative ratio means *better*. ``None`` denotes "can't compare"
    (e.g. ``psutil`` wasn't available for one of the runs).
    """
    if current is None or baseline is None or baseline == 0:
        return None
    return (current / baseline) - 1.0


def _fmt_pct(r: float | None) -> str:
    if r is None:
        return "    n/a"
    sign = "+" if r >= 0 else ""
    return f"{sign}{r * 100:5.1f}%"


def _check(
    baseline: dict,
    current: dict,
    rss_tol: float,
    wall_tol: float,
    disk_tol: float,
) -> tuple[bool, list[str]]:
    """Return ``(regressed?, lines_to_print)``.

    Compares the totals + per-stage wall/rss numbers. A regression is
    counted only when a tolerance is *exceeded* in the worse direction;
    improvements are still printed but never flag failure.
    """
    lines: list[str] = []
    regressed = False

    b_tot = baseline.get("totals", {}) or {}
    c_tot = current.get("totals", {}) or {}

    lines.append("── Totals ─────────────────────────────────────────────")
    lines.append(f"  files:   baseline={b_tot.get('files')} current={c_tot.get('files')}")
    lines.append(f"  nodes:   baseline={b_tot.get('nodes')} current={c_tot.get('nodes')}")
    lines.append(f"  edges:   baseline={b_tot.get('edges')} current={c_tot.get('edges')}")

    # Header
    lines.append("")
    lines.append(f"  {'metric':<22s} {'baseline':>12s} {'current':>12s} {'delta':>8s}  status")

    def _row(label: str, b: float | None, c: float | None,
             tol: float, unit: str) -> None:
        nonlocal regressed
        r = _ratio(c, b)
        status = "OK"
        if r is not None and r > tol:
            status = f"REGRESSED (>{tol * 100:.0f}%)"
            regressed = True
        elif r is not None and r < 0:
            status = "improved"
        b_str = f"{b:>10}{unit}" if b is not None else f"{'n/a':>12}"
        c_str = f"{c:>10}{unit}" if c is not None else f"{'n/a':>12}"
        lines.append(f"  {label:<22s} {b_str} {c_str} {_fmt_pct(r):>8s}  {status}")

    _row("peak_rss_mb", b_tot.get("peak_rss_mb"), c_tot.get("peak_rss_mb"),
         rss_tol, "MB")
    _row("wall_s",      b_tot.get("wall_s"),      c_tot.get("wall_s"),
         wall_tol, "s")
    _row("on_disk_mb",  b_tot.get("on_disk_graph_mb"),
         c_tot.get("on_disk_graph_mb"), disk_tol, "MB")

    # Per-stage timings — wall-clock only (peak-rss per stage tends to
    # be noisier and double-counts when stages overlap post-Phase 6/7).
    b_stages = baseline.get("stages") or {}
    c_stages = current.get("stages") or {}
    all_stages = sorted(set(b_stages) | set(c_stages))
    if all_stages:
        lines.append("")
        lines.append("── Per-stage wall-clock ───────────────────────────────")
        for name in all_stages:
            b = (b_stages.get(name) or {}).get("wall_s")
            c = (c_stages.get(name) or {}).get("wall_s")
            _row(name, b, c, wall_tol, "s")

    return regressed, lines


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", required=True, type=Path,
                   help="Path to the baseline bench_index report.")
    p.add_argument("--current", required=True, type=Path,
                   help="Path to the current bench_index report.")
    p.add_argument("--rss-tolerance", type=float, default=0.10,
                   help="Max acceptable peak-RSS regression ratio (default 0.10).")
    p.add_argument("--wall-tolerance", type=float, default=0.15,
                   help="Max acceptable wall-clock regression ratio (default 0.15).")
    p.add_argument("--disk-tolerance", type=float, default=0.05,
                   help="Max acceptable on-disk size regression ratio (default 0.05).")
    p.add_argument("--warn-only", action="store_true",
                   help="Exit 0 even when regressions are detected (CI non-blocking mode).")
    args = p.parse_args()

    baseline = _load(args.baseline)
    current = _load(args.current)

    regressed, lines = _check(
        baseline, current,
        rss_tol=args.rss_tolerance,
        wall_tol=args.wall_tolerance,
        disk_tol=args.disk_tolerance,
    )
    print("\n".join(lines))

    if regressed and not args.warn_only:
        print("\nbench_check: FAIL — one or more metrics regressed beyond tolerance.")
        return 1
    if regressed:
        print("\nbench_check: WARN (non-blocking) — regressions detected; see above.")
        return 0
    print("\nbench_check: OK — no metric regressed beyond tolerance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
