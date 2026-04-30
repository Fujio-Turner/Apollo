"""
plugins.lua5 — Lua 5 source-file plugin for Apollo
===================================================

Parses Lua (``.lua``) source files into Apollo's structured result dict.

Extracts:
- Local and global function definitions
- Tables and their assignments
- require() and other module references
- Function calls within functions
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class LuaParser(BaseParser):
    """Parse Lua source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".lua"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".lua"])
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
        """Parse Lua source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_functions(source, lines),
            "classes": self._extract_tables(source, lines),
            "imports": self._extract_requires(source, lines),
            "variables": self._extract_variables(source, lines),
        }

    def _extract_requires(self, source: str, lines: list[str]) -> list[dict]:
        """Extract require() statements."""
        imports = []
        # require("module") or require 'module'
        require_re = re.compile(
            r'require\s*[\(]?\s*["\'](\w+)["\'][)]?',
            re.MULTILINE,
        )
        for m in require_re.finditer(source):
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
        # local function name(...) or function name(...)
        func_re = re.compile(
            r'(?:local\s+)?function\s+([.\w]+)\s*\([^)]*\)',
            re.MULTILINE,
        )
        
        for m in func_re.finditer(source):
            name = m.group(1)
            line_start = source[:m.start()].count("\n") + 1
            line_end = self._find_end_keyword(source, m.end(), lines, "end")
            if line_start <= line_end and line_end <= len(lines):
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

    def _extract_tables(self, source: str, lines: list[str]) -> list[dict]:
        """Extract table definitions (treated as classes)."""
        tables = []
        # name = { ... }
        table_re = re.compile(
            r'(?:local\s+)?(\w+)\s*=\s*\{',
            re.MULTILINE,
        )
        
        for m in table_re.finditer(source):
            name = m.group(1)
            line_start = source[:m.start()].count("\n") + 1
            line_end = self._find_matching_brace(source, m.end() - 1)
            if line_start <= line_end and line_end <= len(lines):
                table_lines = lines[line_start - 1:line_end]
                table_source = "\n".join(table_lines)
                
                tables.append({
                    "name": name,
                    "line_start": line_start,
                    "line_end": line_end,
                    "source": table_source,
                    "methods": [],
                    "bases": [],
                })
        return tables

    def _extract_variables(self, source: str, lines: list[str]) -> list[dict]:
        """Extract variable assignments."""
        variables = []
        # local name = ... or name = ...
        var_re = re.compile(r'^(?:\s*local\s+)?(\w+)\s*=', re.MULTILINE)
        
        for m in var_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            name = m.group(1)
            variables.append({"name": name, "line": line_num})
        return variables

    def _extract_calls(self, func_source: str) -> list[dict]:
        """Extract function calls from source."""
        calls = []
        # Pattern: word( or word{
        call_re = re.compile(r'\b(\w+)\s*[\({]')
        seen = set()
        for m in call_re.finditer(func_source):
            call_name = m.group(1)
            lua_keywords = {"if", "for", "while", "repeat", "function", "then", "do", "else", "elseif", "end", "local"}
            if call_name not in lua_keywords:
                if call_name not in seen:
                    seen.add(call_name)
                    calls.append({"name": call_name, "source": call_name})
        return calls

    def _find_end_keyword(self, source: str, start_pos: int, lines: list[str], keyword: str) -> int:
        """Find the line number of the matching 'end' keyword."""
        # Count nested function/if/for/while/do keywords and match with 'end'
        depth = 1
        lua_keywords = {"function", "if", "for", "while", "do", "repeat"}
        line_num = source[:start_pos].count("\n") + 1
        
        for i in range(start_pos, len(source)):
            word_match = re.match(r'\b(\w+)\b', source[i:])
            if word_match:
                word = word_match.group(1)
                if word in lua_keywords:
                    depth += 1
                elif word == "end":
                    depth -= 1
                    if depth == 0:
                        return source[:i].count("\n") + 1
            if source[i] == "\n":
                line_num += 1
        
        return line_num

    def _find_matching_brace(self, source: str, open_pos: int) -> int:
        """Find the line number of the matching closing brace."""
        depth = 1
        for i in range(open_pos + 1, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    return source[:i].count("\n") + 1
        return source.count("\n") + 1
