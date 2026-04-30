"""plugins.lua5 — Apollo plugin."""
from .parser import LuaParser

PLUGIN = LuaParser

__all__ = ["LuaParser", "PLUGIN"]
