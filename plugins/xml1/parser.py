"""
XML 1 plugin for Apollo — parses .xml files.

Extracts elements, attributes, xmlns declarations,
and internal id/href references.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# Patterns for internal references
_ID_RE = re.compile(r'id\s*=\s*["\']([^"\']+)["\']')
_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']')
_XMLNS_RE = re.compile(r'xmlns(?::(\w+))?\s*=\s*["\']([^"\']+)["\']')


class XMLParser(BaseParser):
    """Parser for XML files."""

    def __init__(self, config: Optional[dict] = None):
        """Initialize with optional config."""
        self.config = config or {"enabled": True, "extensions": [".xml"]}

    def can_parse(self, filepath: str) -> bool:
        """Return True if this is a .xml file and plugin is enabled."""
        if not self.config.get("enabled", True):
            return False
        return filepath.lower().endswith(".xml")

    def parse_file(self, filepath: str) -> dict | None:
        """Parse XML file and extract structure."""
        try:
            path = Path(filepath)
            source = path.read_text(encoding="utf-8")
            return self.parse_source(source, filepath)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", filepath, e)
            return None

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse XML source and extract entities."""
        variables = []
        imports = []
        seen_ids = set()
        seen_ns = set()

        # Try proper XML parsing
        try:
            root = ET.fromstring(source)
            # Extract root element as variable
            variables.append({
                "name": root.tag,
                "line": 1,
            })
            # Extract all element names from the tree
            for elem in root.iter():
                if elem.tag not in variables:
                    variables.append({
                        "name": elem.tag,
                        "line": 1,
                    })
        except ET.ParseError:
            # Fall back to regex parsing
            pass

        # Extract xmlns namespace declarations
        for match in _XMLNS_RE.finditer(source):
            prefix = match.group(1) or "default"
            ns_uri = match.group(2)
            ns_name = f"xmlns:{prefix}" if prefix != "default" else "xmlns"
            if ns_name not in seen_ns:
                variables.append({
                    "name": ns_name,
                    "line": source[:match.start()].count('\n') + 1,
                })
                seen_ns.add(ns_name)
            if ns_uri and ns_uri.startswith("http"):
                imports.append({
                    "module": ns_uri,
                    "line": source[:match.start()].count('\n') + 1,
                })

        # Extract id attributes (internal references)
        for match in _ID_RE.finditer(source):
            elem_id = match.group(1)
            if elem_id not in seen_ids:
                variables.append({
                    "name": f"@{elem_id}",
                    "line": source[:match.start()].count('\n') + 1,
                })
                seen_ids.add(elem_id)

        # Extract href attributes (internal/external links)
        for match in _HREF_RE.finditer(source):
            href = match.group(1)
            line = source[:match.start()].count('\n') + 1
            imports.append({
                "module": href,
                "line": line,
            })

        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": imports,
            "variables": variables,
        }
