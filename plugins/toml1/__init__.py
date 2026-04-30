"""
TOML 1 plugin package for Apollo.

Parses .toml files and extracts tables, dependency lists,
and key-value structures.
"""
from .parser import TOMLParser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = TOMLParser

__all__ = ["TOMLParser", "PLUGIN"]
