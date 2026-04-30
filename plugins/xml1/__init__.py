"""
XML 1 plugin package for Apollo.

Parses .xml files and extracts elements, attributes, xmlns declarations,
and internal id/href references.
"""
from .parser import XMLParser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = XMLParser

__all__ = ["XMLParser", "PLUGIN"]
