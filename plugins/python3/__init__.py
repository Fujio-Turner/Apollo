"""
Python 3 plugin package.

Self-contained subpackage style:

    plugins/python3/
    ├── __init__.py   ← this file: re-exports the parser and PLUGIN
    └── parser.py     ← the BaseParser implementation

Add helper modules (extra extractors, vendored support code, etc.)
alongside ``parser.py`` and import them from there. Everything the
plugin needs lives inside this folder.
"""
from .parser import PythonParser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = PythonParser

__all__ = ["PythonParser", "PLUGIN"]
