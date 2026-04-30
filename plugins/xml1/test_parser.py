"""Self-contained smoke tests for the xml1 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.xml1 import XMLParser


class TestXML1PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, XMLParser) for p in plugins)


class TestXML1PluginRecognisesExtension:
    def test_recognises_xml_extension(self, tmp_path):
        f = tmp_path / "config.xml"
        f.write_text("<root></root>")
        assert XMLParser().can_parse(str(f))

    def test_rejects_non_xml_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not XMLParser().can_parse(str(f))


class TestXML1PluginParsesRealXML:
    def test_parses_minimal_xml(self, tmp_path):
        path = tmp_path / "test.xml"
        path.write_text("<root><item>test</item></root>")
        result = XMLParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        assert "functions" in result
        assert "classes" in result
        assert "imports" in result
        assert "variables" in result

    def test_extracts_root_element(self, tmp_path):
        path = tmp_path / "test.xml"
        path.write_text("<root><child>value</child></root>")
        result = XMLParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert "root" in var_names

    def test_extracts_namespaces(self, tmp_path):
        path = tmp_path / "test.xml"
        path.write_text('<root xmlns="http://example.com"><item>test</item></root>')
        result = XMLParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert any("xmlns" in str(v) for v in var_names)

    def test_extracts_id_references(self, tmp_path):
        path = tmp_path / "test.xml"
        path.write_text('<root><item id="item1">test</item><ref href="item1"/></root>')
        result = XMLParser().parse_file(str(path))

        var_names = {v["name"] for v in result["variables"]}
        assert any("item1" in str(v) for v in var_names)
