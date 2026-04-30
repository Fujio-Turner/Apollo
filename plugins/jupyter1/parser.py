"""
plugins.jupyter1 — Jupyter notebook plugin for Apollo.

Parses .ipynb files using the standard library json module.
Extracts code cells as functions, markdown cells as sections, and imports/calls.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)


class JupyterParser(BaseParser):
    """Parse Jupyter notebooks (.ipynb) into Apollo's standard result dict."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".ipynb"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".ipynb"])
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
        try:
            notebook = json.loads(source)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("invalid JSON in %s: %s", filepath, exc)
            return None

        if not isinstance(notebook, dict) or "cells" not in notebook:
            logger.warning("not a valid notebook: %s", filepath)
            return None

        cells = notebook.get("cells", [])
        functions = []
        variables = []
        imports = []
        comments = []

        line_no = 0
        for cell_idx, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "")
            source_lines = cell.get("source", [])

            if isinstance(source_lines, str):
                source_lines = source_lines.splitlines(keepends=False)

            if not source_lines:
                continue

            cell_start = line_no + 1
            cell_source = "\n".join(source_lines) if source_lines else ""
            line_no += len(source_lines)

            if cell_type == "code":
                # Extract imports
                for src_line in source_lines:
                    if src_line.strip().startswith("import ") or src_line.strip().startswith("from "):
                        parts = src_line.split()
                        if parts[0] == "import":
                            imports.append({
                                "module": parts[1] if len(parts) > 1 else "",
                                "names": [],
                                "alias": None,
                                "line": cell_start + source_lines.index(src_line),
                                "level": 0,
                            })
                        elif parts[0] == "from":
                            module = parts[1] if len(parts) > 1 else ""
                            imports.append({
                                "module": module,
                                "names": parts[3:] if len(parts) > 3 else [],
                                "alias": None,
                                "line": cell_start + source_lines.index(src_line),
                                "level": 0,
                            })

                # Treat code cell as a function block
                func_name = f"cell_{cell_idx}"
                functions.append({
                    "name": func_name,
                    "line_start": cell_start,
                    "line_end": line_no,
                    "source": cell_source,
                    "docstring": None,
                    "parameters": [],
                    "decorators": [],
                    "calls": [],
                })

                # Extract variable assignments
                for src_idx, src_line in enumerate(source_lines):
                    if "=" in src_line and not src_line.strip().startswith("#"):
                        match = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=", src_line)
                        if match:
                            var_name = match.group(1)
                            variables.append({
                                "name": var_name,
                                "line": cell_start + src_idx,
                            })

            elif cell_type == "markdown":
                # Treat markdown cell as a comment/section
                section_name = f"section_{cell_idx}"
                comments.append({
                    "tag": "SECTION",
                    "text": " ".join(source_lines)[:100],
                    "line": cell_start,
                })

        return {
            "file": filepath,
            "functions": functions,
            "classes": [],
            "imports": imports,
            "variables": variables,
            "comments": comments,
        }
