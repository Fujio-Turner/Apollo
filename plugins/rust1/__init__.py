# SPDX-License-Identifier: BUSL-1.1
"""Rust plugin package."""
from .parser import RustParser

PLUGIN = RustParser

__all__ = ["RustParser", "PLUGIN"]
