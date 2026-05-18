# SPDX-License-Identifier: BUSL-1.1
"""C++17 plugin package."""
from .parser import CppParser

PLUGIN = CppParser

__all__ = ["CppParser", "PLUGIN"]
