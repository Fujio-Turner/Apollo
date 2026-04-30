"""
plugins.r1 — R language source-file plugin for Apollo
======================================================

Parses R (``.R`` or ``.r``) source files into Apollo's structured result dict.

Extracts:
- Functions with line ranges and source
- Function calls and their arguments
- library() and require() statements
- Top-level variable assignments
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class RParser(BaseParser):
    """Parse R source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".r", ".R"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".R", ".r"])
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
            logger.warning("could not read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse R source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_functions(source, lines),
            "classes": [],
            "imports": self._extract_imports(source, lines),
            "variables": self._extract_variables(source, lines),
        }

    def _extract_imports(self, source: str, lines: list[str]) -> list[dict]:
        """Extract library() and require() calls."""
        imports = []
        # library(package) or require(package)
        import_re = re.compile(
            r'^\s*(?:library|require)\s*\(\s*["\']?(\w+)["\']?\s*\)',
            re.MULTILINE,
        )
        for m in import_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            module = m.group(1)
            imports.append({
                "module": module,
                "names": [],
                "alias": None,
                "line": line_num,
            })
        return imports

    def _extract_functions(self, source: str, lines: list[str]) -> list[dict]:
        """Extract function definitions."""
        functions = []
        # name <- function(...) { ... }
        func_re = re.compile(
            r'^(\w+)\s*<-\s*function\s*\([^)]*\)\s*\{',
            re.MULTILINE,
        )
        
        for m in func_re.finditer(source):
            name = m.group(1)
            line_start = source[:m.start()].count("\n") + 1
            line_end = self._find_block_end(source, m.end(), lines)
            func_lines = lines[line_start - 1:line_end]
            func_source = "\n".join(func_lines)
            
            functions.append({
                "name": name,
                "line_start": line_start,
                "line_end": line_end,
                "source": func_source,
                "calls": self._extract_calls(func_source),
            })
        return functions

    def _extract_variables(self, source: str, lines: list[str]) -> list[dict]:
        """Extract variable assignments."""
        variables = []
        # name <- value or name = value
        var_re = re.compile(r'^(\w+)\s*(?:<-|=)\s*', re.MULTILINE)
        
        for m in var_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            name = m.group(1)
            # Skip if this is a function definition (checked earlier)
            line_text = source[m.start():source.find("\n", m.start())].strip()
            if "function" not in line_text:
                variables.append({"name": name, "line": line_num})
        return variables

    def _extract_calls(self, func_source: str) -> list[dict]:
        """Extract function calls from source."""
        calls = []
        # Pattern: word(
        call_re = re.compile(r'\b(\w+)\s*\(')
        seen = set()
        for m in call_re.finditer(func_source):
            call_name = m.group(1)
            if call_name not in {"if", "for", "while", "repeat", "switch"}:
                if call_name not in seen:
                    seen.add(call_name)
                    calls.append({"name": call_name, "source": call_name})
        return calls

    def _find_block_end(self, source: str, start_pos: int, lines: list[str]) -> int:
        """Find the end line of a block starting at start_pos."""
        depth = 0
        line_num = source[:start_pos].count("\n") + 1
        for i in range(start_pos, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth <= 0:
                    return source[:i].count("\n") + 1
            elif source[i] == "\n":
                line_num += 1
        return line_num
