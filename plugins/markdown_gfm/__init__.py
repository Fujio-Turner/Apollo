"""
GitHub Flavored Markdown plugin package.

Self-contained subpackage style:

    plugins/markdown_gfm/
    ├── __init__.py   ← this file: re-exports the parser and PLUGIN
    └── parser.py     ← the BaseParser implementation

Add helper modules (extra extractors, vendored support code, etc.)
alongside ``parser.py`` and import them from there. Everything the
plugin needs lives inside this folder.
"""
from .parser import MarkdownParser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = MarkdownParser

__all__ = ["MarkdownParser", "PLUGIN"]
