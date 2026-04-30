"""Self-contained smoke tests for the php8 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.php8 import PHPParser


class TestPHP8PluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, PHPParser) for p in plugins)


class TestPHP8PluginRecognisesExtension:
    def test_recognises_php_extension(self, tmp_path):
        f = tmp_path / "index.php"
        f.write_text("<?php\necho 'Hello';\n")
        assert PHPParser().can_parse(str(f))

    def test_rejects_non_php_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not PHPParser().can_parse(str(f))


class TestPHP8PluginParsesRealPHP:
    def test_parses_minimal_module(self, tmp_path):
        path = tmp_path / "index.php"
        path.write_text("<?php\nclass MyClass {}\nfunction doSomething() {}\n")
        result = PHPParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        # All required keys must be present.
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestPHP8EmitsRequiredBuilderKeys:
    """Verify per-entity required keys from
    ``guides/making_plugins.md`` § 0.2.
    """

    SOURCE = (
        "<?php\n"
        "namespace App;\n"
        "\n"
        "use App\\Util;\n"
        "require_once 'helpers.php';\n"
        "\n"
        "class Mailer extends Base implements MailerInterface {\n"
        "    public string $name;\n"
        "\n"
        "    public function send(): void {\n"
        "        Util::log($this->name);\n"
        "    }\n"
        "}\n"
        "\n"
        "function bootstrap() {\n"
        "    $m = new Mailer();\n"
        "    $m->send();\n"
        "}\n"
    )

    def _parse(self, tmp_path):
        path = tmp_path / "Mailer.php"
        path.write_text(self.SOURCE)
        return PHPParser().parse_file(str(path))

    def test_classes_have_required_keys(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["classes"]
        for cls in result["classes"]:
            for key in ("name", "line_start", "line_end", "source", "bases", "methods"):
                assert key in cls
            for m in cls["methods"]:
                for key in ("name", "line_start", "line_end", "source", "calls"):
                    assert key in m

    def test_inheritance_captured_as_bases(self, tmp_path):
        result = self._parse(tmp_path)
        cls = result["classes"][0]
        assert "Base" in cls["bases"]
        assert "MailerInterface" in cls["bases"]

    def test_methods_attached_to_class(self, tmp_path):
        result = self._parse(tmp_path)
        cls = result["classes"][0]
        assert any(m["name"] == "send" for m in cls["methods"])

    def test_top_level_function_separate_from_methods(self, tmp_path):
        result = self._parse(tmp_path)
        assert any(f["name"] == "bootstrap" for f in result["functions"])
        # bootstrap must NOT also appear as a method on the class
        cls = result["classes"][0]
        assert not any(m["name"] == "bootstrap" for m in cls["methods"])

    def test_imports_have_line_numbers(self, tmp_path):
        result = self._parse(tmp_path)
        modules = {imp["module"] for imp in result["imports"]}
        assert any(m.endswith("Util") for m in modules)
        for imp in result["imports"]:
            assert "line" in imp

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "Mailer.php"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[PHPParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
