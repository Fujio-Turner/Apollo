"""Self-contained smoke tests for the kotlin2 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.kotlin2 import KotlinParser


class TestKotlinPluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, KotlinParser) for p in plugins)


class TestKotlinPluginRecognisesExtension:
    def test_recognises_kt_extension(self, tmp_path):
        f = tmp_path / "Main.kt"
        f.write_text("class Main {}")
        assert KotlinParser().can_parse(str(f))

    def test_recognises_kts_extension(self, tmp_path):
        f = tmp_path / "build.kts"
        f.write_text("fun main() {}")
        assert KotlinParser().can_parse(str(f))

    def test_rejects_non_kt_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not KotlinParser().can_parse(str(f))


class TestKotlinPluginParsesReal:
    def test_parses_minimal_class(self, tmp_path):
        path = tmp_path / "Main.kt"
        path.write_text("class Main {\n  fun run() {}\n}\n")
        result = KotlinParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestKotlinEmitsRequiredBuilderKeys:
    """Verify required keys are present on every emit."""

    SOURCE = (
        "import java.util.List\n"
        "\n"
        "class HelloService(val name: String) {\n"
        "\n"
        "    fun greet(): String {\n"
        "        return \"Hello $name\"\n"
        "    }\n"
        "\n"
        "    fun run() {\n"
        "        println(greet())\n"
        "    }\n"
        "}\n"
        "\n"
        "data class Person(\n"
        "    val name: String,\n"
        "    val age: Int\n"
        ")\n"
        "\n"
        "fun main() {\n"
        "    val svc = HelloService(\"World\")\n"
        "    svc.run()\n"
        "}\n"
    )

    def _parse(self, tmp_path):
        path = tmp_path / "HelloService.kt"
        path.write_text(self.SOURCE)
        return KotlinParser().parse_file(str(path))

    def test_classes_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["classes"], "expected at least one class"
        class_names = [c["name"] for c in result["classes"]]
        assert "HelloService" in class_names

    def test_data_classes_marked(self, tmp_path):
        result = self._parse(tmp_path)
        data_classes = [c for c in result["classes"] if c["is_dataclass"]]
        assert data_classes, "expected at least one data class"
        assert any(c["name"] == "Person" for c in data_classes)

    def test_functions_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["functions"], "expected at least one function"
        func_names = [f["name"] for f in result["functions"]]
        assert "main" in func_names

    def test_imports_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["imports"], "expected at least one import"
        modules = [i["module"] for i in result["imports"]]
        assert any("util" in m for m in modules)

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
        path = tmp_path / "HelloService.kt"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[KotlinParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
