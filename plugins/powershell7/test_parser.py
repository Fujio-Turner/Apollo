"""Self-contained tests for powershell7 plugin."""
from __future__ import annotations

from plugins.powershell7 import PowerShellParser
from apollo.plugins import discover_plugins


class TestPowerShell7PluginDiscovery:
    def test_recognises_ps1_extension(self, tmp_path):
        f = tmp_path / "script.ps1"
        f.write_text("Write-Host 'hello'\n")
        assert PowerShellParser().can_parse(str(f))

    def test_rejects_non_powershell_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not PowerShellParser().can_parse(str(f))


class TestPowerShell7PluginParsesRealCode:
    def test_parses_minimal_script(self, tmp_path):
        path = tmp_path / "script.ps1"
        path.write_text("Write-Host 'hello'\n")
        result = PowerShellParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_functions(self, tmp_path):
        path = tmp_path / "script.ps1"
        path.write_text(
            "function Add {\n"
            "  param([int]$a, [int]$b)\n"
            "  return $a + $b\n"
            "}\n"
        )
        result = PowerShellParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        assert any(f["name"] == "Add" for f in result["functions"])

    def test_extracts_dot_sources(self, tmp_path):
        path = tmp_path / "script.ps1"
        path.write_text(
            ". .\\lib.ps1\n"
            ". \"C:\\scripts\\config.ps1\"\n"
        )
        result = PowerShellParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_extracts_variables(self, tmp_path):
        path = tmp_path / "script.ps1"
        path.write_text(
            "$Name = 'World'\n"
            "$Path = 'C:\\temp'\n"
        )
        result = PowerShellParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_function_has_required_keys(self, tmp_path):
        path = tmp_path / "script.ps1"
        path.write_text(
            "function Greet {\n"
            "  Write-Host 'Hello'\n"
            "}\n"
        )
        result = PowerShellParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        for fn in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in fn


class TestPluginIsDiscovered:
    def test_powershell7_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, PowerShellParser) for p in plugins), (
            "powershell7 plugin missing PLUGIN export in __init__.py"
        )
