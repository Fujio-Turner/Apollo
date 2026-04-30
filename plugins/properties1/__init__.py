"""Properties files plugin package for Apollo."""
from .parser import PropertiesParser

PLUGIN = PropertiesParser

__all__ = ["PropertiesParser", "PLUGIN"]
