"""Self-contained smoke tests for the json1 plugin."""
from __future__ import annotations

import json

from apollo.plugins import discover_plugins
from plugins.json1 import JSONParser


class TestJSON1PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, JSONParser) for p in plugins)


class TestJSON1PluginRecognisesExtension:
    def test_recognises_json_extension(self, tmp_path):
        f = tmp_path / "config.json"
        f.write_text("{}")
        assert JSONParser().can_parse(str(f))

    def test_rejects_non_json_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not JSONParser().can_parse(str(f))


class TestJSON1PluginParsesRealJSON:
    def test_parses_minimal_json(self, tmp_path):
        path = tmp_path / "test.json"
        path.write_text('{"name": "test", "version": "1.0"}')
        result = JSONParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        assert "functions" in result
        assert "classes" in result
        assert "imports" in result
        assert "variables" in result

    def test_extracts_top_level_keys(self, tmp_path):
        path = tmp_path / "test.json"
        path.write_text('{"name": "test", "version": "1.0", "author": "me"}')
        result = JSONParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert "name" in var_names
        assert "version" in var_names
        assert "author" in var_names

    def test_extracts_ref_references(self, tmp_path):
        path = tmp_path / "schema.json"
        schema = {
            "type": "object",
            "properties": {
                "user": {"$ref": "#/definitions/User"}
            }
        }
        path.write_text(json.dumps(schema))
        result = JSONParser().parse_file(str(path))

        import_modules = {imp["module"] for imp in result["imports"]}
        assert "#/definitions/User" in import_modules
