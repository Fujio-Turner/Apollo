"""plugins.rst1 — Apollo plugin."""
from .parser import RstParser

PLUGIN = RstParser

__all__ = ["RstParser", "PLUGIN"]
