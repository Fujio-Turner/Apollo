"""
YAML 1 plugin for Apollo — parses .yaml/.yml files.

Extracts top-level keys, anchors/aliases, !include directives,
and internal references.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# Patterns for YAML references
_INCLUDE_RE = re.compile(r'!include\s+([^\s\n]+)', re.IGNORECASE)
_ANCHOR_RE = re.compile(r'&(\w+)\s*')
_ALIAS_RE = re.compile(r'\*(\w+)')
_KEY_RE = re.compile(r'^(\s*)([a-zA-Z_]\w*):', re.MULTILINE)
_REF_RE = re.compile(r'(\$ref|ref):\s*([^\s\n]+)')


class YAMLParser(BaseParser):
    """Parser for YAML files (.yaml, .yml)."""

    def __init__(self, config: Optional[dict] = None):
        """Initialize with optional config."""
        self.config = config or {"enabled": True, "extensions": [".yaml", ".yml"]}

    def can_parse(self, filepath: str) -> bool:
        """Return True if this is a .yaml/.yml file and plugin is enabled."""
        if not self.config.get("enabled", True):
            return False
        lower = filepath.lower()
        return lower.endswith((".yaml", ".yml"))

    def parse_file(self, filepath: str) -> dict | None:
        """Parse YAML file and extract structure."""
        try:
            path = Path(filepath)
            source = path.read_text(encoding="utf-8")
            return self.parse_source(source, filepath)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", filepath, e)
            return None

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse YAML source and extract entities."""
        variables = []
        imports = []
        seen_vars = set()

        # Extract top-level keys (indent level 0)
        for match in _KEY_RE.finditer(source):
            indent = match.group(1)
            key = match.group(2)
            # Top-level keys have no indentation
            if not indent and key not in seen_vars:
                variables.append({
                    "name": key,
                    "line": source[:match.start()].count('\n') + 1,
                })
                seen_vars.add(key)

        # Extract !include directives (dependencies)
        for match in _INCLUDE_RE.finditer(source):
            include_path = match.group(1)
            line = source[:match.start()].count('\n') + 1
            imports.append({
                "module": include_path,
                "line": line,
            })

        # Extract references like $ref or ref:
        for match in _REF_RE.finditer(source):
            ref_target = match.group(2)
            line = source[:match.start()].count('\n') + 1
            imports.append({
                "module": ref_target,
                "line": line,
            })

        # Extract anchors as variables
        for match in _ANCHOR_RE.finditer(source):
            anchor_name = match.group(1)
            line = source[:match.start()].count('\n') + 1
            if anchor_name not in seen_vars:
                variables.append({
                    "name": f"@{anchor_name}",  # prefix with @ to indicate anchor
                    "line": line,
                })
                seen_vars.add(anchor_name)

        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": imports,
            "variables": variables,
        }
