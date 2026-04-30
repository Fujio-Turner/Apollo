"""Go 1.x plugin package."""
from .parser import GoParser

PLUGIN = GoParser

__all__ = ["GoParser", "PLUGIN"]
