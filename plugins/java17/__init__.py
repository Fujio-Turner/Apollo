"""Java 17 plugin package."""
from .parser import JavaParser

PLUGIN = JavaParser

__all__ = ["JavaParser", "PLUGIN"]
