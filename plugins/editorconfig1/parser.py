"""
plugins.editorconfig1 — EditorConfig plugin for Apollo.

Parses .editorconfig files to extract sections and properties as variables.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)


class EditorConfigParser(BaseParser):
    """Parse EditorConfig files into Apollo's standard result dict."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".editorconfig"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        return Path(filepath).name == ".editorconfig"

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

        section_pattern = re.compile(r"^\[(.+)\]")
        property_pattern = re.compile(r"^(\w+)\s*=\s*(.+)")
        comment_pattern = re.compile(r"^[;#]\s*(.+)")

        current_section = None

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1
            line = line.strip()

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

            # Sections
            section_match = section_pattern.match(line)
            if section_match:
                current_section = section_match.group(1)
                variables.append({
                    "name": f"section_{current_section}",
                    "line": line_no,
                })
                continue

            # Properties
            prop_match = property_pattern.match(line)
            if prop_match:
                prop_name = prop_match.group(1)
                prop_value = prop_match.group(2)
                full_name = f"{current_section}.{prop_name}" if current_section else prop_name
                variables.append({
                    "name": full_name,
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
