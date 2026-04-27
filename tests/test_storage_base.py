"""Tests for storage.base.GraphStore Protocol."""
from storage.base import GraphStore
from storage.json_store import JsonStore


class TestGraphStoreProtocol:
    def test_json_store_satisfies_protocol(self, tmp_path):
        store = JsonStore(str(tmp_path / "graph.json"))
        # runtime_checkable Protocol — runtime isinstance check
        assert isinstance(store, GraphStore)

    def test_arbitrary_object_does_not_satisfy(self):
        class NotAStore:
            pass

        assert not isinstance(NotAStore(), GraphStore)
