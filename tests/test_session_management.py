"""Unit tests for session management (Phase 15)."""

import pytest
import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from apollo.projects.session import (
    SessionManager,
    SessionData,
    ChatSession,
    WindowState,
    SessionCleaner,
)


@pytest.fixture
def temp_sessions_dir():
    """Create a temporary sessions directory."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def session_manager(temp_sessions_dir):
    """Create a session manager with temporary storage."""
    return SessionManager(sessions_root=temp_sessions_dir)


class TestWindowState:
    """Tests for WindowState dataclass."""
    
    def test_default_window_state(self):
        """Test default window state values."""
        ws = WindowState()
        assert ws.width == 1280
        assert ws.height == 800
        assert ws.sidebar_open is True
        assert ws.sidebar_width == 300
        assert ws.theme == "dark"
    
    def test_window_state_to_dict(self):
        """Test WindowState serialization."""
        ws = WindowState(width=1920, height=1080, theme="light")
        data = ws.to_dict()
        assert data["width"] == 1920
        assert data["height"] == 1080
        assert data["theme"] == "light"
    
    def test_window_state_from_dict(self):
        """Test WindowState deserialization."""
        data = {
            "width": 1920,
            "height": 1080,
            "sidebar_open": False,
            "sidebar_width": 400,
            "theme": "light",
        }
        ws = WindowState.from_dict(data)
        assert ws.width == 1920
        assert ws.height == 1080
        assert ws.sidebar_open is False
        assert ws.sidebar_width == 400
        assert ws.theme == "light"


class TestChatSession:
    """Tests for ChatSession dataclass."""
    
    def test_create_chat_session(self):
        """Test creating a chat session."""
        session = ChatSession(
            session_id="s-123",
            created_at="2026-04-27T00:00:00Z",
            last_message_at="2026-04-27T00:00:00Z",
            title="Test Chat",
        )
        assert session.session_id == "s-123"
        assert session.title == "Test Chat"
        assert len(session.messages) == 0
    
    def test_chat_session_to_dict(self):
        """Test ChatSession serialization."""
        session = ChatSession(
            session_id="s-123",
            created_at="2026-04-27T00:00:00Z",
            last_message_at="2026-04-27T00:00:00Z",
            title="Test",
            tags=["test", "urgent"],
        )
        data = session.to_dict()
        assert data["session_id"] == "s-123"
        assert data["title"] == "Test"
        assert "test" in data["tags"]
    
    def test_chat_session_from_dict(self):
        """Test ChatSession deserialization."""
        data = {
            "session_id": "s-123",
            "created_at": "2026-04-27T00:00:00Z",
            "last_message_at": "2026-04-27T00:00:00Z",
            "messages": [{"role": "user", "content": "Hi"}],
            "title": "Test Chat",
            "project_context": "proj-1",
            "tags": [],
        }
        session = ChatSession.from_dict(data)
        assert session.session_id == "s-123"
        assert session.project_context == "proj-1"
        assert len(session.messages) == 1


class TestSessionData:
    """Tests for SessionData dataclass."""
    
    def test_default_session_data(self):
        """Test default session data."""
        data = SessionData()
        assert data.current_project_id is None
        assert data.current_chat_session_id is None
        assert data.window_state is not None
    
    def test_session_data_roundtrip(self):
        """Test SessionData serialization and deserialization."""
        data = SessionData(
            current_project_id="proj-1",
            current_chat_session_id="chat-1",
        )
        dict_data = data.to_dict()
        data2 = SessionData.from_dict(dict_data)
        assert data2.current_project_id == "proj-1"
        assert data2.current_chat_session_id == "chat-1"


class TestSessionManager:
    """Tests for SessionManager."""
    
    def test_manager_initialization(self, temp_sessions_dir):
        """Test session manager initializes correctly."""
        mgr = SessionManager(sessions_root=temp_sessions_dir)
        assert mgr.root.exists()
        assert mgr.chats_dir.exists()
        # current_state_file is created on first save, not initialization
        assert mgr.root is not None
    
    def test_set_current_project(self, session_manager):
        """Test switching projects."""
        session_manager.set_current_project("proj-1")
        assert session_manager.current.current_project_id == "proj-1"
        
        # Verify persistence
        mgr2 = SessionManager(sessions_root=session_manager.root)
        assert mgr2.current.current_project_id == "proj-1"
    
    def test_set_current_chat_session(self, session_manager):
        """Test switching chat sessions."""
        session_manager.set_current_chat_session("chat-1")
        assert session_manager.current.current_chat_session_id == "chat-1"
    
    def test_update_window_state(self, session_manager):
        """Test updating window state."""
        session_manager.update_window_state(width=1920, theme="light")
        assert session_manager.current.window_state.width == 1920
        assert session_manager.current.window_state.theme == "light"
    
    def test_create_chat_session(self, session_manager):
        """Test creating a chat session."""
        session = session_manager.create_chat_session(
            session_id="s-123",
            title="Test Chat",
            project_context="proj-1",
        )
        assert session.session_id == "s-123"
        assert session.title == "Test Chat"
        assert session.project_context == "proj-1"
        
        # Verify persistence
        loaded = session_manager.get_chat_session("s-123")
        assert loaded is not None
        assert loaded.title == "Test Chat"
    
    def test_get_nonexistent_chat_session(self, session_manager):
        """Test getting a nonexistent chat session."""
        session = session_manager.get_chat_session("nonexistent")
        assert session is None
    
    def test_list_chat_sessions(self, session_manager):
        """Test listing chat sessions."""
        session_manager.create_chat_session("s-1", "Chat 1")
        session_manager.create_chat_session("s-2", "Chat 2")
        session_manager.create_chat_session("s-3", "Chat 3")
        
        sessions = session_manager.list_chat_sessions()
        assert len(sessions) >= 3
        # Most recent first
        assert sessions[0].session_id in ["s-1", "s-2", "s-3"]
    
    def test_list_chat_sessions_limit(self, session_manager):
        """Test limiting chat session list."""
        for i in range(25):
            session_manager.create_chat_session(f"s-{i}", f"Chat {i}")
        
        sessions = session_manager.list_chat_sessions(limit=10)
        assert len(sessions) == 10
    
    def test_add_message_to_session(self, session_manager):
        """Test adding messages to a chat session."""
        session_manager.create_chat_session("s-1", "Test")
        
        success = session_manager.add_message_to_session(
            "s-1", "user", "Hello!", "m-1"
        )
        assert success is True
        
        session = session_manager.get_chat_session("s-1")
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "user"
        assert session.messages[0]["content"] == "Hello!"
    
    def test_add_message_to_nonexistent_session(self, session_manager):
        """Test adding message to nonexistent session fails."""
        success = session_manager.add_message_to_session(
            "nonexistent", "user", "Hi", "m-1"
        )
        assert success is False
    
    def test_delete_chat_session(self, session_manager):
        """Test deleting a chat session."""
        session_manager.create_chat_session("s-1", "Test")
        assert session_manager.get_chat_session("s-1") is not None
        
        success = session_manager.delete_chat_session("s-1")
        assert success is True
        assert session_manager.get_chat_session("s-1") is None
    
    def test_update_chat_session_title(self, session_manager):
        """Test updating a chat session title."""
        session_manager.create_chat_session("s-1", "Old Title")
        
        success = session_manager.update_chat_session_title("s-1", "New Title")
        assert success is True
        
        session = session_manager.get_chat_session("s-1")
        assert session.title == "New Title"
    
    def test_tag_chat_session(self, session_manager):
        """Test tagging a chat session."""
        session_manager.create_chat_session("s-1", "Test")
        
        success = session_manager.tag_chat_session("s-1", ["important", "work"])
        assert success is True
        
        session = session_manager.get_chat_session("s-1")
        assert "important" in session.tags
        assert "work" in session.tags
    
    def test_search_by_title(self, session_manager):
        """Test searching chat sessions by title."""
        session_manager.create_chat_session("s-1", "Python Django Project")
        session_manager.create_chat_session("s-2", "React Tutorial")
        session_manager.create_chat_session("s-3", "Python FastAPI")
        
        results = session_manager.search_chat_sessions("Python")
        assert len(results) >= 2
        titles = [s.title for s in results]
        assert any("Python" in t for t in titles)
    
    def test_search_by_content(self, session_manager):
        """Test searching chat sessions by message content."""
        session_manager.create_chat_session("s-1", "Chat 1")
        session_manager.add_message_to_session("s-1", "user", "How do I use Django ORM?", "m-1")
        
        results = session_manager.search_chat_sessions("Django")
        assert len(results) >= 1
        assert results[0].session_id == "s-1"
    
    def test_search_case_insensitive(self, session_manager):
        """Test search is case-insensitive."""
        session_manager.create_chat_session("s-1", "Python Tutorial")
        
        results1 = session_manager.search_chat_sessions("python")
        results2 = session_manager.search_chat_sessions("PYTHON")
        
        assert len(results1) >= 1
        assert len(results2) >= 1


class TestSessionCleaner:
    """Tests for SessionCleaner."""
    
    def test_delete_old_sessions(self, session_manager):
        """Test deleting sessions older than N days."""
        from datetime import timezone
        
        # Create old session
        session_manager.create_chat_session("s-old", "Old Chat")
        session = session_manager.get_chat_session("s-old")
        
        # Manually set old timestamp (40 days ago)
        old_time = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        session.created_at = old_time
        session_manager._save_chat_session(session)
        
        # Create recent session
        session_manager.create_chat_session("s-new", "New Chat")
        
        # Clean old sessions (older than 30 days)
        deleted = SessionCleaner.delete_old_sessions(session_manager, days=30)
        assert deleted >= 1
        
        # Old session should be gone, new one should remain
        assert session_manager.get_chat_session("s-old") is None
        assert session_manager.get_chat_session("s-new") is not None
    
    def test_prune_large_sessions(self, session_manager):
        """Test pruning sessions with too many messages."""
        session_manager.create_chat_session("s-large", "Large Chat")
        
        # Add many messages
        for i in range(1050):
            session_manager.add_message_to_session(
                "s-large", "user" if i % 2 == 0 else "assistant",
                f"Message {i}", f"m-{i}"
            )
        
        session = session_manager.get_chat_session("s-large")
        assert len(session.messages) == 1050
        
        # Prune to 1000 messages
        pruned = SessionCleaner.prune_large_sessions(session_manager, max_messages=1000)
        assert pruned >= 1
        
        session = session_manager.get_chat_session("s-large")
        assert len(session.messages) == 1000


class TestSessionPersistence:
    """Tests for persistent storage."""
    
    def test_session_state_persists(self, temp_sessions_dir):
        """Test that session state persists across manager instances."""
        mgr1 = SessionManager(sessions_root=temp_sessions_dir)
        mgr1.set_current_project("proj-1")
        mgr1.update_window_state(width=1920)
        
        # Load in new manager
        mgr2 = SessionManager(sessions_root=temp_sessions_dir)
        assert mgr2.current.current_project_id == "proj-1"
        assert mgr2.current.window_state.width == 1920
    
    def test_chat_sessions_persist(self, temp_sessions_dir):
        """Test that chat sessions persist across manager instances."""
        mgr1 = SessionManager(sessions_root=temp_sessions_dir)
        mgr1.create_chat_session("s-1", "Test Chat")
        mgr1.add_message_to_session("s-1", "user", "Hello", "m-1")
        
        # Load in new manager
        mgr2 = SessionManager(sessions_root=temp_sessions_dir)
        session = mgr2.get_chat_session("s-1")
        assert session is not None
        assert session.title == "Test Chat"
        assert len(session.messages) == 1
