"""
OpenAPI 3 plugin for Apollo — parses OpenAPI 3.x YAML/JSON specs.

Extracts endpoints, schemas, parameters, and $ref edge relationships
for API documentation integration.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# Pattern for $ref in OpenAPI specs
_REF_RE = re.compile(r'"\$ref"\s*:\s*"([^"]+)"')


class OpenAPI3Parser(BaseParser):
    """Parser for OpenAPI 3.x YAML and JSON specs."""

    def __init__(self, config: Optional[dict] = None):
        """Initialize with optional config."""
        self.config = config or {
            "enabled": True,
            "extensions": [".yaml", ".yml", ".json"],
        }

    def can_parse(self, filepath: str) -> bool:
        """Return True if this looks like an OpenAPI spec file."""
        if not self.config.get("enabled", True):
            return False

        lower = filepath.lower()
        # Accept standard OpenAPI filenames or .yaml/.yml/.json
        if "openapi" in lower or "swagger" in lower:
            return lower.endswith((".yaml", ".yml", ".json"))

        return False

    def parse_file(self, filepath: str) -> dict | None:
        """Parse OpenAPI spec and extract structure."""
        try:
            path = Path(filepath)
            source = path.read_text(encoding="utf-8")
            return self.parse_source(source, filepath)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", filepath, e)
            return None

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse OpenAPI source and extract entities."""
        # Try parsing as JSON first, then YAML
        data = None
        try:
            data = json.loads(source)
        except json.JSONDecodeError:
            # Try YAML
            try:
                import yaml
                data = yaml.safe_load(source)
            except Exception as e:
                logger.warning("OpenAPI parse error in %s: %s", filepath, e)
                return None

        if not data:
            return None

        variables = []
        imports = []

        # Extract top-level OpenAPI structure
        if isinstance(data, dict):
            # Add info as variable
            if "info" in data:
                info = data["info"]
                if isinstance(info, dict) and "title" in info:
                    variables.append({
                        "name": info["title"],
                        "line": 1,
                    })

            # Extract paths as endpoints (functions-like)
            paths = data.get("paths", {})
            if isinstance(paths, dict):
                for path_name in paths.keys():
                    variables.append({
                        "name": path_name,
                        "line": 1,
                    })

            # Extract schema definitions
            components = data.get("components", {})
            if isinstance(components, dict):
                schemas = components.get("schemas", {})
                if isinstance(schemas, dict):
                    for schema_name in schemas.keys():
                        variables.append({
                            "name": schema_name,
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
