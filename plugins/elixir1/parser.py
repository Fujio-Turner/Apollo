"""
plugins.elixir1 — Elixir source-file plugin for Apollo
=======================================================

Parses Elixir (``.ex`` or ``.exs``) source files into Apollo's structured result dict.

Extracts:
- Module definitions and their functions
- Function definitions with arity
- Import/alias statements
- Pipe chain operations
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class ElixirParser(BaseParser):
    """Parse Elixir source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".ex", ".exs"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".ex", ".exs"])
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
        """Parse Elixir source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_functions(source, lines),
            "classes": self._extract_modules(source, lines),
            "imports": self._extract_imports(source, lines),
            "variables": self._extract_variables(source, lines),
        }

    def _extract_imports(self, source: str, lines: list[str]) -> list[dict]:
        """Extract import/alias/require statements."""
        imports = []
        # import Module, alias Module, require Module
        import_re = re.compile(
            r'^(?:\s*)?(?:import|alias|require)\s+((?:\w+\.)*\w+)(?:\s+as\s+(\w+))?',
            re.MULTILINE,
        )
        for m in import_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            module = m.group(1)
            alias = m.group(2)
            imports.append({
                "module": module,
                "names": [],
                "alias": alias,
                "line": line_num,
            })
        return imports

    def _extract_modules(self, source: str, lines: list[str]) -> list[dict]:
        """Extract module definitions."""
        modules = []
        # defmodule Name do ... end
        module_re = re.compile(
            r'^\s*defmodule\s+((?:\w+\.)*\w+)\s+do',
            re.MULTILINE,
        )
        
        for m in module_re.finditer(source):
            name = m.group(1)
            line_start = source[:m.start()].count("\n") + 1
            line_end = self._find_end_keyword(source, m.end())
            
            module_lines = lines[line_start - 1:line_end]
            module_source = "\n".join(module_lines)
            
            modules.append({
                "name": name,
                "line_start": line_start,
                "line_end": line_end,
                "source": module_source,
                "methods": self._extract_module_functions(module_source),
                "bases": [],
            })
        return modules

    def _extract_functions(self, source: str, lines: list[str]) -> list[dict]:
        """Extract function definitions."""
        functions = []
        # def name(...) or defp name(...) or def name/arity
        func_re = re.compile(
            r'^\s*(?:def|defp|defmacro)\s+(\w+)(?:/\d+|\s*\()',
            re.MULTILINE,
        )
        
        for m in func_re.finditer(source):
            name = m.group(1)
            line_start = source[:m.start()].count("\n") + 1
            line_end = self._find_end_keyword(source, m.end())
            
            if line_start > 0 and line_end > 0 and line_start <= len(lines) and line_end <= len(lines):
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

    def _extract_module_functions(self, module_source: str) -> list[dict]:
        """Extract functions from a module."""
        functions = []
        func_re = re.compile(r'\b(?:def|defp|defmacro)\s+(\w+)(?:/\d+|\s*\()?')
        
        seen = set()
        for m in func_re.finditer(module_source):
            name = m.group(1)
            if name not in seen:
                functions.append({"name": name})
                seen.add(name)
        
        return functions

    def _extract_variables(self, source: str, lines: list[str]) -> list[dict]:
        """Extract variable assignments (captured in pattern matching, @module vars)."""
        variables = []
        # @attribute_name = value
        attr_re = re.compile(r'^\s*@(\w+)\s*=', re.MULTILINE)
        
        for m in attr_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            name = m.group(1)
            variables.append({"name": name, "line": line_num})
        return variables

    def _extract_calls(self, func_source: str) -> list[dict]:
        """Extract function calls and pipe operations."""
        calls = []
        # Pattern: word( or |> word or . word
        call_re = re.compile(r'(?:\|>|\.)\s*(\w+)|\b(\w+)\s*\(')
        seen = set()
        
        for m in call_re.finditer(func_source):
            call_name = m.group(1) or m.group(2)
            if call_name:
                elixir_keywords = {"if", "cond", "case", "do", "for", "when", "end"}
                if call_name not in elixir_keywords:
                    if call_name not in seen:
                        seen.add(call_name)
                        calls.append({"name": call_name, "source": call_name})
        return calls

    def _find_end_keyword(self, source: str, start_pos: int) -> int:
        """Find the line number of the matching 'end' keyword."""
        depth = 1
        keywords = {"do", "defmodule", "def", "defp", "defmacro", "if", "cond", "case", "for"}
        line_num = source[:start_pos].count("\n") + 1
        
        for i in range(start_pos, len(source)):
            word_match = re.match(r'\b(\w+)\b', source[i:])
            if word_match:
                word = word_match.group(1)
                if word in keywords:
                    depth += 1
                elif word == "end":
                    depth -= 1
                    if depth == 0:
                        return source[:i].count("\n") + 1
            if source[i] == "\n":
                line_num += 1
        
        return line_num
