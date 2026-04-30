"""Self-contained smoke tests for the java17 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.java17 import JavaParser


class TestJava17PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, JavaParser) for p in plugins)


class TestJava17PluginRecognisesExtension:
    def test_recognises_java_extension(self, tmp_path):
        f = tmp_path / "Main.java"
        f.write_text("public class Main {}")
        assert JavaParser().can_parse(str(f))

    def test_rejects_non_java_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not JavaParser().can_parse(str(f))


class TestJava17PluginParsesRealJava:
    def test_parses_minimal_class(self, tmp_path):
        path = tmp_path / "Main.java"
        path.write_text("public class Main {\n  public static void main(String[] args) {}\n}\n")
        result = JavaParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        # All required keys must be present.
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestJava17EmitsRequiredBuilderKeys:
    """Verify the per-entity required keys from
    ``guides/making_plugins.md`` § 0.2 are present on every emit.
    """

    SOURCE = (
        "package demo;\n"
        "\n"
        "import java.util.List;\n"
        "\n"
        "public class Hello extends Object implements Runnable {\n"
        "    private String name;\n"
        "\n"
        "    public String greet() {\n"
        "        return \"hi \" + name;\n"
        "    }\n"
        "\n"
        "    public void run() {\n"
        "        greet();\n"
        "    }\n"
        "}\n"
    )

    def _parse(self, tmp_path):
        path = tmp_path / "Hello.java"
        path.write_text(self.SOURCE)
        return JavaParser().parse_file(str(path))

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
        assert "Object" in cls["bases"]
        assert "Runnable" in cls["bases"]

    def test_methods_under_class_not_top_level(self, tmp_path):
        result = self._parse(tmp_path)
        # No top-level functions in Java
        assert result["functions"] == []
        cls = result["classes"][0]
        names = [m["name"] for m in cls["methods"]]
        assert "greet" in names
        assert "run" in names

    def test_call_extraction(self, tmp_path):
        result = self._parse(tmp_path)
        cls = result["classes"][0]
        run = next(m for m in cls["methods"] if m["name"] == "run")
        assert any(c["name"] == "greet" for c in run["calls"])

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "Hello.java"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[JavaParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
