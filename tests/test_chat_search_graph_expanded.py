# SPDX-License-Identifier: BUSL-1.1
"""Tests for the `search_graph_expanded` chat tool (Phase D).

Drives :meth:`ChatService._exec_tool_impl` directly with a fake
embedder + small fixture graph so the contract that the LLM sees
(JSON shape, cluster fields, neighbor envelope) is pinned down.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import networkx as nx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeEmbedder:
    def embed_single(self, text):
        if "alpha" in text:
            return [1.0, 0.0]
        return [1.0, 1.0]


@pytest.fixture
def chat_service():
    from chat.service import ChatService
    from search.semantic import SemanticSearch

    g = nx.DiGraph()
    g.add_node(
        "func::a.py::seed_fn", name="seed_fn", type="function",
        path="a.py", line_start=10, line_end=20, embedding=[1.0, 0.0],
    )
    g.add_node(
        "func::a.py::caller_fn", name="caller_fn", type="function",
        path="a.py", line_start=1, line_end=5, embedding=[0.1, 0.1],
    )
    g.add_edge("func::a.py::caller_fn", "func::a.py::seed_fn", type="calls")

    embedder = _FakeEmbedder()
    search = SemanticSearch(g, embedder)
    return ChatService(g, search=search, embedder=embedder)


class TestDispatch:
    def test_returns_clustered_seed_with_caller_neighbor(self, chat_service):
        raw = chat_service._exec_tool_impl(
            "search_graph_expanded",
            {"query": "alpha", "top": 1, "expand": "callers", "depth": 1},
        )
        payload = json.loads(raw)
        assert payload["expand"] == "callers"
        assert payload["depth"] == 1
        assert len(payload["results"]) == 1
        seed = payload["results"][0]
        assert seed["id"] == "func::a.py::seed_fn"
        assert seed["neighbors"] and seed["neighbors"][0]["id"] == "func::a.py::caller_fn"
        n = seed["neighbors"][0]
        assert n["direction"] == "in"
        assert n["edge"] == "calls"
        assert n["depth"] == 1

    def test_default_expand_is_callers(self, chat_service):
        raw = chat_service._exec_tool_impl(
            "search_graph_expanded", {"query": "alpha"},
        )
        payload = json.loads(raw)
        assert payload["expand"] == "callers"


class TestWarningOnMissingEmbeddings:
    def test_returns_warning_when_no_embeddings(self):
        from chat.service import ChatService
        g = nx.DiGraph()
        g.add_node("func::a.py::foo", name="foo", type="function", path="a.py")
        service = ChatService(g)  # no search / no embedder
        raw = service._exec_tool_impl(
            "search_graph_expanded", {"query": "anything", "expand": "callers"},
        )
        payload = json.loads(raw)
        assert payload["results"] == []
        assert "warning" in payload
        assert "search_graph_expanded" in payload["warning"]


class TestToolRegistered:
    def test_tool_is_in_chat_request_catalog(self):
        catalog = json.loads(
            (Path(__file__).resolve().parents[1] / "ai" / "chat_request.json").read_text()
        )
        tool_names = [t["function"]["name"] for t in catalog["tools"]]
        assert "search_graph_expanded" in tool_names
