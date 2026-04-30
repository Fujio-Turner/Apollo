"""Self-contained tests for cmake1 plugin."""
from __future__ import annotations

from plugins.cmake1 import CMakeParser
from apollo.plugins import discover_plugins


class TestCMake1PluginDiscovery:
    def test_recognises_cmakelists(self, tmp_path):
        f = tmp_path / "CMakeLists.txt"
        f.write_text("cmake_minimum_required(VERSION 3.10)\n")
        assert CMakeParser().can_parse(str(f))

    def test_recognises_cmake_extension(self, tmp_path):
        f = tmp_path / "config.cmake"
        f.write_text("set(VAR value)\n")
        assert CMakeParser().can_parse(str(f))

    def test_rejects_other_files(self, tmp_path):
        f = tmp_path / "config.txt"
        f.write_text("hi")
        assert not CMakeParser().can_parse(str(f))


class TestCMake1PluginParsesRealCode:
    def test_parses_minimal_cmake(self, tmp_path):
        path = tmp_path / "CMakeLists.txt"
        path.write_text("cmake_minimum_required(VERSION 3.10)\nproject(MyProject)\n")
        result = CMakeParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_targets(self, tmp_path):
        path = tmp_path / "CMakeLists.txt"
        path.write_text(
            "add_executable(myapp main.cpp utils.cpp)\n"
            "add_library(mylib SHARED lib.cpp)\n"
        )
        result = CMakeParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 2

    def test_extracts_includes(self, tmp_path):
        path = tmp_path / "CMakeLists.txt"
        path.write_text(
            "include(FindBoost)\n"
            "include(GNUInstallDirs)\n"
        )
        result = CMakeParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_extracts_variables(self, tmp_path):
        path = tmp_path / "CMakeLists.txt"
        path.write_text(
            "set(SOURCES main.cpp utils.cpp)\n"
            "set(CMAKE_CXX_STANDARD 17)\n"
        )
        result = CMakeParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_target_has_required_keys(self, tmp_path):
        path = tmp_path / "CMakeLists.txt"
        path.write_text("add_executable(test main.cpp)\n")
        result = CMakeParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        for fn in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in fn


class TestPluginIsDiscovered:
    def test_cmake1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, CMakeParser) for p in plugins), (
            "cmake1 plugin missing PLUGIN export in __init__.py"
        )
