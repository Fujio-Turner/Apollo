# SPDX-License-Identifier: BUSL-1.1
"""Gradle plugin package for Apollo."""
from .parser import GradleParser

PLUGIN = GradleParser

__all__ = ["GradleParser", "PLUGIN"]
