"""
plugins.rmarkdown1 — R Markdown plugin for Apollo.

Parses .Rmd files to extract R code chunks as functions, markdown sections, and imports/calls.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)


class RMarkdownParser(BaseParser):
    """Parse R Markdown files (.Rmd) into Apollo's standard result dict."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".rmd", ".Rmd"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".rmd"])
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
        functions = []
        variables = []
        imports = []
        comments = []

        chunk_pattern = re.compile(r"^```\{r(.*)?\}")
        import_pattern = re.compile(r"\b(library|require)\s*\(\s*([\"']?)([a-zA-Z0-9_.]+)\2\s*\)")
        var_pattern = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*(?:<-|=)")

        line_no = 0
        in_chunk = False
        chunk_start = 0
        chunk_lines = []
        chunk_num = 0

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1

            if chunk_pattern.match(line):
                if in_chunk:
                    # End previous chunk
                    chunk_source = "\n".join(chunk_lines)
                    chunk_num += 1
                    functions.append({
                        "name": f"chunk_{chunk_num}",
                        "line_start": chunk_start,
                        "line_end": line_no - 1,
                        "source": chunk_source,
                        "docstring": None,
                        "parameters": [],
                        "decorators": [],
                        "calls": [],
                    })
                    chunk_lines = []

                in_chunk = True
                chunk_start = line_no + 1

            elif in_chunk and line.strip().startswith("```"):
                # End chunk
                chunk_source = "\n".join(chunk_lines)
                chunk_num += 1
                functions.append({
                    "name": f"chunk_{chunk_num}",
                    "line_start": chunk_start,
                    "line_end": line_no,
                    "source": chunk_source,
                    "docstring": None,
                    "parameters": [],
                    "decorators": [],
                    "calls": [],
                })
                chunk_lines = []
                in_chunk = False

            elif in_chunk:
                chunk_lines.append(line)

                # Extract imports from chunk
                import_match = import_pattern.search(line)
                if import_match:
                    lib_name = import_match.group(3)
                    imports.append({
                        "module": lib_name,
                        "names": [],
                        "alias": None,
                        "line": line_no,
                        "level": 0,
                    })

                # Extract variable assignments
                var_match = var_pattern.match(line)
                if var_match:
                    var_name = var_match.group(1)
                    variables.append({
                        "name": var_name,
                        "line": line_no,
                    })

            else:
                # Markdown section
                if line.strip().startswith("#"):
                    level = len(line) - len(line.lstrip("#"))
                    text = line.lstrip("#").strip()
                    comments.append({
                        "tag": "SECTION",
                        "text": text,
                        "line": line_no,
                    })

        # Handle unclosed chunk
        if in_chunk and chunk_lines:
            chunk_source = "\n".join(chunk_lines)
            chunk_num += 1
            functions.append({
                "name": f"chunk_{chunk_num}",
                "line_start": chunk_start,
                "line_end": line_no,
                "source": chunk_source,
                "docstring": None,
                "parameters": [],
                "decorators": [],
                "calls": [],
            })

        return {
            "file": filepath,
            "functions": functions,
            "classes": [],
            "imports": imports,
            "variables": variables,
            "comments": comments,
        }
