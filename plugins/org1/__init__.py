# SPDX-License-Identifier: BUSL-1.1
"""plugins.org1 — Apollo plugin."""
from .parser import OrgParser

PLUGIN = OrgParser

__all__ = ["OrgParser", "PLUGIN"]
