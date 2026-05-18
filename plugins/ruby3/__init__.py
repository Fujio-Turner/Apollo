# SPDX-License-Identifier: BUSL-1.1
"""Ruby plugin package."""
from .parser import RubyParser

PLUGIN = RubyParser

__all__ = ["RubyParser", "PLUGIN"]
