"""C# 12 plugin package."""
from .parser import CSharpParser

PLUGIN = CSharpParser

__all__ = ["CSharpParser", "PLUGIN"]
