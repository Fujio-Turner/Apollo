"""Self-contained tests for r1 plugin."""
from __future__ import annotations

from plugins.r1 import RParser
from apollo.plugins import discover_plugins


class TestR1PluginDiscovery:
    def test_recognises_r_extension(self, tmp_path):
        f = tmp_path / "script.R"
        f.write_text("x <- 1\n")
        assert RParser().can_parse(str(f))

    def test_recognises_lowercase_r_extension(self, tmp_path):
        f = tmp_path / "script.r"
        f.write_text("x <- 1\n")
        assert RParser().can_parse(str(f))

    def test_rejects_non_r_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not RParser().can_parse(str(f))


class TestR1PluginParsesRealCode:
    def test_parses_minimal_script(self, tmp_path):
        path = tmp_path / "script.R"
        path.write_text("x <- 1\n")
        result = RParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_functions(self, tmp_path):
        path = tmp_path / "script.R"
        path.write_text(
            "add <- function(a, b) {\n"
            "  return(a + b)\n"
            "}\n"
        )
        result = RParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        assert any(f["name"] == "add" for f in result["functions"])

    def test_extracts_imports(self, tmp_path):
        path = tmp_path / "script.R"
        path.write_text(
            "library(ggplot2)\n"
            "require(dplyr)\n"
        )
        result = RParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_extracts_variables(self, tmp_path):
        path = tmp_path / "script.R"
        path.write_text(
            "x <- 10\n"
            "y = 20\n"
        )
        result = RParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_function_has_required_keys(self, tmp_path):
        path = tmp_path / "script.R"
        path.write_text(
            "foo <- function(x) {\n"
            "  print(x)\n"
            "}\n"
        )
        result = RParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        for fn in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in fn


class TestPluginIsDiscovered:
    def test_r1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, RParser) for p in plugins), (
            "r1 plugin missing PLUGIN export in __init__.py"
        )
