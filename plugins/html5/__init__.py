"""
HTML5 plugin package.

Self-contained subpackage style:

    plugins/html5/
    ├── __init__.py   ← this file: re-exports the parser and PLUGIN
    ├── parser.py     ← the BaseParser implementation
    └── plugin.md     ← manifest shown in Settings → Plugins

Add helper modules (extra extractors, vendored support code, etc.)
alongside ``parser.py`` and import them from there. Everything the
plugin needs lives inside this folder.
"""
from .parser import HtmlParser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = HtmlParser

__all__ = ["HtmlParser", "PLUGIN"]
