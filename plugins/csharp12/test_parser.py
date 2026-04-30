"""Self-contained smoke tests for the csharp12 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.csharp12 import CSharpParser


class TestCSharpPluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, CSharpParser) for p in plugins)


class TestCSharpPluginRecognisesExtension:
    def test_recognises_cs_extension(self, tmp_path):
        f = tmp_path / "Program.cs"
        f.write_text("class Program {}")
        assert CSharpParser().can_parse(str(f))

    def test_rejects_non_cs_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not CSharpParser().can_parse(str(f))


class TestCSharpPluginParsesReal:
    def test_parses_minimal_class(self, tmp_path):
        path = tmp_path / "Program.cs"
        path.write_text("public class Program {\n  public void Run() {}\n}\n")
        result = CSharpParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestCSharpEmitsRequiredBuilderKeys:
    """Verify required keys are present on every emit."""

    SOURCE = (
        "using System;\n"
        "using System.Collections.Generic;\n"
        "\n"
        "namespace MyApp\n"
        "{\n"
        "    public class HelloService : IService\n"
        "    {\n"
        "        private string name = \"World\";\n"
        "\n"
        "        public string Greet()\n"
        "        {\n"
        "            return $\"Hello {name}\";\n"
        "        }\n"
        "\n"
        "        public void Run()\n"
        "        {\n"
        "            Greet();\n"
        "        }\n"
        "    }\n"
        "}\n"
    )

    def _parse(self, tmp_path):
        path = tmp_path / "HelloService.cs"
        path.write_text(self.SOURCE)
        return CSharpParser().parse_file(str(path))

    def test_classes_have_required_keys(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["classes"], "expected at least one class"
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "bases", "methods"):
                assert key in cls, f"class missing {key}: {cls}"
            for method in cls["methods"]:
                for key in ("name", "line_start", "line_end", "source", "calls"):
                    assert key in method, f"method missing {key}: {method}"

    def test_inheritance_captured_as_bases(self, tmp_path):
        result = self._parse(tmp_path)
        cls = result["classes"][0]
        assert "IService" in cls["bases"]

    def test_methods_under_class_not_top_level(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["functions"] == []
        cls = result["classes"][0]
        names = [m["name"] for m in cls["methods"]]
        assert "Greet" in names
        assert "Run" in names

    def test_call_extraction(self, tmp_path):
        result = self._parse(tmp_path)
        cls = result["classes"][0]
        run = next(m for m in cls["methods"] if m["name"] == "Run")
        assert isinstance(run["calls"], list)

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "HelloService.cs"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[CSharpParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
