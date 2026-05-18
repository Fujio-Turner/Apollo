# SPDX-License-Identifier: BUSL-1.1
"""Gitignore plugin package for Apollo."""
from .parser import GitIgnoreParser

PLUGIN = GitIgnoreParser

__all__ = ["GitIgnoreParser", "PLUGIN"]
