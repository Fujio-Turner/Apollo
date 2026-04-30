"""
Chat history persistence — stores conversation threads.

Uses Couchbase Lite when available (collection: "chat_threads"),
falls back to a JSON file at <project>/_apollo/chat_history.json
(or .apollo/chat_history.json when no project is open).

When a ``project_manager`` is supplied, thread storage is scoped to the
currently-open project so each folder keeps its own "Recents":
  * JSON backend: storage path follows the active project root.
  * CBL backend:  thread docs are tagged with ``project_id`` and
                  ``list_threads`` / ``get_thread`` filter on it.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Global fallback used when no project is open. Tests monkeypatch this.
HISTORY_PATH = Path(".apollo/chat_history.json")


class ChatHistory:
    """Manages chat thread persistence."""

    def __init__(self, cbl_store=None, project_manager=None):
        self._cbl = cbl_store
        self._collection = None
        self._project_manager = project_manager
        if self._cbl:
            try:
                self._collection = self._cbl.cbl.get_or_create_collection("chat_threads")
            except Exception:
                self._cbl = None

    # ── Project-scoping helpers ─────────────────────────────────
    def _current_project_id(self) -> Optional[str]:
        """Return the active project's id, or None if no project is open."""
        pm = self._project_manager
        if pm is None:
            return None
        try:
            manifest = pm.manifest
            if manifest is None:
                return None
            return getattr(manifest, "project_id", None)
        except Exception:
            return None

    def _history_path(self) -> Path:
        """Resolve the JSON history path, preferring the active project."""
        pm = self._project_manager
        if pm is not None:
            try:
                root = pm.root_dir
                if root is not None:
                    return Path(root) / "_apollo" / "chat_history.json"
            except Exception:
                pass
        return HISTORY_PATH

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_thread(self, title: str = "New Chat", model: str = "") -> dict:
        """Create a new empty chat thread and persist it."""
        thread = {
            "id": str(uuid.uuid4()),
            "title": title,
            "created_at": self._now(),
            "updated_at": self._now(),
            "model": model,
            "messages": [],
        }
        # Tag with the active project_id so listings can be scoped per
        # folder. Threads created before a project is opened (or with
        # no ProjectManager wired in) get an empty tag and remain
        # visible to "global" listings only.
        pid = self._current_project_id()
        if pid:
            thread["project_id"] = pid
        self._save_thread(thread)
        return thread

    def add_message(self, thread_id: str, role: str, content: str) -> dict | None:
        """Append a message to an existing thread."""
        thread = self.get_thread(thread_id)
        if not thread:
            return None
        thread["messages"].append({
            "role": role,
            "content": content,
            "timestamp": self._now(),
        })
        thread["updated_at"] = self._now()
        # Auto-title from first user message
        if thread["title"] == "New Chat" and role == "user":
            thread["title"] = content[:60] + ("..." if len(content) > 60 else "")
        self._save_thread(thread)
        return thread

    def replace_last_message(self, thread_id: str, role: str, content: str) -> dict | None:
        """Replace the last message of a thread (used for regenerate).

        Only replaces if the last message has the given role; otherwise no-op.
        Returns the updated thread, or None if not found.
        """
        thread = self.get_thread(thread_id)
        if not thread or not thread.get("messages"):
            return None
        last = thread["messages"][-1]
        if last.get("role") != role:
            return None
        last["content"] = content
        last["timestamp"] = self._now()
        thread["updated_at"] = self._now()
        self._save_thread(thread)
        return thread

    def get_thread(self, thread_id: str) -> dict | None:
        """Load a single thread by ID (scoped to the active project)."""
        active_pid = self._current_project_id()
        if self._cbl and self._collection:
            try:
                doc_json = self._cbl.cbl.get_document_json(self._collection, thread_id)
                if doc_json:
                    data = json.loads(doc_json)
                    data["id"] = thread_id
                    data.pop("_id", None)
                    # Project scoping: if a project is open, only return
                    # threads belonging to it (untagged threads are
                    # treated as global and remain visible).
                    if active_pid:
                        tpid = data.get("project_id")
                        if tpid and tpid != active_pid:
                            return None
                    return data
            except Exception:
                pass
            return None

        # JSON fallback
        threads = self._load_json()
        return threads.get(thread_id)

    def list_threads(self) -> list[dict]:
        """Return all threads (summary only: id, title, created_at, updated_at, model, message_count).

        When a project is open, the listing is filtered to threads
        belonging to that project (legacy untagged threads are excluded
        from per-project listings to avoid leaking history between
        folders).
        """
        active_pid = self._current_project_id()
        if self._cbl and self._collection:
            try:
                if active_pid:
                    rows = self._cbl.cbl.execute_query(
                        "SELECT META().id AS _id, title, created_at, updated_at, model, project_id, "
                        "ARRAY_LENGTH(messages) AS message_count "
                        "FROM chat_threads WHERE project_id = $pid ORDER BY updated_at DESC",
                        {"pid": active_pid},
                    )
                else:
                    rows = self._cbl.cbl.execute_query(
                        "SELECT META().id AS _id, title, created_at, updated_at, model, project_id, "
                        "ARRAY_LENGTH(messages) AS message_count "
                        "FROM chat_threads ORDER BY updated_at DESC"
                    )
                result = []
                for row in rows:
                    result.append({
                        "id": row.get("_id"),
                        "title": row.get("title", ""),
                        "created_at": row.get("created_at", ""),
                        "updated_at": row.get("updated_at", ""),
                        "model": row.get("model", ""),
                        "message_count": row.get("message_count", 0),
                    })
                return result
            except Exception:
                # Older Apollo CBL stores may not support parameterised
                # queries; fall back to in-memory filtering.
                try:
                    rows = self._cbl.cbl.execute_query(
                        "SELECT META().id AS _id, title, created_at, updated_at, model, project_id, "
                        "ARRAY_LENGTH(messages) AS message_count "
                        "FROM chat_threads ORDER BY updated_at DESC"
                    )
                    result = []
                    for row in rows:
                        if active_pid:
                            row_pid = row.get("project_id")
                            if row_pid and row_pid != active_pid:
                                continue
                            if not row_pid:
                                # Legacy untagged thread: hide from
                                # project-scoped listings.
                                continue
                        result.append({
                            "id": row.get("_id"),
                            "title": row.get("title", ""),
                            "created_at": row.get("created_at", ""),
                            "updated_at": row.get("updated_at", ""),
                            "model": row.get("model", ""),
                            "message_count": row.get("message_count", 0),
                        })
                    return result
                except Exception:
                    return []

        # JSON fallback — the on-disk file already lives inside the
        # active project's _apollo/ directory (see _history_path()), so
        # everything in it belongs to that project by construction.
        threads = self._load_json()
        result = []
        for tid, t in threads.items():
            result.append({
                "id": tid,
                "title": t.get("title", ""),
                "created_at": t.get("created_at", ""),
                "updated_at": t.get("updated_at", ""),
                "model": t.get("model", ""),
                "message_count": len(t.get("messages", [])),
            })
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return result

    def delete_thread(self, thread_id: str) -> bool:
        """Delete a thread by ID."""
        if self._cbl and self._collection:
            try:
                self._cbl.cbl.purge_document(self._collection, thread_id)
                return True
            except Exception:
                return False

        threads = self._load_json()
        if thread_id in threads:
            del threads[thread_id]
            self._save_json(threads)
            return True
        return False

    def _save_thread(self, thread: dict) -> None:
        tid = thread["id"]
        if self._cbl and self._collection:
            try:
                doc = {k: v for k, v in thread.items() if k != "id"}
                self._cbl.cbl.save_document_json(
                    self._collection, tid, json.dumps(doc, default=str)
                )
                return
            except Exception:
                pass

        # JSON fallback
        threads = self._load_json()
        threads[tid] = thread
        self._save_json(threads)

    def _load_json(self) -> dict:
        path = self._history_path()
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_json(self, threads: dict) -> None:
        path = self._history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(threads, f, separators=(",", ":"))
