"""Self-contained smoke tests for the cpp17 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.cpp17 import CppParser


class TestCppPluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, CppParser) for p in plugins)


class TestCppPluginRecognisesExtension:
    def test_recognises_cpp_extension(self, tmp_path):
        f = tmp_path / "main.cpp"
        f.write_text("int main() {}")
        assert CppParser().can_parse(str(f))

    def test_recognises_hpp_extension(self, tmp_path):
        f = tmp_path / "header.hpp"
        f.write_text("class MyClass {}")
        assert CppParser().can_parse(str(f))

    def test_rejects_non_cpp_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not CppParser().can_parse(str(f))


class TestCppPluginParsesReal:
    def test_parses_minimal_class(self, tmp_path):
        path = tmp_path / "Main.cpp"
        path.write_text("class Main {\npublic:\n  void run() {}\n};\n")
        result = CppParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestCppEmitsRequiredBuilderKeys:
    """Verify required keys are present on every emit."""

    SOURCE = (
        "#include <iostream>\n"
        "#include \"header.hpp\"\n"
        "\n"
        "class HelloService : public IService\n"
        "{\n"
        "private:\n"
        "    std::string name;\n"
        "\n"
        "public:\n"
        "    std::string greet()\n"
        "    {\n"
        "        return \"Hello \" + name;\n"
        "    }\n"
        "\n"
        "    void run()\n"
        "    {\n"
        "        greet();\n"
        "    }\n"
        "};\n"
    )

    def _parse(self, tmp_path):
        path = tmp_path / "HelloService.cpp"
        path.write_text(self.SOURCE)
        return CppParser().parse_file(str(path))

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
        assert "greet" in names
        assert "run" in names

    def test_call_extraction(self, tmp_path):
        result = self._parse(tmp_path)
        cls = result["classes"][0]
        run = next(m for m in cls["methods"] if m["name"] == "run")
        assert isinstance(run["calls"], list)

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "HelloService.cpp"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[CppParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
