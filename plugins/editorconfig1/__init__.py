"""EditorConfig plugin package for Apollo."""
from .parser import EditorConfigParser

PLUGIN = EditorConfigParser

__all__ = ["EditorConfigParser", "PLUGIN"]
