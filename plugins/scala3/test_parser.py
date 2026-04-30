"""Self-contained tests for scala3 plugin."""
from __future__ import annotations

from plugins.scala3 import ScalaParser
from apollo.plugins import discover_plugins


class TestScala3PluginDiscovery:
    def test_recognises_scala_extension(self, tmp_path):
        f = tmp_path / "main.scala"
        f.write_text("object Main {}\n")
        assert ScalaParser().can_parse(str(f))

    def test_rejects_non_scala_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not ScalaParser().can_parse(str(f))


class TestScala3PluginParsesRealCode:
    def test_parses_minimal_module(self, tmp_path):
        path = tmp_path / "main.scala"
        path.write_text("object Main {}\n")
        result = ScalaParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_classes(self, tmp_path):
        path = tmp_path / "test.scala"
        path.write_text(
            "class Greeter(name: String) {\n"
            "  def greet(): String = s\"Hello, $name\"\n"
            "}\n"
        )
        result = ScalaParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        assert any(c["name"] == "Greeter" for c in result["classes"])

    def test_extracts_functions(self, tmp_path):
        path = tmp_path / "test.scala"
        path.write_text(
            "def add(a: Int, b: Int): Int = a + b\n"
        )
        result = ScalaParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        assert any(f["name"] == "add" for f in result["functions"])

    def test_extracts_imports(self, tmp_path):
        path = tmp_path / "test.scala"
        path.write_text(
            "import scala.io.Source\n"
            "import scala.util.{Try, Failure}\n"
        )
        result = ScalaParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_extracts_variables(self, tmp_path):
        path = tmp_path / "test.scala"
        path.write_text(
            "val x = 42\n"
            "var y = \"hello\"\n"
        )
        result = ScalaParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2


class TestPluginIsDiscovered:
    def test_scala3_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, ScalaParser) for p in plugins), (
            "scala3 plugin missing PLUGIN export in __init__.py"
        )
