"""Integration tests for session_routes using a real SessionManager + TestClient.

NOTE: There is a route-ordering bug in apollo/projects/session_routes.py — the
``POST /api/session/chat/{session_id}`` handler is registered before
``POST /api/session/chat/new`` and ``POST /api/session/chat/search``, so the
``new`` and ``search`` paths are shadowed and unreachable via HTTP. To still
exercise the create/search/list code paths, those tests create sessions
directly through the manager.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apollo.projects.session import SessionManager
from apollo.projects.session_routes import router, get_session_manager


@pytest.fixture
def manager(tmp_path):
    return SessionManager(sessions_root=str(tmp_path))


@pytest.fixture
def client(manager):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session_manager] = lambda: manager
    return TestClient(app)


def _make_session(manager, title="Test"):
    """Helper: create a chat session directly via the manager (route shadowed)."""
    from ulid import ULID
    sid = str(ULID())
    manager.create_chat_session(session_id=sid, title=title)
    return sid


class TestSessionState:
    def test_get_current_state(self, client):
        resp = client.get("/api/session/current")
        assert resp.status_code == 200
        body = resp.json()
        assert "window_state" in body
        assert "current_project_id" in body

    def test_set_current_project(self, client):
        resp = client.post("/api/session/project/proj-1")
        assert resp.status_code == 200
        assert resp.json()["project_id"] == "proj-1"

    def test_clear_current_project(self, client):
        client.post("/api/session/project/proj-1")
        resp = client.delete("/api/session/project")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestWindowState:
    def test_update_window_full(self, client):
        resp = client.post(
            "/api/session/window?width=1024&height=768"
            "&sidebar_open=true&sidebar_width=300&theme=dark"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["theme"] == "dark"
        assert body["width"] == 1024

    def test_update_window_partial(self, client):
        resp = client.post("/api/session/window?theme=light")
        assert resp.status_code == 200
        assert resp.json()["theme"] == "light"

    def test_invalid_theme(self, client):
        resp = client.post("/api/session/window?theme=neon")
        assert resp.status_code == 400


class TestChatSessions:
    def test_get_existing_chat(self, client, manager):
        sid = _make_session(manager, title="Hello")
        resp = client.get(f"/api/session/chat/{sid}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Hello"

    def test_get_missing_chat(self, client):
        resp = client.get("/api/session/chat/does-not-exist")
        assert resp.status_code == 404

    def test_set_current_chat_unknown(self, client):
        resp = client.post("/api/session/chat/missing")
        assert resp.status_code == 404

    def test_set_current_chat(self, client, manager):
        sid = _make_session(manager)
        resp = client.post(f"/api/session/chat/{sid}")
        assert resp.status_code == 200

    def test_list_sessions(self, client, manager):
        _make_session(manager, title="One")
        _make_session(manager, title="Two")
        resp = client.get("/api/session/chat")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] >= 2

    def test_list_sessions_empty(self, client):
        resp = client.get("/api/session/chat")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_add_message(self, client, manager):
        sid = _make_session(manager)
        resp = client.post(
            f"/api/session/chat/{sid}/message?role=user&content=Hello"
        )
        assert resp.status_code == 200
        assert resp.json()["added"] is True

    def test_add_message_unknown_session(self, client):
        resp = client.post(
            "/api/session/chat/missing/message?role=user&content=x"
        )
        assert resp.status_code == 404

    def test_update_title(self, client, manager):
        sid = _make_session(manager)
        resp = client.put(f"/api/session/chat/{sid}/title?title=Updated")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated"

    def test_update_title_unknown(self, client):
        resp = client.put("/api/session/chat/missing/title?title=X")
        assert resp.status_code == 404

    def test_update_tags(self, client, manager):
        sid = _make_session(manager)
        resp = client.put(
            f"/api/session/chat/{sid}/tags",
            json=["tag1", "tag2"],
        )
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["tag1", "tag2"]

    def test_update_tags_unknown(self, client):
        resp = client.put("/api/session/chat/missing/tags", json=["x"])
        assert resp.status_code == 404

    def test_delete_session(self, client, manager):
        sid = _make_session(manager)
        resp = client.delete(f"/api/session/chat/{sid}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_unknown(self, client):
        resp = client.delete("/api/session/chat/missing")
        assert resp.status_code == 404


class TestCleanup:
    def test_cleanup_old(self, client):
        resp = client.post("/api/session/cleanup/old?days=30")
        assert resp.status_code == 200
        assert "deleted" in resp.json()

    def test_prune_large(self, client):
        resp = client.post("/api/session/cleanup/prune?max_messages=500")
        assert resp.status_code == 200
        assert "pruned" in resp.json()


class TestUnconfiguredDependency:
    def test_default_dependency_raises(self):
        with pytest.raises(NotImplementedError):
            get_session_manager()
