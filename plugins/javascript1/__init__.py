# SPDX-License-Identifier: BUSL-1.1
"""JavaScript 1.x plugin package."""
from .parser import JavaScriptParser

PLUGIN = JavaScriptParser

__all__ = ["JavaScriptParser", "PLUGIN"]
