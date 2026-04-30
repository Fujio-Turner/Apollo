"""Self-contained smoke tests for the openapi3 plugin."""
from __future__ import annotations

import json

from apollo.plugins import discover_plugins
from plugins.openapi3 import OpenAPI3Parser


class TestOpenAPI3PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, OpenAPI3Parser) for p in plugins)


class TestOpenAPI3PluginRecognisesExtension:
    def test_recognises_openapi_json(self, tmp_path):
        f = tmp_path / "openapi.json"
        f.write_text("{}")
        assert OpenAPI3Parser().can_parse(str(f))

    def test_recognises_swagger_yaml(self, tmp_path):
        f = tmp_path / "swagger.yaml"
        f.write_text("openapi: 3.0.0\n")
        assert OpenAPI3Parser().can_parse(str(f))

    def test_rejects_non_spec_json(self, tmp_path):
        f = tmp_path / "config.json"
        f.write_text("{}")
        assert not OpenAPI3Parser().can_parse(str(f))


class TestOpenAPI3PluginParsesRealSpec:
    def test_parses_minimal_openapi(self, tmp_path):
        path = tmp_path / "openapi.json"
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0"},
            "paths": {}
        }
        path.write_text(json.dumps(spec))
        result = OpenAPI3Parser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        assert "functions" in result
        assert "classes" in result
        assert "imports" in result
        assert "variables" in result

    def test_extracts_api_title(self, tmp_path):
        path = tmp_path / "openapi.json"
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "My API", "version": "1.0"},
            "paths": {}
        }
        path.write_text(json.dumps(spec))
        result = OpenAPI3Parser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert "My API" in var_names

    def test_extracts_endpoints(self, tmp_path):
        path = tmp_path / "openapi.json"
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0"},
            "paths": {
                "/users": {"get": {}},
                "/users/{id}": {"get": {}}
            }
        }
        path.write_text(json.dumps(spec))
        result = OpenAPI3Parser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert "/users" in var_names
        assert "/users/{id}" in var_names

    def test_extracts_schema_refs(self, tmp_path):
        path = tmp_path / "openapi.json"
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0"},
            "paths": {},
            "components": {
                "schemas": {
                    "User": {"$ref": "#/components/schemas/BaseUser"}
                }
            }
        }
        path.write_text(json.dumps(spec))
        result = OpenAPI3Parser().parse_file(str(path))

        imports = {imp["module"] for imp in result["imports"]}
        assert "#/components/schemas/BaseUser" in imports
