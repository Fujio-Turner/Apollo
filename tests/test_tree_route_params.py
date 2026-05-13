"""§7.3 follow-up — `/api/tree` honours `depth` and `glob` query params.

Resolves the known follow-up captured in
`docs/work/PLAN_MORE_LOCAL_AI_FUNCTIONS.md` §7.3 — the AI
`get_directory_tree` tool already honoured `depth`/`glob`; the
human-facing HTTP endpoint now does too. Legacy callers (no params) get
the same nested tree they always have.
"""
from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient").TestClient


@pytest.fixture
def client(tmp_path: Path):
    """Build a small synthetic graph with `contains` edges and three files."""
    G = nx.DiGraph()
    # Root project dir.
    G.add_node("dir::.", type="directory", path=".", abs_path=str(tmp_path),
               name=".")
    # One sub-directory `pkg/` with two files inside it.
    G.add_node("dir::pkg", type="directory", path="pkg",
               abs_path=str(tmp_path / "pkg"), name="pkg")
    G.add_node("file::pkg/calc.py", type="file", path="pkg/calc.py",
               abs_path=str(tmp_path / "pkg" / "calc.py"), name="calc.py")
    G.add_node("file::pkg/notes.md", type="file", path="pkg/notes.md",
               abs_path=str(tmp_path / "pkg" / "notes.md"), name="notes.md")
    # One top-level file directly under the root.
    G.add_node("file::README.md", type="file", path="README.md",
               abs_path=str(tmp_path / "README.md"), name="README.md")

    G.add_edge("dir::.", "dir::pkg", type="contains")
    G.add_edge("dir::.", "file::README.md", type="contains")
    G.add_edge("dir::pkg", "file::pkg/calc.py", type="contains")
    G.add_edge("dir::pkg", "file::pkg/notes.md", type="contains")

    class _StubStore:
        backend = "json"
        _G = G

        def load(self, include_embeddings: bool = True):  # noqa: ARG002
            return self._G

    from web.server import create_app

    app = create_app(_StubStore(), backend="json", root_dir=str(tmp_path))
    return fastapi_testclient(app)


def _walk(node, out):
    out.append(node)
    for c in node.get("children", []):
        _walk(c, out)


def test_tree_no_params_returns_full_tree(client):
    """Legacy behaviour — no params → full nested tree, no filtering."""
    resp = client.get("/api/tree")
    assert resp.status_code == 200, resp.text
    root = resp.json()
    flat: list[dict] = []
    _walk(root, flat)
    paths = {n.get("path") for n in flat}
    # All four nodes (root + pkg + 3 files) should be present.
    assert "pkg" in paths
    assert "pkg/calc.py" in paths
    assert "pkg/notes.md" in paths
    assert "README.md" in paths


def test_tree_depth_zero_returns_root_only(client):
    """`depth=0` → no descendants below the root entry."""
    resp = client.get("/api/tree", params={"depth": 0})
    assert resp.status_code == 200
    root = resp.json()
    assert root["children"] == []


def test_tree_depth_one_returns_only_immediate_children(client):
    """`depth=1` → only the root's direct children, no grandchildren."""
    resp = client.get("/api/tree", params={"depth": 1})
    assert resp.status_code == 200
    root = resp.json()
    children_paths = {c["path"] for c in root["children"]}
    assert children_paths == {"pkg", "README.md"}
    # The `pkg/` subdir must have NO children at this depth.
    pkg = next(c for c in root["children"] if c["path"] == "pkg")
    assert pkg["children"] == []


def test_tree_glob_drops_non_matching_files(client):
    """`glob=*.py` → only `.py` files survive; directories are kept."""
    resp = client.get("/api/tree", params={"glob": "*.py"})
    assert resp.status_code == 200
    root = resp.json()
    flat: list[dict] = []
    _walk(root, flat)
    files = [n for n in flat if n.get("type") == "file"]
    file_paths = {f["path"] for f in files}
    # Only the .py file is kept.
    assert file_paths == {"pkg/calc.py"}
    # The `pkg/` directory is still present (we don't collapse empties).
    dirs = {n["path"] for n in flat if n.get("type") == "directory"}
    assert "pkg" in dirs


def test_tree_glob_path_pattern(client):
    """Glob is applied against the full relative path, not just the basename."""
    resp = client.get("/api/tree", params={"glob": "pkg/*.md"})
    assert resp.status_code == 200
    root = resp.json()
    flat: list[dict] = []
    _walk(root, flat)
    file_paths = {n["path"] for n in flat if n.get("type") == "file"}
    # README.md is at the root, so `pkg/*.md` excludes it.
    assert file_paths == {"pkg/notes.md"}


def test_tree_invalid_depth_returns_400(client):
    """Non-integer `depth` should fall back to a 400 from FastAPI's
    query-validation layer (handled by the global validation handler)."""
    resp = client.get("/api/tree", params={"depth": "not-a-number"})
    # FastAPI raises a 422 for query-param validation by default.
    assert resp.status_code in (400, 422)
