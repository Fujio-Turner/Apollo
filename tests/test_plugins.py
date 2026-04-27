"""Tests for the plugin discovery system in ``plugins/``."""
from __future__ import annotations

import importlib

import pytest

from apollo.parser.base import BaseParser
from apollo.plugins import discover_plugins, iter_plugin_modules


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------

class TestDiscoverPlugins:
    """Tests for ``plugins.discover_plugins()``."""

    def test_returns_list_of_baseparser_instances(self):
        plugins = discover_plugins()
        assert isinstance(plugins, list)
        assert len(plugins) > 0
        for p in plugins:
            assert isinstance(p, BaseParser)

    def test_includes_builtin_python3_plugin(self):
        from plugins.python3 import PythonParser
        plugins = discover_plugins()
        assert any(isinstance(p, PythonParser) for p in plugins)

    def test_includes_builtin_markdown_gfm_plugin(self):
        from plugins.markdown_gfm import MarkdownParser
        plugins = discover_plugins()
        assert any(isinstance(p, MarkdownParser) for p in plugins)

    def test_returns_fresh_instances_each_call(self):
        a = discover_plugins()
        b = discover_plugins()
        assert a is not b
        # Same classes, different instances.
        assert {type(x) for x in a} == {type(x) for x in b}
        for x in a:
            assert all(x is not y for y in b)

    def test_plugins_are_alphabetical_by_module_name(self):
        """Discovery order should be deterministic (alphabetical)."""
        modules = list(iter_plugin_modules())
        assert modules == sorted(modules)


# ----------------------------------------------------------------------
# Plugin contract
# ----------------------------------------------------------------------

class TestPluginContract:
    """Every discovered plugin module must follow the documented contract."""

    @pytest.fixture
    def plugin_modules(self):
        return [importlib.import_module(name) for name in iter_plugin_modules()]

    def test_each_module_exposes_PLUGIN_attribute(self, plugin_modules):
        for mod in plugin_modules:
            assert hasattr(mod, "PLUGIN"), (
                f"{mod.__name__} is missing the required `PLUGIN` attribute"
            )

    def test_PLUGIN_is_a_class(self, plugin_modules):
        for mod in plugin_modules:
            assert isinstance(mod.PLUGIN, type), (
                f"{mod.__name__}.PLUGIN must be a class, "
                f"got {type(mod.PLUGIN).__name__}"
            )

    def test_PLUGIN_subclasses_BaseParser(self, plugin_modules):
        for mod in plugin_modules:
            assert issubclass(mod.PLUGIN, BaseParser), (
                f"{mod.__name__}.PLUGIN must subclass BaseParser"
            )

    def test_PLUGIN_can_be_instantiated_with_no_args(self, plugin_modules):
        for mod in plugin_modules:
            instance = mod.PLUGIN()
            assert isinstance(instance, BaseParser)


# ----------------------------------------------------------------------
# Backward compatibility
# ----------------------------------------------------------------------

class TestBackwardCompatibility:
    """Old import paths must keep working after the move to ``plugins/``."""

    def test_apollo_parser_still_exports_python_parser(self):
        from apollo.parser import PythonParser
        from plugins.python3 import PythonParser as PluginPythonParser
        assert PythonParser is PluginPythonParser

    def test_apollo_parser_still_exports_markdown_parser(self):
        from apollo.parser import MarkdownParser
        from plugins.markdown_gfm import MarkdownParser as PluginMarkdownParser
        assert MarkdownParser is PluginMarkdownParser

    def test_apollo_parser_re_exports_baseparser(self):
        from apollo.parser import BaseParser as ReExported
        assert ReExported is BaseParser


# ----------------------------------------------------------------------
# Sanity check: plugins actually parse files end-to-end
# ----------------------------------------------------------------------

class TestPluginsParseRealFiles:
    """Smoke test that discovered plugins can parse trivial sample input."""

    def test_python3_plugin_parses_python_source(self, tmp_path):
        from plugins.python3 import PythonParser
        f = tmp_path / "hello.py"
        f.write_text("def hi():\n    return 1\n")
        result = PythonParser().parse_file(str(f))
        assert result is not None
        assert result["file"] == str(f)
        assert any(fn["name"] == "hi" for fn in result["functions"])

    def test_markdown_gfm_plugin_parses_markdown_source(self, tmp_path):
        from plugins.markdown_gfm import MarkdownParser
        f = tmp_path / "doc.md"
        f.write_text("# Title\n\nHello world.\n")
        result = MarkdownParser().parse_file(str(f))
        assert result is not None
        assert result["file"] == str(f)
        assert result["title"] == "Title"
