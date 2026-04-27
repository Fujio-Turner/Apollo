"""Session state management for Apollo."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union, Any

logger = logging.getLogger(__name__)


@dataclass
class WindowState:
    """Captures browser/window state for UI restoration."""
    width: int = 1280
    height: int = 800
    sidebar_open: bool = True
    sidebar_width: int = 300
    theme: str = "dark"  # "light" or "dark"
    
    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "sidebar_open": self.sidebar_open,
            "sidebar_width": self.sidebar_width,
            "theme": self.theme,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> WindowState:
        return cls(**data)


@dataclass
class ChatSession:
    """Represents a single chat conversation session."""
    session_id: str  # ULID
    created_at: str  # ISO timestamp
    last_message_at: str  # ISO timestamp
    messages: list[dict] = field(default_factory=list)  # { role, content, id, timestamp }
    title: str = "New Chat"
    project_context: Optional[str] = None  # project_id, if scoped
    tags: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "last_message_at": self.last_message_at,
            "messages": self.messages,
            "title": self.title,
            "project_context": self.project_context,
            "tags": self.tags,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> ChatSession:
        return cls(**data)


@dataclass
class SessionData:
    """Current Apollo session state."""
    current_project_id: Optional[str] = None
    current_chat_session_id: Optional[str] = None
    window_state: WindowState = field(default_factory=WindowState)
    last_activity_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    def to_dict(self) -> dict:
        return {
            "current_project_id": self.current_project_id,
            "current_chat_session_id": self.current_chat_session_id,
            "window_state": self.window_state.to_dict(),
            "last_activity_at": self.last_activity_at,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> SessionData:
        ws = WindowState.from_dict(data.pop("window_state", {}))
        return cls(window_state=ws, **data)


class SessionManager:
    """Manages session state, chat history, and UI restoration."""
    
    def __init__(self, sessions_root: Optional[Union[str, Path]] = None):
        """Initialize session manager.
        
        Args:
            sessions_root: Root path for session storage. Defaults to data/sessions/ in Apollo root.
        """
        if sessions_root is None:
            root = Path(__file__).parent.parent.parent
            sessions_root = root / "data" / "sessions"
        
        self.root = Path(sessions_root)
        self.root.mkdir(parents=True, exist_ok=True)
        
        self.current_state_file = self.root / "current.json"
        self.chats_dir = self.root / "chats"
        self.chats_dir.mkdir(parents=True, exist_ok=True)
        
        self._current = self._load_current_state()
    
    def _load_current_state(self) -> SessionData:
        """Load current session state or create default."""
        if self.current_state_file.exists():
            try:
                with open(self.current_state_file) as f:
                    data = json.load(f)
                return SessionData.from_dict(data)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("failed to load session state from %s: %s; using defaults", self.current_state_file, e)
        
        return SessionData()
    
    def save_current(self) -> None:
        """Persist current session state."""
        self._current.last_activity_at = datetime.utcnow().isoformat() + "Z"
        with open(self.current_state_file, "w") as f:
            json.dump(self._current.to_dict(), f, indent=2)
    
    @property
    def current(self) -> SessionData:
        """Get current session state."""
        return self._current
    
    def set_current_project(self, project_id: Optional[str]) -> None:
        """Switch to a different project (affects graph/search context)."""
        self._current.current_project_id = project_id
        self.save_current()
    
    def set_current_chat_session(self, session_id: Optional[str]) -> None:
        """Switch to a different chat session."""
        self._current.current_chat_session_id = session_id
        self.save_current()
    
    def update_window_state(self, **kwargs) -> None:
        """Update window state fields (width, height, sidebar_open, etc)."""
        for key, value in kwargs.items():
            if hasattr(self._current.window_state, key):
                setattr(self._current.window_state, key, value)
        self.save_current()
    
    # Chat Session Management
    
    def create_chat_session(
        self,
        session_id: str,
        title: str = "New Chat",
        project_context: Optional[str] = None,
    ) -> ChatSession:
        """Create a new chat session."""
        now = datetime.utcnow().isoformat() + "Z"
        session = ChatSession(
            session_id=session_id,
            created_at=now,
            last_message_at=now,
            title=title,
            project_context=project_context,
        )
        self._save_chat_session(session)
        return session
    
    def get_chat_session(self, session_id: str) -> Optional[ChatSession]:
        """Load a chat session from disk."""
        path = self.chats_dir / f"{session_id}.json"
        if not path.exists():
            return None
        
        try:
            with open(path) as f:
                data = json.load(f)
            return ChatSession.from_dict(data)
        except (json.JSONDecodeError, ValueError):
            return None
    
    def list_chat_sessions(self, limit: int = 20) -> list[ChatSession]:
        """List all chat sessions, most recent first."""
        sessions = []
        for path in sorted(self.chats_dir.glob("*.json"), reverse=True, key=os.path.getctime)[:limit]:
            session = self.get_chat_session(path.stem)
            if session:
                sessions.append(session)
        return sessions
    
    def add_message_to_session(
        self,
        session_id: str,
        role: str,
        content: str,
        message_id: str,
    ) -> bool:
        """Add a message to a chat session."""
        session = self.get_chat_session(session_id)
        if not session:
            return False
        
        session.messages.append({
            "role": role,
            "content": content,
            "id": message_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })
        session.last_message_at = datetime.utcnow().isoformat() + "Z"
        self._save_chat_session(session)
        return True
    
    def delete_chat_session(self, session_id: str) -> bool:
        """Delete a chat session."""
        path = self.chats_dir / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False
    
    def update_chat_session_title(self, session_id: str, title: str) -> bool:
        """Update a chat session title."""
        session = self.get_chat_session(session_id)
        if not session:
            return False
        
        session.title = title
        self._save_chat_session(session)
        return True
    
    def tag_chat_session(self, session_id: str, tags: list[str]) -> bool:
        """Update tags for a chat session."""
        session = self.get_chat_session(session_id)
        if not session:
            return False
        
        session.tags = tags
        self._save_chat_session(session)
        return True
    
    def search_chat_sessions(self, query: str) -> list[ChatSession]:
        """Search chat sessions by title and content."""
        query_lower = query.lower()
        results = []
        
        for session in self.list_chat_sessions(limit=100):
            # Check title
            if query_lower in session.title.lower():
                results.append(session)
                continue
            
            # Check message content
            for msg in session.messages:
                if query_lower in msg.get("content", "").lower():
                    results.append(session)
                    break
        
        return results[:20]  # Return top 20
    
    def _save_chat_session(self, session: ChatSession) -> None:
        """Persist a chat session to disk."""
        path = self.chats_dir / f"{session.session_id}.json"
        with open(path, "w") as f:
            json.dump(session.to_dict(), f, indent=2)


class SessionCleaner:
    """Utilities for cleaning up old session data."""
    
    @staticmethod
    def delete_old_sessions(session_mgr: SessionManager, days: int = 30) -> int:
        """Delete chat sessions older than N days. Returns count deleted."""
        from datetime import timedelta, timezone
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = 0
        
        for session in session_mgr.list_chat_sessions(limit=1000):
            created = datetime.fromisoformat(session.created_at.replace("Z", "+00:00"))
            if created < cutoff:
                if session_mgr.delete_chat_session(session.session_id):
                    deleted += 1
        
        return deleted
    
    @staticmethod
    def prune_large_sessions(session_mgr: SessionManager, max_messages: int = 1000) -> int:
        """Truncate sessions with more than max_messages, keeping only most recent. Returns count pruned."""
        pruned = 0
        
        for session in session_mgr.list_chat_sessions(limit=1000):
            if len(session.messages) > max_messages:
                session.messages = session.messages[-max_messages:]
                session_mgr._save_chat_session(session)
                pruned += 1
        
        return pruned
