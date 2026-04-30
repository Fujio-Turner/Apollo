"""Self-contained tests for rst1 plugin."""
from __future__ import annotations

from plugins.rst1 import RstParser
from apollo.plugins import discover_plugins


class TestRst1PluginDiscovery:
    def test_recognises_rst_extension(self, tmp_path):
        f = tmp_path / "readme.rst"
        f.write_text("Hello\n=====\n")
        assert RstParser().can_parse(str(f))

    def test_rejects_non_rst_extension(self, tmp_path):
        f = tmp_path / "readme.txt"
        f.write_text("hi")
        assert not RstParser().can_parse(str(f))


class TestRst1PluginParsesRealCode:
    def test_parses_minimal_document(self, tmp_path):
        path = tmp_path / "readme.rst"
        path.write_text("Hello\n=====\n")
        result = RstParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_sections(self, tmp_path):
        path = tmp_path / "readme.rst"
        path.write_text(
            "Main Title\n"
            "==========\n"
            "\n"
            "Subsection\n"
            "-----------\n"
        )
        result = RstParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 2

    def test_extracts_references(self, tmp_path):
        path = tmp_path / "readme.rst"
        path.write_text(
            ":ref:`my-label`\n"
            "`Read more <http://example.com>`_\n"
            "See https://github.com\n"
        )
        result = RstParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 3

    def test_extracts_directives(self, tmp_path):
        path = tmp_path / "readme.rst"
        path.write_text(
            ".. note:: This is a note\n"
            ".. code-block:: python\n"
        )
        result = RstParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_section_has_required_keys(self, tmp_path):
        path = tmp_path / "readme.rst"
        path.write_text("Title\n=====\n")
        result = RstParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        for sec in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "methods", "bases"):
                assert key in sec


class TestPluginIsDiscovered:
    def test_rst1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, RstParser) for p in plugins), (
            "rst1 plugin missing PLUGIN export in __init__.py"
        )
