"""Phase 4 (PLAN_INDEX_MEMORY_AND_CONCURRENCY) regression tests.

Asserts that the per-node ``source`` attribute is no longer written by
the graph builder; instead, the file's full source lives once in
``graph.graph["_file_text"]`` and readers slice it on demand via
:func:`graph.query.get_source`.

What we pin in place:

1. **Builder behaviour** — no ``function``/``method``/``class``/
   ``document``/``section``/``code_block`` node carries a ``source``
   attr after a fresh build.
2. **Sidecar populated** — ``graph.graph["_file_text"]`` contains the
   full text for every parseable file (one entry per ``rel_path``).
3. **``get_source`` correctness** — slicing the sidecar by a node's
   ``line_start``/``line_end`` returns the same text the parser
   originally extracted (within the doc/section/code-block tolerance
   the plan accepts for Markdown fence-line inclusion).
4. **Backward compat** — a legacy graph that *still* has per-node
   ``source`` attrs (e.g. loaded from a pre-Phase-4 JSON file) is
   handled transparently by ``get_source``.
5. **Embedder integration** — ``embed_graph`` reads through
   ``get_source`` and produces a non-empty cache from the sidecar
   alone.
6. **JSON storage round-trip** — the sidecar lives in ``graph.graph``
   and travels through ``JsonStore.save`` / ``JsonStore.load``
   unchanged.
7. **Incremental merge** — re-parsing a file via
   ``ResolveFullStrategy.run`` updates the sidecar entry for that
   file's ``rel_path``.
"""
from __future__ import annotations

from pathlib import Path

import networkx as nx
import numpy as np

from graph.builder import GraphBuilder
from graph.query import get_source
from storage.json_store import JsonStore


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
_SAMPLE_PY = '''\
"""Module docstring."""


def foo(x):
    """Foo docstring."""
    return x + 1


class Bar:
    """Bar docstring."""

    def baz(self, y):
        return y * 2
'''

_SAMPLE_MD = """\
# Title

Some intro paragraph that's long enough for embedding consideration.

## Section A

Body of section A goes here.

```python
print("hello phase 4")
```
"""


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text(_SAMPLE_PY)
    (tmp_path / "notes.md").write_text(_SAMPLE_MD)
    return tmp_path


def _build(tmp_path: Path) -> nx.DiGraph:
    builder = GraphBuilder()
    return builder.build(str(tmp_path))


# ─────────────────────────────────────────────────────────────────────
# Core builder invariants
# ─────────────────────────────────────────────────────────────────────
def test_builder_drops_source_attr_on_code_nodes(tmp_path: Path):
    _make_project(tmp_path)
    g = _build(tmp_path)

    code_types = {"function", "method", "class", "document", "section",
                  "code_block"}
    nodes_with_source = [
        nid for nid, data in g.nodes(data=True)
        if data.get("type") in code_types and "source" in data
    ]
    assert nodes_with_source == [], (
        "Phase 4: these nodes still have a per-node ``source`` attr: "
        f"{nodes_with_source[:5]}"
    )


def test_builder_populates_file_text_sidecar(tmp_path: Path):
    _make_project(tmp_path)
    g = _build(tmp_path)

    ft = g.graph.get("_file_text")
    assert isinstance(ft, dict), "expected graph.graph['_file_text'] dict"
    assert ft.get("a.py") == _SAMPLE_PY
    assert ft.get("notes.md") == _SAMPLE_MD


def test_get_source_slices_function_correctly(tmp_path: Path):
    _make_project(tmp_path)
    g = _build(tmp_path)

    func_id = "func::a.py::foo"
    assert func_id in g.nodes
    src = get_source(g, func_id)
    # The slice must include the def line and the return line.
    assert "def foo(x):" in src
    assert "return x + 1" in src
    # And must NOT spill into the class below.
    assert "class Bar" not in src


def test_get_source_slices_method_correctly(tmp_path: Path):
    _make_project(tmp_path)
    g = _build(tmp_path)

    method_id = "method::a.py::Bar::baz"
    assert method_id in g.nodes
    src = get_source(g, method_id)
    assert "def baz(self, y):" in src
    assert "return y * 2" in src


def test_get_source_handles_missing_file_text(tmp_path: Path):
    g = nx.DiGraph()
    g.add_node("func::x.py::foo", type="function", path="x.py",
               line_start=1, line_end=2)
    assert get_source(g, "func::x.py::foo") == ""


def test_get_source_returns_legacy_source_attr():
    """Backward-compat: graphs loaded from pre-Phase-4 JSON files still
    carry per-node ``source`` strings — ``get_source`` returns them
    verbatim without touching the sidecar."""
    g = nx.DiGraph()
    g.add_node("func::legacy.py::foo", type="function", path="legacy.py",
               line_start=1, line_end=2, source="def foo():\n    pass\n")
    assert get_source(g, "func::legacy.py::foo") == "def foo():\n    pass\n"


def test_get_source_missing_node_returns_empty():
    g = nx.DiGraph()
    assert get_source(g, "nope") == ""


# ─────────────────────────────────────────────────────────────────────
# Embedder integration
# ─────────────────────────────────────────────────────────────────────
class _FakeST:
    """SentenceTransformer stand-in — returns a deterministic vector
    per input string so the test doesn't depend on the real model."""

    def encode(self, texts, batch_size=256, show_progress_bar=False):
        out = []
        for t in texts:
            v = np.zeros(8, dtype=np.float32)
            v[0] = float(len(t) % 17)
            v[1] = float(sum(ord(c) for c in t[:32]) % 251)
            out.append(v)
        return np.stack(out) if out else np.zeros((0, 8), dtype=np.float32)


def test_embed_graph_reads_from_file_text_sidecar(tmp_path: Path,
                                                  monkeypatch):
    from embeddings import embedder as emb_mod

    _make_project(tmp_path)
    g = _build(tmp_path)

    # Confirm no per-node source attr — we want to prove the embedder
    # works against the sidecar alone, not against a stray attr.
    for _nid, data in g.nodes(data=True):
        if data.get("type") in {"function", "method", "class"}:
            assert "source" not in data

    e = emb_mod.Embedder()
    e._model = _FakeST()
    cache = e.embed_graph(g)

    # At least one function-shaped node got embedded.
    func_id = "func::a.py::foo"
    assert "embedding" in g.nodes[func_id]
    assert isinstance(g.nodes[func_id]["embedding"], np.ndarray)
    assert g.nodes[func_id]["embedding"].dtype == np.float32

    # Cache should be non-empty.
    assert cache, "embed_graph returned empty cache despite eligible nodes"


# ─────────────────────────────────────────────────────────────────────
# JsonStore round-trip
# ─────────────────────────────────────────────────────────────────────
def test_jsonstore_round_trips_file_text_sidecar(tmp_path: Path):
    _make_project(tmp_path)
    g = _build(tmp_path)
    path = tmp_path / "graph.json"

    JsonStore(str(path)).save(g)
    g2 = JsonStore(str(path)).load()

    ft2 = g2.graph.get("_file_text")
    assert isinstance(ft2, dict)
    assert ft2.get("a.py") == _SAMPLE_PY
    assert ft2.get("notes.md") == _SAMPLE_MD

    # And get_source on the loaded graph still works (no per-node
    # source attr, only the sidecar).
    src = get_source(g2, "func::a.py::foo")
    assert "def foo(x):" in src


# ─────────────────────────────────────────────────────────────────────
# Incremental merge
# ─────────────────────────────────────────────────────────────────────
def test_incremental_resolve_full_refreshes_file_text(tmp_path: Path):
    """A re-parsed file must update its ``_file_text`` entry."""
    from graph.incremental import ResolveFullStrategy

    _make_project(tmp_path)
    g_initial = _build(tmp_path)

    # Mutate one file on disk so the strategy re-parses it.
    new_py = _SAMPLE_PY + "\n\ndef added_after():\n    return 99\n"
    (tmp_path / "a.py").write_text(new_py)

    strategy = ResolveFullStrategy()
    result = strategy.run(
        root_dir=str(tmp_path),
        graph_in=g_initial,
        prev_hashes={},          # forces all files to look "changed"
        prev_dep_index={},
    )

    ft = result.graph_out.graph.get("_file_text")
    assert isinstance(ft, dict)
    assert ft.get("a.py") == new_py, (
        "ResolveFullStrategy.run did not refresh the file-text sidecar "
        "for the re-parsed file"
    )
    # The new function node should also exist with the right slice.
    added_id = "func::a.py::added_after"
    assert added_id in result.graph_out.nodes
    src = get_source(result.graph_out, added_id)
    assert "return 99" in src
