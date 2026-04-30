"""
JSON 1 plugin for Apollo — parses .json files.

Extracts top-level keys as variables, detects $ref references
for schema relationships, and analyzes JSON structure.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# Regex to find $ref and other reference patterns
_REF_RE = re.compile(r'"\$ref"\s*:\s*"([^"]+)"')
_ANCHOR_RE = re.compile(r'"\$(?:id|anchor)"\s*:\s*"([^"]+)"')


class JSONParser(BaseParser):
    """Parser for JSON files."""

    def __init__(self, config: Optional[dict] = None):
        """Initialize with optional config."""
        self.config = config or {"enabled": True, "extensions": [".json"]}

    def can_parse(self, filepath: str) -> bool:
        """Return True if this is a .json file and plugin is enabled."""
        if not self.config.get("enabled", True):
            return False
        return filepath.lower().endswith(".json")

    def parse_file(self, filepath: str) -> dict | None:
        """Parse JSON file and extract structure."""
        try:
            path = Path(filepath)
            source = path.read_text(encoding="utf-8")
            return self.parse_source(source, filepath)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", filepath, e)
            return None

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse JSON source and extract entities."""
        try:
            data = json.loads(source)
        except json.JSONDecodeError as e:
            logger.warning("JSON parse error in %s: %s", filepath, e)
            return None

        variables = []
        imports = []

        # Extract top-level keys as variables
        if isinstance(data, dict):
            for key in data.keys():
                variables.append({
                    "name": key,
                    "line": 1,
                })

        # Find all $ref references (schema edges)
        for match in _REF_RE.finditer(source):
            ref_target = match.group(1)
            line = source[:match.start()].count('\n') + 1
            imports.append({
                "module": ref_target,
                "line": line,
            })

        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": imports,
            "variables": variables,
        }
