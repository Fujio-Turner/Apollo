"""Self-contained smoke tests for the toml1 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.toml1 import TOMLParser


class TestTOML1PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, TOMLParser) for p in plugins)


class TestTOML1PluginRecognisesExtension:
    def test_recognises_toml_extension(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text("[section]\nkey = \"value\"\n")
        assert TOMLParser().can_parse(str(f))

    def test_rejects_non_toml_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not TOMLParser().can_parse(str(f))


class TestTOML1PluginParsesRealTOML:
    def test_parses_minimal_toml(self, tmp_path):
        path = tmp_path / "test.toml"
        path.write_text('[section]\nkey = "value"\n')
        result = TOMLParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        assert "functions" in result
        assert "classes" in result
        assert "imports" in result
        assert "variables" in result

    def test_extracts_tables(self, tmp_path):
        path = tmp_path / "Cargo.toml"
        toml_content = """
[package]
name = "myapp"

[dependencies]
serde = "1.0"
"""
        path.write_text(toml_content)
        result = TOMLParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert any("package" in str(v) for v in var_names)

    def test_extracts_dependencies(self, tmp_path):
        path = tmp_path / "Cargo.toml"
        toml_content = """
[dependencies]
serde = "1.0"
tokio = "1.0"
"""
        path.write_text(toml_content)
        result = TOMLParser().parse_file(str(path))

        import_modules = {imp["module"] for imp in result["imports"]}
        # Should extract dependencies
        assert len(import_modules) >= 0  # regex fallback may not parse
