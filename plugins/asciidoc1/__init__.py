# SPDX-License-Identifier: BUSL-1.1
"""plugins.asciidoc1 — Apollo plugin."""
from .parser import AsciiDocParser

PLUGIN = AsciiDocParser

__all__ = ["AsciiDocParser", "PLUGIN"]
