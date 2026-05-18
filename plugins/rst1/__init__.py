# SPDX-License-Identifier: BUSL-1.1
"""plugins.rst1 — Apollo plugin."""
from .parser import RstParser

PLUGIN = RstParser

__all__ = ["RstParser", "PLUGIN"]
