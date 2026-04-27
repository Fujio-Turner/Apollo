"""Tests for web.routes_reindex FastAPI endpoints."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apollo.projects.manager import ProjectManager
from apollo.projects.reindex import ReindexHistory
from apollo.graph.incremental import ReindexStats
from web.routes_reindex import register_reindex_routes


def _make_stats(strategy: str = "resolve_local", duration_ms: int = 100) -> ReindexStats:
    return ReindexStats(
        strategy=strategy,
        started_at=time.time(),
        duration_ms=duration_ms,
        files_total=5,
        files_parsed=5,
        files_skipped=0,
        affected_files=1,
        edges_resolved=10,
        edges_added=2,
        edges_removed=1,
        bytes_written=512,
    )


@pytest.fixture
def project_root(tmp_path):
    """Initialise a real on-disk project so ProjectManager has manifest+root."""
    pm = ProjectManager(version="0.7.2")
    pm.open(str(tmp_path))
    return tmp_path, pm


@pytest.fixture
def client(project_root):
    """FastAPI test client with reindex routes mounted."""
    _, pm = project_root
    app = FastAPI()
    register_reindex_routes(app, pm)
    return TestClient(app)


@pytest.fixture
def history(project_root):
    """Reindex history for the open project."""
    root, _ = project_root
    return ReindexHistory(root)


class TestReindexHistoryEndpoint:
    def test_empty_history(self, client):
        resp = client.get("/api/index/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"total_runs": 0, "limit": 20, "runs": []}

    def test_history_returns_recent_runs(self, client, history):
        for i in range(3):
            history.append(_make_stats(duration_ms=i * 10))

        resp = client.get("/api/index/history?limit=2")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_runs"] == 3
        assert body["limit"] == 2
        assert len(body["runs"]) == 2
        # Newest runs (durations 10, 20)
        assert [r["duration_ms"] for r in body["runs"]] == [10, 20]

    def test_history_limit_validation(self, client):
        resp = client.get("/api/index/history?limit=0")
        assert resp.status_code == 422


class TestLastEndpoint:
    def test_no_runs(self, client):
        resp = client.get("/api/index/last")
        assert resp.status_code == 200
        assert resp.json() == {"has_run": False, "stats": None}

    def test_returns_latest(self, client, history):
        history.append(_make_stats(duration_ms=50))
        history.append(_make_stats(duration_ms=200))

        resp = client.get("/api/index/last")
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_run"] is True
        assert body["stats"]["duration_ms"] == 200


class TestSummaryEndpoint:
    def test_summary_empty(self, client):
        resp = client.get("/api/index/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["total_runs"] == 0
        assert body["configuration"]["strategy"] == "auto"

    def test_summary_with_history(self, client, history):
        history.append(_make_stats(duration_ms=100, strategy="full"))
        history.append(_make_stats(duration_ms=200, strategy="resolve_local"))

        resp = client.get("/api/index/summary")
        body = resp.json()
        assert body["summary"]["total_runs"] == 2
        assert body["summary"]["avg_duration_ms"] == 150


class TestConfigEndpoint:
    def test_get_config(self, client):
        resp = client.get("/api/index/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["strategy"] == "auto"
        assert body["effective_foreground_strategy"] == "resolve_local"
        assert body["effective_background_strategy"] == "resolve_full"

    def test_update_config_strategy(self, client):
        resp = client.post("/api/index/config?strategy=full")
        assert resp.status_code == 200
        body = resp.json()
        assert body["updated"] is True
        assert body["config"]["strategy"] == "full"

    def test_update_config_multiple_fields(self, client):
        resp = client.post(
            "/api/index/config?strategy=resolve_local&local_max_hops=3&sweep_interval_minutes=15"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["strategy"] == "resolve_local"
        assert body["config"]["local_max_hops"] == 3
        assert body["config"]["sweep_interval_minutes"] == 15

    def test_update_config_no_args(self, client):
        resp = client.post("/api/index/config")
        assert resp.status_code == 400

    def test_update_config_invalid_strategy(self, client):
        resp = client.post("/api/index/config?strategy=bogus")
        assert resp.status_code == 400

    def test_update_config_invalid_value(self, client):
        resp = client.post("/api/index/config?local_max_hops=0")
        assert resp.status_code == 400


class TestNoProjectOpen:
    """Endpoints reject calls when no project is open."""

    def test_history_without_project(self, tmp_path):
        pm = ProjectManager(version="0.7.2")  # never opened
        app = FastAPI()
        register_reindex_routes(app, pm)
        client = TestClient(app)

        resp = client.get("/api/index/history")
        assert resp.status_code == 400
        assert "No project is open" in resp.json()["detail"]
