"""
plugins.shell1 — Shell/Bash script plugin for Apollo
=======================================================

Parses shell scripts (``.sh``, ``.bash``) into Apollo's structured result dict.

Extracts:
- Function definitions
- Source/. statements (sourced files)
- Command calls
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class ShellParser(BaseParser):
    """Parse shell/bash scripts."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".sh", ".bash"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".sh", ".bash"])
        )

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        ext = Path(filepath).suffix.lower()
        if ext in self._extensions:
            return True
        # Also check for scripts with shebang
        try:
            with open(filepath, 'rb') as f:
                first_line = f.readline().decode('utf-8', errors='ignore')
                return first_line.startswith(('#!', '#!/bin/sh', '#!/bin/bash'))
        except Exception:
            return False

    def parse_file(self, filepath: str) -> dict | None:
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("could not read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, str(filepath))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse shell script source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_functions(source, lines),
            "classes": [],
            "imports": self._extract_sources(source, lines),
            "variables": self._extract_variables(source, lines),
        }

    def _extract_sources(self, source: str, lines: list[str]) -> list[dict]:
        """Extract source/. statements."""
        imports = []
        # source file or . file
        source_re = re.compile(
            r'^\s*(?:source|\.)\s+(?:"([^"]+)"|\'([^\']+)\'|(\S+))',
            re.MULTILINE,
        )
        for m in source_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            filepath = m.group(1) or m.group(2) or m.group(3)
            imports.append({
                "module": filepath,
                "names": [],
                "alias": None,
                "line": line_num,
            })
        return imports

    def _extract_functions(self, source: str, lines: list[str]) -> list[dict]:
        """Extract function definitions."""
        functions = []
        # function_name() { ... } or function function_name { ... }
        func_re = re.compile(
            r'^(?:\s*function\s+)?(\w+)\s*\(\s*\)\s*\{',
            re.MULTILINE,
        )
        
        for m in func_re.finditer(source):
            name = m.group(1)
            line_start = source[:m.start()].count("\n") + 1
            line_end = self._find_block_end(source, m.end())
            
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

    def _extract_variables(self, source: str, lines: list[str]) -> list[dict]:
        """Extract variable assignments."""
        variables = []
        # NAME=value or export NAME=value
        var_re = re.compile(
            r'^(?:\s*(?:export|local|declare)\s+)?([A-Za-z_]\w*)\s*=',
            re.MULTILINE,
        )
        
        for m in var_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            name = m.group(1)
            variables.append({"name": name, "line": line_num})
        return variables

    def _extract_calls(self, func_source: str) -> list[dict]:
        """Extract command calls."""
        calls = []
        # Bash command pattern: word at line start or after pipes/semicolons
        call_re = re.compile(r'(?:^|\s+|[|;])\s*(\w+)(?:\s|$|\||;|[){])', re.MULTILINE)
        
        seen = set()
        for m in call_re.finditer(func_source):
            cmd_name = m.group(1)
            # Filter out shell keywords and builtins
            builtins = {
                "if", "then", "else", "elif", "fi", "for", "while", "do", "done",
                "case", "esac", "function", "return", "exit", "break", "continue",
                "local", "export", "declare", "read", "echo", "printf", "test",
                "true", "false", "[", "cd", "pwd", "ls", "mkdir", "rm", "cp",
            }
            if cmd_name not in builtins:
                if cmd_name not in seen:
                    seen.add(cmd_name)
                    calls.append({"name": cmd_name, "source": cmd_name})
        
        return calls

    def _find_block_end(self, source: str, start_pos: int) -> int:
        """Find the end line of a block (between { and })."""
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
