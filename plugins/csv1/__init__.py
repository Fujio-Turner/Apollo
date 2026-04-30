"""
CSV 1 plugin package for Apollo.

Parses .csv files and extracts headers, row counts,
and structured table data.
"""
from .parser import CSVParser

# Plugin entry point — discovered by plugins.discover_plugins()
PLUGIN = CSVParser

__all__ = ["CSVParser", "PLUGIN"]
