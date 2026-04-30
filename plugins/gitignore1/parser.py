"""
plugins.gitignore1 — Gitignore plugin for Apollo.

Parses .gitignore files to extract patterns and comments as variables.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)


class GitIgnoreParser(BaseParser):
    """Parse .gitignore files into Apollo's standard result dict."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".gitignore"],
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
        return name == ".gitignore" or name.endswith(".gitignore")

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

        pattern_num = 0

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1
            orig_line = line
            line = line.strip()

            if not line:
                continue

            # Comments
            if line.startswith("#"):
                comment_text = line[1:].strip()
                comments.append({
                    "tag": "NOTE",
                    "text": comment_text,
                    "line": line_no,
                })
                continue

            # Negation patterns
            if line.startswith("!"):
                pattern_num += 1
                variables.append({
                    "name": f"negation_{pattern_num}",
                    "line": line_no,
                })
            else:
                pattern_num += 1
                # Normalize pattern for name
                safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", line[:20])
                variables.append({
                    "name": f"pattern_{safe_name}",
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
