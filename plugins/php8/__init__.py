"""PHP 8 plugin package."""
from .parser import PHPParser

PLUGIN = PHPParser

__all__ = ["PHPParser", "PLUGIN"]
