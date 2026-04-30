"""
plugins.terraform1 — Terraform plugin for Apollo.

Parses .tf files to extract resources, data sources, variables, and module sources as imports.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)


class TerraformParser(BaseParser):
    """Parse Terraform files (.tf) into Apollo's standard result dict."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".tf"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".tf"])
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
        classes = []

        # Patterns
        resource_pattern = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"\s*\{')
        data_pattern = re.compile(r'^\s*data\s+"([^"]+)"\s+"([^"]+)"\s*\{')
        variable_pattern = re.compile(r'^\s*variable\s+"([^"]+)"\s*\{')
        module_pattern = re.compile(r'^\s*module\s+"([^"]+)"\s*\{')
        source_pattern = re.compile(r'^\s*source\s+=\s+"([^"]+)"')
        assignment_pattern = re.compile(r'^\s*(\w+)\s*=\s*(.+)')

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1

            # Resource definitions
            res_match = resource_pattern.match(line)
            if res_match:
                res_type = res_match.group(1)
                res_name = res_match.group(2)
                functions.append({
                    "name": f"{res_type}.{res_name}",
                    "line_start": line_no,
                    "line_end": line_no,
                    "source": line.strip(),
                    "docstring": None,
                    "parameters": [],
                    "decorators": [],
                    "calls": [],
                })

            # Data source definitions
            data_match = data_pattern.match(line)
            if data_match:
                data_type = data_match.group(1)
                data_name = data_match.group(2)
                classes.append({
                    "name": f"{data_type}.{data_name}",
                    "line_start": line_no,
                    "line_end": line_no,
                    "source": line.strip(),
                    "methods": [],
                    "docstring": None,
                })

            # Variable definitions
            var_match = variable_pattern.match(line)
            if var_match:
                var_name = var_match.group(1)
                variables.append({
                    "name": var_name,
                    "line": line_no,
                })

            # Module definitions
            mod_match = module_pattern.match(line)
            if mod_match:
                mod_name = mod_match.group(1)
                # Look ahead for source
                for next_idx in range(line_idx + 1, min(line_idx + 10, len(lines))):
                    next_line = lines[next_idx]
                    src_match = source_pattern.match(next_line)
                    if src_match:
                        source_path = src_match.group(1)
                        imports.append({
                            "module": source_path,
                            "names": [],
                            "alias": mod_name,
                            "line": next_idx + 1,
                            "level": 0,
                        })
                        break
                    if next_line.strip() == "}":
                        break

        return {
            "file": filepath,
            "functions": functions,
            "classes": classes,
            "imports": imports,
            "variables": variables,
            "comments": [],
        }
