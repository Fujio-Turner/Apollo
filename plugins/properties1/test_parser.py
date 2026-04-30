"""Self-contained tests for the properties1 plugin."""
from __future__ import annotations

import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.properties1 import PropertiesParser


class TestPropertiesPluginDiscovery:
    def test_properties_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, PropertiesParser) for p in plugins)


class TestPropertiesPluginRecognisesFile:
    def test_recognises_properties_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ["app.properties", "config.props"]:
                f = Path(tmp) / name
                f.write_text("")
                assert PropertiesParser().can_parse(str(f))

    def test_rejects_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.ini"
            f.write_text("")
            assert not PropertiesParser().can_parse(str(f))


class TestPropertiesPluginParsesFile:
    def test_parses_valid_properties(self):
        content = """
# Application configuration
app.name=MyApp
app.version=1.0.0
app.debug=true

# Database settings
db.url=jdbc:postgresql://localhost:5432/mydb
db.username=admin
db.password=secret123
db.pool.size=10

# Logging
logging.level=INFO
logging.pattern=%d{yyyy-MM-dd HH:mm:ss}
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "app.properties"
            f.write_text(content)
            result = PropertiesParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "variables" in result
        assert "comments" in result
        assert len(result["variables"]) > 0
        var_names = {v["name"] for v in result["variables"]}
        # Keys are normalized, so "app.name" becomes "app_name"
        assert any("app" in v for v in var_names)

    def test_parses_colon_separated_properties(self):
        content = """
# Using colons as separator
key.one : value1
key.two : value2
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.props"
            f.write_text(content)
            result = PropertiesParser().parse_file(str(f))

        assert result is not None
        assert len(result["variables"]) > 0

    def test_returns_valid_for_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "empty.properties"
            f.write_text("")
            result = PropertiesParser().parse_file(str(f))

        assert result is not None
        assert "variables" in result


class TestPropertiesPluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "app.properties"
            f.write_text("")
            parser = PropertiesParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False
