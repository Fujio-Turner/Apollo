# Phase 15 Integration Guide: Session Management in web/server.py

This guide shows how to integrate the Phase 15 session management system into the FastAPI application.

## Step 1: Import Session Classes

Add these imports to `web/server.py`:

```python
from apollo.projects.session import SessionManager
from apollo.projects.session_routes import router as session_router
```

## Step 2: Initialize SessionManager Singleton

In the FastAPI startup, create the session manager:

```python
# Near the top of the file, after app initialization
session_manager: Optional[SessionManager] = None

@app.on_event("startup")
async def startup_event():
    global session_manager
    root = Path(__file__).parent.parent
    session_manager = SessionManager(sessions_root=root / "data" / "sessions")
    print(f"✓ Session manager initialized: {session_manager.root}")
```

## Step 3: Set Up Dependency Override

Create a dependency function that FastAPI can use to inject the SessionManager:

```python
from fastapi import Depends

def get_session_manager_impl() -> SessionManager:
    """Provide SessionManager as a dependency."""
    if session_manager is None:
        raise RuntimeError("SessionManager not initialized")
    return session_manager

# Override the routes' dependency
from apollo.projects.session_routes import get_session_manager
app.dependency_overrides[get_session_manager] = get_session_manager_impl
```

## Step 4: Include the Router

After all other route includes:

```python
# Include session routes (should be last)
app.include_router(session_router)
print("✓ Session routes registered")
```

## Step 5: Test the Integration

Quick test in Python:

```python
import httpx

client = httpx.AsyncClient()

# Get current session
response = await client.get("http://localhost:8000/api/session/current")
assert response.status_code == 200
print(response.json())

# Create a new chat
response = await client.post(
    "http://localhost:8000/api/session/chat/new",
    params={"title": "Test Chat"}
)
assert response.status_code == 200
chat = response.json()
session_id = chat["session_id"]

# Add a message
response = await client.post(
    f"http://localhost:8000/api/session/chat/{session_id}/message",
    params={"role": "user", "content": "Hello!"}
)
assert response.status_code == 200

# List chats
response = await client.get("http://localhost:8000/api/session/chat")
assert response.status_code == 200
assert len(response.json()["sessions"]) > 0
```

## Step 6: Frontend Integration (app.js)

Update `web/static/app.js` to restore session state and persist changes:

```javascript
// On page load, restore session state
async function restoreSessionState() {
    try {
        const response = await fetch('/api/session/current');
        const state = await response.json();
        
        // Restore window dimensions
        window.resizeTo(state.window_state.width, state.window_state.height);
        
        // Restore theme
        document.documentElement.setAttribute('data-theme', state.window_state.theme);
        
        // Restore active project (if any)
        if (state.current_project_id) {
            await selectProject(state.current_project_id);
        }
        
        // Restore active chat (if any)
        if (state.current_chat_session_id) {
            await selectChat(state.current_chat_session_id);
        }
    } catch (e) {
        console.error('Failed to restore session:', e);
    }
}

// Persist window state on resize
window.addEventListener('resize', async () => {
    await fetch('/api/session/window', {
        method: 'POST',
        params: {
            width: window.innerWidth,
            height: window.innerHeight,
        }
    });
});

// Persist theme change
function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    fetch('/api/session/window', {
        method: 'POST',
        params: { theme }
    });
}

// When chat message is added
async function addMessageToChat(sessionId, role, content) {
    const response = await fetch(`/api/session/chat/${sessionId}/message`, {
        method: 'POST',
        params: { role, content }
    });
    return response.json();
}

// Call restoreSessionState() on DOMContentLoaded
document.addEventListener('DOMContentLoaded', restoreSessionState);
```

## Complete Minimal Example

Here's a complete minimal `web/server.py` addition:

```python
# At the top of web/server.py
from pathlib import Path
from typing import Optional
from apollo.projects.session import SessionManager
from apollo.projects.session_routes import router as session_router, get_session_manager

# Global session manager
session_manager: Optional[SessionManager] = None

@app.on_event("startup")
async def init_session_manager():
    """Initialize session manager on app startup."""
    global session_manager
    root = Path(__file__).parent.parent
    session_manager = SessionManager(sessions_root=root / "data" / "sessions")
    
    # Override dependency
    def get_session_manager_impl() -> SessionManager:
        if session_manager is None:
            raise RuntimeError("SessionManager not initialized")
        return session_manager
    
    app.dependency_overrides[get_session_manager] = get_session_manager_impl
    
    # Include routes
    app.include_router(session_router)
    
    print(f"✓ Session manager initialized at {session_manager.root}")

# ... rest of app initialization ...
```

## Testing the Integration

```bash
# Run the test suite
python3 -m pytest tests/test_session_management.py tests/test_session_routes.py -v

# Manual test with httpx
python3 -c "
import asyncio
import httpx

async def test():
    async with httpx.AsyncClient() as client:
        # Test GET current session
        r = await client.get('http://localhost:8000/api/session/current')
        print('Session state:', r.status_code, r.json())
        
        # Test create chat
        r = await client.post('http://localhost:8000/api/session/chat/new',
                             params={'title': 'Test'})
        print('Created chat:', r.status_code)

asyncio.run(test())
"
```

## Verification Checklist

- [ ] SessionManager imports without errors
- [ ] Session routes import without errors
- [ ] app.on_event("startup") initializes SessionManager
- [ ] Dependency override registered correctly
- [ ] Routes registered in FastAPI app
- [ ] GET /api/session/current returns 200
- [ ] POST /api/session/chat/new creates session
- [ ] POST /api/session/chat/{id}/message appends message
- [ ] Window state persists to disk
- [ ] Chat history persists across app restarts
- [ ] Frontend restores state on page reload

## Troubleshooting

### "SessionManager not initialized"
- Check that app.on_event("startup") is defined and called
- Verify app is running with correct root path

### "get_session_manager not found"
- Ensure `from apollo.projects.session_routes import get_session_manager`
- Check app.dependency_overrides assignment

### "Sessions not persisting"
- Verify `data/sessions/` directory exists and is writable
- Check file permissions: `ls -la data/sessions/`
- Look for errors in console output

### "Routes not responding"
- Check `app.include_router(session_router)` is called
- Verify routes are registered: `GET http://localhost:8000/openapi.json`
- Look for FastAPI errors in logs

## Next Steps

1. **Frontend UI**: Update app.js to call session endpoints
2. **Cleanup Job**: Add periodic cleanup task for old sessions
3. **Analytics**: Track session activity for metrics
4. **Cloud Sync**: Add optional cloud backup (Phase 15.2)
5. **Encryption**: Add session encryption at rest (Phase 15.1)
