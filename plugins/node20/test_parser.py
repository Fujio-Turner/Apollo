"""Self-contained smoke tests for the node20 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.node20 import Node20Parser


class TestNode20PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, Node20Parser) for p in plugins)


class TestNode20PluginRecognisesExtension:
    def test_recognises_js_extension(self, tmp_path):
        f = tmp_path / "index.js"
        f.write_text("const http = require('http');")
        assert Node20Parser().can_parse(str(f))

    def test_recognises_mjs_extension(self, tmp_path):
        f = tmp_path / "module.mjs"
        f.write_text("export default {};")
        assert Node20Parser().can_parse(str(f))

    def test_recognises_cjs_extension(self, tmp_path):
        f = tmp_path / "compat.cjs"
        f.write_text("module.exports = {};")
        assert Node20Parser().can_parse(str(f))

    def test_rejects_non_js_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not Node20Parser().can_parse(str(f))


class TestNode20PluginParsesRealNode:
    def test_parses_minimal_module(self, tmp_path):
        path = tmp_path / "index.js"
        path.write_text("const express = require('express');\nfunction startServer() {}\n")
        result = Node20Parser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        # All required keys must be present.
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestNode20EmitsRequiredBuilderKeys:
    """Verify per-entity required keys from
    ``guides/making_plugins.md`` § 0.2.
    """

    SOURCE = (
        'const http = require("http");\n'
        '\n'
        'class Server extends Base {\n'
        '    listen() {\n'
        '        http.createServer().listen(8080);\n'
        '    }\n'
        '}\n'
        '\n'
        'module.exports.start = function start() {\n'
        '    new Server().listen();\n'
        '};\n'
    )

    def _parse(self, tmp_path):
        path = tmp_path / "server.cjs"
        path.write_text(self.SOURCE)
        return Node20Parser().parse_file(str(path))

    def test_classes_have_required_keys(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["classes"]
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "bases", "methods"):
                assert key in cls
            for m in cls["methods"]:
                for key in ("name", "line_start", "line_end", "source", "calls"):
                    assert key in m

    def test_require_imports_recognised(self, tmp_path):
        result = self._parse(tmp_path)
        modules = {imp["module"] for imp in result["imports"]}
        assert "http" in modules
        for imp in result["imports"]:
            assert "line" in imp

    def test_module_exports_surface_as_variables(self, tmp_path):
        result = self._parse(tmp_path)
        names = {var["name"] for var in result["variables"]}
        assert "start" in names

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "server.cjs"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[Node20Parser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
