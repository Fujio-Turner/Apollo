# SPDX-License-Identifier: BUSL-1.1
"""C plugin package."""
from .parser import CParser

PLUGIN = CParser

__all__ = ["CParser", "PLUGIN"]
