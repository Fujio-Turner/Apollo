"""Phase 13 — unit tests for the read-only file & source inspection module."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import networkx as nx
import pytest

import file_inspect as fi
from file_inspect import (
    FileAccessError,
    FileChangedError,
    file_md5,
    file_stats,
    file_content,
    get_file_section,
    get_function_source,
    file_search,
    project_search,
    safe_path,
    MAX_SECTION_LINES,
    MAX_FILE_SEARCH_MATCHES,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A small Python project: pkg/__init__.py, pkg/calc.py, README.md."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "calc.py").write_text(
        textwrap.dedent(
            '''\
            """Tiny calc module."""
            import os
            from typing import Optional

            CONST = 42

            def add(a, b):
                """Add two numbers."""
                return a + b

            def needle_function():
                value = "needle"
                return value

            class Calculator:
                """A simple calculator."""

                def __init__(self):
                    self.value = 0

                def reset(self):
                    self.value = 0
            '''
        )
    )
    (tmp_path / "README.md").write_text("# hello\nneedle in markdown\n")
    return tmp_path


@pytest.fixture
def graph(project_dir: Path) -> nx.DiGraph:
    """Graph with file/dir nodes for the project, simulating an indexed project."""
    G = nx.DiGraph()
    G.add_node("dir::.", type="directory", path=".", abs_path=str(project_dir))
    G.add_node(
        "file::pkg/calc.py", type="file", path="pkg/calc.py",
        abs_path=str(project_dir / "pkg" / "calc.py"),
    )
    G.add_node(
        "file::README.md", type="file", path="README.md",
        abs_path=str(project_dir / "README.md"),
    )
    G.add_node(
        "dir::pkg", type="directory", path="pkg",
        abs_path=str(project_dir / "pkg"),
    )
    return G


# ── safe_path / sandbox ────────────────────────────────────────────────────


def test_safe_path_accepts_indexed_file(graph, project_dir):
    p = safe_path(str(project_dir / "pkg" / "calc.py"), graph, str(project_dir))
    assert p.is_file()


def test_safe_path_accepts_relative_under_root(graph, project_dir):
    p = safe_path("pkg/calc.py", graph, str(project_dir))
    assert p == (project_dir / "pkg" / "calc.py").resolve()


def test_safe_path_rejects_outside_root(graph, project_dir, tmp_path):
    outside = tmp_path.parent / "definitely_outside_xyz_123"
    with pytest.raises(FileAccessError):
        safe_path(str(outside), graph, str(project_dir))


def test_safe_path_rejects_etc_passwd(graph, project_dir):
    with pytest.raises(FileAccessError):
        safe_path("/etc/passwd", graph, str(project_dir))


def test_safe_path_rejects_parent_escape(graph, project_dir):
    with pytest.raises(FileAccessError):
        safe_path("../../../etc/passwd", graph, str(project_dir))


# ── file_md5 ───────────────────────────────────────────────────────────────


def test_file_md5_changes_with_content(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("a")
    h1 = file_md5(f)
    f.write_text("b")
    h2 = file_md5(f)
    assert h1 != h2


# ── file_stats ─────────────────────────────────────────────────────────────


def test_file_stats_python(graph, project_dir):
    res = file_stats(graph, str(project_dir), "pkg/calc.py")
    assert res["language"] == "python"
    assert res["function_count"] >= 2
    assert res["class_count"] == 1
    assert res["line_count"] > 5
    assert any("import os" in s for s in res["top_level_imports"])
    assert len(res["md5"]) == 32


def test_file_stats_markdown(graph, project_dir):
    res = file_stats(graph, str(project_dir), "README.md")
    assert res["language"] == "markdown"
    assert res["function_count"] == 0
    assert res["class_count"] == 0


def test_file_stats_missing_file_raises(graph, project_dir):
    with pytest.raises(FileAccessError):
        file_stats(graph, str(project_dir), "no_such_file.py")


# ── file_content ───────────────────────────────────────────────────────────


def test_file_content_returns_full_text(graph, project_dir):
    res = file_content(graph, str(project_dir), "pkg/calc.py")
    assert "needle_function" in res["content"]
    assert res["truncated"] is False
    assert res["language"] == "python"


# ── get_file_section ───────────────────────────────────────────────────────


def test_get_file_section_returns_inclusive_range(graph, project_dir):
    res = get_file_section(graph, str(project_dir), "pkg/calc.py", 1, 3)
    assert res["start_line"] == 1
    assert len(res["lines"]) == 3
    assert res["lines"][0]["n"] == 1


def test_get_file_section_invalid_range(graph, project_dir):
    with pytest.raises(FileAccessError):
        get_file_section(graph, str(project_dir), "pkg/calc.py", 5, 1)


def test_get_file_section_caps_at_max(graph, project_dir, tmp_path):
    big = project_dir / "big.py"
    big.write_text("\n".join(f"x = {i}" for i in range(MAX_SECTION_LINES + 200)))
    graph.add_node(
        "file::big.py", type="file", path="big.py",
        abs_path=str(big),
    )
    res = get_file_section(graph, str(project_dir), "big.py", 1, MAX_SECTION_LINES + 100)
    assert len(res["lines"]) <= MAX_SECTION_LINES


def test_get_file_section_md5_match(graph, project_dir):
    md5 = file_md5(project_dir / "pkg" / "calc.py")
    res = get_file_section(
        graph, str(project_dir), "pkg/calc.py", 1, 2, expected_md5=md5,
    )
    assert res["md5"] == md5


def test_get_file_section_md5_mismatch(graph, project_dir):
    with pytest.raises(FileChangedError) as exc:
        get_file_section(
            graph, str(project_dir), "pkg/calc.py", 1, 2,
            expected_md5="0" * 32,
        )
    assert exc.value.status_code == 409
    assert exc.value.expected == "0" * 32


# ── get_function_source ────────────────────────────────────────────────────


def test_get_function_source_bare_function(graph, project_dir):
    res = get_function_source(graph, str(project_dir), "pkg/calc.py", "add")
    assert "def add" in res["source"]
    assert res["kind"] == "FunctionDef"


def test_get_function_source_qualified_method(graph, project_dir):
    res = get_function_source(graph, str(project_dir), "pkg/calc.py", "Calculator.reset")
    assert "def reset" in res["source"]
    assert "self.value = 0" in res["source"]


def test_get_function_source_class(graph, project_dir):
    res = get_function_source(graph, str(project_dir), "pkg/calc.py", "Calculator")
    assert "class Calculator" in res["source"]


def test_get_function_source_not_found(graph, project_dir):
    with pytest.raises(FileAccessError) as exc:
        get_function_source(graph, str(project_dir), "pkg/calc.py", "no_such_fn")
    assert exc.value.status_code == 404


# ── file_search ────────────────────────────────────────────────────────────


def test_file_search_literal_match(graph, project_dir):
    res = file_search(
        graph, str(project_dir), "pkg/calc.py", "needle", regex=False,
    )
    assert res["match_count"] >= 1
    assert any("needle" in m["text"] for m in res["matches"])


def test_file_search_regex(graph, project_dir):
    res = file_search(
        graph, str(project_dir), "pkg/calc.py", r"^def\s+\w+", regex=True,
    )
    assert res["match_count"] >= 2


def test_file_search_invalid_regex(graph, project_dir):
    with pytest.raises(FileAccessError) as exc:
        file_search(graph, str(project_dir), "pkg/calc.py", "(", regex=True)
    assert exc.value.status_code == 400


def test_file_search_no_match(graph, project_dir):
    res = file_search(
        graph, str(project_dir), "pkg/calc.py", "absent_token_xyz", regex=False,
    )
    assert res["match_count"] == 0
    assert res["matches"] == []


def test_file_search_caps_matches(graph, project_dir):
    spam = project_dir / "spam.py"
    spam.write_text("hit\n" * (MAX_FILE_SEARCH_MATCHES + 50))
    graph.add_node(
        "file::spam.py", type="file", path="spam.py", abs_path=str(spam),
    )
    res = file_search(graph, str(project_dir), "spam.py", "hit", regex=False)
    assert res["match_count"] == MAX_FILE_SEARCH_MATCHES
    assert res["truncated"] is True


# ── project_search ─────────────────────────────────────────────────────────


def test_project_search_finds_match_across_files(graph, project_dir):
    res = project_search(
        graph, str(project_dir), "needle", file_glob="*.py,*.md", regex=False,
    )
    assert res["match_count"] >= 1
    paths = {m["path"] for m in res["matches"]}
    assert any(p.endswith("calc.py") for p in paths)


def test_project_search_glob_filter_excludes(graph, project_dir):
    res = project_search(
        graph, str(project_dir), "needle", file_glob="*.md", regex=False,
    )
    for m in res["matches"]:
        assert m["path"].endswith(".md")


def test_project_search_requires_root(graph):
    with pytest.raises(FileAccessError):
        project_search(graph, None, "x")
