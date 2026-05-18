"""Phase 1 (PLAN_INDEX_MEMORY_AND_CONCURRENCY) regression tests.

Asserts that ``GraphBuilder._parse_build_resolve_streaming`` actually
drops the heavy per-file ``parsed`` dicts after their nodes land in the
graph — i.e. they don't survive into the resolve phase the way the
legacy ``parsed_files`` list did.

Uses :mod:`weakref` + :func:`gc.collect` to prove the dict objects are
reclaimable mid-build.
"""
from __future__ import annotations

import gc
import textwrap
import weakref
from pathlib import Path

import pytest

from graph.builder import GraphBuilder, _minimal_resolve_record
import graph.builder as builder_mod


def _write_sample_project(root: Path) -> None:
    """Tiny mixed-content project with enough material that the parsed
    dicts have real ``source`` strings worth shedding."""
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "utils.py").write_text(textwrap.dedent("""
        \"\"\"Module docstring.\"\"\"

        def helper(x):
            \"\"\"Return x doubled.\"\"\"
            return x * 2

        class Thing:
            \"\"\"A thing.\"\"\"
            def do(self, x):
                return helper(x)
    """).strip() + "\n")
    (root / "pkg" / "main.py").write_text(textwrap.dedent("""
        from .utils import Thing, helper

        def run():
            t = Thing()
            return t.do(helper(3))
    """).strip() + "\n")
    (root / "README.md").write_text(textwrap.dedent("""
        # Sample
        Some prose.

        ## Section A
        ```python
        print("hello")
        ```
    """).strip() + "\n")


def test_minimal_resolve_record_only_keeps_resolve_inputs():
    """The minimal record must shed ``source``, ``docstring``, etc."""
    parsed = {
        "rel_path": "x.py",
        "module_docstring": "should be dropped",
        "patterns": ["should", "also", "be", "dropped"],
        "imports": [{"module": "os", "names": ["path"], "line": 1}],
        "functions": [
            {
                "name": "f",
                "source": "def f(): pass" * 1000,  # heavy
                "docstring": "x" * 5000,
                "calls": [{"name": "g", "line": 2}],
            },
        ],
        "classes": [
            {
                "name": "C",
                "source": "huge class source" * 1000,
                "methods": [
                    {
                        "name": "m",
                        "source": "method source " * 500,
                        "calls": [{"name": "h", "line": 5}],
                    }
                ],
            }
        ],
        "documents": [{"name": "d", "content": "x" * 100_000}],
        "sections": [{"name": "s", "content": "x" * 100_000}],
        "code_blocks": [{"language": "py", "content": "x" * 100_000}],
    }
    rec = _minimal_resolve_record(parsed)
    assert rec["rel_path"] == "x.py"
    assert rec["imports"] == parsed["imports"]
    assert rec["functions"] == [
        {"name": "f", "calls": [{"name": "g", "line": 2}]},
    ]
    assert rec["classes"] == [
        {
            "name": "C",
            "methods": [
                {"name": "m", "calls": [{"name": "h", "line": 5}]},
            ],
        }
    ]
    # Heavy fields must not appear anywhere in the record.
    flat = repr(rec)
    assert "huge class source" not in flat
    assert "should be dropped" not in flat
    assert "docstring" not in flat or "x" * 5000 not in flat
    # Documents / sections / code_blocks aren't part of resolve at all.
    assert "documents" not in rec
    assert "sections" not in rec
    assert "code_blocks" not in rec


def test_streaming_build_produces_same_graph_as_legacy_path(tmp_path):
    """Streaming + resolve must give the same nodes/edges as the
    legacy parse → build → resolve path."""
    _write_sample_project(tmp_path)

    # New (streaming) path — what ``build()`` now does.
    g_new = GraphBuilder().build(str(tmp_path))

    # Legacy two-pass behavior, simulated explicitly.
    legacy = GraphBuilder()
    legacy._root = tmp_path.resolve()
    files, dir_set = legacy._discover_files(tmp_path.resolve())
    legacy._build_dir_nodes_lazy(tmp_path.resolve(), dir_set)
    parsed_files = legacy._parse_files_parallel(files)
    for p in parsed_files:
        legacy._build_file_nodes(p, p["rel_path"])
    for p in parsed_files:
        legacy._resolve_calls(p)
    g_legacy = legacy.graph

    assert set(g_new.nodes()) == set(g_legacy.nodes())
    assert set(g_new.edges()) == set(g_legacy.edges())


class _WeakableDict(dict):
    """Tiny ``dict`` subclass that supports :mod:`weakref` references —
    plain ``dict`` does not. Used by the streaming test below so we can
    actually take weakrefs on the per-file parsed payloads.
    """
    __slots__ = ("__weakref__",)


def test_streaming_drops_parsed_dicts_midbuild(tmp_path, monkeypatch):
    """Prove the parsed dicts are reclaimable as soon as their nodes are
    in the graph.

    We wrap :func:`graph.builder._parse_one` so every returned parsed
    payload is a :class:`_WeakableDict` we can take a weakref to. After
    the streaming pass finishes, all of those weakrefs must be dead —
    that's the whole point of Phase 1.
    """
    _write_sample_project(tmp_path)

    weak_refs: list[weakref.ref] = []
    real_parse_one = builder_mod._parse_one
    call_count = {"n": 0}

    def _tracking_parse_one(item):
        call_count["n"] += 1
        parsed = real_parse_one(item)
        if parsed is None:
            return None
        wrapped = _WeakableDict(parsed)
        weak_refs.append(weakref.ref(wrapped))
        return wrapped

    monkeypatch.setattr(builder_mod, "_parse_one", _tracking_parse_one)

    b = GraphBuilder()
    b._root = tmp_path.resolve()
    files, dir_set = b._discover_files(tmp_path.resolve())
    assert len(files) > 0, f"discover found 0 files in {tmp_path}"
    b._build_dir_nodes_lazy(tmp_path.resolve(), dir_set)

    records = b._parse_build_resolve_streaming(files)
    assert call_count["n"] > 0, (
        f"_parse_one (monkeypatched) was never called; "
        f"files passed in = {len(files)}; records = {len(records)}"
    )

    # At least one parsed payload was actually produced.
    assert len(weak_refs) > 0, "no files parsed — fixture is wrong"

    # Force a collection; the streaming path is supposed to have
    # released every reference into the parsed dicts.
    del files
    gc.collect()

    alive = [r() for r in weak_refs if r() is not None]
    assert not alive, (
        f"{len(alive)} parsed dict(s) survived streaming build; "
        f"Phase 1's streaming pattern is not freeing them. "
        f"Sample keys: {sorted(alive[0].keys()) if alive else 'n/a'}"
    )

    # The minimal records *do* survive (resolve still has to run on
    # them). Sanity check they're shaped correctly.
    assert all("rel_path" in r and "imports" in r for r in records)
    # And they're much smaller than the original dicts would have been —
    # no source / docstring / sections / etc.
    for r in records:
        assert "source" not in r
        assert "documents" not in r


def test_streaming_then_resolve_creates_expected_edges(tmp_path):
    """Sanity check: after Phase 1, ``call`` edges still get resolved."""
    _write_sample_project(tmp_path)
    g = GraphBuilder().build(str(tmp_path))
    call_edges = [
        (u, v) for u, v, d in g.edges(data=True) if d.get("type") == "calls"
    ]
    # main.run -> Thing.do (via t.do), main.run -> helper.
    callers = {u for u, _ in call_edges}
    assert any("main" in c and "run" in c for c in callers), (
        f"no call edges from main.run found in {callers}"
    )
