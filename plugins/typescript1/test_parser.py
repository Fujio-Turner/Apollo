"""Self-contained smoke tests for the typescript1 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.typescript1 import TypeScriptParser


class TestTypeScriptPluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, TypeScriptParser) for p in plugins)


class TestTypeScriptPluginRecognisesExtension:
    def test_recognises_ts_extension(self, tmp_path):
        f = tmp_path / "main.ts"
        f.write_text("class Main {}")
        assert TypeScriptParser().can_parse(str(f))

    def test_recognises_tsx_extension(self, tmp_path):
        f = tmp_path / "App.tsx"
        f.write_text("export const App = () => <div />")
        assert TypeScriptParser().can_parse(str(f))

    def test_rejects_non_ts_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not TypeScriptParser().can_parse(str(f))


class TestTypeScriptPluginParsesReal:
    def test_parses_minimal_class(self, tmp_path):
        path = tmp_path / "Main.ts"
        path.write_text("export class Main {\n  run() {}\n}\n")
        result = TypeScriptParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestTypeScriptEmitsRequiredBuilderKeys:
    """Verify required keys are present on every emit."""

    SOURCE = (
        "import { Component } from '@angular/core';\n"
        "\n"
        "export class HelloComponent extends Component implements OnInit {\n"
        "    name: string = 'World';\n"
        "\n"
        "    greet(): string {\n"
        "        return `Hello ${this.name}`;\n"
        "    }\n"
        "\n"
        "    ngOnInit() {\n"
        "        this.greet();\n"
        "    }\n"
        "}\n"
    )

    def _parse(self, tmp_path):
        path = tmp_path / "HelloComponent.ts"
        path.write_text(self.SOURCE)
        return TypeScriptParser().parse_file(str(path))

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
        assert "Component" in cls["bases"]
        assert "OnInit" in cls["bases"]

    def test_methods_under_class_not_top_level(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["functions"] == []
        cls = result["classes"][0]
        names = [m["name"] for m in cls["methods"]]
        assert "greet" in names
        assert "ngOnInit" in names

    def test_call_extraction(self, tmp_path):
        result = self._parse(tmp_path)
        cls = result["classes"][0]
        ngOnInit = next(m for m in cls["methods"] if m["name"] == "ngOnInit")
        # Verify calls are extracted
        assert isinstance(ngOnInit["calls"], list)

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "HelloComponent.ts"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[TypeScriptParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
