"""Self-contained tests for dart3 plugin."""
from __future__ import annotations

from plugins.dart3 import DartParser
from apollo.plugins import discover_plugins


class TestDart3PluginDiscovery:
    def test_recognises_dart_extension(self, tmp_path):
        f = tmp_path / "main.dart"
        f.write_text("void main() {}\n")
        assert DartParser().can_parse(str(f))

    def test_rejects_non_dart_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not DartParser().can_parse(str(f))


class TestDart3PluginParsesRealCode:
    def test_parses_minimal_script(self, tmp_path):
        path = tmp_path / "main.dart"
        path.write_text("void main() {}\n")
        result = DartParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_classes(self, tmp_path):
        path = tmp_path / "test.dart"
        path.write_text(
            "class Greeter {\n"
            "  String greet() => 'Hello';\n"
            "}\n"
        )
        result = DartParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        assert any(c["name"] == "Greeter" for c in result["classes"])

    def test_extracts_imports(self, tmp_path):
        path = tmp_path / "test.dart"
        path.write_text(
            "import 'package:flutter/material.dart';\n"
            "import 'dart:async';\n"
        )
        result = DartParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_extracts_variables(self, tmp_path):
        path = tmp_path / "test.dart"
        path.write_text(
            "final x = 10;\n"
            "var y = 'hello';\n"
        )
        result = DartParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_class_has_required_keys(self, tmp_path):
        path = tmp_path / "test.dart"
        path.write_text(
            "class Foo {\n"
            "  void bar() {}\n"
            "}\n"
        )
        result = DartParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "methods", "bases"):
                assert key in cls


class TestPluginIsDiscovered:
    def test_dart3_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, DartParser) for p in plugins), (
            "dart3 plugin missing PLUGIN export in __init__.py"
        )
