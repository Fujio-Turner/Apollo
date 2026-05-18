# SPDX-License-Identifier: BUSL-1.1
"""Java 17 plugin package."""
from .parser import JavaParser

PLUGIN = JavaParser

__all__ = ["JavaParser", "PLUGIN"]
