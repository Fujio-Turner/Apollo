"""Integration tests for session routes (Phase 15)."""

import pytest
from fastapi.testclient import TestClient
from tempfile import TemporaryDirectory
from pathlib import Path

# Note: This is a template for integration testing when session routes are integrated into web/server.py
# For now, we test the route functions directly


def test_session_routes_structure():
    """Test that session routes module can be imported."""
    from apollo.projects.session_routes import router
    assert router.prefix == "/api/session"
    assert len(router.routes) > 0


def test_session_routes_endpoints():
    """Test that session routes define expected endpoints."""
    from apollo.projects.session_routes import router
    
    # Collect all routes
    paths = set()
    for route in router.routes:
        paths.add(route.path)
    
    # Check expected endpoints exist (with /api/session prefix)
    expected = {
        "/api/session/current",
        "/api/session/project/{project_id}",
        "/api/session/chat/{session_id}",
        "/api/session/chat/new",
        "/api/session/chat",
        "/api/session/chat/{session_id}/message",
        "/api/session/chat/{session_id}/title",
        "/api/session/chat/{session_id}/tags",
        "/api/session/cleanup/old",
        "/api/session/cleanup/prune",
    }
    
    for path in expected:
        assert path in paths, f"Expected route {path} not found"


def test_session_manager_dependency_injection():
    """Test that SessionManager can be injected as a dependency."""
    from apollo.projects.session_routes import router
    from apollo.projects.session import SessionManager
    from tempfile import TemporaryDirectory
    
    # This would be set up in a FastAPI dependency override
    with TemporaryDirectory() as tmpdir:
        mgr = SessionManager(sessions_root=tmpdir)
        assert mgr is not None
        assert mgr.root.exists()


def test_session_routes_imports_no_errors():
    """Test that session route module imports cleanly."""
    try:
        from apollo.projects.session_routes import (
            get_current_session,
            set_current_project,
            create_chat_session,
            get_chat_session,
            list_chat_sessions,
            add_message,
            update_chat_title,
            update_chat_tags,
            delete_chat_session,
            search_chat_sessions,
            cleanup_old_sessions,
            prune_large_sessions,
        )
        assert callable(get_current_session)
        assert callable(create_chat_session)
    except ImportError as e:
        pytest.fail(f"Failed to import session routes: {e}")


def test_session_manager_in_projects_module():
    """Test that SessionManager is accessible from apollo.projects."""
    from apollo.projects.session import SessionManager, SessionData, ChatSession
    assert SessionManager is not None
    assert SessionData is not None
    assert ChatSession is not None


def test_session_exports():
    """Test that session module exports expected classes."""
    from apollo.projects import session
    
    assert hasattr(session, "SessionManager")
    assert hasattr(session, "SessionData")
    assert hasattr(session, "ChatSession")
    assert hasattr(session, "WindowState")
    assert hasattr(session, "SessionCleaner")
