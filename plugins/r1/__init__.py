# SPDX-License-Identifier: BUSL-1.1
"""plugins.r1 — Apollo plugin."""
from .parser import RParser

PLUGIN = RParser

__all__ = ["RParser", "PLUGIN"]
