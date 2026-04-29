"""Tests for the Phase 2A loader work:

* ``apollo.projects.settings.load_plugin_config()`` merges on-disk
  config.json with user overrides from data/settings.json.
* ``plugins.discover_plugins()`` skips plugins whose merged config has
  ``"enabled": False`` and forwards the merged config to plugins whose
  ``__init__`` accepts a ``config`` kwarg.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from apollo.projects.settings import load_plugin_config


# ----------------------------------------------------------------------
# load_plugin_config()
# ----------------------------------------------------------------------

class TestLoadPluginConfig:
    def test_returns_on_disk_config_when_no_overrides(self, tmp_path):
        # No user settings.json yet — get the bundled config.
        cfg = load_plugin_config("python3", settings_path=tmp_path / "settings.json")
        assert cfg.get("enabled") is True
        assert ".py" in cfg.get("extensions", [])

    def test_user_override_wins_over_on_disk(self, tmp_path):
        sp = tmp_path / "settings.json"
        sp.write_text(json.dumps({
            "plugins": {
                "python3": {"config": {"enabled": False}}
            }
        }))
        cfg = load_plugin_config("python3", settings_path=sp)
        assert cfg.get("enabled") is False
        # Other keys still come from disk.
        assert ".py" in cfg.get("extensions", [])

    def test_stale_override_keys_are_dropped(self, tmp_path, caplog):
        sp = tmp_path / "settings.json"
        sp.write_text(json.dumps({
            "plugins": {
                "python3": {"config": {"this_key_does_not_exist": 42}}
            }
        }))
        with caplog.at_level("WARNING"):
            cfg = load_plugin_config("python3", settings_path=sp)
        assert "this_key_does_not_exist" not in cfg
        assert any("stale override" in rec.message for rec in caplog.records)

    def test_returns_empty_dict_for_unknown_plugin(self, tmp_path):
        cfg = load_plugin_config("nonexistent_plugin_xyz",
                                 settings_path=tmp_path / "settings.json")
        assert cfg == {}

    def test_description_sibling_keys_are_stripped(self, tmp_path):
        # On-disk config.json files ship ``_<key>`` description siblings
        # for the Settings UI. Those are docs, not runtime data, so the
        # merged dict the parser receives must never contain them.
        cfg = load_plugin_config("python3",
                                 settings_path=tmp_path / "settings.json")
        assert cfg, "python3 plugin should ship a non-empty config"
        for key in cfg:
            assert not key.startswith("_"), (
                f"runtime config still contains description sibling {key!r}"
            )
        # The matching real keys are still present.
        assert "enabled" in cfg
        assert "extensions" in cfg


# ----------------------------------------------------------------------
# discover_plugins() with disabled plugin
# ----------------------------------------------------------------------

class TestDiscoverWithDisabledPlugin:
    def test_disabled_plugin_is_skipped_end_to_end(self, tmp_path, monkeypatch):
        # Write a settings.json that disables markdown_gfm and place it
        # where load_plugin_config() will find it (the project's
        # default ``data/settings.json`` path). Use monkeypatch on the
        # ``Path.exists`` / ``open`` machinery indirectly by pointing
        # the loader at our temporary settings via ``settings_path``…
        # but ``discover_plugins()`` calls ``load_plugin_config(name)``
        # without that arg, so we monkeypatch the module-level helper.
        from apollo.projects import settings as settings_mod
        from apollo import plugins as plugins_pkg

        original = settings_mod.load_plugin_config

        def fake_loader(name, settings_path=None):
            base = original(name, settings_path=tmp_path / "settings.json")
            if name == "markdown_gfm":
                base = {**base, "enabled": False}
            return base

        # Patch the *binding* used inside discover_plugins (it does a
        # local import of load_plugin_config from the settings module).
        monkeypatch.setattr(settings_mod, "load_plugin_config", fake_loader)

        plugins = plugins_pkg.discover_plugins()
        names = {type(p).__name__ for p in plugins}
        assert "MarkdownParser" not in names
        # Other built-ins still discovered.
        assert "PythonParser" in names

    def test_enabled_plugin_receives_merged_config(self, monkeypatch):
        """When a plugin's ``__init__`` accepts ``config``, it gets the
        merged dict from :func:`load_plugin_config`."""
        from apollo.projects import settings as settings_mod
        from apollo import plugins as plugins_pkg

        original = settings_mod.load_plugin_config

        def fake_loader(name, settings_path=None):
            base = original(name)
            if name == "python3":
                # Custom comment_tags via "user override" — exercise the
                # merge path that flows into the parser instance.
                base = {**base, "comment_tags": ["TODO", "REVIEW"]}
            return base

        monkeypatch.setattr(settings_mod, "load_plugin_config", fake_loader)

        plugins = plugins_pkg.discover_plugins()
        from plugins.python3 import PythonParser
        py = next(p for p in plugins if isinstance(p, PythonParser))
        assert py.config["comment_tags"] == ["TODO", "REVIEW"]
