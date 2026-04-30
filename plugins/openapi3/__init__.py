"""
OpenAPI 3 plugin package for Apollo.

Parses OpenAPI 3.x YAML/JSON specs and extracts endpoints,
schemas, and $ref edge relationships.
"""
from .parser import OpenAPI3Parser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = OpenAPI3Parser

__all__ = ["OpenAPI3Parser", "PLUGIN"]
