# SPDX-License-Identifier: BUSL-1.1
"""plugins.powershell7 — Apollo plugin."""
from .parser import PowerShellParser

PLUGIN = PowerShellParser

__all__ = ["PowerShellParser", "PLUGIN"]
