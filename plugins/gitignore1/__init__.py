"""Gitignore plugin package for Apollo."""
from .parser import GitIgnoreParser

PLUGIN = GitIgnoreParser

__all__ = ["GitIgnoreParser", "PLUGIN"]
