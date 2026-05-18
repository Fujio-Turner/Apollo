# SPDX-License-Identifier: BUSL-1.1
"""Phase 9 (PLAN_INDEX_MEMORY_AND_CONCURRENCY) regression tests.

Pins the contract of :func:`graph.builder._rehash_file_for_incremental`
and :func:`graph.builder._parallel_rehash` plus their integration into
``GraphBuilder.build_incremental`` and
``ResolveFullStrategy.run``.

What we pin:

* Per-file rehash returns the correct shape and ``changed`` flag for
  both unchanged-content and changed-content cases.
* Files that vanish between discovery and rehash yield ``None``
  (preserves the legacy "skip silently" behavior).
* The parallel orchestrator uses pool worker threads when the batch
  is non-trivial (>8 jobs).
* ``GraphBuilder.build_incremental`` still re-parses changed files
  and skips unchanged ones — i.e. the parallel-rehash refactor did
  not change the high-level incremental contract.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from graph.builder import (
    GraphBuilder,
    _parallel_rehash,
    _rehash_file_for_incremental,
)


# ─────────────────────────────────────────────────────────────────────
# _rehash_file_for_incremental — happy paths + boundary cases
# ─────────────────────────────────────────────────────────────────────
def test_rehash_changed_content(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("def foo():\n    return 1\n")
    st = f.stat()

    rec = _rehash_file_for_incremental(
        f, "a.py", st.st_mtime_ns, st.st_size, prev_sha="stale-sha",
    )
    assert rec is not None
    assert rec["changed"] is True
    assert rec["rel_path"] == "a.py"
    assert rec["source_text"] == "def foo():\n    return 1\n"
    assert rec["file_md5_hex"] is not None
    assert rec["new_hash"]["mtime_ns"] == st.st_mtime_ns
    assert rec["new_hash"]["size"] == st.st_size


def test_rehash_unchanged_content_skips_decode_and_md5(tmp_path: Path):
    """If sha256 matches prev_sha, the worker must short-circuit and
    NOT compute the (potentially expensive) md5 + UTF-8 decode."""
    f = tmp_path / "a.py"
    body = b"def foo():\n    return 1\n"
    f.write_text(body.decode())
    cur_sha = hashlib.sha256(body).hexdigest()
    st = f.stat()

    rec = _rehash_file_for_incremental(
        f, "a.py", st.st_mtime_ns, st.st_size, prev_sha=cur_sha,
    )
    assert rec is not None
    assert rec["changed"] is False
    assert rec["source_text"] is None  # no decode happened
    assert rec["file_md5_hex"] is None  # no md5 computed
    assert rec["new_hash"]["sha256"] == cur_sha


def test_rehash_missing_file_returns_none(tmp_path: Path):
    rec = _rehash_file_for_incremental(
        tmp_path / "missing.py", "missing.py",
        cur_mtime=0, cur_size=0, prev_sha=None,
    )
    assert rec is None


# ─────────────────────────────────────────────────────────────────────
# _parallel_rehash — orchestration
# ─────────────────────────────────────────────────────────────────────
def test_parallel_rehash_empty_jobs_returns_empty():
    assert _parallel_rehash([]) == []


def test_parallel_rehash_tiny_batch_runs_inline(tmp_path: Path):
    """Batches ≤ 8 jobs intentionally skip pool setup."""
    jobs = []
    for i in range(3):
        f = tmp_path / f"f{i}.py"
        f.write_text(f"def x{i}(): return {i}\n")
        st = f.stat()
        jobs.append((f, f.name, st.st_mtime_ns, st.st_size, None))

    seen = set()

    real = _rehash_file_for_incremental

    def _track(*args, **kwargs):
        seen.add(threading.current_thread().name)
        return real(*args, **kwargs)

    # Monkeypatch via the module so the inline path sees our wrapper.
    import graph.builder as gb
    orig = gb._rehash_file_for_incremental
    gb._rehash_file_for_incremental = _track
    try:
        recs = _parallel_rehash(jobs)
    finally:
        gb._rehash_file_for_incremental = orig

    assert len(recs) == 3
    # Inline path → only the caller's thread should appear.
    assert seen == {threading.current_thread().name}


def test_parallel_rehash_large_batch_uses_pool(tmp_path: Path):
    """Batches > 8 jobs should fan out to the executor — worker
    threads with the ``apollo-rehash`` prefix should appear."""
    jobs = []
    for i in range(16):
        f = tmp_path / f"f{i}.py"
        f.write_text(f"def x{i}(): return {i}\n")
        st = f.stat()
        jobs.append((f, f.name, st.st_mtime_ns, st.st_size, None))

    seen: set[str] = set()
    lock = threading.Lock()

    import graph.builder as gb
    real = gb._rehash_file_for_incremental

    def _track(*args, **kwargs):
        with lock:
            seen.add(threading.current_thread().name)
        return real(*args, **kwargs)

    gb._rehash_file_for_incremental = _track
    try:
        recs = _parallel_rehash(jobs)
    finally:
        gb._rehash_file_for_incremental = real

    assert len(recs) == 16
    assert any(name.startswith("apollo-rehash") for name in seen), (
        f"expected pool worker threads, saw: {seen}"
    )


def test_parallel_rehash_skips_vanished_files(tmp_path: Path):
    jobs = [(tmp_path / "missing.py", "missing.py", 0, 0, None)]
    assert _parallel_rehash(jobs) == []


# ─────────────────────────────────────────────────────────────────────
# Integration — build_incremental still works after the refactor
# ─────────────────────────────────────────────────────────────────────
def test_build_incremental_skips_unchanged_files_via_parallel_path(tmp_path: Path):
    (tmp_path / "a.py").write_text("def foo(): return 1\n")
    (tmp_path / "b.py").write_text("def bar(): return 2\n")

    # First pass — no prev_hashes, both files re-parsed.
    builder1 = GraphBuilder()
    g1, hashes_after_first = builder1.build_incremental(str(tmp_path), prev_hashes={})
    assert "func::a.py::foo" in g1.nodes
    assert "func::b.py::bar" in g1.nodes
    assert set(hashes_after_first.keys()) == {"a.py", "b.py"}

    # Second pass — pass hashes back; nothing changed → fast path.
    builder2 = GraphBuilder()
    g2, hashes_after_second = builder2.build_incremental(
        str(tmp_path), prev_hashes=hashes_after_first,
    )
    # Hashes round-trip unchanged.
    assert hashes_after_second == hashes_after_first


def test_build_incremental_reparses_changed_file(tmp_path: Path):
    (tmp_path / "a.py").write_text("def foo(): return 1\n")
    (tmp_path / "b.py").write_text("def bar(): return 2\n")

    builder1 = GraphBuilder()
    _, prev = builder1.build_incremental(str(tmp_path), prev_hashes={})

    # Modify b.py only (rewriting forces a new mtime).
    (tmp_path / "b.py").write_text("def bar(): return 9\n\ndef baz(): return 99\n")

    builder2 = GraphBuilder()
    g, new = builder2.build_incremental(str(tmp_path), prev_hashes=prev)

    # The new function from b.py shows up post-rebuild.
    assert "func::b.py::baz" in g.nodes
    # a.py's hash is unchanged in the new map.
    assert new["a.py"] == prev["a.py"]
    # b.py's hash changed.
    assert new["b.py"] != prev["b.py"]
