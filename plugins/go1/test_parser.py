"""Self-contained smoke tests for the go1 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.go1 import GoParser


class TestGo1PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, GoParser) for p in plugins)


class TestGo1PluginRecognisesExtension:
    def test_recognises_go_extension(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text("package main\n")
        assert GoParser().can_parse(str(f))

    def test_rejects_non_go_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not GoParser().can_parse(str(f))


class TestGo1PluginParsesRealGo:
    def test_parses_minimal_module(self, tmp_path):
        path = tmp_path / "main.go"
        path.write_text("package main\n\nfunc main() {}\n")
        result = GoParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        # All required keys must be present.
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestGo1EmitsRequiredBuilderKeys:
    """The graph builder dereferences ``func['line_start']`` etc.
    directly. Plugins that omit these crash the indexer, so they're
    treated as required by ``guides/making_plugins.md`` § 0.2.
    """

    SOURCE = (
        'package main\n\n'
        'import "fmt"\n\n'
        'type Greeter struct {\n'
        '\tname string\n'
        '}\n\n'
        'func (g *Greeter) Hello() string {\n'
        '\treturn fmt.Sprintf("hi %s", g.name)\n'
        '}\n\n'
        'var Default = "world"\n\n'
        'func main() {\n'
        '\tg := Greeter{name: Default}\n'
        '\tfmt.Println(g.Hello())\n'
        '}\n'
    )

    def _parse(self, tmp_path):
        path = tmp_path / "main.go"
        path.write_text(self.SOURCE)
        return GoParser().parse_file(str(path))

    def test_functions_have_required_keys(self, tmp_path):
        result = self._parse(tmp_path)
        for fn in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in fn, f"function missing {key}: {fn}"

    def test_classes_have_required_keys(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["classes"], "expected at least one struct"
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "bases", "methods"):
                assert key in cls, f"class missing {key}: {cls}"
            for method in cls["methods"]:
                for key in ("name", "line_start", "line_end", "source", "calls"):
                    assert key in method, f"method missing {key}: {method}"

    def test_variables_have_name_and_line(self, tmp_path):
        result = self._parse(tmp_path)
        for var in result["variables"]:
            assert "name" in var and "line" in var, var

    def test_methods_attached_to_struct(self, tmp_path):
        result = self._parse(tmp_path)
        greeter = next(c for c in result["classes"] if c["name"] == "Greeter")
        method_names = [m["name"] for m in greeter["methods"]]
        assert "Hello" in method_names

    def test_call_extraction_in_function_body(self, tmp_path):
        result = self._parse(tmp_path)
        main_fn = next(f for f in result["functions"] if f["name"] == "main")
        call_names = {c["name"] for c in main_fn["calls"]}
        assert "fmt.Println" in call_names

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "main.go"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[GoParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
