# SPDX-License-Identifier: BUSL-1.1
"""plugins.shell1 — Apollo plugin."""
from .parser import ShellParser

PLUGIN = ShellParser

__all__ = ["ShellParser", "PLUGIN"]
