"""
plugins.asciidoc1 — AsciiDoc plugin for Apollo
==============================================

Parses AsciiDoc (``.adoc``, ``.asciidoc``) files into Apollo's structured result dict.

Extracts:
- Document sections (= heading levels)
- Internal xrefs (cross-references)
- Includes and external references
- Code blocks
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class AsciiDocParser(BaseParser):
    """Parse AsciiDoc files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".adoc", ".asciidoc"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".adoc", ".asciidoc"])
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
        """Parse AsciiDoc source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_code_blocks(source, lines),
            "classes": self._extract_sections(source, lines),
            "imports": self._extract_includes_and_xrefs(source, lines),
            "variables": self._extract_attributes(source, lines),
        }

    def _extract_sections(self, source: str, lines: list[str]) -> list[dict]:
        """Extract document sections (headings)."""
        sections = []
        # AsciiDoc heading levels: = == === ==== =====
        # = is title, == is level 1, === is level 2, etc.
        heading_re = re.compile(r'^(=+)\s+(.+?)\s*$', re.MULTILINE)
        
        for m in heading_re.finditer(source):
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            line_start = source[:m.start()].count("\n") + 1
            line_end = line_start
            
            sections.append({
                "name": heading_text,
                "line_start": line_start,
                "line_end": line_end,
                "source": heading_text,
                "methods": [],
                "bases": [],
            })
        
        return sections

    def _extract_includes_and_xrefs(self, source: str, lines: list[str]) -> list[dict]:
        """Extract include directives and cross-references."""
        references = []
        
        # include::file[] or include::dir/file.adoc[]
        include_re = re.compile(r'include::([^\[\]]+)\[\]')
        for m in include_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            filepath = m.group(1)
            references.append({
                "module": filepath,
                "names": [],
                "alias": None,
                "line": line_num,
            })
        
        # xref:id[text] cross-references
        xref_re = re.compile(r'xref:([^\[\]]+)\[([^\]]*)\]')
        for m in xref_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            ref_id = m.group(1)
            ref_text = m.group(2) or ref_id
            references.append({
                "module": f"xref:{ref_id}",
                "names": [ref_text],
                "alias": None,
                "line": line_num,
            })
        
        # URLs in format link:url[text]
        link_re = re.compile(r'link:([^\[\]]+)\[([^\]]*)\]')
        for m in link_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            url = m.group(1)
            link_text = m.group(2)
            references.append({
                "module": url,
                "names": [link_text] if link_text else [],
                "alias": None,
                "line": line_num,
            })
        
        return references

    def _extract_code_blocks(self, source: str, lines: list[str]) -> list[dict]:
        """Extract code blocks."""
        blocks = []
        # Code blocks: [source,language] followed by ---- ... ----
        # or [listing] followed by .... ....
        code_block_re = re.compile(
            r'\[(?:source|listing).*?\]\n([-=`]+)\n(.*?)\n\1',
            re.DOTALL,
        )
        
        block_num = 0
        for m in code_block_re.finditer(source):
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

    def _extract_attributes(self, source: str, lines: list[str]) -> list[dict]:
        """Extract attribute definitions."""
        variables = []
        # :attribute: value or :attribute: (document attributes)
        attr_re = re.compile(r'^:([A-Za-z_][\w-]*?):\s*(.+)?$', re.MULTILINE)
        
        for m in attr_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            attr_name = m.group(1)
            variables.append({"name": attr_name, "line": line_num})
        
        return variables
