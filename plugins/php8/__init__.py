# SPDX-License-Identifier: BUSL-1.1
"""PHP 8 plugin package."""
from .parser import PHPParser

PLUGIN = PHPParser

__all__ = ["PHPParser", "PLUGIN"]
