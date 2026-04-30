"""
JSON 1 plugin package for Apollo.

Parses .json files and extracts top-level keys, $ref references,
and schema structure.
"""
from .parser import JSONParser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = JSONParser

__all__ = ["JSONParser", "PLUGIN"]
