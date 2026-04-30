"""plugins.sql1 — Apollo plugin."""
from .parser import SQLParser

PLUGIN = SQLParser

__all__ = ["SQLParser", "PLUGIN"]
