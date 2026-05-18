# SPDX-License-Identifier: BUSL-1.1
"""plugins.makefile1 — Apollo plugin."""
from .parser import MakefileParser

PLUGIN = MakefileParser

__all__ = ["MakefileParser", "PLUGIN"]
