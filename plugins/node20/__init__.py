# SPDX-License-Identifier: BUSL-1.1
"""Node.js 20 plugin package."""
from .parser import Node20Parser

PLUGIN = Node20Parser

__all__ = ["Node20Parser", "PLUGIN"]
