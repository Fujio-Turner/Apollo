# SPDX-License-Identifier: BUSL-1.1
"""Go 1.x plugin package."""
from .parser import GoParser

PLUGIN = GoParser

__all__ = ["GoParser", "PLUGIN"]
