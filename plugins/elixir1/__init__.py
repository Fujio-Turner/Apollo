# SPDX-License-Identifier: BUSL-1.1
"""plugins.elixir1 — Apollo plugin."""
from .parser import ElixirParser

PLUGIN = ElixirParser

__all__ = ["ElixirParser", "PLUGIN"]
