"""Rust plugin package."""
from .parser import RustParser

PLUGIN = RustParser

__all__ = ["RustParser", "PLUGIN"]
