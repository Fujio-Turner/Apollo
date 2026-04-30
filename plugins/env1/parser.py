"""
plugins.env1 — Environment variables (.env) plugin for Apollo.

Parses .env and .env.* files to extract KEY=value pairs as variables.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)


class EnvParser(BaseParser):
    """Parse .env files into Apollo's standard result dict."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".env"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        name = Path(filepath).name
        return name == ".env" or name.startswith(".env.")

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

        assignment_pattern = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.+)")
        comment_pattern = re.compile(r"^#\s*(.+)")

        for line_idx, line in enumerate(lines):
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

            # KEY=value assignments
            assign_match = assignment_pattern.match(line)
            if assign_match:
                var_name = assign_match.group(1)
                var_value = assign_match.group(2).strip()
                
                # Strip quotes if present
                if (var_value.startswith('"') and var_value.endswith('"')) or \
                   (var_value.startswith("'") and var_value.endswith("'")):
                    var_value = var_value[1:-1]

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
