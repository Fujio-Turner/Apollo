"""Gradle plugin package for Apollo."""
from .parser import GradleParser

PLUGIN = GradleParser

__all__ = ["GradleParser", "PLUGIN"]
