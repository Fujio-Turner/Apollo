"""Phase 2B — integration tests for the per-plugin config PATCH endpoint.

Covers the four cases called out in
``docs/work/PLAN_PLUGIN_CONFIGS.md``:

* happy path (a known key with a matching type),
* invalid key (rejected with 400, never persisted),
* invalid value type (rejected with 400, never persisted),
* flipping ``enabled`` (persisted + ``_reload_parsers`` swaps the live
  parser list so the disabled plugin disappears).
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient").TestClient


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Point both the server and the plugin loader at a temp settings.json.

    The web server's :data:`SETTINGS_PATH` is a CWD-relative
    ``data/settings.json`` while :func:`load_plugin_config` defaults to
    the apollo package root's ``data/settings.json``. We pin both onto
    the same temporary file so PATCH writes and subsequent reloads
    actually round-trip.
    """
    sp = tmp_path / "settings.json"
    sp.write_text("{}")

    from apollo.web import server
    from apollo.projects import settings as settings_mod

    monkeypatch.setattr(server, "SETTINGS_PATH", sp)

    original_loader = settings_mod.load_plugin_config

    def patched_loader(name, settings_path=None):
        return original_loader(name, settings_path=settings_path or sp)

    monkeypatch.setattr(settings_mod, "load_plugin_config", patched_loader)
    return sp


@pytest.fixture
def client(isolated_settings, tmp_path):
    """A FastAPI TestClient wired up with an empty in-memory graph store."""
    G = nx.DiGraph()

    class _StubStore:
        backend = "json"

        def load(self, include_embeddings=True):
            return G

        def save(self, *_a, **_k):
            pass

    from apollo.web import server as srv
    app = srv.create_app(_StubStore(), backend="json", root_dir=str(tmp_path))
    return fastapi_testclient(app)


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------

class TestPluginConfigHappyPath:
    def test_patch_persists_known_key_and_returns_merged_config(
        self, client, isolated_settings
    ):
        # markdown_gfm.config.json declares ``extract_frontmatter: true``.
        r = client.patch(
            "/api/settings/plugins/markdown_gfm/config",
            json={"extract_frontmatter": False},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "saved"
        assert body["plugin"] == "markdown_gfm"
        assert body["config"]["extract_frontmatter"] is False
        # _reload_parsers() returns the count; should be > 0 because the
        # remaining plugins are still active.
        assert body["active_parsers"] >= 1

        # Persisted to the temp settings file.
        saved = json.loads(isolated_settings.read_text())
        assert (
            saved["plugins"]["markdown_gfm"]["config"]["extract_frontmatter"]
            is False
        )

    def test_patch_merges_with_existing_override(
        self, client, isolated_settings
    ):
        # First write seeds an override.
        r1 = client.patch(
            "/api/settings/plugins/markdown_gfm/config",
            json={"extract_frontmatter": False},
        )
        assert r1.status_code == 200, r1.text
        # Second write must NOT erase the first.
        r2 = client.patch(
            "/api/settings/plugins/markdown_gfm/config",
            json={"extract_links": False},
        )
        assert r2.status_code == 200, r2.text
        merged = r2.json()["config"]
        assert merged["extract_frontmatter"] is False
        assert merged["extract_links"] is False


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------

class TestPluginConfigValidation:
    def test_unknown_plugin_is_404(self, client):
        r = client.patch(
            "/api/settings/plugins/no_such_plugin/config",
            json={"enabled": False},
        )
        assert r.status_code == 404

    def test_unknown_key_is_400_and_not_persisted(
        self, client, isolated_settings
    ):
        r = client.patch(
            "/api/settings/plugins/markdown_gfm/config",
            json={"this_key_is_not_real": 42},
        )
        assert r.status_code == 400
        # The plugin metadata block may exist (auto-mirrored from disk),
        # but no user override should be stored under it.
        saved = json.loads(isolated_settings.read_text())
        entry = (saved.get("plugins") or {}).get("markdown_gfm") or {}
        assert "config" not in entry

    def test_wrong_value_type_is_400_and_not_persisted(
        self, client, isolated_settings
    ):
        # ``extract_frontmatter`` defaults to a bool — sending a string
        # must be rejected.
        r = client.patch(
            "/api/settings/plugins/markdown_gfm/config",
            json={"extract_frontmatter": "yes please"},
        )
        assert r.status_code == 400
        saved = json.loads(isolated_settings.read_text())
        entry = (saved.get("plugins") or {}).get("markdown_gfm") or {}
        assert "config" not in entry

    def test_enabled_must_be_bool(self, client):
        r = client.patch(
            "/api/settings/plugins/markdown_gfm/config",
            json={"enabled": "off"},
        )
        assert r.status_code == 400

    def test_description_sibling_keys_are_rejected(self, client):
        # `_<key>` siblings are docs for the Settings UI, not runtime
        # values — patching them via the API must be rejected even
        # when the key technically exists in the on-disk config.json.
        r = client.patch(
            "/api/settings/plugins/markdown_gfm/config",
            json={"_enabled": "I'm just a docstring"},
        )
        assert r.status_code == 400
        assert "read-only" in r.json().get("error", {}).get("message", "").lower()


# ----------------------------------------------------------------------
# enabled flip → live parser reload
# ----------------------------------------------------------------------

class TestPluginEnabledFlipReloadsParsers:
    def test_disabling_plugin_drops_it_from_active_parsers(
        self, client
    ):
        from apollo.web import server as srv
        # Sanity: markdown_gfm is initially in the active parser list
        # (build_active_parsers ran at create_app() time).
        before = srv._build_active_parsers()
        assert any(
            type(p).__name__ == "MarkdownParser" for p in before
        ), "markdown_gfm should be discovered before the disable flip"

        r = client.patch(
            "/api/settings/plugins/markdown_gfm/config",
            json={"enabled": False},
        )
        assert r.status_code == 200, r.text

        # After the PATCH, a fresh discovery must skip markdown_gfm.
        after = srv._build_active_parsers()
        assert not any(
            type(p).__name__ == "MarkdownParser" for p in after
        ), "markdown_gfm should be skipped once enabled=False is persisted"
