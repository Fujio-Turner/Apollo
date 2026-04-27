"""HTTP API routes for session management."""

from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional
from datetime import datetime
from ulid import ULID

from .session import SessionManager, ChatSession

router = APIRouter(prefix="/api/session", tags=["session"])


# Dependency injection for SessionManager
# This will be set up in web/server.py via app.dependency_overrides
def get_session_manager() -> SessionManager:
    """Dependency that provides SessionManager instance."""
    # This is a placeholder; actual implementation will use the global app instance
    raise NotImplementedError("SessionManager dependency must be configured in FastAPI app")


# Session State Management

@router.get("/current")
async def get_current_session(manager: SessionManager = Depends(get_session_manager)):
    """Get current session state (project, chat, window)."""
    return manager.current.to_dict()


@router.post("/project/{project_id}")
async def set_current_project(
    project_id: str,
    manager: SessionManager = Depends(get_session_manager),
):
    """Switch to a different project."""
    manager.set_current_project(project_id)
    return {"status": "ok", "project_id": project_id}


@router.delete("/project")
async def clear_current_project(manager: SessionManager = Depends(get_session_manager)):
    """Clear current project selection."""
    manager.set_current_project(None)
    return {"status": "ok"}


@router.post("/chat/{session_id}")
async def set_current_chat(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
):
    """Switch to a chat session."""
    # Verify session exists
    session = manager.get_chat_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    
    manager.set_current_chat_session(session_id)
    return {"status": "ok", "session_id": session_id}


@router.post("/window")
async def update_window_state(
    width: Optional[int] = None,
    height: Optional[int] = None,
    sidebar_open: Optional[bool] = None,
    sidebar_width: Optional[int] = None,
    theme: Optional[str] = None,
    manager: SessionManager = Depends(get_session_manager),
):
    """Update window state (for UI restoration on reload)."""
    kwargs = {}
    if width is not None:
        kwargs["width"] = width
    if height is not None:
        kwargs["height"] = height
    if sidebar_open is not None:
        kwargs["sidebar_open"] = sidebar_open
    if sidebar_width is not None:
        kwargs["sidebar_width"] = sidebar_width
    if theme is not None:
        if theme not in ("light", "dark"):
            raise HTTPException(status_code=400, detail="Invalid theme")
        kwargs["theme"] = theme
    
    manager.update_window_state(**kwargs)
    return manager.current.window_state.to_dict()


# Chat Session Management

@router.post("/chat/new")
async def create_chat_session(
    title: Optional[str] = None,
    project_context: Optional[str] = None,
    manager: SessionManager = Depends(get_session_manager),
):
    """Create a new chat session."""
    session_id = str(ULID())
    session = manager.create_chat_session(
        session_id=session_id,
        title=title or "New Chat",
        project_context=project_context,
    )
    manager.set_current_chat_session(session_id)
    return session.to_dict()


@router.get("/chat/{session_id}")
async def get_chat_session(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
):
    """Get a chat session."""
    session = manager.get_chat_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session.to_dict()


@router.get("/chat")
async def list_chat_sessions(
    limit: int = Query(20, ge=1, le=100),
    manager: SessionManager = Depends(get_session_manager),
):
    """List recent chat sessions."""
    sessions = manager.list_chat_sessions(limit=limit)
    return {
        "sessions": [s.to_dict() for s in sessions],
        "count": len(sessions),
    }


@router.post("/chat/{session_id}/message")
async def add_message(
    session_id: str,
    role: str,
    content: str,
    manager: SessionManager = Depends(get_session_manager),
):
    """Add a message to a chat session."""
    message_id = str(ULID())
    success = manager.add_message_to_session(session_id, role, content, message_id)
    if not success:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"id": message_id, "added": True}


@router.put("/chat/{session_id}/title")
async def update_chat_title(
    session_id: str,
    title: str,
    manager: SessionManager = Depends(get_session_manager),
):
    """Update a chat session title."""
    success = manager.update_chat_session_title(session_id, title)
    if not success:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"session_id": session_id, "title": title}


@router.put("/chat/{session_id}/tags")
async def update_chat_tags(
    session_id: str,
    tags: list[str],
    manager: SessionManager = Depends(get_session_manager),
):
    """Update tags for a chat session."""
    success = manager.tag_chat_session(session_id, tags)
    if not success:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"session_id": session_id, "tags": tags}


@router.delete("/chat/{session_id}")
async def delete_chat_session(
    session_id: str,
    manager: SessionManager = Depends(get_session_manager),
):
    """Delete a chat session."""
    success = manager.delete_chat_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"deleted": True}


@router.post("/chat/search")
async def search_chat_sessions(
    query: str,
    manager: SessionManager = Depends(get_session_manager),
):
    """Search chat sessions by title and content."""
    if not query or len(query) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")
    
    sessions = manager.search_chat_sessions(query)
    return {
        "query": query,
        "results": [s.to_dict() for s in sessions],
        "count": len(sessions),
    }


# Cleanup & Maintenance

@router.post("/cleanup/old")
async def cleanup_old_sessions(
    days: int = Query(30, ge=1, le=365),
    manager: SessionManager = Depends(get_session_manager),
):
    """Delete chat sessions older than N days."""
    from .session import SessionCleaner
    deleted = SessionCleaner.delete_old_sessions(manager, days=days)
    return {"deleted": deleted, "days": days}


@router.post("/cleanup/prune")
async def prune_large_sessions(
    max_messages: int = Query(1000, ge=100, le=10000),
    manager: SessionManager = Depends(get_session_manager),
):
    """Truncate sessions with too many messages."""
    from .session import SessionCleaner
    pruned = SessionCleaner.prune_large_sessions(manager, max_messages=max_messages)
    return {"pruned": pruned, "max_messages": max_messages}
