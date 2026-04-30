"""
TOML 1 plugin for Apollo — parses .toml files.

Extracts tables, keys, and dependency lists from TOML configuration
and project files.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# TOML patterns
_TABLE_RE = re.compile(r'^\[\[?([a-zA-Z0-9\-_.]+)\]?\]', re.MULTILINE)
_KEY_RE = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_-]*)\s*=', re.MULTILINE)
_DEPENDENCY_RE = re.compile(r'([a-zA-Z0-9_-]+)\s*=')


class TOMLParser(BaseParser):
    """Parser for TOML files."""

    def __init__(self, config: Optional[dict] = None):
        """Initialize with optional config."""
        self.config = config or {"enabled": True, "extensions": [".toml"]}

    def can_parse(self, filepath: str) -> bool:
        """Return True if this is a .toml file and plugin is enabled."""
        if not self.config.get("enabled", True):
            return False
        return filepath.lower().endswith(".toml")

    def parse_file(self, filepath: str) -> dict | None:
        """Parse TOML file and extract structure."""
        try:
            path = Path(filepath)
            source = path.read_text(encoding="utf-8")
            return self.parse_source(source, filepath)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", filepath, e)
            return None

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse TOML source and extract entities."""
        # Try to use tomllib (3.11+) or fall back to tomli
        try:
            if sys.version_info >= (3, 11):
                import tomllib
                data = tomllib.loads(source)
            else:
                import tomli
                data = tomli.loads(source)
        except Exception as e:
            logger.warning("TOML parse error in %s: %s", filepath, e)
            # Fall back to regex parsing
            return self._parse_regex(source, filepath)

        variables = []
        imports = []

        # Extract top-level keys as variables
        if isinstance(data, dict):
            for key in data.keys():
                variables.append({
                    "name": key,
                    "line": 1,
                })

            # Extract dependency package names from [tool.poetry.dependencies], etc.
            deps_sections = [
                data.get("dependencies", {}),
                data.get("project", {}).get("dependencies", []),
            ]
            if "tool" in data and isinstance(data["tool"], dict):
                if "poetry" in data["tool"] and isinstance(data["tool"]["poetry"], dict):
                    deps_sections.append(data["tool"]["poetry"].get("dependencies", {}))

            for deps in deps_sections:
                if isinstance(deps, dict):
                    for dep in deps.keys():
                        imports.append({
                            "module": dep,
                            "line": 1,
                        })
                elif isinstance(deps, list):
                    for dep in deps:
                        if isinstance(dep, str):
                            # Extract package name before version specifier
                            pkg_name = re.split(r'[<>=!]', dep)[0].strip()
                            if pkg_name:
                                imports.append({
                                    "module": pkg_name,
                                    "line": 1,
                                })

        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": imports,
            "variables": variables,
        }

    def _parse_regex(self, source: str, filepath: str) -> dict:
        """Fallback regex-based TOML parsing."""
        variables = []
        imports = []
        seen = set()

        # Extract tables as sections
        for match in _TABLE_RE.finditer(source):
            table = match.group(1)
            if table not in seen:
                variables.append({
                    "name": f"[{table}]",
                    "line": source[:match.start()].count('\n') + 1,
                })
                seen.add(table)

        # Extract top-level keys
        for match in _KEY_RE.finditer(source):
            key = match.group(1)
            # Check if it's before any [table] declaration
            pos = match.start()
            if not _TABLE_RE.search(source, 0, pos) or key not in seen:
                if key not in seen:
                    variables.append({
                        "name": key,
                        "line": source[:match.start()].count('\n') + 1,
                    })
                    seen.add(key)

        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": imports,
            "variables": variables,
        }
