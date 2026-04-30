"""Self-contained smoke tests for the csv1 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.csv1 import CSVParser


class TestCSV1PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, CSVParser) for p in plugins)


class TestCSV1PluginRecognisesExtension:
    def test_recognises_csv_extension(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("name,age\nAlice,30\n")
        assert CSVParser().can_parse(str(f))

    def test_rejects_non_csv_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not CSVParser().can_parse(str(f))


class TestCSV1PluginParsesRealCSV:
    def test_parses_minimal_csv(self, tmp_path):
        path = tmp_path / "test.csv"
        path.write_text("name,age\nAlice,30\nBob,25\n")
        result = CSVParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        assert "functions" in result
        assert "classes" in result
        assert "imports" in result
        assert "variables" in result

    def test_extracts_headers(self, tmp_path):
        path = tmp_path / "test.csv"
        path.write_text("name,age,email\nAlice,30,alice@example.com\n")
        result = CSVParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert "name" in var_names
        assert "age" in var_names
        assert "email" in var_names

    def test_counts_rows(self, tmp_path):
        path = tmp_path / "test.csv"
        path.write_text("name,age\nAlice,30\nBob,25\nCarol,28\n")
        result = CSVParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        # Should have row count variable
        assert any("_rows=" in v for v in var_names)

    def test_handles_empty_csv(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("")
        result = CSVParser().parse_file(str(path))

        assert result is not None
        assert result["variables"] == []
