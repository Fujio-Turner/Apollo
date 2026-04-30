"""Self-contained tests for makefile1 plugin."""
from __future__ import annotations

from plugins.makefile1 import MakefileParser
from apollo.plugins import discover_plugins


class TestMakefile1PluginDiscovery:
    def test_recognises_makefile(self, tmp_path):
        f = tmp_path / "Makefile"
        f.write_text("test:\n\techo hello\n")
        assert MakefileParser().can_parse(str(f))

    def test_recognises_makefile_lowercase(self, tmp_path):
        f = tmp_path / "makefile"
        f.write_text("test:\n\techo hello\n")
        assert MakefileParser().can_parse(str(f))

    def test_recognises_mk_extension(self, tmp_path):
        f = tmp_path / "build.mk"
        f.write_text("test:\n\techo hello\n")
        assert MakefileParser().can_parse(str(f))

    def test_rejects_other_files(self, tmp_path):
        f = tmp_path / "config.txt"
        f.write_text("hi")
        assert not MakefileParser().can_parse(str(f))


class TestMakefile1PluginParsesRealCode:
    def test_parses_minimal_makefile(self, tmp_path):
        path = tmp_path / "Makefile"
        path.write_text("test:\n\techo hello\n")
        result = MakefileParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_targets(self, tmp_path):
        path = tmp_path / "Makefile"
        path.write_text(
            "build: deps\n"
            "\tgcc -o app app.c\n"
            "deps:\n"
            "\techo building deps\n"
        )
        result = MakefileParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 2

    def test_extracts_includes(self, tmp_path):
        path = tmp_path / "Makefile"
        path.write_text(
            "include config.mk\n"
            "-include optional.mk\n"
        )
        result = MakefileParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_extracts_variables(self, tmp_path):
        path = tmp_path / "Makefile"
        path.write_text(
            "CC = gcc\n"
            "CFLAGS := -Wall\n"
            "OUT ?= app\n"
        )
        result = MakefileParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 3

    def test_target_has_required_keys(self, tmp_path):
        path = tmp_path / "Makefile"
        path.write_text("build: deps\n\techo hi\n")
        result = MakefileParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        for fn in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in fn


class TestPluginIsDiscovered:
    def test_makefile1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, MakefileParser) for p in plugins), (
            "makefile1 plugin missing PLUGIN export in __init__.py"
        )
