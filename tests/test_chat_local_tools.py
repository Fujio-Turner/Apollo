"""Tests for chat/local_tools.py — the PLAN_MORE_LOCAL_AI_FUNCTIONS tools.

Covers all 14 new tools at the helper level. Each tool is independently
testable on a tiny in-memory graph; HTTP wrappers and chat-tool dispatch
are thin enough that the helper tests are the meaningful surface.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import networkx as nx
import pytest

from chat import local_tools


# ─────────────────────────── fixtures ───────────────────────────

@pytest.fixture
def small_graph():
    g = nx.DiGraph()
    g.add_node("dir::.", type="directory", path=".", abs_path="/tmp/dummy",
               name="root")
    g.add_node("dir::a", type="directory", path="a", name="a")
    g.add_node("file::a/foo.py", type="file", path="a/foo.py", name="foo.py",
               language="python", size_bytes=120, patterns=["main_block"])
    g.add_node("file::a/bar.py", type="file", path="a/bar.py", name="bar.py",
               language="python", size_bytes=80)
    g.add_node("file::tests/test_foo.py", type="file",
               path="tests/test_foo.py", name="test_foo.py", language="python")
    g.add_node("func::a/foo.py::main", type="function", path="a/foo.py",
               name="main", line_start=10, line_end=20, loc=11, complexity=5,
               params=[{"name": "x"}], signature_hash="h1",
               source="def main(x):\n    return x")
    g.add_node("func::a/bar.py::helper", type="function", path="a/bar.py",
               name="helper", line_start=5, line_end=10, loc=6, complexity=2,
               params=[{"name": "x"}], signature_hash="h1")
    g.add_node("func::tests/test_foo.py::test_main", type="function",
               path="tests/test_foo.py", name="test_main", is_test=True,
               line_start=3)
    g.add_node("class::a/foo.py::Base", type="class", path="a/foo.py",
               name="Base", line_start=1)
    g.add_node("class::a/foo.py::Child", type="class", path="a/foo.py",
               name="Child", line_start=30)
    g.add_edge("dir::.", "dir::a", type="contains")
    g.add_edge("dir::a", "file::a/foo.py", type="contains")
    g.add_edge("dir::a", "file::a/bar.py", type="contains")
    g.add_edge("dir::.", "file::tests/test_foo.py", type="contains")
    g.add_edge("file::a/foo.py", "func::a/foo.py::main", type="defines")
    g.add_edge("file::a/foo.py", "class::a/foo.py::Base", type="defines")
    g.add_edge("file::a/foo.py", "class::a/foo.py::Child", type="defines")
    g.add_edge("file::a/bar.py", "func::a/bar.py::helper", type="defines")
    g.add_edge("func::a/foo.py::main", "func::a/bar.py::helper", type="calls")
    g.add_edge("class::a/foo.py::Child", "class::a/foo.py::Base",
               type="inherits")
    g.add_edge("file::a/bar.py", "file::a/foo.py", type="imports")
    return g


# ─────────────────────────── Phase 1 ───────────────────────────

def test_batch_get_nodes_caps_at_20_and_reports_missing(small_graph):
    r = local_tools.batch_get_nodes(small_graph,
                                    ["func::a/foo.py::main", "missing"])
    assert len(r["nodes"]) == 1
    assert r["missing"] == ["missing"]
    assert r["nodes"][0]["id"] == "func::a/foo.py::main"
    assert "edges_in" in r["nodes"][0] and "edges_out" in r["nodes"][0]


def test_batch_get_nodes_max_20(small_graph):
    ids = ["func::a/foo.py::main"] * 25
    r = local_tools.batch_get_nodes(small_graph, ids)
    # Only 20 are processed (the first 20).
    assert r["requested"] == 20


def test_batch_get_nodes_skip_source(small_graph):
    r = local_tools.batch_get_nodes(small_graph, ["func::a/foo.py::main"],
                                    include_source=False, include_edges=False)
    n = r["nodes"][0]
    assert "source" not in n
    assert "edges_in" not in n


def test_batch_file_sections_real_file(tmp_path):
    f = tmp_path / "demo.py"
    f.write_text("\n".join(f"line{i}" for i in range(1, 21)))
    g = nx.DiGraph()
    g.add_node("dir::.", type="directory", path=".",
               abs_path=str(tmp_path), name="root")
    g.add_node(f"file::{f.name}", type="file", path=str(f), abs_path=str(f),
               name=f.name, language="python")
    r = local_tools.batch_file_sections(g, str(tmp_path), [
        {"path": str(f), "start": 1, "end": 3},
        {"path": str(f), "start": 5, "end": 7},
    ])
    assert r["served"] == 2
    assert len(r["sections"][0]["lines"]) == 3
    assert r["sections"][0]["lines"][0]["text"] == "line1"


def test_batch_file_sections_caps_at_10():
    g = nx.DiGraph()
    ranges = [{"path": "x", "start": 1, "end": 1}] * 15
    r = local_tools.batch_file_sections(g, None, ranges)
    assert len(r["sections"]) == 10


def test_get_directory_tree_uniform_array(small_graph):
    r = local_tools.get_directory_tree(small_graph, root=".", depth=3)
    assert r["count"] >= 3
    # Uniform shape — every entry has the same keys (TOON-friendly).
    keys = {tuple(sorted(e.keys())) for e in r["entries"]}
    assert len(keys) == 1


def test_get_directory_tree_glob(small_graph):
    r = local_tools.get_directory_tree(small_graph, root=".", depth=3,
                                       glob="*.py", include_dirs=False)
    assert all(e["kind"] == "file" for e in r["entries"])
    assert all(e["path"].endswith(".py") for e in r["entries"])


def test_project_stats_detailed_groups_top_files(small_graph):
    r = local_tools.project_stats_detailed(small_graph, top_n=5, group="dir")
    assert r["group"] == "dir"
    assert any(g["key"] == "a" for g in r["groups"])
    # Top files should be sorted by node_count desc.
    counts = [f["node_count"] for f in r["top_files"]]
    assert counts == sorted(counts, reverse=True)


# ─────────────────────────── Phase 2 ───────────────────────────

def test_get_paths_between(small_graph):
    r = local_tools.get_paths_between(small_graph,
                                      "func::a/foo.py::main",
                                      "func::a/bar.py::helper")
    assert r["count"] >= 1
    # The direct `calls` edge is a length-1 path; assert it's in the result set.
    edge_types = [
        e["type"]
        for p in r["paths"]
        for e in p["edges"]
    ]
    assert "calls" in edge_types


def test_get_paths_between_shortest_only(small_graph):
    r = local_tools.get_paths_between(small_graph,
                                      "func::a/foo.py::main",
                                      "func::a/bar.py::helper",
                                      shortest_only=True)
    assert r["count"] == 1
    assert r["paths"][0]["length"] == 1


def test_get_paths_between_filter_by_edge_type(small_graph):
    # `inherits` only: main → helper has no inherits-edge path.
    r = local_tools.get_paths_between(small_graph,
                                      "func::a/foo.py::main",
                                      "func::a/bar.py::helper",
                                      edge_types=["inherits"])
    assert r["count"] == 0


def test_get_paths_between_unknown_node(small_graph):
    r = local_tools.get_paths_between(small_graph, "missing", "alsomissing")
    assert "error" in r


def test_get_subgraph(small_graph):
    r = local_tools.get_subgraph(small_graph, ["file::a/foo.py"], depth=1)
    assert r["node_count"] >= 2
    ids = {n["id"] for n in r["nodes"]}
    assert "file::a/foo.py" in ids
    # depth-1 should pull at least one of its successors.


def test_get_inheritance_tree_descendants(small_graph):
    r = local_tools.get_inheritance_tree(small_graph,
                                         "class::a/foo.py::Base",
                                         include_methods=False)
    desc_ids = {d["id"] for d in r["descendants"]}
    assert "class::a/foo.py::Child" in desc_ids


def test_get_inheritance_tree_methods(small_graph):
    r = local_tools.get_inheritance_tree(small_graph,
                                         "class::a/foo.py::Base",
                                         include_methods=True)
    # Base has no methods in this fixture; ensure the field exists.
    assert "methods" in r


def test_get_transitive_imports_in(small_graph):
    r = local_tools.get_transitive_imports(small_graph, "file::a/foo.py",
                                           direction="in")
    paths = {row["path"] for row in r["imports"]}
    assert "a/bar.py" in paths


def test_get_transitive_imports_out(small_graph):
    r = local_tools.get_transitive_imports(small_graph, "file::a/bar.py",
                                           direction="out")
    paths = {row["path"] for row in r["imports"]}
    assert "a/foo.py" in paths


# ─────────────────────────── Phase 3 ───────────────────────────

def test_get_code_metrics_top_n(small_graph):
    r = local_tools.get_code_metrics(small_graph, top_n=5, sort_by="complexity")
    assert r["scope"] == "top_n"
    assert r["metrics"][0]["complexity"] >= r["metrics"][-1]["complexity"]


def test_get_code_metrics_explicit_ids(small_graph):
    r = local_tools.get_code_metrics(small_graph,
                                     node_ids=["func::a/foo.py::main", "miss"])
    assert r["scope"] == "explicit"
    assert "miss" in r["missing"]


def test_search_graph_by_signature_hash(small_graph):
    r = local_tools.search_graph_by_signature(small_graph,
                                              signature_hash="h1")
    assert r["count"] == 2


def test_search_graph_by_signature_param_names(small_graph):
    r = local_tools.search_graph_by_signature(small_graph,
                                              param_names=["x"])
    assert r["count"] == 2


def test_search_graph_by_signature_requires_input(small_graph):
    r = local_tools.search_graph_by_signature(small_graph)
    assert "error" in r


def test_find_test_correspondents_heuristic(small_graph):
    # No explicit `tests` edges in fixture, but heuristic should match.
    r = local_tools.find_test_correspondents(small_graph,
                                             "func::a/foo.py::main")
    # `test_main` matches the heuristic pattern.
    h_ids = {h["id"] for h in r["heuristic"]}
    assert "func::tests/test_foo.py::test_main" in h_ids


def test_detect_entry_points_finds_main_block(small_graph):
    r = local_tools.detect_entry_points(small_graph)
    kinds = {e["kind"] for e in r["entry_points"]}
    assert "main" in kinds


def test_detect_entry_points_filter_kinds(small_graph):
    r = local_tools.detect_entry_points(small_graph, kinds=["main"])
    assert all(e["kind"] == "main" for e in r["entry_points"])


# ─────────────────────────── Phase 4 ───────────────────────────

def test_get_git_context_no_repo(small_graph):
    r = local_tools.get_git_context(small_graph, "/tmp/definitely-not-a-repo",
                                    "x.py")
    assert r["git_available"] is False


def test_get_git_context_real_repo(tmp_path):
    """Init a real git repo and verify get_git_context returns commits."""
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text("print('hi')\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "add", "x.py"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo,
                   check=True, env=env)
    g = nx.DiGraph()
    g.add_node("dir::.", type="directory", path=".",
               abs_path=str(repo), name="root")
    r = local_tools.get_git_context(g, str(repo), "x.py")
    assert r["git_available"] is True
    assert r["commits"]
    assert r["commits"][0]["summary"] == "init"


def test_search_notes_fulltext_no_manager():
    r = local_tools.search_notes_fulltext(None, "anything")
    assert r["results"] == []


def test_search_notes_fulltext_substring():
    class FakeAnno:
        def __init__(self, **kw):
            self._d = kw
        def to_dict(self):
            return self._d

    class FakeMgr:
        def list_all(self):
            return [
                FakeAnno(id="an::1", type="note", target="x",
                         content="couchbase is great", tags=["db"]),
                FakeAnno(id="an::2", type="note", target="y",
                         content="something else", tags=[]),
                FakeAnno(id="an::3", type="bookmark", target="z",
                         content="couch", tags=[]),
            ]

    r = local_tools.search_notes_fulltext(FakeMgr(), "couch")
    ids = [row["id"] for row in r["results"]]
    # All three contain the substring "couch".
    assert "an::1" in ids
    assert "an::3" in ids
    # an::2 ("something else") doesn't match.
    assert "an::2" not in ids


def test_search_notes_fulltext_type_filter():
    class FakeAnno:
        def __init__(self, **kw):
            self._d = kw
            self.type = kw.get("type")
        def to_dict(self):
            return self._d

    class FakeMgr:
        def list_all(self):
            return [
                FakeAnno(id="an::1", type="note", content="couch", target="x"),
                FakeAnno(id="an::2", type="bookmark", content="couch",
                         target="y"),
            ]
    r = local_tools.search_notes_fulltext(FakeMgr(), "couch",
                                          type_filter="note")
    assert len(r["results"]) == 1
    assert r["results"][0]["type"] == "note"


# ─────────────────────────── Phase 8 ───────────────────────────

def _phase8_graph(tmp_path: Path):
    """Tiny graph + on-disk file matching the JS-flavored example in
    PLAN_MORE_LOCAL_AI_FUNCTIONS §8.3.1 / §8.3.2.
    """
    f = tmp_path / "cache.js"
    f.write_text(
        "// cache module\n"
        "const parseTimeCache = new Map();\n"
        "const normalizeStatementCache = new Map();\n"
        "const seen = new WeakSet();\n"
        "export function clearCaches() {\n"
        "    parseTimeCache.clear();\n"
        "    normalizeStatementCache.clear();\n"
        "}\n"
        "export function getOperators(s) {\n"
        "    if (parseTimeCache.has(s)) return parseTimeCache.get(s);\n"
        "    parseTimeCache.set(s, 0);\n"
        "    return parseTimeCache;\n"
        "}\n"
    )
    g = nx.DiGraph()
    g.add_node("dir::.", type="directory", path=".",
               abs_path=str(tmp_path), name="root")
    g.add_node("file::cache.js", type="file", path="cache.js",
               abs_path=str(f), name="cache.js", language="javascript")
    g.add_node("func::cache.js::clearCaches", type="function",
               path="cache.js", name="clearCaches", line_start=5, line_end=8)
    g.add_node("func::cache.js::getOperators", type="function",
               path="cache.js", name="getOperators", line_start=9, line_end=13)
    g.add_edge("dir::.", "file::cache.js", type="contains")
    g.add_edge("file::cache.js", "func::cache.js::clearCaches", type="defines")
    g.add_edge("file::cache.js", "func::cache.js::getOperators", type="defines")
    return g, f


def test_outline_file_uniform_shape(tmp_path):
    g, f = _phase8_graph(tmp_path)
    r = local_tools.outline_file(g, str(tmp_path), "cache.js")
    assert r["count"] >= 2
    assert r["accuracy"] == "ast"
    keys = {tuple(sorted(e.keys())) for e in r["outline"]}
    assert len(keys) == 1  # uniform shape — TOON-friendly
    names = [e["name"] for e in r["outline"]]
    assert "clearCaches" in names and "getOperators" in names


def test_outline_file_unknown_returns_none(tmp_path):
    g, _f = _phase8_graph(tmp_path)
    r = local_tools.outline_file(g, str(tmp_path), "nope.js")
    assert r["outline"] == []
    assert r["accuracy"] == "none"


def _html_fixture_graph(tmp_path: Path):
    """HTML file outside the parser graph — exercises the regex fallback.

    Mirrors the §8.13 benchmark scenario: large HTML with embedded
    <script> blocks containing JS function declarations. Closes the
    `outline_file` gap that forced the model into round 2.
    """
    f = tmp_path / "index.html"
    f.write_text(
        "<!DOCTYPE html>\n"               # 1
        "<html>\n"                         # 2
        "<head>\n"                         # 3
        "  <title>Demo</title>\n"          # 4
        "</head>\n"                        # 5
        "<body>\n"                         # 6
        "  <h1>Hello</h1>\n"               # 7
        "  <section id=\"main\">\n"        # 8
        "    <h2>Inner</h2>\n"             # 9
        "  </section>\n"                   # 10
        "  <script>\n"                     # 11
        "function clearCaches() {\n"       # 12
        "  parseTimeCache.clear();\n"      # 13
        "}\n"                              # 14
        "const operatorsCache = new Map();\n"  # 15
        "class Helper {}\n"                # 16
        "  </script>\n"                    # 17
        "</body>\n"                        # 18
        "</html>\n"                        # 19
    )
    # Graph WITHOUT a file node for index.html, so outline_file must
    # take the regex path.
    g = nx.DiGraph()
    g.add_node("dir::.", type="directory", path=".",
               abs_path=str(tmp_path), name="root")
    return g, f


def test_outline_file_html_regex_fallback(tmp_path):
    g, _f = _html_fixture_graph(tmp_path)
    r = local_tools.outline_file(g, str(tmp_path), "index.html", depth=3)
    assert r["accuracy"] == "regex"
    assert r["count"] >= 5

    # Uniform-shape rows.
    keys = {tuple(sorted(e.keys())) for e in r["outline"]}
    assert len(keys) == 1

    by_kind = {}
    for row in r["outline"]:
        by_kind.setdefault(row["kind"], []).append(row)

    # Tag tree: head, body, section, script all surface.
    tag_names = [r["name"] for r in by_kind.get("tag", [])]
    assert any("<head>" in n for n in tag_names), tag_names
    assert any("<body>" in n for n in tag_names), tag_names
    assert any("section" in n and "#main" in n for n in tag_names), tag_names

    # Headings come through as their own rows.
    assert any("<h1>" in r["name"] for r in by_kind.get("heading", []))
    assert any("<h2>" in r["name"] for r in by_kind.get("heading", []))

    # Script block is its own row, with line range covering its body.
    scripts = by_kind.get("script", [])
    assert scripts, "expected a <script> row"
    assert scripts[0]["line_start"] == 11 and scripts[0]["line_end"] == 17

    # JS decls inside the <script> appear as nested rows (depth >= 2).
    js_names = {r["name"] for r in r["outline"]
                if r["kind"] in {"function", "class", "const"}}
    assert "clearCaches" in js_names
    assert "operatorsCache" in js_names
    assert "Helper" in js_names


def test_outline_file_html_depth_one_skips_script_decls(tmp_path):
    """`depth=1` must not nest JS decls inside <script>."""
    g, _f = _html_fixture_graph(tmp_path)
    r = local_tools.outline_file(g, str(tmp_path), "index.html", depth=1)
    assert r["accuracy"] == "regex"
    kinds = {row["kind"] for row in r["outline"]}
    # Only top-level tag-tree rows; no nested function/class/const.
    assert kinds.issubset({"tag", "heading", "script"})


def test_list_declarations_finds_map_decls(tmp_path):
    g, f = _phase8_graph(tmp_path)
    r = local_tools.list_declarations(g, str(tmp_path), "cache.js")
    kinds_by_name = {d["name"]: d["kind"] for d in r["declarations"]}
    # Map / WeakSet bindings come from the regex fallback, not the graph.
    assert kinds_by_name.get("parseTimeCache") == "map_decl"
    assert kinds_by_name.get("normalizeStatementCache") == "map_decl"
    assert kinds_by_name.get("seen") == "weakset_decl"
    # Functions still come from the graph.
    assert kinds_by_name.get("clearCaches") == "function"
    # Uniform shape — TOON-friendly.
    keys = {tuple(sorted(d.keys())) for d in r["declarations"]}
    assert len(keys) == 1


def test_list_declarations_kinds_filter(tmp_path):
    g, _f = _phase8_graph(tmp_path)
    r = local_tools.list_declarations(g, str(tmp_path), "cache.js",
                                       kinds=["map_decl", "weakset_decl"])
    kinds = {d["kind"] for d in r["declarations"]}
    assert kinds == {"map_decl", "weakset_decl"}


def test_find_symbol_usages_classifies(tmp_path):
    g, _f = _phase8_graph(tmp_path)
    r = local_tools.find_symbol_usages(g, str(tmp_path), "cache.js",
                                        "parseTimeCache")
    by_kind = {}
    for u in r["usages"]:
        by_kind.setdefault(u["kind"], []).append(u["line_no"])
    # One declaration line.
    assert "declaration" in by_kind
    # `.set(...)` and `.clear()` register as writes (mutating method calls).
    assert "write" in by_kind
    # `.has(...)` / `.get(...)` register as reads (non-mutating method calls
    # on the map are NOT in the mutating list, so they fall through to read).
    assert "read" in by_kind or "call" in by_kind


def test_find_symbol_usages_kinds_filter(tmp_path):
    g, _f = _phase8_graph(tmp_path)
    r = local_tools.find_symbol_usages(g, str(tmp_path), "cache.js",
                                        "parseTimeCache",
                                        kinds=["declaration"])
    assert all(u["kind"] == "declaration" for u in r["usages"])
    assert len(r["usages"]) == 1


def test_find_symbol_usages_batch_mode(tmp_path):
    """`symbols=[..]` returns `results[]` with one entry per symbol.

    The whole point of batch mode (Phase 8 §8.13 follow-up) is to fold
    N sequential calls into ONE — so the file is read once and the
    response shape is always `{results: [{symbol, usages, count}, …]}`.
    """
    g, _f = _phase8_graph(tmp_path)
    r = local_tools.find_symbol_usages(
        g, str(tmp_path), "cache.js",
        symbols=["parseTimeCache", "normalizeStatementCache", "clearCaches"],
    )
    # Batch shape, not the legacy flat shape.
    assert "results" in r
    assert "symbol" not in r
    assert "total" in r
    assert {x["symbol"] for x in r["results"]} == {
        "parseTimeCache", "normalizeStatementCache", "clearCaches",
    }
    # Every requested symbol resolves to ≥1 hit in the fixture.
    by_sym = {x["symbol"]: x for x in r["results"]}
    for s in ("parseTimeCache", "normalizeStatementCache", "clearCaches"):
        assert by_sym[s]["count"] >= 1, s
    # `total` matches sum of per-symbol counts.
    assert r["total"] == sum(x["count"] for x in r["results"])


def test_find_symbol_usages_batch_dedupes_and_caps(tmp_path):
    """Duplicate symbols collapse; the per-call cap holds at 20."""
    g, _f = _phase8_graph(tmp_path)
    # Duplicates and >20 entries.
    syms = ["parseTimeCache"] * 3 + [f"sym{i}" for i in range(30)]
    r = local_tools.find_symbol_usages(g, str(tmp_path), "cache.js",
                                        symbols=syms)
    out = [x["symbol"] for x in r["results"]]
    # Order preserved, duplicates removed.
    assert out[0] == "parseTimeCache"
    assert out.count("parseTimeCache") == 1
    # Hard cap.
    assert len(out) <= 20


def test_find_symbol_usages_batch_empty_returns_error(tmp_path):
    """`symbols=[]` is rejected with the batch-shape error payload."""
    g, _f = _phase8_graph(tmp_path)
    r = local_tools.find_symbol_usages(g, str(tmp_path), "cache.js",
                                        symbols=[])
    assert r["results"] == []
    assert r["total"] == 0
    assert "error" in r


def test_classify_hit_basic_cases():
    from apollo import file_inspect
    cls = file_inspect._classify_hit
    assert cls("def foo(x):", "foo") == "declaration"
    assert cls("class Bar:", "Bar") == "declaration"
    assert cls("const cache = new Map();", "cache") == "declaration"
    assert cls("foo(1, 2)", "foo") == "call"
    assert cls("x = foo + 1", "foo") == "read"
    assert cls("x = 5", "x") == "write"
    assert cls("# foo is the helper", "foo") == "comment"
    assert cls('// using bar', "bar") == "comment"
    assert cls('msg = "foo only here"', "foo") == "string"
    assert cls("cache.set(k, 1)", "cache") == "write"
