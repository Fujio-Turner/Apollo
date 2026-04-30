"""Self-contained tests for the gradle1 plugin."""
from __future__ import annotations

import tempfile
from pathlib import Path

from apollo.plugins import discover_plugins
from plugins.gradle1 import GradleParser


class TestGradlePluginDiscovery:
    def test_gradle_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, GradleParser) for p in plugins)


class TestGradlePluginRecognisesExtension:
    def test_recognises_gradle_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ["build.gradle", "build.gradle.kts"]:
                f = Path(tmp) / name
                f.write_text("")
                assert GradleParser().can_parse(str(f))

    def test_rejects_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.ini"
            f.write_text("")
            assert not GradleParser().can_parse(str(f))


class TestGradlePluginParsesBuild:
    def test_parses_valid_gradle(self):
        content = """
plugins {
    id 'java'
}

dependencies {
    implementation 'junit:junit:4.13'
    testImplementation 'org.junit.jupiter:junit-jupiter-api:5.7.0'
}

task myTask {
    doLast {
        println 'Hello'
    }
}

version = '1.0'
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "build.gradle"
            f.write_text(content)
            result = GradleParser().parse_file(str(f))

        assert result is not None
        assert result["file"] == str(f)
        assert "imports" in result
        assert "functions" in result
        assert len(result["imports"]) > 0

    def test_parses_kotlin_gradle(self):
        content = """
plugins {
    kotlin("jvm")
}

dependencies {
    implementation("org.jetbrains.kotlin:kotlin-stdlib")
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "build.gradle.kts"
            f.write_text(content)
            result = GradleParser().parse_file(str(f))

        assert result is not None
        assert "imports" in result


class TestGradlePluginConfig:
    def test_disabled_plugin_can_parse_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "build.gradle"
            f.write_text("")
            parser = GradleParser(config={"enabled": False})
            assert parser.can_parse(str(f)) is False
