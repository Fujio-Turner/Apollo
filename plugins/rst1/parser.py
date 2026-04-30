"""
plugins.rst1 — reStructuredText plugin for Apollo
=================================================

Parses reStructuredText (``.rst``) files into Apollo's structured result dict.

Extracts:
- Document sections (headings)
- Internal references (:ref:)
- External links and URLs
- Code blocks
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class RstParser(BaseParser):
    """Parse reStructuredText files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".rst"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".rst"])
        )

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        return Path(filepath).suffix.lower() in self._extensions

    def parse_file(self, filepath: str) -> dict | None:
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("could not read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse reStructuredText source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_code_blocks(source, lines),
            "classes": self._extract_sections(source, lines),
            "imports": self._extract_references(source, lines),
            "variables": self._extract_directives(source, lines),
        }

    def _extract_sections(self, source: str, lines: list[str]) -> list[dict]:
        """Extract document sections (headings)."""
        sections = []
        # RST heading patterns: underline with ====, ----, ~~~~, etc.
        heading_re = re.compile(
            r'^([^=\-~`\'"~^_*+#:<>\\]+)\s*$\n(=+|-+|~+|`+|\'+|~+|\\+|\^+|_+|\*+|#+|<+|>+)\s*$',
            re.MULTILINE,
        )
        
        for m in heading_re.finditer(source):
            heading_text = m.group(1).strip()
            line_start = source[:m.start()].count("\n") + 1
            line_end = line_start + 1  # Underline is next line
            
            sections.append({
                "name": heading_text,
                "line_start": line_start,
                "line_end": line_end,
                "source": heading_text,
                "methods": [],
                "bases": [],
            })
        
        # Also catch overline+text+underline style
        styled_re = re.compile(
            r'^(=+|-+|~+|`+)\s*$\n([^=\-~`\'"]+)\n\1\s*$',
            re.MULTILINE,
        )
        
        for m in styled_re.finditer(source):
            heading_text = m.group(2).strip()
            line_start = source[:m.start()].count("\n") + 1
            line_end = line_start + 2
            
            sections.append({
                "name": heading_text,
                "line_start": line_start,
                "line_end": line_end,
                "source": heading_text,
                "methods": [],
                "bases": [],
            })
        
        return sections

    def _extract_references(self, source: str, lines: list[str]) -> list[dict]:
        """Extract internal references and links."""
        references = []
        
        # :ref:`label` internal references
        ref_re = re.compile(r':ref:`([^`]+)`')
        for m in ref_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            ref_name = m.group(1)
            references.append({
                "module": f"ref:{ref_name}",
                "names": [],
                "alias": None,
                "line": line_num,
            })
        
        # `text <url>`_ external links
        link_re = re.compile(r'`([^<>`]+)\s*<([^>]+)>`_')
        for m in link_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            link_text = m.group(1)
            link_url = m.group(2)
            references.append({
                "module": link_url,
                "names": [link_text],
                "alias": None,
                "line": line_num,
            })
        
        # URLs
        url_re = re.compile(r'https?://\S+')
        for m in url_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            url = m.group(0)
            references.append({
                "module": url,
                "names": [],
                "alias": None,
                "line": line_num,
            })
        
        return references

    def _extract_code_blocks(self, source: str, lines: list[str]) -> list[dict]:
        """Extract code blocks."""
        blocks = []
        # Code blocks preceded by :: and indented
        code_re = re.compile(
            r'::[ ]*$\n((?:(?:    |\t).+$\n?)*)',
            re.MULTILINE,
        )
        
        block_num = 0
        for m in code_re.finditer(source):
            code_content = m.group(1).strip()
            if code_content:
                line_start = source[:m.start()].count("\n") + 1
                line_end = source[:m.end()].count("\n") + 1
                
                blocks.append({
                    "name": f"code_block_{block_num}",
                    "line_start": line_start,
                    "line_end": line_end,
                    "source": code_content,
                    "calls": [],
                })
                block_num += 1
        
        return blocks

    def _extract_directives(self, source: str, lines: list[str]) -> list[dict]:
        """Extract RST directives."""
        variables = []
        # .. directive:: argument
        directive_re = re.compile(r'^\.\.\s+([\w:-]+)::\s*(.+)?', re.MULTILINE)
        
        for m in directive_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            directive_name = m.group(1)
            variables.append({"name": directive_name, "line": line_num})
        
        return variables
