"""
plugins.properties1 — Java properties files plugin for Apollo.

Parses .properties and .props files to extract key=value pairs as variables.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)


class PropertiesParser(BaseParser):
    """Parse Java properties files into Apollo's standard result dict."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".properties", ".props"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".properties"])
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
            logger.warning("failed to read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        lines = source.splitlines(keepends=False)
        variables = []
        comments = []

        # Handle line continuations by joining lines
        joined_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            while i < len(lines) - 1 and line.rstrip().endswith("\\"):
                line = line.rstrip()[:-1] + lines[i + 1]
                i += 1
            joined_lines.append(line)
            i += 1

        assignment_pattern = re.compile(r"^([^=:#\s][^=:#]*?)\s*[:=]\s*(.+)")
        comment_pattern = re.compile(r"^[#!]\s*(.+)")

        for line_idx, line in enumerate(joined_lines):
            line_no = line_idx + 1
            line = line.rstrip()

            if not line:
                continue

            # Comments
            comment_match = comment_pattern.match(line)
            if comment_match:
                comments.append({
                    "tag": "NOTE",
                    "text": comment_match.group(1),
                    "line": line_no,
                })
                continue

            # key=value or key:value assignments
            assign_match = assignment_pattern.match(line)
            if assign_match:
                key = assign_match.group(1).strip()
                value = assign_match.group(2).strip()

                # Normalize key (replace dots with underscores for valid identifiers)
                var_name = re.sub(r"[^a-zA-Z0-9_]", "_", key)

                variables.append({
                    "name": var_name,
                    "line": line_no,
                })

        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": [],
            "variables": variables,
            "comments": comments,
        }
