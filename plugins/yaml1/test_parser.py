"""Self-contained smoke tests for the yaml1 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.yaml1 import YAMLParser


class TestYAML1PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, YAMLParser) for p in plugins)


class TestYAML1PluginRecognisesExtension:
    def test_recognises_yaml_extension(self, tmp_path):
        f = tmp_path / "config.yaml"
        f.write_text("key: value\n")
        assert YAMLParser().can_parse(str(f))

    def test_recognises_yml_extension(self, tmp_path):
        f = tmp_path / "config.yml"
        f.write_text("key: value\n")
        assert YAMLParser().can_parse(str(f))

    def test_rejects_non_yaml_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not YAMLParser().can_parse(str(f))


class TestYAML1PluginParsesRealYAML:
    def test_parses_minimal_yaml(self, tmp_path):
        path = tmp_path / "test.yaml"
        path.write_text("name: test\nversion: 1.0\n")
        result = YAMLParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        assert "functions" in result
        assert "classes" in result
        assert "imports" in result
        assert "variables" in result

    def test_extracts_top_level_keys(self, tmp_path):
        path = tmp_path / "test.yaml"
        path.write_text("name: test\nversion: 1.0\nauthor: me\n")
        result = YAMLParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert "name" in var_names
        assert "version" in var_names
        assert "author" in var_names

    def test_extracts_anchors(self, tmp_path):
        path = tmp_path / "test.yaml"
        path.write_text("defaults: &default_config\n  timeout: 30\nservice:\n  <<: *default_config\n")
        result = YAMLParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert any("default_config" in v for v in var_names)

    def test_extracts_includes(self, tmp_path):
        path = tmp_path / "test.yaml"
        path.write_text("config: !include ./other.yaml\n")
        result = YAMLParser().parse_file(str(path))

        import_modules = {imp["module"] for imp in result["imports"]}
        assert "./other.yaml" in import_modules
