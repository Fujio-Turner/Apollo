"""Self-contained tests for the gitignore1 plugin."""
from __future__ import annotations

import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.gitignore1 import GitIgnoreParser


class TestGitignorePluginDiscovery:
    def test_gitignore_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, GitIgnoreParser) for p in plugins)


class TestGitignorePluginRecognisesFile:
    def test_recognises_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / ".gitignore"
            f.write_text("")
            assert GitIgnoreParser().can_parse(str(f))

    def test_rejects_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "ignore.txt"
            f.write_text("")
            assert not GitIgnoreParser().can_parse(str(f))


class TestGitignorePluginParsesFile:
    def test_parses_valid_gitignore(self):
        content = """
# Dependencies
node_modules/
venv/

# Environment files
.env
.env.local

# Build artifacts
/dist/
/build/
*.pyc

# IDE
.vscode/
.idea/

# Negation
!important.pyc
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / ".gitignore"
            f.write_text(content)
            result = GitIgnoreParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "variables" in result
        assert "comments" in result
        assert len(result["variables"]) > 0


class TestGitignorePluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / ".gitignore"
            f.write_text("")
            parser = GitIgnoreParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False
