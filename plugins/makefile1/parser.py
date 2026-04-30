"""
plugins.makefile1 — Makefile plugin for Apollo
===============================================

Parses Makefile into Apollo's structured result dict.

Extracts:
- Targets as entities
- Prerequisites as dependencies
- Recipes as execution
- Variables
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class MakefileParser(BaseParser):
    """Parse Makefile."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "filenames": ["Makefile", "makefile", "GNUmakefile"],
        "extensions": [".mk"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._filenames = frozenset(
            self.config.get("filenames") or ["Makefile", "makefile", "GNUmakefile"]
        )
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".mk"])
        )

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        path = Path(filepath)
        if path.name in self._filenames:
            return True
        return path.suffix.lower() in self._extensions

    def parse_file(self, filepath: str) -> dict | None:
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("could not read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse Makefile source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_targets(source, lines),
            "classes": [],
            "imports": self._extract_includes(source, lines),
            "variables": self._extract_variables(source, lines),
        }

    def _extract_includes(self, source: str, lines: list[str]) -> list[dict]:
        """Extract include directives."""
        imports = []
        # include file or -include file
        include_re = re.compile(r'^-?include\s+([^\s#]+)', re.MULTILINE)
        
        for m in include_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            filepath = m.group(1)
            imports.append({
                "module": filepath,
                "names": [],
                "alias": None,
                "line": line_num,
            })
        
        return imports

    def _extract_targets(self, source: str, lines: list[str]) -> list[dict]:
        """Extract make targets."""
        targets = []
        # target: prerequisites
        target_re = re.compile(
            r'^([a-zA-Z0-9_./-]+)\s*:([^=]*?)$',
            re.MULTILINE,
        )
        
        for m in target_re.finditer(source):
            target_name = m.group(1).strip()
            # Skip variable assignments that look like targets
            if "=" not in target_name:
                prerequisites = m.group(2).strip()
                line_start = source[:m.start()].count("\n") + 1
                
                # Find end of target (recipe lines + next target)
                next_target = target_re.search(source[m.end():])
                if next_target:
                    line_end = source[:m.end() + next_target.start()].count("\n") + 1 - 1
                else:
                    line_end = len(lines)
                
                if line_start <= len(lines) and line_end <= len(lines):
                    target_lines = lines[line_start - 1:min(line_end, len(lines))]
                    target_source = "\n".join(target_lines)
                    
                    targets.append({
                        "name": target_name,
                        "line_start": line_start,
                        "line_end": line_end,
                        "source": target_source,
                        "calls": self._extract_prerequisites(prerequisites),
                    })
        
        return targets

    def _extract_variables(self, source: str, lines: list[str]) -> list[dict]:
        """Extract variable assignments."""
        variables = []
        # VAR = value or VAR := value or VAR ?= value
        var_re = re.compile(r'^([A-Za-z_]\w*)\s*(?::=|=|\?=|::=)', re.MULTILINE)
        
        for m in var_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            name = m.group(1)
            variables.append({"name": name, "line": line_num})
        
        return variables

    def _extract_prerequisites(self, prerequisites_str: str) -> list[dict]:
        """Extract prerequisites (dependencies) from a target line."""
        calls = []
        # Split on whitespace, filter out variables and special chars
        for prereq in prerequisites_str.split():
            # Skip variables and special chars
            if prereq and not prereq.startswith("$") and not prereq.startswith("-"):
                calls.append({"name": prereq, "source": prereq})
        
        return calls
