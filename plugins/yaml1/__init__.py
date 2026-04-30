"""
YAML 1 plugin package for Apollo.

Parses .yaml/.yml files and extracts keys, anchors/aliases,
!include directives, and internal references.
"""
from .parser import YAMLParser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = YAMLParser

__all__ = ["YAMLParser", "PLUGIN"]
