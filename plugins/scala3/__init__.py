"""plugins.scala3 — Apollo plugin."""
from .parser import ScalaParser

PLUGIN = ScalaParser

__all__ = ["ScalaParser", "PLUGIN"]
