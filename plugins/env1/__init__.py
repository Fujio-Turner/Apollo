"""Environment variables plugin package for Apollo."""
from .parser import EnvParser

PLUGIN = EnvParser

__all__ = ["EnvParser", "PLUGIN"]
