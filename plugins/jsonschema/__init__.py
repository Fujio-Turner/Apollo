"""
JSON Schema plugin package for Apollo.

Parses .schema.json files and extracts schema definitions,
$ref relationships, and type hierarchies.
"""
from .parser import JSONSchemaParser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = JSONSchemaParser

__all__ = ["JSONSchemaParser", "PLUGIN"]
