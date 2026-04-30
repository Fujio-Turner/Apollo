"""Self-contained tests for sql1 plugin."""
from __future__ import annotations

from plugins.sql1 import SQLParser
from apollo.plugins import discover_plugins


class TestSQL1PluginDiscovery:
    def test_recognises_sql_extension(self, tmp_path):
        f = tmp_path / "schema.sql"
        f.write_text("SELECT 1;\n")
        assert SQLParser().can_parse(str(f))

    def test_rejects_non_sql_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not SQLParser().can_parse(str(f))


class TestSQL1PluginParsesRealCode:
    def test_parses_minimal_script(self, tmp_path):
        path = tmp_path / "schema.sql"
        path.write_text("SELECT 1;\n")
        result = SQLParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_tables(self, tmp_path):
        path = tmp_path / "schema.sql"
        path.write_text(
            "CREATE TABLE users (\n"
            "  id INT PRIMARY KEY,\n"
            "  name VARCHAR(100)\n"
            ");\n"
        )
        result = SQLParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        assert any(c["name"] == "users" for c in result["classes"])

    def test_extracts_views(self, tmp_path):
        path = tmp_path / "schema.sql"
        path.write_text(
            "CREATE VIEW user_summary AS\n"
            "SELECT id, name FROM users;\n"
        )
        result = SQLParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        assert any(c["name"] == "user_summary" for c in result["classes"])

    def test_extracts_functions(self, tmp_path):
        path = tmp_path / "schema.sql"
        path.write_text(
            "CREATE FUNCTION get_user(id INT)\n"
            "RETURNS TABLE(...)\n"
            "AS ...\n"
        )
        result = SQLParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1

    def test_extracts_variables(self, tmp_path):
        path = tmp_path / "schema.sql"
        path.write_text(
            "DECLARE @UserId INT;\n"
            "DECLARE @Name VARCHAR(100);\n"
        )
        result = SQLParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_class_has_required_keys(self, tmp_path):
        path = tmp_path / "schema.sql"
        path.write_text("CREATE TABLE test (id INT);\n")
        result = SQLParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "bases", "methods"):
                assert key in cls


class TestPluginIsDiscovered:
    def test_sql1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, SQLParser) for p in plugins), (
            "sql1 plugin missing PLUGIN export in __init__.py"
        )
