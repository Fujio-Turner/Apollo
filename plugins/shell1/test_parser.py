"""Self-contained tests for shell1 plugin."""
from __future__ import annotations

from plugins.shell1 import ShellParser
from apollo.plugins import discover_plugins


class TestShell1PluginDiscovery:
    def test_recognises_sh_extension(self, tmp_path):
        f = tmp_path / "script.sh"
        f.write_text("#!/bin/bash\necho 'hello'\n")
        assert ShellParser().can_parse(str(f))

    def test_recognises_bash_extension(self, tmp_path):
        f = tmp_path / "script.bash"
        f.write_text("#!/bin/bash\necho 'hello'\n")
        assert ShellParser().can_parse(str(f))

    def test_rejects_non_shell_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not ShellParser().can_parse(str(f))


class TestShell1PluginParsesRealCode:
    def test_parses_minimal_script(self, tmp_path):
        path = tmp_path / "script.sh"
        path.write_text("#!/bin/bash\necho 'hello'\n")
        result = ShellParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_functions(self, tmp_path):
        path = tmp_path / "script.sh"
        path.write_text(
            "#!/bin/bash\n"
            "add() {\n"
            "  echo $((\\$1 + \\$2))\n"
            "}\n"
        )
        result = ShellParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        assert any(f["name"] == "add" for f in result["functions"])

    def test_extracts_sources(self, tmp_path):
        path = tmp_path / "script.sh"
        path.write_text(
            "#!/bin/bash\n"
            "source ./lib.sh\n"
            ". ./config.sh\n"
        )
        result = ShellParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 2

    def test_extracts_variables(self, tmp_path):
        path = tmp_path / "script.sh"
        path.write_text(
            "#!/bin/bash\n"
            "NAME='world'\n"
            "export PATH=/usr/bin\n"
        )
        result = ShellParser().parse_file(str(path))
        
        assert len(result["variables"]) >= 2

    def test_function_has_required_keys(self, tmp_path):
        path = tmp_path / "script.sh"
        path.write_text(
            "#!/bin/bash\n"
            "greet() {\n"
            "  echo \"Hello\"\n"
            "}\n"
        )
        result = ShellParser().parse_file(str(path))
        
        assert len(result["functions"]) >= 1
        for fn in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in fn


class TestPluginIsDiscovered:
    def test_shell1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, ShellParser) for p in plugins), (
            "shell1 plugin missing PLUGIN export in __init__.py"
        )
