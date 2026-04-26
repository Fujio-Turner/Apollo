"""
Chat history persistence — stores conversation threads.

Uses Couchbase Lite when available (collection: "chat_threads"),
falls back to a JSON file at .apollo/chat_history.json.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


HISTORY_PATH = Path(".apollo/chat_history.json")


class ChatHistory:
    """Manages chat thread persistence."""

    def __init__(self, cbl_store=None):
        self._cbl = cbl_store
        self._collection = None
        if self._cbl:
            try:
                self._collection = self._cbl.cbl.get_or_create_collection("chat_threads")
            except Exception:
                self._cbl = None

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
        """Load a single thread by ID."""
        if self._cbl and self._collection:
            try:
                doc_json = self._cbl.cbl.get_document_json(self._collection, thread_id)
                if doc_json:
                    data = json.loads(doc_json)
                    data["id"] = thread_id
                    data.pop("_id", None)
                    return data
            except Exception:
                pass
            return None

        # JSON fallback
        threads = self._load_json()
        return threads.get(thread_id)

    def list_threads(self) -> list[dict]:
        """Return all threads (summary only: id, title, created_at, updated_at, model, message_count)."""
        if self._cbl and self._collection:
            try:
                rows = self._cbl.cbl.execute_query(
                    "SELECT META().id AS _id, title, created_at, updated_at, model, ARRAY_LENGTH(messages) AS message_count FROM chat_threads ORDER BY updated_at DESC"
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
                return []

        # JSON fallback
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
        if HISTORY_PATH.exists():
            try:
                with open(HISTORY_PATH) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_json(self, threads: dict) -> None:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "w") as f:
            json.dump(threads, f, separators=(",", ":"))
