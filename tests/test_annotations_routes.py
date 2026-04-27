"""Integration tests for /api/annotations/* HTTP routes (Phase 11)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from apollo.projects.manager import ProjectManager
from apollo.projects.routes import register_project_routes


@pytest.fixture
def client(tmp_path):
    """FastAPI test client with an open project."""
    app = FastAPI()
    pm = ProjectManager(version="0.7.2")
    register_project_routes(app, pm, MagicMock(), "json")
    # Open a project so annotation routes have a valid manifest+root
    pm.open(str(tmp_path))
    return TestClient(app), pm


class TestAnnotationCRUDRoutes:
    def test_create_annotation(self, client):
        c, _ = client
        r = c.post("/api/annotations/create", json={
            "type": "highlight",
            "target": {"type": "file", "file_path": "src/foo.py"},
            "color": "yellow",
            "tags": ["todo"],
            "highlight_range": {"start_line": 1, "end_line": 5},
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["id"].startswith("an::")
        assert data["type"] == "highlight"

    def test_create_invalid_type_returns_400(self, client):
        c, _ = client
        r = c.post("/api/annotations/create", json={
            "type": "bogus",
            "target": {"type": "file", "file_path": "x.py"},
        })
        assert r.status_code == 400

    def test_create_invalid_target_returns_400(self, client):
        c, _ = client
        r = c.post("/api/annotations/create", json={
            "type": "highlight",
            "target": {"type": "file"},
        })
        assert r.status_code == 400

    def test_get_annotation(self, client):
        c, _ = client
        created = c.post("/api/annotations/create", json={
            "type": "bookmark",
            "target": {"type": "file", "file_path": "a.py"},
        }).json()
        r = c.get(f"/api/annotations/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_get_missing_returns_404(self, client):
        c, _ = client
        r = c.get("/api/annotations/an::missing")
        assert r.status_code == 404

    def test_update_annotation(self, client):
        c, _ = client
        created = c.post("/api/annotations/create", json={
            "type": "note",
            "target": {"type": "file", "file_path": "a.py"},
        }).json()
        r = c.put(f"/api/annotations/{created['id']}", json={
            "content": "new note",
            "tags": ["x", "y"],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["content"] == "new note"
        assert body["tags"] == ["x", "y"]

    def test_update_missing_returns_404(self, client):
        c, _ = client
        r = c.put("/api/annotations/an::nope", json={"content": "x"})
        assert r.status_code == 404

    def test_delete_annotation(self, client):
        c, _ = client
        created = c.post("/api/annotations/create", json={
            "type": "bookmark",
            "target": {"type": "file", "file_path": "a.py"},
        }).json()
        r = c.delete(f"/api/annotations/{created['id']}")
        assert r.status_code == 200
        # Now gone
        assert c.get(f"/api/annotations/{created['id']}").status_code == 404

    def test_delete_missing_returns_404(self, client):
        c, _ = client
        r = c.delete("/api/annotations/an::nope")
        assert r.status_code == 404


class TestAnnotationSearchRoutes:
    def test_by_target_file(self, client):
        c, _ = client
        c.post("/api/annotations/create", json={
            "type": "highlight", "target": {"type": "file", "file_path": "a.py"},
        })
        c.post("/api/annotations/create", json={
            "type": "highlight", "target": {"type": "file", "file_path": "b.py"},
        })
        r = c.get("/api/annotations/by-target", params={"file": "a.py"})
        assert r.status_code == 200
        results = r.json()["annotations"]
        assert len(results) == 1
        assert results[0]["target"]["file_path"] == "a.py"

    def test_by_target_node(self, client):
        c, _ = client
        c.post("/api/annotations/create", json={
            "type": "bookmark", "target": {"type": "node", "node_id": "n1"},
        })
        r = c.get("/api/annotations/by-target", params={"node": "n1"})
        assert r.status_code == 200
        assert len(r.json()["annotations"]) == 1

    def test_by_target_requires_query(self, client):
        c, _ = client
        r = c.get("/api/annotations/by-target")
        assert r.status_code == 400

    def test_by_tag(self, client):
        c, _ = client
        c.post("/api/annotations/create", json={
            "type": "note", "target": {"type": "file", "file_path": "a.py"},
            "tags": ["bug"],
        })
        c.post("/api/annotations/create", json={
            "type": "note", "target": {"type": "file", "file_path": "b.py"},
            "tags": ["bug", "p1"],
        })
        c.post("/api/annotations/create", json={
            "type": "note", "target": {"type": "file", "file_path": "c.py"},
            "tags": ["other"],
        })
        r = c.get("/api/annotations/by-tag", params={"tag": "bug"})
        assert r.status_code == 200
        assert len(r.json()["annotations"]) == 2


class TestAnnotationCollectionRoutes:
    def test_create_and_list_collections(self, client):
        c, _ = client
        ann = c.post("/api/annotations/create", json={
            "type": "bookmark", "target": {"type": "file", "file_path": "a.py"},
        }).json()
        r = c.post("/api/annotations/collections", json={
            "name": "critical",
            "annotation_ids": [ann["id"]],
        })
        assert r.status_code == 200, r.text
        coll = r.json()
        assert coll["id"].startswith("coll::")

        r2 = c.get("/api/annotations/collections")
        assert r2.status_code == 200
        assert len(r2.json()["collections"]) == 1

    def test_create_collection_requires_name(self, client):
        c, _ = client
        r = c.post("/api/annotations/collections", json={})
        assert r.status_code == 400

    def test_delete_collection(self, client):
        c, _ = client
        coll = c.post("/api/annotations/collections", json={"name": "x"}).json()
        r = c.delete(f"/api/annotations/collections/{coll['id']}")
        assert r.status_code == 200
        # Second delete: 404
        assert c.delete(f"/api/annotations/collections/{coll['id']}").status_code == 404


class TestAnnotationsNoProject:
    def test_routes_require_open_project(self, tmp_path):
        """When no project is open, annotation routes return 400."""
        app = FastAPI()
        pm = ProjectManager(version="0.7.2")
        register_project_routes(app, pm, MagicMock(), "json")
        c = TestClient(app)
        r = c.post("/api/annotations/create", json={
            "type": "highlight",
            "target": {"type": "file", "file_path": "a.py"},
        })
        assert r.status_code == 400
