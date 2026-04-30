"""C plugin package."""
from .parser import CParser

PLUGIN = CParser

__all__ = ["CParser", "PLUGIN"]
