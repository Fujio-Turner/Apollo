"""Self-contained tests for the jupyter1 plugin."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.jupyter1 import JupyterParser


class TestJupyterPluginDiscovery:
    def test_jupyter_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, JupyterParser) for p in plugins)


class TestJupyterPluginRecognisesExtension:
    def test_recognises_ipynb_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "notebook.ipynb"
            f.write_text("{}")
            assert JupyterParser().can_parse(str(f))

    def test_rejects_non_ipynb_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("note.md", "page.html", "doc.txt"):
                f = Path(tmp) / name
                f.write_text("{}")
                assert not JupyterParser().can_parse(str(f))


class TestJupyterPluginParsesNotebooks:
    def test_parses_valid_notebook(self):
        nb = {
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["import os\n", "x = 42"],
                },
                {
                    "cell_type": "markdown",
                    "source": ["# Title"],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.ipynb"
            f.write_text(json.dumps(nb))
            result = JupyterParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "functions" in result
        assert "imports" in result
        assert "variables" in result
        assert len(result["functions"]) > 0
        assert len(result["variables"]) > 0

    def test_returns_none_for_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "bad.ipynb"
            f.write_text("{invalid json")
            assert JupyterParser().parse_file(str(f)) is None


class TestJupyterPluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "m.ipynb"
            f.write_text("{}")
            parser = JupyterParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False

    def test_default_config_keeps_can_parse_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "m.ipynb"
            f.write_text("{}")
            assert JupyterParser().can_parse(str(f)) is True
