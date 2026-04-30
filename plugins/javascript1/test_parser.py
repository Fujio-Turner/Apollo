"""Self-contained smoke tests for the javascript1 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.javascript1 import JavaScriptParser


class TestJavaScript1PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, JavaScriptParser) for p in plugins)


class TestJavaScript1PluginRecognisesExtension:
    def test_recognises_js_extension(self, tmp_path):
        f = tmp_path / "main.js"
        f.write_text("const x = 1;")
        assert JavaScriptParser().can_parse(str(f))

    def test_recognises_jsx_extension(self, tmp_path):
        f = tmp_path / "App.jsx"
        f.write_text("const App = () => <div />")
        assert JavaScriptParser().can_parse(str(f))

    def test_recognises_mjs_extension(self, tmp_path):
        f = tmp_path / "module.mjs"
        f.write_text("export const x = 1;")
        assert JavaScriptParser().can_parse(str(f))

    def test_rejects_non_js_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not JavaScriptParser().can_parse(str(f))


class TestJavaScript1PluginParsesRealJS:
    def test_parses_minimal_module(self, tmp_path):
        path = tmp_path / "main.js"
        path.write_text("const x = 1;\nfunction hello() {}\n")
        result = JavaScriptParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        # All required keys must be present.
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestJavaScript1EmitsRequiredBuilderKeys:
    """Verify the per-entity required keys from
    ``guides/making_plugins.md`` § 0.2 are present on every emit.
    """

    SOURCE = (
        'import { mount } from "framework";\n'
        'const helper = require("./helper");\n'
        '\n'
        'export class Widget extends Base {\n'
        '    render() {\n'
        '        return mount(this);\n'
        '    }\n'
        '}\n'
        '\n'
        'function init() {\n'
        '    new Widget().render();\n'
        '}\n'
    )

    def _parse(self, tmp_path):
        path = tmp_path / "app.js"
        path.write_text(self.SOURCE)
        return JavaScriptParser().parse_file(str(path))

    def test_functions_have_required_keys(self, tmp_path):
        result = self._parse(tmp_path)
        for fn in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in fn, f"function missing {key}: {fn}"

    def test_classes_have_required_keys(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["classes"]
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "bases", "methods"):
                assert key in cls
            for method in cls["methods"]:
                for key in ("name", "line_start", "line_end", "source", "calls"):
                    assert key in method

    def test_methods_attached_to_class(self, tmp_path):
        result = self._parse(tmp_path)
        widget = next(c for c in result["classes"] if c["name"] == "Widget")
        assert "Base" in widget["bases"]
        names = [m["name"] for m in widget["methods"]]
        assert "render" in names

    def test_imports_have_line_numbers(self, tmp_path):
        result = self._parse(tmp_path)
        modules = {imp["module"] for imp in result["imports"]}
        assert "framework" in modules
        assert "./helper" in modules
        for imp in result["imports"]:
            assert "line" in imp

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "app.js"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[JavaScriptParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
