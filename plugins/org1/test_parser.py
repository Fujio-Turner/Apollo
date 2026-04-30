"""Self-contained tests for org1 plugin."""
from __future__ import annotations

from plugins.org1 import OrgParser
from apollo.plugins import discover_plugins


class TestOrg1PluginDiscovery:
    def test_recognises_org_extension(self, tmp_path):
        f = tmp_path / "notes.org"
        f.write_text("* Main heading\n")
        assert OrgParser().can_parse(str(f))

    def test_rejects_non_org_extension(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("hi")
        assert not OrgParser().can_parse(str(f))


class TestOrg1PluginParsesRealCode:
    def test_parses_minimal_document(self, tmp_path):
        path = tmp_path / "notes.org"
        path.write_text("* Main heading\n")
        result = OrgParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_headings(self, tmp_path):
        path = tmp_path / "notes.org"
        path.write_text(
            "* Top Level\n"
            "** Section 1\n"
            "*** Subsection\n"
            "* Another Top\n"
        )
        result = OrgParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 4

    def test_extracts_links(self, tmp_path):
        path = tmp_path / "notes.org"
        path.write_text(
            "[[http://example.com][Example Site]]\n"
            "[[file:document.org]]\n"
            "Visit https://github.com\n"
        )
        result = OrgParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 3

    def test_extracts_properties(self, tmp_path):
        path = tmp_path / "notes.org"
        path.write_text(
            "#+TITLE: My Document\n"
            "#+AUTHOR: John Doe\n"
        )
        result = OrgParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_heading_has_required_keys(self, tmp_path):
        path = tmp_path / "notes.org"
        path.write_text("* Heading\n")
        result = OrgParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        for heading in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "methods", "bases"):
                assert key in heading


class TestPluginIsDiscovered:
    def test_org1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, OrgParser) for p in plugins), (
            "org1 plugin missing PLUGIN export in __init__.py"
        )
