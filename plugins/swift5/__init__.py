# SPDX-License-Identifier: BUSL-1.1
"""Swift plugin package."""
from .parser import SwiftParser

PLUGIN = SwiftParser

__all__ = ["SwiftParser", "PLUGIN"]
