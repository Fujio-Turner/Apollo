"""Self-contained smoke tests for the swift5 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.swift5 import SwiftParser


class TestSwiftPluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, SwiftParser) for p in plugins)


class TestSwiftPluginRecognisesExtension:
    def test_recognises_swift_extension(self, tmp_path):
        f = tmp_path / "main.swift"
        f.write_text("class Main {}")
        assert SwiftParser().can_parse(str(f))

    def test_rejects_non_swift_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not SwiftParser().can_parse(str(f))


class TestSwiftPluginParsesReal:
    def test_parses_minimal_class(self, tmp_path):
        path = tmp_path / "Main.swift"
        path.write_text("class Main {\n  func run() {}\n}\n")
        result = SwiftParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestSwiftEmitsRequiredBuilderKeys:
    """Verify required keys are present on every emit."""

    SOURCE = (
        "import Foundation\n"
        "\n"
        "class HelloService {\n"
        "    private var name: String\n"
        "\n"
        "    init(name: String) {\n"
        "        self.name = name\n"
        "    }\n"
        "\n"
        "    func greet() -> String {\n"
        "        return \"Hello \\(name)\"\n"
        "    }\n"
        "\n"
        "    func run() {\n"
        "        print(greet())\n"
        "    }\n"
        "}\n"
        "\n"
        "protocol Greeter {\n"
        "    func greet() -> String\n"
        "}\n"
    )

    def _parse(self, tmp_path):
        path = tmp_path / "HelloService.swift"
        path.write_text(self.SOURCE)
        return SwiftParser().parse_file(str(path))

    def test_classes_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["classes"], "expected at least one class"
        class_names = [c["name"] for c in result["classes"]]
        assert "HelloService" in class_names

    def test_protocols_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        protocols = [c for c in result["classes"] if c["type"] == "protocol"]
        assert protocols, "expected at least one protocol"
        assert any(p["name"] == "Greeter" for p in protocols)

    def test_imports_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["imports"], "expected at least one import"
        modules = [i["module"] for i in result["imports"]]
        assert "Foundation" in modules

    def test_required_class_keys(self, tmp_path):
        result = self._parse(tmp_path)
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "bases", "methods"):
                assert key in cls, f"class missing {key}: {cls}"

    def test_required_function_keys(self, tmp_path):
        result = self._parse(tmp_path)
        for func in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in func, f"function missing {key}: {func}"

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "HelloService.swift"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[SwiftParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
