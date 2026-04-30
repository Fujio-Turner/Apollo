"""Self-contained smoke tests for the ruby3 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.ruby3 import RubyParser


class TestRubyPluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, RubyParser) for p in plugins)


class TestRubyPluginRecognisesExtension:
    def test_recognises_rb_extension(self, tmp_path):
        f = tmp_path / "main.rb"
        f.write_text("class Main\nend\n")
        assert RubyParser().can_parse(str(f))

    def test_rejects_non_rb_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not RubyParser().can_parse(str(f))


class TestRubyPluginParsesReal:
    def test_parses_minimal_class(self, tmp_path):
        path = tmp_path / "main.rb"
        path.write_text("class Main\n  def run\n  end\nend\n")
        result = RubyParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestRubyEmitsRequiredBuilderKeys:
    """Verify required keys are present on every emit."""

    SOURCE = (
        "require 'json'\n"
        "\n"
        "class HelloService\n"
        "  def initialize(name)\n"
        "    @name = name\n"
        "  end\n"
        "\n"
        "  def greet\n"
        "    \"Hello #{@name}\"\n"
        "  end\n"
        "\n"
        "  def run\n"
        "    greet\n"
        "  end\n"
        "end\n"
        "\n"
        "def main\n"
        "  svc = HelloService.new('World')\n"
        "  svc.run\n"
        "end\n"
    )

    def _parse(self, tmp_path):
        path = tmp_path / "hello.rb"
        path.write_text(self.SOURCE)
        return RubyParser().parse_file(str(path))

    def test_classes_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["classes"], "expected at least one class"
        class_names = [c["name"] for c in result["classes"]]
        assert "HelloService" in class_names

    def test_functions_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["functions"], "expected at least one function"
        func_names = [f["name"] for f in result["functions"]]
        assert "main" in func_names

    def test_imports_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["imports"], "expected at least one import"
        modules = [i["module"] for i in result["imports"]]
        assert "json" in modules

    def test_required_class_keys(self, tmp_path):
        result = self._parse(tmp_path)
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "bases", "methods"):
                assert key in cls, f"class missing {key}: {cls}"

    def test_required_function_keys(self, tmp_path):
        result = self._parse(tmp_path)
        for func in result["functions"]:
            for key in ("name", "line_start", "line_end"):
                assert key in func, f"function missing {key}: {func}"

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "hello.rb"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[RubyParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
