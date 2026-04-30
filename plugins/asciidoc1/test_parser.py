"""Self-contained tests for asciidoc1 plugin."""
from __future__ import annotations

from plugins.asciidoc1 import AsciiDocParser
from apollo.plugins import discover_plugins


class TestAsciiDoc1PluginDiscovery:
    def test_recognises_adoc_extension(self, tmp_path):
        f = tmp_path / "readme.adoc"
        f.write_text("= Document Title\n")
        assert AsciiDocParser().can_parse(str(f))

    def test_recognises_asciidoc_extension(self, tmp_path):
        f = tmp_path / "readme.asciidoc"
        f.write_text("= Document Title\n")
        assert AsciiDocParser().can_parse(str(f))

    def test_rejects_non_asciidoc_extension(self, tmp_path):
        f = tmp_path / "readme.txt"
        f.write_text("hi")
        assert not AsciiDocParser().can_parse(str(f))


class TestAsciiDoc1PluginParsesRealCode:
    def test_parses_minimal_document(self, tmp_path):
        path = tmp_path / "readme.adoc"
        path.write_text("= Main Title\n\nContent here.\n")
        result = AsciiDocParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_sections(self, tmp_path):
        path = tmp_path / "readme.adoc"
        path.write_text(
            "= Document Title\n"
            "\n"
            "== Section 1\n"
            "\n"
            "=== Subsection\n"
        )
        result = AsciiDocParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 3

    def test_extracts_includes(self, tmp_path):
        path = tmp_path / "readme.adoc"
        path.write_text(
            "= Main Document\n"
            "\n"
            "include::chapter1.adoc[]\n"
            "include::appendix.adoc[]\n"
        )
        result = AsciiDocParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_extracts_attributes(self, tmp_path):
        path = tmp_path / "readme.adoc"
        path.write_text(
            ":author: John Doe\n"
            ":version: 1.0\n"
        )
        result = AsciiDocParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_section_has_required_keys(self, tmp_path):
        path = tmp_path / "readme.adoc"
        path.write_text("= Title\n")
        result = AsciiDocParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        for sec in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "methods", "bases"):
                assert key in sec


class TestPluginIsDiscovered:
    def test_asciidoc1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, AsciiDocParser) for p in plugins), (
            "asciidoc1 plugin missing PLUGIN export in __init__.py"
        )
