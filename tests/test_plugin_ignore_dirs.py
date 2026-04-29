"""Tests for the per-plugin ignore-set composition in ``GraphBuilder``.

Phase 2A wired each plugin's ``config.json["ignore_dirs"]`` into the
indexer's directory-pruning step. These tests verify two contracts:

1. When a plugin is **enabled**, its ``ignore_dirs`` are honoured —
   directories named in that list are not descended into.
2. When the same plugin is **disabled** (passed via an explicit empty
   ``config`` to the parser), its ignore set is no longer contributed,
   so those directories *are* descended into.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from apollo.graph.builder import GraphBuilder, _CORE_SKIP_DIRS, _compose_ignore_set
from plugins.python3 import PythonParser


def _make_project(root: Path) -> None:
    """Create a synthetic project layout with a ``venv/`` directory."""
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("def main():\n    return 1\n")
    venv = root / "venv"
    venv.mkdir()
    (venv / "should_not_be_indexed.py").write_text("x = 1\n")


class TestPluginContributedIgnoreDirs:
    """Confirm enabled plugins prune their configured ignore_dirs."""

    def test_enabled_python3_plugin_skips_venv_directory(self, tmp_path):
        _make_project(tmp_path)

        # Use the bundled plugin with its on-disk config (which lists
        # ``venv`` in ``ignore_dirs``). The composer pulls those into
        # GraphBuilder._skip_dirs.
        parser = PythonParser()
        # Sanity: the plugin's own config carries the entry we rely on.
        assert "venv" in parser.config["ignore_dirs"]

        builder = GraphBuilder(parsers=[parser])
        graph = builder.build(str(tmp_path))

        files = {
            data["path"]
            for _, data in graph.nodes(data=True)
            if data.get("type") == "file"
        }
        assert "src/main.py" in files
        assert not any(p.startswith("venv") for p in files), (
            f"venv/ should be pruned but found: {files}"
        )

    def test_disabled_python3_plugin_no_longer_contributes_ignore_dirs(
        self, tmp_path
    ):
        _make_project(tmp_path)

        # Force-empty the python3 plugin's ignore set by handing it a
        # config that overrides ``ignore_dirs`` to ``[]``. ``enabled``
        # stays True so it still parses .py files.
        parser = PythonParser(config={"ignore_dirs": [], "ignore_dir_markers": []})
        assert parser.config["ignore_dirs"] == []

        builder = GraphBuilder(parsers=[parser])
        graph = builder.build(str(tmp_path))

        files = {
            data["path"]
            for _, data in graph.nodes(data=True)
            if data.get("type") == "file"
        }
        assert "src/main.py" in files
        # Without the python3 ignore_dirs, the venv folder is descended.
        assert any(p.startswith("venv") for p in files), (
            f"venv/ should be descended into when ignore_dirs is empty: {files}"
        )


class TestComposeIgnoreSet:
    """Unit-level checks on :func:`_compose_ignore_set`."""

    def test_core_skip_dirs_always_present(self):
        dirs, _, _ = _compose_ignore_set(parsers=[])
        for required in (".git", "_apollo", "_apollo_web", ".apollo"):
            assert required in dirs

    def test_plugin_ignore_dirs_are_unioned(self):
        class _Stub:
            config = {
                "enabled": True,
                "ignore_dirs": ["my_thing", "another"],
                "ignore_files": ["*.bak"],
                "ignore_dir_markers": ["MY_MARKER"],
            }

        dirs, files, markers = _compose_ignore_set([_Stub()])
        assert "my_thing" in dirs
        assert "another" in dirs
        assert "*.bak" in files
        assert "MY_MARKER" in markers
        # Core entries still present.
        assert ".git" in dirs

    def test_parsers_without_config_attribute_contribute_nothing(self):
        class _Bare:
            pass

        dirs, files, markers = _compose_ignore_set([_Bare()])
        # Just the core baseline.
        assert dirs == _CORE_SKIP_DIRS
        assert files == []
        assert markers == ()
