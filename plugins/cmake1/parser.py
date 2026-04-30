"""
plugins.cmake1 — CMake plugin for Apollo
=========================================

Parses CMakeLists.txt into Apollo's structured result dict.

Extracts:
- add_executable/add_library targets
- Dependencies between targets
- include directives
- Variable definitions
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class CMakeParser(BaseParser):
    """Parse CMake files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "filenames": ["CMakeLists.txt"],
        "extensions": [".cmake"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._filenames = frozenset(
            self.config.get("filenames") or ["CMakeLists.txt"]
        )
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".cmake"])
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
        """Parse CMake source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_targets(source, lines),
            "classes": [],
            "imports": self._extract_includes(source, lines),
            "variables": self._extract_variables(source, lines),
        }

    def _extract_includes(self, source: str, lines: list[str]) -> list[dict]:
        """Extract include() directives."""
        imports = []
        # include(file) or include_directories(...)
        include_re = re.compile(
            r'^\s*include\s*\(\s*([^\s)]+)\s*\)',
            re.MULTILINE | re.IGNORECASE,
        )
        
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
        """Extract add_executable and add_library targets."""
        targets = []
        # add_executable(name source1 source2)
        # add_library(name type source1 source2)
        target_re = re.compile(
            r'^\s*add_(?:executable|library|custom_target)\s*\(\s*(\w+)',
            re.MULTILINE | re.IGNORECASE,
        )
        
        for m in target_re.finditer(source):
            target_name = m.group(1)
            line_start = source[:m.start()].count("\n") + 1
            
            # Find matching closing paren
            paren_pos = m.end() - 1
            paren_count = 0
            end_pos = paren_pos
            for i in range(paren_pos, len(source)):
                if source[i] == "(":
                    paren_count += 1
                elif source[i] == ")":
                    paren_count -= 1
                    if paren_count == 0:
                        end_pos = i
                        break
            
            line_end = source[:end_pos].count("\n") + 1
            
            if line_start <= len(lines) and line_end <= len(lines):
                target_lines = lines[line_start - 1:line_end]
                target_source = "\n".join(target_lines)
                
                targets.append({
                    "name": target_name,
                    "line_start": line_start,
                    "line_end": line_end,
                    "source": target_source,
                    "calls": self._extract_dependencies(target_source),
                })
        
        return targets

    def _extract_variables(self, source: str, lines: list[str]) -> list[dict]:
        """Extract set() variable definitions."""
        variables = []
        # set(VAR_NAME value) or set(VAR_NAME value CACHE TYPE ...)
        var_re = re.compile(
            r'^\s*set\s*\(\s*([A-Za-z_]\w*)\b',
            re.MULTILINE | re.IGNORECASE,
        )
        
        for m in var_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            name = m.group(1)
            variables.append({"name": name, "line": line_num})
        
        return variables

    def _extract_dependencies(self, target_source: str) -> list[dict]:
        """Extract target dependencies and linked libraries."""
        calls = []
        # target_link_libraries(name PUBLIC/PRIVATE deps...)
        deps_re = re.compile(
            r'target_link_libraries\s*\(\s*\w+\s+(?:PUBLIC|PRIVATE|INTERFACE)\s+([^)]+)',
            re.IGNORECASE,
        )
        
        for m in deps_re.finditer(target_source):
            deps_str = m.group(1)
            for dep in deps_str.split():
                dep = dep.strip()
                if dep and not dep.startswith("$"):
                    calls.append({"name": dep, "source": dep})
        
        return calls
