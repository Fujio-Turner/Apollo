"""plugins.cmake1 — Apollo plugin."""
from .parser import CMakeParser

PLUGIN = CMakeParser

__all__ = ["CMakeParser", "PLUGIN"]
