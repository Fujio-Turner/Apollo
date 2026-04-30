"""Self-contained tests for the maven1 plugin."""
from __future__ import annotations

import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.maven1 import MavenParser


class TestMavenPluginDiscovery:
    def test_maven_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, MavenParser) for p in plugins)


class TestMavenPluginRecognisesExtension:
    def test_recognises_pom_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "pom.xml"
            f.write_text("<?xml version='1.0'?><project/>")
            assert MavenParser().can_parse(str(f))

    def test_rejects_other_xml_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.xml"
            f.write_text("<?xml version='1.0'?><project/>")
            assert not MavenParser().can_parse(str(f))


class TestMavenPluginParsesPom:
    def test_parses_valid_pom(self):
        pom_content = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <modelVersion>4.0.0</modelVersion>
    <properties>
        <maven.compiler.source>11</maven.compiler.source>
    </properties>
    <dependencies>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13</version>
        </dependency>
    </dependencies>
</project>"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "pom.xml"
            f.write_text(pom_content)
            result = MavenParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "imports" in result
        assert "variables" in result
        assert len(result["imports"]) > 0

    def test_returns_none_for_invalid_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "pom.xml"
            f.write_text("<broken>")
            assert MavenParser().parse_file(str(f)) is None


class TestMavenPluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "pom.xml"
            f.write_text("<?xml version='1.0'?><project/>")
            parser = MavenParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False
