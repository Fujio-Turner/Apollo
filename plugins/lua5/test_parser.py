"""Self-contained tests for lua5 plugin."""
from __future__ import annotations

from plugins.lua5 import LuaParser
from apollo.plugins import discover_plugins


class TestLua5PluginDiscovery:
    def test_recognises_lua_extension(self, tmp_path):
        f = tmp_path / "script.lua"
        f.write_text("x = 1\n")
        assert LuaParser().can_parse(str(f))

    def test_rejects_non_lua_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not LuaParser().can_parse(str(f))


class TestLua5PluginParsesRealCode:
    def test_parses_minimal_script(self, tmp_path):
        path = tmp_path / "script.lua"
        path.write_text("x = 1\n")
        result = LuaParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_functions(self, tmp_path):
        path = tmp_path / "script.lua"
        path.write_text(
            "local function add(a, b)\n"
            "  return a + b\n"
            "end\n"
        )
        result = LuaParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        assert any(f["name"] == "add" for f in result["functions"])

    def test_extracts_requires(self, tmp_path):
        path = tmp_path / "script.lua"
        path.write_text(
            "require('socket')\n"
            "require 'json'\n"
        )
        result = LuaParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_extracts_variables(self, tmp_path):
        path = tmp_path / "script.lua"
        path.write_text(
            "x = 10\n"
            "local y = 20\n"
        )
        result = LuaParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_function_has_required_keys(self, tmp_path):
        path = tmp_path / "script.lua"
        path.write_text(
            "function foo(x)\n"
            "  print(x)\n"
            "end\n"
        )
        result = LuaParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        for fn in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in fn


class TestPluginIsDiscovered:
    def test_lua5_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, LuaParser) for p in plugins), (
            "lua5 plugin missing PLUGIN export in __init__.py"
        )
