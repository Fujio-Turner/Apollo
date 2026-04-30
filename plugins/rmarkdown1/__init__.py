"""
R Markdown plugin package.

rmarkdown1 parses .Rmd files as structured code/markdown documents.
"""
from .parser import RMarkdownParser

PLUGIN = RMarkdownParser

__all__ = ["RMarkdownParser", "PLUGIN"]
