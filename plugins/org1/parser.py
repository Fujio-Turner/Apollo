"""
plugins.org1 — Org mode plugin for Apollo
=========================================

Parses Org mode (``.org``) files into Apollo's structured result dict.

Extracts:
- Headings and outline structure
- Links and references
- Code blocks and examples
- Properties and metadata
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class OrgParser(BaseParser):
    """Parse Org mode files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".org"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".org"])
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
        """Parse Org mode source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_code_blocks(source, lines),
            "classes": self._extract_headings(source, lines),
            "imports": self._extract_links(source, lines),
            "variables": self._extract_properties(source, lines),
        }

    def _extract_headings(self, source: str, lines: list[str]) -> list[dict]:
        """Extract Org headings (* ** *** etc.)."""
        headings = []
        # Org headings: ^\\*+ (with optional TODO, tags, etc.)
        heading_re = re.compile(r'^(\*+)\s+(?:TODO|DONE)?\s*(.+?)(?:\s+:\w+:)?\s*$', re.MULTILINE)
        
        for m in heading_re.finditer(source):
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            line_start = source[:m.start()].count("\n") + 1
            line_end = line_start
            
            headings.append({
                "name": heading_text,
                "line_start": line_start,
                "line_end": line_end,
                "source": heading_text,
                "methods": [],
                "bases": [],
            })
        
        # Also catch plain headings without TODO/DONE
        plain_heading_re = re.compile(r'^(\*+)\s+(.+?)(?:\s+:\w+:)?\s*$', re.MULTILINE)
        for m in plain_heading_re.finditer(source):
            if not any(h["line_start"] == source[:m.start()].count("\n") + 1 for h in headings):
                level = len(m.group(1))
                heading_text = m.group(2).strip()
                line_start = source[:m.start()].count("\n") + 1
                line_end = line_start
                
                headings.append({
                    "name": heading_text,
                    "line_start": line_start,
                    "line_end": line_end,
                    "source": heading_text,
                    "methods": [],
                    "bases": [],
                })
        
        return headings

    def _extract_links(self, source: str, lines: list[str]) -> list[dict]:
        """Extract links and references."""
        references = []
        
        # [[link][description]] format
        bracket_link_re = re.compile(r'\[\[([^\]]+)\]\[([^\]]+)\]\]')
        for m in bracket_link_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            link = m.group(1)
            desc = m.group(2)
            references.append({
                "module": link,
                "names": [desc],
                "alias": None,
                "line": line_num,
            })
        
        # [[link]] format
        simple_link_re = re.compile(r'\[\[([^\]]+)\]\]')
        for m in simple_link_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            link = m.group(1)
            # Skip if already added as bracket link
            if not any(r["module"] == link for r in references):
                references.append({
                    "module": link,
                    "names": [],
                    "alias": None,
                    "line": line_num,
                })
        
        # Plain URLs
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
        # #+BEGIN_SRC language ... #+END_SRC or #+BEGIN_EXAMPLE ... #+END_EXAMPLE
        code_re = re.compile(
            r'#\+BEGIN_(?:SRC|EXAMPLE)(?:\s+(\w+))?\s*\n(.*?)\n#\+END_(?:SRC|EXAMPLE)',
            re.DOTALL | re.IGNORECASE,
        )
        
        block_num = 0
        for m in code_re.finditer(source):
            language = m.group(1) or "text"
            code_content = m.group(2).strip()
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

    def _extract_properties(self, source: str, lines: list[str]) -> list[dict]:
        """Extract properties and metadata."""
        variables = []
        
        # #+PROPERTY: key value
        prop_re = re.compile(r'^#\+(\w+):\s*(.+)$', re.MULTILINE | re.IGNORECASE)
        
        for m in prop_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            prop_name = m.group(1)
            variables.append({"name": prop_name, "line": line_num})
        
        # :PROPERTIES: ... :END: blocks
        properties_re = re.compile(r':(\w+):\s*(.+)$', re.MULTILINE)
        for m in properties_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            prop_name = m.group(1)
            # Avoid duplicates
            if not any(v["name"] == prop_name and v["line"] == line_num for v in variables):
                variables.append({"name": prop_name, "line": line_num})
        
        return variables
