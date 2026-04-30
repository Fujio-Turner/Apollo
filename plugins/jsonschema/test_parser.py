"""Self-contained smoke tests for the jsonschema plugin."""
from __future__ import annotations

import json

from apollo.plugins import discover_plugins
from plugins.jsonschema import JSONSchemaParser


class TestJSONSchemaPluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, JSONSchemaParser) for p in plugins)


class TestJSONSchemaPluginRecognisesExtension:
    def test_recognises_schema_json(self, tmp_path):
        f = tmp_path / "user.schema.json"
        f.write_text("{}")
        assert JSONSchemaParser().can_parse(str(f))

    def test_rejects_non_schema_json(self, tmp_path):
        f = tmp_path / "config.json"
        f.write_text("{}")
        assert not JSONSchemaParser().can_parse(str(f))


class TestJSONSchemaPluginParsesRealSchema:
    def test_parses_minimal_schema(self, tmp_path):
        path = tmp_path / "test.schema.json"
        schema = {
            "type": "object",
            "properties": {}
        }
        path.write_text(json.dumps(schema))
        result = JSONSchemaParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        assert "functions" in result
        assert "classes" in result
        assert "imports" in result
        assert "variables" in result

    def test_extracts_schema_title(self, tmp_path):
        path = tmp_path / "user.schema.json"
        schema = {
            "title": "User",
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"}
            }
        }
        path.write_text(json.dumps(schema))
        result = JSONSchemaParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert "User" in var_names
        assert "name" in var_names
        assert "email" in var_names

    def test_extracts_definitions(self, tmp_path):
        path = tmp_path / "api.schema.json"
        schema = {
            "type": "object",
            "$defs": {
                "User": {"type": "object"},
                "Post": {"type": "object"}
            }
        }
        path.write_text(json.dumps(schema))
        result = JSONSchemaParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert "User" in var_names
        assert "Post" in var_names

    def test_extracts_schema_refs(self, tmp_path):
        path = tmp_path / "api.schema.json"
        schema = {
            "type": "object",
            "properties": {
                "author": {"$ref": "#/$defs/User"}
            }
        }
        path.write_text(json.dumps(schema))
        result = JSONSchemaParser().parse_file(str(path))

        imports = {imp["module"] for imp in result["imports"]}
        assert "#/$defs/User" in imports
