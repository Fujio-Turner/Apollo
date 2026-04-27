"""Test API routes for project management."""

import pytest
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from apollo.projects.manager import ProjectManager
from apollo.projects.routes import register_project_routes
from fastapi import FastAPI


@pytest.fixture
def app():
    """Create a FastAPI app with project routes."""
    app = FastAPI()
    project_manager = ProjectManager(version="0.7.2")
    store = MagicMock()
    register_project_routes(app, project_manager, store, "json")
    return app, project_manager


@pytest.fixture
def client(app):
    """FastAPI test client."""
    return TestClient(app[0])


@pytest.fixture
def manager(app):
    """ProjectManager instance."""
    return app[1]


class TestProjectOpenRoute:
    """POST /api/projects/open"""

    def test_open_new_project(self, client, manager, tmp_path):
        """Opening a new folder returns needs_bootstrap=true."""
        response = client.post("/api/projects/open", json={"path": str(tmp_path)})
        assert response.status_code == 200
        data = response.json()
        assert data["needs_bootstrap"] is True
        assert data["project_id"].startswith("ap::")
        assert data["initial_index_completed"] is False

    def test_open_without_path(self, client):
        """Missing path returns 400."""
        response = client.post("/api/projects/open", json={})
        assert response.status_code == 400

    def test_open_invalid_path(self, client):
        """Non-existent path returns 400."""
        response = client.post("/api/projects/open", json={"path": "/nonexistent/path"})
        assert response.status_code == 400


class TestProjectInitRoute:
    """POST /api/projects/init"""

    def test_init_with_filters(self, client, tmp_path):
        """Initialize with custom filters."""
        filters = {
            "mode": "custom",
            "include_dirs": ["src", "docs"],
            "exclude_dirs": ["venv", "node_modules"],
            "exclude_file_globs": ["*.pyc", "*.lock"],
            "include_doc_types": ["py", "md"],
        }
        response = client.post(
            "/api/projects/init",
            json={"path": str(tmp_path), "filters": filters},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["filters"]["mode"] == "custom"
        assert data["filters"]["include_dirs"] == ["src", "docs"]

    def test_init_without_path(self, client):
        """Missing path returns 400."""
        response = client.post("/api/projects/init", json={"filters": {}})
        assert response.status_code == 400


class TestProjectCurrentRoute:
    """GET /api/projects/current"""

    def test_current_no_project(self, client):
        """No open project returns None."""
        response = client.get("/api/projects/current")
        assert response.status_code == 200
        assert response.json() is None

    def test_current_after_open(self, client, manager, tmp_path):
        """Returns ProjectInfo after opening."""
        # Open a project first
        client.post("/api/projects/open", json={"path": str(tmp_path)})
        
        # Now fetch current
        response = client.get("/api/projects/current")
        assert response.status_code == 200
        data = response.json()
        assert data is not None
        assert data["project_id"].startswith("ap::")


class TestProjectTreeRoute:
    """GET /api/projects/tree"""

    def test_tree_no_project(self, client):
        """No open project returns 400."""
        response = client.get("/api/projects/tree")
        assert response.status_code == 400

    def test_tree_with_project(self, client, manager, tmp_path):
        """Returns tree structure for current project."""
        # Create some test files/dirs
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# main")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text("# Readme")
        (tmp_path / "venv").mkdir()
        
        # Open the project
        client.post("/api/projects/open", json={"path": str(tmp_path)})
        
        # Get tree
        response = client.get("/api/projects/tree?depth=2")
        assert response.status_code == 200
        tree = response.json()
        
        # Check root
        assert tree["type"] == "dir"
        assert tree["path"] == "."
        assert tree["child_dir_count"] == 4  # src, docs, venv, _apollo (created by open)
        assert tree["child_file_count"] == 0
        
        # Check children
        children_names = [c["name"] for c in tree.get("children", [])]
        assert "src" in children_names
        assert "docs" in children_names


class TestProjectReprocessRoute:
    """POST /api/projects/reprocess"""

    def test_reprocess_no_project(self, client):
        """No open project returns 400."""
        response = client.post(
            "/api/projects/reprocess",
            json={"mode": "incremental"},
        )
        assert response.status_code == 400

    def test_reprocess_with_project(self, client, tmp_path):
        """Returns queued status after opening project."""
        # Open project
        client.post("/api/projects/open", json={"path": str(tmp_path)})
        
        # Reprocess
        response = client.post(
            "/api/projects/reprocess",
            json={"mode": "incremental"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["mode"] == "incremental"

    def test_reprocess_invalid_mode(self, client, tmp_path):
        """Invalid mode returns 400."""
        client.post("/api/projects/open", json={"path": str(tmp_path)})
        
        response = client.post(
            "/api/projects/reprocess",
            json={"mode": "invalid"},
        )
        assert response.status_code == 400


class TestProjectLeaveRoute:
    """POST /api/projects/leave"""

    def test_leave_requires_confirmation(self, client, tmp_path):
        """Leaving without confirm=true returns 400."""
        client.post("/api/projects/open", json={"path": str(tmp_path)})
        
        response = client.post("/api/projects/leave", json={"confirm": False})
        assert response.status_code == 400

    def test_leave_with_confirmation(self, client, tmp_path):
        """Leaving with confirm=true removes project."""
        client.post("/api/projects/open", json={"path": str(tmp_path)})
        
        response = client.post("/api/projects/leave", json={"confirm": True})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "removed"
        assert "_apollo" in data["deleted"][0]
        
        # Verify project is no longer open
        current = client.get("/api/projects/current")
        assert current.json() is None
