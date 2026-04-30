"""Self-contained smoke tests for the rust1 plugin."""
from __future__ import annotations

from apollo.plugins import discover_plugins
from plugins.rust1 import RustParser


class TestRustPluginDiscovery:
    def test_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, RustParser) for p in plugins)


class TestRustPluginRecognisesExtension:
    def test_recognises_rs_extension(self, tmp_path):
        f = tmp_path / "main.rs"
        f.write_text("fn main() {}")
        assert RustParser().can_parse(str(f))

    def test_rejects_non_rs_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not RustParser().can_parse(str(f))


class TestRustPluginParsesReal:
    def test_parses_minimal_function(self, tmp_path):
        path = tmp_path / "main.rs"
        path.write_text("fn main() {}\n")
        result = RustParser().parse_file(str(path))

        assert result is not None
        assert result["file"] == str(path)
        for key in ("functions", "classes", "imports", "variables"):
            assert key in result


class TestRustEmitsRequiredBuilderKeys:
    """Verify required keys are present on every emit."""

    SOURCE = (
        "use std::fmt;\n"
        "\n"
        "pub struct HelloService {\n"
        "    name: String,\n"
        "}\n"
        "\n"
        "impl HelloService {\n"
        "    pub fn new(name: String) -> Self {\n"
        "        HelloService { name }\n"
        "    }\n"
        "\n"
        "    pub fn greet(&self) -> String {\n"
        "        format!(\"Hello {}\", self.name)\n"
        "    }\n"
        "}\n"
        "\n"
        "pub trait Greeter {\n"
        "    fn say_hello(&self) -> String;\n"
        "}\n"
        "\n"
        "impl Greeter for HelloService {\n"
        "    fn say_hello(&self) -> String {\n"
        "        self.greet()\n"
        "    }\n"
        "}\n"
        "\n"
        "fn main() {\n"
        "    let svc = HelloService::new(\"World\".to_string());\n"
        "    println!(\"{}\", svc.greet());\n"
        "}\n"
    )

    def _parse(self, tmp_path):
        path = tmp_path / "main.rs"
        path.write_text(self.SOURCE)
        return RustParser().parse_file(str(path))

    def test_structs_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["classes"], "expected at least one struct"
        struct_names = [c["name"] for c in result["classes"]]
        assert "HelloService" in struct_names

    def test_traits_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        traits = [c for c in result["classes"] if c["type"] == "trait"]
        assert traits, "expected at least one trait"
        assert any(t["name"] == "Greeter" for t in traits)

    def test_functions_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["functions"], "expected at least one function"
        names = [f["name"] for f in result["functions"]]
        assert "main" in names

    def test_imports_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        assert result["imports"], "expected at least one import"

    def test_required_function_keys(self, tmp_path):
        result = self._parse(tmp_path)
        for func in result["functions"]:
            for key in ("name", "line_start", "line_end", "source", "calls"):
                assert key in func, f"function missing {key}: {func}"

    def test_calls_extracted(self, tmp_path):
        result = self._parse(tmp_path)
        main_fn = next(f for f in result["functions"] if f["name"] == "main")
        assert isinstance(main_fn["calls"], list)

    def test_drives_graph_builder_without_crashing(self, tmp_path):
        from graph.builder import GraphBuilder
        path = tmp_path / "main.rs"
        path.write_text(self.SOURCE)
        gb = GraphBuilder(parsers=[RustParser()])
        gb.build(str(tmp_path))
        assert gb.graph.number_of_nodes() > 0
