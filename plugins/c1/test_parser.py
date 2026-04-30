"""Self-contained smoke tests for the c1 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.c1 import CParser


class TestCPluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, CParser) for p in plugins)


class TestCPluginRecognisesExtension:
    def test_recognises_c_extension(self, tmp_path):
        f = tmp_path / "main.c"
        f.write_text("int main() {}")
        assert CParser().can_parse(str(f))

    def test_recognises_h_extension(self, tmp_path):
        f = tmp_path / "header.h"
        f.write_text("void greet();")
        assert CParser().can_parse(str(f))

    def test_rejects_non_c_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not CParser().can_parse(str(f))


class TestCPluginParsesReal:
    def test_parses_minimal_function(self, tmp_path):
        path = tmp_path / "main.c"
        path.write_text("int main() {\n  return 0;\n}\n")
        result = CParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestCEmitsRequiredBuilderKeys:
    """Verify required keys are present on every emit."""

    SOURCE = (
        "#include <stdio.h>\n"
        "#include \"greet.h\"\n"
        "\n"
        "struct HelloService\n"
        "{\n"
        "    char name[256];\n"
        "};\n"
        "\n"
        "char* greet(struct HelloService* svc)\n"
        "{\n"
        "    return \"Hello\";\n"
        "}\n"
        "\n"
        "int main()\n"
        "{\n"
        "    struct HelloService svc;\n"
        "    greet(&svc);\n"
        "    return 0;\n"
        "}\n"
    )

    def _parse(self, tmp_path):
        path = tmp_path / "main.c"
        path.write_text(self.SOURCE)
        return CParser().parse_file(str(path))

    def test_functions_have_required_keys(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["functions"], "expected at least one function"
        for func in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in func, f"function missing {key}: {func}"

    def test_structs_as_pseudo_classes(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["classes"], "expected at least one struct"
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "source"):
                assert key in cls, f"struct missing {key}: {cls}"

    def test_includes_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["imports"], "expected at least one import"
        for imp in result["imports"]:
            assert "module" in imp

    def test_functions_not_nested(self, tmp_path):
        result = self._parse(tmp_path)
        # In C, all functions are top-level
        assert len(result["functions"]) > 0
        names = [f["name"] for f in result["functions"]]
        assert "greet" in names
        assert "main" in names

    def test_call_extraction(self, tmp_path):
        result = self._parse(tmp_path)
        main_fn = next(f for f in result["functions"] if f["name"] == "main")
        assert isinstance(main_fn["calls"], list)

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "main.c"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[CParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
