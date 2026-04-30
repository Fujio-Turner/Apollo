"""Self-contained tests for elixir1 plugin."""
from __future__ import annotations

from plugins.elixir1 import ElixirParser
from apollo.plugins import discover_plugins


class TestElixir1PluginDiscovery:
    def test_recognises_ex_extension(self, tmp_path):
        f = tmp_path / "main.ex"
        f.write_text("defmodule Main do\nend\n")
        assert ElixirParser().can_parse(str(f))

    def test_recognises_exs_extension(self, tmp_path):
        f = tmp_path / "test.exs"
        f.write_text("ExUnit.start()\n")
        assert ElixirParser().can_parse(str(f))

    def test_rejects_non_elixir_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not ElixirParser().can_parse(str(f))


class TestElixir1PluginParsesRealCode:
    def test_parses_minimal_module(self, tmp_path):
        path = tmp_path / "main.ex"
        path.write_text("defmodule Main do\nend\n")
        result = ElixirParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result

    def test_extracts_modules(self, tmp_path):
        path = tmp_path / "test.ex"
        path.write_text(
            "defmodule Greeter do\n"
            "  def greet(name), do: \"Hello, #{name}\"\n"
            "end\n"
        )
        result = ElixirParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        assert any(c["name"] == "Greeter" for c in result["classes"])

    def test_extracts_imports(self, tmp_path):
        path = tmp_path / "test.ex"
        path.write_text(
            "import Enum\n"
            "alias MyApp.Utils\n"
            "require Logger\n"
        )
        result = ElixirParser().parse_file(str(path))
        
        assert len(result["imports"]) >= 3

    def test_module_has_required_keys(self, tmp_path):
        path = tmp_path / "test.ex"
        path.write_text(
            "defmodule Foo do\n"
            "  def bar, do: :ok\n"
            "end\n"
        )
        result = ElixirParser().parse_file(str(path))
        
        assert len(result["classes"]) >= 1
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "methods", "bases"):
                assert key in cls


class TestPluginIsDiscovered:
    def test_elixir1_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, ElixirParser) for p in plugins), (
            "elixir1 plugin missing PLUGIN export in __init__.py"
        )
