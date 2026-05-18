# SPDX-License-Identifier: BUSL-1.1
"""Integration tests for the `/api/search` endpoint with combined
semantic + graph-expansion params (Phase C).
"""
from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeEmbedder:
    def embed_single(self, text):
        if "alpha" in text:
            return [1.0, 0.0]
        return [1.0, 1.0]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Stand up the FastAPI app against a JSON store seeded with two
    nodes wired by a `calls` edge."""
    from fastapi.testclient import TestClient

    from apollo.storage.json_store import JsonStore
    import apollo.embeddings.embedder as embedder_module
    import apollo.web.server as server_module

    g = nx.DiGraph()
    g.add_node(
        "func::a.py::seed_fn", name="seed_fn", type="function",
        path="a.py", line_start=10, line_end=20, embedding=[1.0, 0.0],
    )
    g.add_node(
        "func::a.py::caller_fn", name="caller_fn", type="function",
        path="a.py", line_start=1, line_end=5, embedding=[0.1, 0.1],
    )
    g.add_edge("func::a.py::caller_fn", "func::a.py::seed_fn", type="calls")

    index_path = tmp_path / "graph.json"
    JsonStore(str(index_path)).save(g)

    # Patch the embedder before create_app pulls it in.
    monkeypatch.setattr(embedder_module, "get_shared_embedder", lambda: _FakeEmbedder())

    # Point SETTINGS_PATH at an empty tmp file so create_app's "auto-open
    # last project" path is a no-op — otherwise the test session would
    # try to reopen the developer's real workspace and clobber our
    # fixture store with a real-embedded graph.
    fake_settings = tmp_path / "settings.json"
    fake_settings.write_text("{}")
    monkeypatch.setattr(server_module, "SETTINGS_PATH", fake_settings)
    # Reset the module-level settings cache so the patched path is read.
    server_module._SETTINGS_CACHE.update(
        {"mtime": -1.0, "payload": None, "plugin_check_at": 0.0}
    )

    store = JsonStore(str(index_path))
    app = server_module.create_app(store, backend="json")
    return TestClient(app)


class TestFlatShapeUnchanged:
    def test_default_returns_flat_results(self, client):
        r = client.get("/api/search", params={"q": "alpha", "top": 3})
        assert r.status_code == 200
        body = r.json()
        assert "results" in body
        # Every hit has the legacy field set, none gained a `neighbors`
        # field by accident.
        for hit in body["results"]:
            assert {"id", "name", "type", "path", "line_start", "score"} <= hit.keys()
            assert "neighbors" not in hit

    def test_explicit_expand_none_is_byte_identical(self, client):
        a = client.get("/api/search", params={"q": "alpha", "top": 3}).json()
        b = client.get(
            "/api/search", params={"q": "alpha", "top": 3, "expand": "none"},
        ).json()
        assert a == b


class TestExpandedShape:
    def test_expand_callers_returns_clusters(self, client):
        r = client.get(
            "/api/search",
            params={"q": "alpha", "top": 1, "expand": "callers", "depth": 1},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["results"]) == 1
        seed = body["results"][0]
        assert seed["id"] == "func::a.py::seed_fn"
        assert "neighbors" in seed
        ids = [n["id"] for n in seed["neighbors"]]
        assert "func::a.py::caller_fn" in ids
        n = next(n for n in seed["neighbors"] if n["id"] == "func::a.py::caller_fn")
        assert n["direction"] == "in"
        assert n["edge"] == "calls"
        assert n["depth"] == 1


class TestValidation:
    def test_unknown_expand_returns_422(self, client):
        r = client.get("/api/search", params={"q": "alpha", "expand": "bogus"})
        assert r.status_code == 422

    def test_zero_depth_with_expansion_returns_422(self, client):
        r = client.get(
            "/api/search",
            params={"q": "alpha", "expand": "callers", "depth": 0},
        )
        assert r.status_code == 422
