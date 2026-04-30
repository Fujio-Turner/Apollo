"""Maven plugin package for Apollo."""
from .parser import MavenParser

PLUGIN = MavenParser

__all__ = ["MavenParser", "PLUGIN"]
