"""
plugins.powershell7 — PowerShell 7 script plugin for Apollo
===========================================================

Parses PowerShell scripts (``.ps1``) into Apollo's structured result dict.

Extracts:
- Function definitions
- Dot-source references (. .\file.ps1)
- Cmdlet calls
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class PowerShellParser(BaseParser):
    """Parse PowerShell scripts."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".ps1"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".ps1"])
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
        """Parse PowerShell source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_functions(source, lines),
            "classes": [],
            "imports": self._extract_dot_sources(source, lines),
            "variables": self._extract_variables(source, lines),
        }

    def _extract_dot_sources(self, source: str, lines: list[str]) -> list[dict]:
        """Extract dot-source statements."""
        imports = []
        # . .\file.ps1 or . "path\file.ps1"
        dot_source_re = re.compile(
            r'^\s*\.\s+(?:"([^"]+)"|\'([^\']+)\'|([^\s;]+))',
            re.MULTILINE,
        )
        for m in dot_source_re.finditer(source):
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
        # function name { ... } or function name() { ... }
        func_re = re.compile(
            r'^\s*function\s+([A-Za-z_]\w*)\s*(?:\(.*?\))?\s*\{',
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
        # $name = value
        var_re = re.compile(r'^\s*\$([A-Za-z_]\w*)\s*=', re.MULTILINE)
        
        for m in var_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            name = m.group(1)
            variables.append({"name": name, "line": line_num})
        return variables

    def _extract_calls(self, func_source: str) -> list[dict]:
        """Extract cmdlet calls."""
        calls = []
        # PowerShell cmdlet pattern: CmdletName-Verb or CmdletName
        # Matches PascalCase-PascalCase or Get-ChildItem style
        call_re = re.compile(
            r'\b([A-Z][a-zA-Z]*(?:-[A-Z][a-zA-Z]*)?)\s*(?:\(|-)',
            re.MULTILINE,
        )
        
        seen = set()
        for m in call_re.finditer(func_source):
            cmd_name = m.group(1)
            ps_keywords = {
                "If", "Then", "Else", "ElseIf", "EndIf", "For", "ForEach",
                "While", "Do", "Until", "Break", "Continue", "Return",
                "Function", "Param", "DynamicParam", "Begin", "Process", "End",
            }
            if cmd_name not in ps_keywords:
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
