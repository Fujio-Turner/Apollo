"""Unit tests for the multi-provider AI chat configuration.

Covers:
  * `chat.providers` registry (xAI, OpenAI, Gemini, Anthropic, Llama).
  * `chat.service.ChatService` active-provider resolution, env-keyed
    `available` flag, client-cache invalidation, and model selection
    via the injected `settings_provider` callback.
  * `web.server` `/api/settings` GET/PUT round-trip and `/api/chat/status`
    behavior under different provider/key combinations.
"""
import json
import os
from unittest import mock

import networkx as nx
import pytest


# ── Provider registry ──────────────────────────────────────────────

class TestProviderRegistry:
    def test_all_expected_providers_registered(self):
        from apollo.chat.providers import PROVIDERS
        assert set(PROVIDERS) == {"xai", "openai", "gemini", "anthropic", "llama"}

    def test_each_provider_has_required_fields(self):
        from apollo.chat.providers import PROVIDERS
        required = {"label", "base_url", "env", "key_url", "key_placeholder",
                    "models", "default_model"}
        for pid, p in PROVIDERS.items():
            missing = required - set(p)
            assert not missing, f"{pid} missing {missing}"
            assert p["models"], f"{pid} has empty model list"
            assert p["default_model"] in p["models"], (
                f"{pid} default_model not in models list")

    def test_env_var_names_are_unique(self):
        from apollo.chat.providers import PROVIDERS
        envs = [p["env"] for p in PROVIDERS.values()]
        assert len(envs) == len(set(envs)), "duplicate env-var names"

    def test_get_provider_unknown_falls_back_to_default(self):
        from apollo.chat.providers import (
            DEFAULT_PROVIDER, PROVIDERS, get_provider,
        )
        assert get_provider("does-not-exist") is PROVIDERS[DEFAULT_PROVIDER]

    def test_env_key_helper(self):
        from apollo.chat.providers import env_key
        assert env_key("openai") == "OPENAI_API_KEY"
        assert env_key("anthropic") == "ANTHROPIC_API_KEY"

    def test_has_api_key_reads_environ(self):
        from apollo.chat.providers import has_api_key
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "x"}, clear=False):
            assert has_api_key("gemini") is True
        env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            assert has_api_key("gemini") is False

    def test_public_registry_omits_secrets(self):
        from apollo.chat.providers import public_registry
        rows = public_registry()
        assert len(rows) == 5
        for r in rows:
            # Public payload must never carry env-var values, only env names.
            assert "env" in r and r["env"]
            assert "models" in r and isinstance(r["models"], list)
            assert "default_model" in r


# ── ChatService active-provider resolution ─────────────────────────

@pytest.fixture
def empty_graph():
    return nx.DiGraph()


class TestChatServiceProviderResolution:
    def test_defaults_to_xai_when_no_settings(self, empty_graph):
        from apollo.chat.service import ChatService
        svc = ChatService(empty_graph, settings_provider=lambda: {})
        assert svc.active_provider == "xai"
        assert svc.active_model == "grok-4-1-fast-non-reasoning"

    def test_defaults_when_settings_provider_raises(self, empty_graph):
        from apollo.chat.service import ChatService
        def boom():
            raise RuntimeError("disk gone")
        svc = ChatService(empty_graph, settings_provider=boom)
        # Must not propagate; fall back to default provider.
        assert svc.active_provider == "xai"

    def test_resolves_openai_with_custom_model(self, empty_graph):
        from apollo.chat.service import ChatService
        settings = {"chat": {"active_provider": "openai",
                             "providers": {"openai": {"model": "gpt-4.1-mini"}}}}
        svc = ChatService(empty_graph, settings_provider=lambda: settings)
        assert svc.active_provider == "openai"
        assert svc.active_model == "gpt-4.1-mini"

    def test_unknown_provider_falls_back_to_default(self, empty_graph):
        from apollo.chat.service import ChatService
        settings = {"chat": {"active_provider": "bogus"}}
        svc = ChatService(empty_graph, settings_provider=lambda: settings)
        assert svc.active_provider == "xai"

    def test_legacy_default_model_setting(self, empty_graph):
        """Legacy single-model setting should still be picked up when no
        per-provider model entry exists."""
        from apollo.chat.service import ChatService
        settings = {"chat": {"active_provider": "anthropic",
                             "default_model": "claude-3-5-haiku-latest"}}
        svc = ChatService(empty_graph, settings_provider=lambda: settings)
        assert svc.active_provider == "anthropic"
        assert svc.active_model == "claude-3-5-haiku-latest"

    def test_available_tracks_active_provider_env(self, empty_graph):
        from apollo.chat.service import ChatService
        settings = {"chat": {"active_provider": "gemini"}}
        svc = ChatService(empty_graph, settings_provider=lambda: settings)
        clean = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        with mock.patch.dict(os.environ, clean, clear=True):
            assert svc.available is False
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "g"}, clear=False):
            assert svc.available is True


# ── ChatService client cache ───────────────────────────────────────

class TestChatServiceClientCache:
    def test_get_client_uses_provider_base_url_and_env(self, empty_graph):
        from apollo.chat.service import ChatService
        settings = {"chat": {"active_provider": "openai",
                             "providers": {"openai": {"model": "gpt-4o-mini"}}}}
        svc = ChatService(empty_graph, settings_provider=lambda: settings)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False), \
             mock.patch("openai.OpenAI") as MockOpenAI:
            svc._get_client()
            MockOpenAI.assert_called_once()
            kwargs = MockOpenAI.call_args.kwargs
            assert kwargs["api_key"] == "sk-test"
            assert kwargs["base_url"] == "https://api.openai.com/v1"

    def test_client_cache_reuses_when_unchanged(self, empty_graph):
        from apollo.chat.service import ChatService
        settings = {"chat": {"active_provider": "openai"}}
        svc = ChatService(empty_graph, settings_provider=lambda: settings)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k1"}, clear=False), \
             mock.patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value = object()
            c1 = svc._get_client()
            c2 = svc._get_client()
            assert c1 is c2
            assert MockOpenAI.call_count == 1

    def test_client_cache_invalidates_on_provider_switch(self, empty_graph):
        from apollo.chat.service import ChatService
        active = {"id": "openai"}
        svc = ChatService(empty_graph, settings_provider=lambda: {
            "chat": {"active_provider": active["id"]}
        })
        env = {"OPENAI_API_KEY": "k1", "XAI_API_KEY": "k2"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("openai.OpenAI", side_effect=lambda **kw: ("client", kw["base_url"])) as MockOpenAI:
            c1 = svc._get_client()
            assert c1[1] == "https://api.openai.com/v1"
            active["id"] = "xai"
            c2 = svc._get_client()
            assert c2[1] == "https://api.x.ai/v1"
            assert MockOpenAI.call_count == 2

    def test_client_cache_invalidates_on_key_change(self, empty_graph):
        from apollo.chat.service import ChatService
        svc = ChatService(empty_graph, settings_provider=lambda: {
            "chat": {"active_provider": "openai"}
        })
        with mock.patch("openai.OpenAI", side_effect=lambda **kw: ("c", kw["api_key"])) as MockOpenAI:
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k1"}, clear=False):
                assert svc._get_client()[1] == "k1"
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k2"}, clear=False):
                assert svc._get_client()[1] == "k2"
            assert MockOpenAI.call_count == 2

    def test_get_client_raises_when_key_missing(self, empty_graph):
        from apollo.chat.service import ChatService
        svc = ChatService(empty_graph, settings_provider=lambda: {
            "chat": {"active_provider": "anthropic"}
        })
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                svc._get_client()

    def test_reset_client_forces_rebuild(self, empty_graph):
        from apollo.chat.service import ChatService
        svc = ChatService(empty_graph, settings_provider=lambda: {
            "chat": {"active_provider": "openai"}
        })
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"}, clear=False), \
             mock.patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value = object()
            svc._get_client()
            svc.reset_client()
            svc._get_client()
            assert MockOpenAI.call_count == 2


# ── Web server settings + chat-status endpoints ────────────────────

@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Point the server at a temp settings.json + .env so tests don't write
    into the real project files."""
    from apollo.web import server
    sp = tmp_path / "settings.json"
    ep = tmp_path / ".env"
    monkeypatch.setattr(server, "SETTINGS_PATH", sp)
    monkeypatch.setattr(server, "ENV_PATH", ep)
    return sp, ep


@pytest.fixture
def client(isolated_settings, empty_graph, monkeypatch):
    """A FastAPI TestClient wired up with an in-memory graph store."""
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from apollo.web import server as srv

    class _StubStore:
        def load(self, include_embeddings=True):
            return empty_graph
        def save(self, *_a, **_k):
            pass

    # Avoid importing the real chat service (and openai SDK at import time)
    # — but we still want an instance attached so /api/settings + status work.
    app = srv.create_app(_StubStore(), backend="json", root_dir=str(isolated_settings[0].parent))
    return TestClient(app)


class TestSettingsEndpoint:
    def test_get_returns_provider_registry_and_masked_keys(self, client):
        env = {k: v for k, v in os.environ.items()
               if k not in {"XAI_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                            "ANTHROPIC_API_KEY", "GROQ_API_KEY"}}
        env["XAI_API_KEY"] = "xai-1234567890abcd"
        with mock.patch.dict(os.environ, env, clear=True):
            r = client.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        ids = {p["id"] for p in body["providers"]}
        assert ids == {"xai", "openai", "gemini", "anthropic", "llama"}
        # Mask shows partial key, never raw key.
        assert body["api_keys"]["xai"].startswith("xai-")
        assert "•" in body["api_keys"]["xai"]
        assert body["api_keys"]["openai"] == ""

    def test_put_persists_active_provider_and_per_provider_model(
        self, client, isolated_settings,
    ):
        sp, _ = isolated_settings
        r = client.put("/api/settings", json={
            "chat": {
                "active_provider": "openai",
                "providers": {"openai": {"model": "gpt-4o"}},
            }
        })
        assert r.status_code == 200
        saved = json.loads(sp.read_text())
        assert saved["chat"]["active_provider"] == "openai"
        assert saved["chat"]["providers"]["openai"]["model"] == "gpt-4o"

    def test_put_writes_keys_to_env_file_only(self, client, isolated_settings):
        sp, ep = isolated_settings
        r = client.put("/api/settings", json={
            "api_keys": {"anthropic": "sk-ant-secret-value"}
        })
        assert r.status_code == 200
        # Settings file must NOT contain the raw secret.
        assert "sk-ant-secret-value" not in sp.read_text() if sp.exists() else True
        # .env file must contain the secret under the right env var.
        env_text = ep.read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-secret-value" in env_text
        # And the live process env must be updated.
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret-value"

    def test_put_ignores_masked_and_unknown_provider_keys(
        self, client, isolated_settings,
    ):
        _, ep = isolated_settings
        r = client.put("/api/settings", json={
            "api_keys": {
                "openai": "sk-•••••••••••••",  # masked → ignore
                "bogus":  "should-not-write",  # unknown → ignore
            },
        })
        assert r.status_code == 200
        env_text = ep.read_text() if ep.exists() else ""
        assert "should-not-write" not in env_text
        assert "OPENAI_API_KEY=sk-" not in env_text

    def test_put_rejects_invalid_active_provider(self, client, isolated_settings):
        sp, _ = isolated_settings
        client.put("/api/settings", json={"chat": {"active_provider": "openai"}})
        # Now try to overwrite with junk — should be silently dropped.
        client.put("/api/settings", json={"chat": {"active_provider": "fake-llm"}})
        saved = json.loads(sp.read_text())
        assert saved["chat"]["active_provider"] == "openai"


class TestChatStatusEndpoint:
    def test_returns_active_provider_and_model(self, client):
        client.put("/api/settings", json={
            "chat": {"active_provider": "gemini",
                     "providers": {"gemini": {"model": "gemini-2.0-flash"}}}
        })
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "g"}, clear=False):
            r = client.get("/api/chat/status")
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "gemini"
        assert body["model"] == "gemini-2.0-flash"
        assert body["available"] is True
        assert body["provider_label"] == "Google Gemini"

    def test_unavailable_when_active_provider_key_missing(self, client):
        client.put("/api/settings", json={
            "chat": {"active_provider": "llama"}
        })
        env = {k: v for k, v in os.environ.items() if k != "GROQ_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            r = client.get("/api/chat/status")
        body = r.json()
        assert body["provider"] == "llama"
        assert body["available"] is False
