# SPDX-License-Identifier: BUSL-1.1
"""plugins.dart3 — Apollo plugin."""
from .parser import DartParser

PLUGIN = DartParser

__all__ = ["DartParser", "PLUGIN"]
