"""Self-contained tests for the editorconfig1 plugin."""
from __future__ import annotations

import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.editorconfig1 import EditorConfigParser


class TestEditorconfigPluginDiscovery:
    def test_editorconfig_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, EditorConfigParser) for p in plugins)


class TestEditorconfigPluginRecognisesFile:
    def test_recognises_editorconfig(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / ".editorconfig"
            f.write_text("")
            assert EditorConfigParser().can_parse(str(f))

    def test_rejects_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.ini"
            f.write_text("")
            assert not EditorConfigParser().can_parse(str(f))


class TestEditorconfigPluginParsesFile:
    def test_parses_valid_editorconfig(self):
        content = """
# EditorConfig root
root = true

[*]
charset = utf-8
end_of_line = lf
indent_style = space
indent_size = 2

[*.py]
indent_size = 4

[*.md]
trim_trailing_whitespace = false
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / ".editorconfig"
            f.write_text(content)
            result = EditorConfigParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "variables" in result
        assert "comments" in result
        assert len(result["variables"]) > 0


class TestEditorconfigPluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / ".editorconfig"
            f.write_text("")
            parser = EditorConfigParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False
