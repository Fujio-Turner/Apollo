"""
JSON Schema plugin for Apollo — parses .schema.json files.

Extracts schema definitions, $ref relationships, type hierarchies,
and property definitions for schema documentation.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# Pattern for $ref in JSON Schema
_REF_RE = re.compile(r'"\$ref"\s*:\s*"([^"]+)"')
_DEFS_RE = re.compile(r'"(\$)?def(?:initions)?"\s*:', re.IGNORECASE)


class JSONSchemaParser(BaseParser):
    """Parser for JSON Schema files (.schema.json)."""

    def __init__(self, config: Optional[dict] = None):
        """Initialize with optional config."""
        self.config = config or {"enabled": True, "extensions": [".schema.json"]}

    def can_parse(self, filepath: str) -> bool:
        """Return True if this is a .schema.json file and plugin is enabled."""
        if not self.config.get("enabled", True):
            return False
        return filepath.lower().endswith(".schema.json")

    def parse_file(self, filepath: str) -> dict | None:
        """Parse JSON Schema file and extract structure."""
        try:
            path = Path(filepath)
            source = path.read_text(encoding="utf-8")
            return self.parse_source(source, filepath)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", filepath, e)
            return None

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse JSON Schema source and extract entities."""
        try:
            data = json.loads(source)
        except json.JSONDecodeError as e:
            logger.warning("JSON parse error in %s: %s", filepath, e)
            return None

        variables = []
        imports = []

        # Extract schema title or root element name
        if isinstance(data, dict):
            if "title" in data:
                variables.append({
                    "name": data["title"],
                    "line": 1,
                })
            if "$id" in data:
                variables.append({
                    "name": f"${data['$id']}",
                    "line": 1,
                })

            # Extract property definitions
            if "properties" in data and isinstance(data["properties"], dict):
                for prop_name in data["properties"].keys():
                    variables.append({
                        "name": prop_name,
                        "line": 1,
                    })

            # Extract definitions from $defs or definitions
            defs = data.get("$defs") or data.get("definitions", {})
            if isinstance(defs, dict):
                for def_name in defs.keys():
                    variables.append({
                        "name": def_name,
                        "line": 1,
                    })

        # Find all $ref references (creates edges between schemas)
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
