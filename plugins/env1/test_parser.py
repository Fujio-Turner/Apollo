"""Self-contained tests for the env1 plugin."""
from __future__ import annotations

import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.env1 import EnvParser


class TestEnvPluginDiscovery:
    def test_env_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, EnvParser) for p in plugins)


class TestEnvPluginRecognisesFile:
    def test_recognises_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in [".env", ".env.local", ".env.production"]:
                f = Path(tmp) / name
                f.write_text("")
                assert EnvParser().can_parse(str(f))

    def test_rejects_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "environ.txt"
            f.write_text("")
            assert not EnvParser().can_parse(str(f))


class TestEnvPluginParsesFile:
    def test_parses_valid_env_file(self):
        content = """
# Database configuration
DB_HOST=localhost
DB_PORT=5432
DB_USER=admin
DB_PASSWORD="secret123"
DB_NAME='mydb'

# API Keys
API_KEY=abc123xyz789
SECRET_KEY="my-secret-key"

# Feature flags
DEBUG=true
LOG_LEVEL=info
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / ".env"
            f.write_text(content)
            result = EnvParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "variables" in result
        assert "comments" in result
        assert len(result["variables"]) > 0
        var_names = {v["name"] for v in result["variables"]}
        assert "DB_HOST" in var_names
        assert "API_KEY" in var_names

    def test_returns_valid_for_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / ".env"
            f.write_text("")
            result = EnvParser().parse_file(str(f))

        assert result is not None
        assert "variables" in result


class TestEnvPluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / ".env"
            f.write_text("")
            parser = EnvParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False
