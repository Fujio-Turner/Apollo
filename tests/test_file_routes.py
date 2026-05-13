"""Phase 13 — integration tests for the /api/file/* and /api/project/search HTTP endpoints."""
from __future__ import annotations

import textwrap
from pathlib import Path

import networkx as nx
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient").TestClient


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "calc.py").write_text(
        textwrap.dedent(
            '''\
            """Calc module."""
            import os

            def add(a, b):
                """Add."""
                return a + b

            class Calculator:
                def reset(self):
                    self.value = 0
            '''
        )
    )
    (tmp_path / "README.md").write_text("# hello\nneedle in markdown\n")
    return tmp_path


@pytest.fixture
def client(project_dir: Path):
    """Real FastAPI app over a synthesized in-memory graph for the test project."""
    G = nx.DiGraph()
    G.add_node("dir::.", type="directory", path=".", abs_path=str(project_dir))
    for rel in ("pkg/__init__.py", "pkg/calc.py", "README.md"):
        G.add_node(
            f"file::{rel}", type="file", path=rel,
            abs_path=str(project_dir / rel),
        )
    G.add_node(
        "dir::pkg", type="directory", path="pkg",
        abs_path=str(project_dir / "pkg"),
    )

    class _StubStore:
        backend = "json"
        _G = G

        def load(self, include_embeddings: bool = True):
            return self._G

    from web.server import create_app

    app = create_app(_StubStore(), backend="json", root_dir=str(project_dir))
    return fastapi_testclient(app)


# ── /api/file/stats ────────────────────────────────────────────────────────


def test_stats_indexed_python_file(client):
    resp = client.get("/api/file/stats", params={"path": "pkg/calc.py"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["language"] == "python"
    assert body["function_count"] >= 1
    assert body["class_count"] >= 1
    assert len(body["md5"]) == 32


def test_stats_path_outside_sandbox_returns_403(client):
    resp = client.get("/api/file/stats", params={"path": "/etc/passwd"})
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "FORBIDDEN"


def test_stats_missing_file_returns_404(client):
    resp = client.get("/api/file/stats", params={"path": "pkg/no_such.py"})
    assert resp.status_code == 404


# ── /api/file/section ──────────────────────────────────────────────────────


def test_section_returns_lines(client):
    resp = client.get(
        "/api/file/section",
        params={"path": "pkg/calc.py", "start": 1, "end": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["lines"]) == 3
    assert body["lines"][0]["n"] == 1


def test_section_md5_mismatch_returns_409(client):
    resp = client.get(
        "/api/file/section",
        params={"path": "pkg/calc.py", "start": 1, "end": 1, "md5": "0" * 32},
    )
    assert resp.status_code == 409


# ── /api/file/function ─────────────────────────────────────────────────────


def test_function_extracts_source(client):
    resp = client.get(
        "/api/file/function",
        params={"path": "pkg/calc.py", "name": "add"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "def add" in body["source"]
    assert body["kind"] == "FunctionDef"


def test_function_unknown_name_returns_404(client):
    resp = client.get(
        "/api/file/function",
        params={"path": "pkg/calc.py", "name": "no_such_fn"},
    )
    assert resp.status_code == 404


# ── /api/file/search ───────────────────────────────────────────────────────


def test_file_search_literal(client):
    resp = client.post(
        "/api/file/search",
        json={"path": "pkg/calc.py", "pattern": "def ", "regex": False, "context": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["match_count"] >= 1


def test_file_search_missing_required_returns_400(client):
    resp = client.post("/api/file/search", json={"path": "pkg/calc.py"})
    assert resp.status_code == 400


# ── /api/project/search ────────────────────────────────────────────────────


def test_project_search_finds_match_in_md(client):
    resp = client.post(
        "/api/project/search",
        json={"pattern": "needle", "regex": False, "file_glob": "*.md", "context": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["match_count"] >= 1
    for m in body["matches"]:
        assert m["path"].endswith(".md")


def test_project_search_missing_pattern_returns_400(client):
    resp = client.post("/api/project/search", json={})
    assert resp.status_code == 400


# ── Error envelope conformance ────────────────────────────────────────────


def test_error_responses_use_standard_envelope(client):
    resp = client.get("/api/file/stats", params={"path": "/etc/passwd"})
    body = resp.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]
    assert body["status_code"] == 403


# ── Phase 8: /api/files/outline, /api/files/declarations, /api/files/usages ─


def test_outline_route_returns_uniform_array(client):
    # File is in the graph but has no defines edges in this fixture, so
    # outline returns empty rows and accuracy='none' — but the route shape
    # must be valid + uniform.
    resp = client.get("/api/files/outline", params={"path": "pkg/calc.py"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "outline" in body and "accuracy" in body
    if body["outline"]:
        keys = {tuple(sorted(e.keys())) for e in body["outline"]}
        assert len(keys) == 1


def test_declarations_route_finds_python_defs_via_regex(client):
    # The fixture has no function nodes, so list_declarations falls back
    # to its regex pass — which still surfaces `def add(...)` and `class Calculator`.
    resp = client.get("/api/files/declarations", params={"path": "pkg/calc.py"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {d["name"] for d in body["declarations"]}
    assert "add" in names
    assert "Calculator" in names
    # Uniform shape — TOON-friendly.
    keys = {tuple(sorted(d.keys())) for d in body["declarations"]}
    assert len(keys) == 1


def test_usages_route_classifies_lines(client):
    resp = client.get(
        "/api/files/usages",
        params={"path": "pkg/calc.py", "symbol": "add"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any(u["kind"] == "declaration" and u["line_no"] >= 1
               for u in body["usages"])


def test_usages_route_requires_symbol(client):
    """When neither `symbol` nor `symbols` is given, the route 400s.

    Both params are optional individually (so callers can pick the
    single-symbol or batch shape), but at least one is required.
    """
    resp = client.get("/api/files/usages", params={"path": "pkg/calc.py"})
    assert resp.status_code == 400


def test_usages_route_batch_mode(client):
    """`symbols=a,b` triggers batch mode and returns the `results[]` shape."""
    resp = client.get(
        "/api/files/usages",
        params={"path": "pkg/calc.py", "symbols": "add,Calculator"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert {r["symbol"] for r in data["results"]} == {"add", "Calculator"}
    assert "total" in data
    # `add` appears at least as a declaration; `Calculator` appears as a class decl.
    by_sym = {r["symbol"]: r for r in data["results"]}
    assert by_sym["add"]["count"] >= 1
    assert by_sym["Calculator"]["count"] >= 1
