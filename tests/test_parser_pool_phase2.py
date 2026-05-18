"""Phase 2 of PLAN_INDEX_MEMORY_AND_CONCURRENCY — parser-pool modes.

Pins:

1. ``_parser_key`` is stable across re-imports (forms the basis of the
   process-pool key swap).
2. ``_resolve_parser_pool_mode`` honors precedence (explicit > env >
   default) and downgrades ``process`` to ``thread`` when any parser
   advertises ``safe_for_processes = False``.
3. ``GraphBuilder._parse_build_resolve_streaming`` with ``sync`` mode
   produces the same nodes/edges as the default ``thread`` mode.
4. ``GraphBuilder._parse_build_resolve_streaming`` with ``process``
   mode produces the same nodes/edges as the default ``thread`` mode
   (correctness — wall-clock comparison is left to ``bench_index``).
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from apollo.graph.builder import (
    GraphBuilder,
    _parser_key,
    _resolve_parser_pool_mode,
)
from apollo.parser import PythonParser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def small_corpus(tmp_path: Path) -> Path:
    """A tiny multi-file Python project — enough to exercise resolve."""
    (tmp_path / "a.py").write_text(textwrap.dedent("""
        def greet(name):
            return f"hello {name}"

        def main():
            print(greet("world"))
    """).strip() + "\n")
    (tmp_path / "b.py").write_text(textwrap.dedent("""
        from a import greet

        class Caller:
            def shout(self, who):
                return greet(who).upper()
    """).strip() + "\n")
    (tmp_path / "c.py").write_text(textwrap.dedent("""
        def helper(x):
            return x * 2
    """).strip() + "\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Unit-level helpers
# ---------------------------------------------------------------------------

def test_parser_key_is_module_path():
    p = PythonParser()
    key = _parser_key(p)
    assert isinstance(key, str) and key
    # Stable: identical instance returns identical key.
    assert _parser_key(PythonParser()) == key
    # The key must round-trip through pickle (it's what crosses the
    # ProcessPool boundary).
    import pickle
    assert pickle.loads(pickle.dumps(key)) == key


def test_parser_key_none_passthrough():
    assert _parser_key(None) is None


def test_resolve_pool_mode_default_is_process(monkeypatch):
    # Phase 2: the default was flipped from "thread" to "process" once
    # the Apollo-self A/B (2.7× parse, 2.3× total) showed the process
    # pool is a strict win on every safe-parser project.
    monkeypatch.delenv("APOLLO_PARSER_POOL", raising=False)
    assert _resolve_parser_pool_mode([PythonParser()]) == "process"


def test_resolve_pool_mode_env_var(monkeypatch):
    monkeypatch.setenv("APOLLO_PARSER_POOL", "sync")
    assert _resolve_parser_pool_mode([PythonParser()]) == "sync"


def test_resolve_pool_mode_explicit_beats_env(monkeypatch):
    monkeypatch.setenv("APOLLO_PARSER_POOL", "process")
    assert _resolve_parser_pool_mode(
        [PythonParser()], requested="thread") == "thread"


def test_resolve_pool_mode_invalid_falls_back_to_default(monkeypatch):
    # Invalid mode strings collapse to the default ("process" since
    # Phase 2 flipped the default).
    monkeypatch.delenv("APOLLO_PARSER_POOL", raising=False)
    assert _resolve_parser_pool_mode(
        [PythonParser()], requested="garbage") == "process"


def test_resolve_pool_mode_downgrades_unsafe_parsers(monkeypatch):
    monkeypatch.delenv("APOLLO_PARSER_POOL", raising=False)

    class _Unsafe(PythonParser):
        safe_for_processes = False

    assert _resolve_parser_pool_mode(
        [_Unsafe()], requested="process") == "thread"


def test_resolve_pool_mode_keeps_process_when_all_safe(monkeypatch):
    monkeypatch.delenv("APOLLO_PARSER_POOL", raising=False)
    # PythonParser doesn't set the attr → default True via getattr.
    assert _resolve_parser_pool_mode(
        [PythonParser()], requested="process") == "process"


# ---------------------------------------------------------------------------
# End-to-end correctness across pool modes
# ---------------------------------------------------------------------------

def _build(root: Path, mode: str | None):
    builder = GraphBuilder(parsers=[PythonParser()])
    # Drive the mode via env so the public ``build()`` entry point
    # also exercises _resolve_parser_pool_mode.
    prev = os.environ.get("APOLLO_PARSER_POOL")
    if mode is None:
        os.environ.pop("APOLLO_PARSER_POOL", None)
    else:
        os.environ["APOLLO_PARSER_POOL"] = mode
    try:
        graph = builder.build(str(root))
    finally:
        if prev is None:
            os.environ.pop("APOLLO_PARSER_POOL", None)
        else:
            os.environ["APOLLO_PARSER_POOL"] = prev
    return graph


def _graph_signature(g):
    """Stable comparable shape — independent of node-insert order."""
    nodes = sorted(g.nodes())
    edges = sorted(
        (u, v, d.get("type"))
        for u, v, d in g.edges(data=True)
    )
    return nodes, edges


def test_sync_mode_matches_thread_mode(small_corpus):
    thr = _build(small_corpus, "thread")
    syn = _build(small_corpus, "sync")
    assert _graph_signature(thr) == _graph_signature(syn)


def test_process_mode_matches_thread_mode(small_corpus):
    """ProcessPoolExecutor parses must produce an identical graph.

    Wall-clock vs. thread mode is *not* asserted here — for a 3-file
    fixture the spawn overhead dwarfs the parse, so process is
    expected to be slower. The bench harness in
    ``scripts/bench_index.py`` measures real-corpus wins.
    """
    thr = _build(small_corpus, "thread")
    proc = _build(small_corpus, "process")
    assert _graph_signature(thr) == _graph_signature(proc)
