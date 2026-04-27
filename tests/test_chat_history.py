"""Tests for chat.history.ChatHistory (JSON fallback path)."""
import json

import pytest

import chat.history as chat_history_module
from chat.history import ChatHistory


@pytest.fixture
def history(tmp_path, monkeypatch):
    """ChatHistory backed by a tmpdir JSON file (no CBL)."""
    monkeypatch.setattr(chat_history_module, "HISTORY_PATH", tmp_path / "chat_history.json")
    return ChatHistory(cbl_store=None)


class TestCreateThread:
    def test_creates_with_defaults(self, history):
        thread = history.create_thread()
        assert thread["title"] == "New Chat"
        assert thread["messages"] == []
        assert thread["model"] == ""
        assert "id" in thread
        assert "created_at" in thread
        assert "updated_at" in thread

    def test_creates_with_custom_title_and_model(self, history):
        thread = history.create_thread(title="Test", model="gpt-4")
        assert thread["title"] == "Test"
        assert thread["model"] == "gpt-4"

    def test_persists_to_disk(self, history):
        history.create_thread(title="A")
        threads = history.list_threads()
        assert any(t["title"] == "A" for t in threads)


class TestAddMessage:
    def test_appends_user_message(self, history):
        thread = history.create_thread()
        updated = history.add_message(thread["id"], "user", "Hello")
        assert updated is not None
        assert len(updated["messages"]) == 1
        assert updated["messages"][0]["role"] == "user"
        assert updated["messages"][0]["content"] == "Hello"

    def test_first_user_message_sets_title(self, history):
        thread = history.create_thread()
        updated = history.add_message(thread["id"], "user", "What is the meaning of life?")
        assert updated["title"] == "What is the meaning of life?"

    def test_long_first_user_message_truncates_title(self, history):
        thread = history.create_thread()
        long = "x" * 200
        updated = history.add_message(thread["id"], "user", long)
        assert updated["title"].endswith("...")
        assert len(updated["title"]) == 63  # 60 chars + "..."

    def test_assistant_first_does_not_set_title(self, history):
        thread = history.create_thread()
        updated = history.add_message(thread["id"], "assistant", "Hi there")
        assert updated["title"] == "New Chat"

    def test_returns_none_for_unknown_thread(self, history):
        assert history.add_message("does-not-exist", "user", "x") is None


class TestReplaceLastMessage:
    def test_replaces_when_role_matches(self, history):
        thread = history.create_thread()
        history.add_message(thread["id"], "user", "Q")
        history.add_message(thread["id"], "assistant", "A1")

        updated = history.replace_last_message(thread["id"], "assistant", "A2")
        assert updated is not None
        assert updated["messages"][-1]["content"] == "A2"

    def test_noop_when_role_mismatch(self, history):
        thread = history.create_thread()
        history.add_message(thread["id"], "user", "Q")
        result = history.replace_last_message(thread["id"], "assistant", "A2")
        assert result is None

    def test_noop_when_empty_thread(self, history):
        thread = history.create_thread()
        assert history.replace_last_message(thread["id"], "assistant", "x") is None

    def test_unknown_thread(self, history):
        assert history.replace_last_message("nope", "assistant", "x") is None


class TestGetThread:
    def test_returns_existing_thread(self, history):
        t = history.create_thread(title="A")
        loaded = history.get_thread(t["id"])
        assert loaded["id"] == t["id"]

    def test_returns_none_for_missing(self, history):
        assert history.get_thread("missing") is None


class TestListThreads:
    def test_empty(self, history):
        assert history.list_threads() == []

    def test_lists_summaries(self, history):
        t1 = history.create_thread(title="One")
        t2 = history.create_thread(title="Two")
        history.add_message(t1["id"], "user", "msg1")
        history.add_message(t2["id"], "user", "msg2a")
        history.add_message(t2["id"], "user", "msg2b")

        threads = history.list_threads()
        ids_to_count = {t["id"]: t["message_count"] for t in threads}
        assert ids_to_count[t1["id"]] == 1
        assert ids_to_count[t2["id"]] == 2

    def test_sorted_by_updated_at_desc(self, history):
        t1 = history.create_thread(title="Old")
        t2 = history.create_thread(title="Newer")
        history.add_message(t2["id"], "user", "bump")

        threads = history.list_threads()
        assert threads[0]["id"] == t2["id"]


class TestDeleteThread:
    def test_deletes_existing_thread(self, history):
        t = history.create_thread()
        assert history.delete_thread(t["id"]) is True
        assert history.get_thread(t["id"]) is None

    def test_deletes_missing_thread_returns_false(self, history):
        assert history.delete_thread("nope") is False


class TestLoadCorruptFile:
    def test_corrupt_json_returns_empty(self, tmp_path, monkeypatch):
        path = tmp_path / "chat_history.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json")
        monkeypatch.setattr(chat_history_module, "HISTORY_PATH", path)

        h = ChatHistory(cbl_store=None)
        assert h.list_threads() == []


class TestCblFallback:
    """When a CBL store is provided but raises, fall through to JSON path."""

    def test_failed_cbl_init_falls_back_to_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(chat_history_module, "HISTORY_PATH", tmp_path / "h.json")

        class BrokenCblShim:
            class cbl:
                @staticmethod
                def get_or_create_collection(_):
                    raise RuntimeError("boom")

        h = ChatHistory(cbl_store=BrokenCblShim())
        # Should not raise
        thread = h.create_thread(title="x")
        assert h.get_thread(thread["id"]) is not None
