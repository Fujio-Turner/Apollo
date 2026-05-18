# SPDX-License-Identifier: BUSL-1.1
"""TypeScript plugin package."""
from .parser import TypeScriptParser

PLUGIN = TypeScriptParser

__all__ = ["TypeScriptParser", "PLUGIN"]
