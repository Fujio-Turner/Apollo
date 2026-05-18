# SPDX-License-Identifier: BUSL-1.1
"""plugins.scala3 — Apollo plugin."""
from .parser import ScalaParser

PLUGIN = ScalaParser

__all__ = ["ScalaParser", "PLUGIN"]
