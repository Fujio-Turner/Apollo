# SPDX-License-Identifier: BUSL-1.1
"""
Jupyter plugin package.

jupyter1 parses .ipynb Jupyter notebooks as structured code/markdown documents.
"""
from .parser import JupyterParser

PLUGIN = JupyterParser

__all__ = ["JupyterParser", "PLUGIN"]
