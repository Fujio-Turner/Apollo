"""Self-contained smoke tests for the python3 plugin.

These tests live **inside the plugin folder** so removing the plugin is
one ``rm -rf plugins/python3/`` away. The exhaustive parser unit tests
(many fixtures, edge cases) still live under ``tests/`` for now —
this file is the discoverability + contract check.
"""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.python3 import PythonParser


SAMPLE_PY = '''"""Sample module."""
import os

CONST = 42


def add(a, b):
    """Add two numbers."""
    return a + b


class Calc:
    """A tiny calculator."""

    def double(self, x):
        return add(x, x)
'''


class TestPython3PluginDiscovery:
    def test_python3_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, PythonParser) for p in plugins)


class TestPython3PluginRecognisesExtension:
    def test_recognises_py_extension(self, tmp_path):
        f = tmp_path / "m.py"
        f.write_text("x = 1\n")
        assert PythonParser().can_parse(str(f))

    def test_rejects_non_py_extension(self, tmp_path):
        for name in ("note.md", "page.html", "doc.txt", "src.js"):
            f = tmp_path / name
            f.write_text("x")
            assert not PythonParser().can_parse(str(f))


class TestPython3PluginParsesRealPython:
    def test_parses_sample_module(self, tmp_path):
        path = tmp_path / "sample.py"
        path.write_text(SAMPLE_PY)
        result = PythonParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        # Required code-shape keys are present.
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

        names = {f["name"] for f in result["functions"]}
        assert "add" in names

        class_names = {c["name"] for c in result["classes"]}
        assert "Calc" in class_names

        import_modules = {i["module"] for i in result["imports"]}
        assert "os" in import_modules

        var_names = {v["name"] for v in result["variables"]}
        assert "CONST" in var_names

    def test_returns_none_for_syntax_error(self, tmp_path):
        f = tmp_path / "broken.py"
        f.write_text("def oops(:\n    pass\n")
        assert PythonParser().parse_file(str(f)) is None


class TestPython3PluginConfig:
    """Phase 2A: parser receives its merged config and respects ``enabled``."""

    def test_disabled_plugin_can_parse_returns_false(self, tmp_path):
        f = tmp_path / "m.py"
        f.write_text("x = 1\n")
        parser = PythonParser(config={"enabled": False})
        assert parser.can_parse(str(f)) is False

    def test_default_config_keeps_can_parse_true(self, tmp_path):
        f = tmp_path / "m.py"
        f.write_text("x = 1\n")
        assert PythonParser().can_parse(str(f)) is True

    def test_custom_comment_tags_are_honoured(self, tmp_path):
        src = "# REVIEW: look me over\nx = 1\n"
        f = tmp_path / "m.py"
        f.write_text(src)
        parser = PythonParser(config={"comment_tags": ["REVIEW"]})
        result = parser.parse_file(str(f))
        assert result is not None
        tags = {c["tag"] for c in result["comments"]}
        assert "REVIEW" in tags
