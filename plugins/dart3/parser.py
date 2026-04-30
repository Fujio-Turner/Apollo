"""
plugins.dart3 — Dart 3 source-file plugin for Apollo
=====================================================

Parses Dart (``.dart``) source files into Apollo's structured result dict.

Extracts:
- Classes and their methods
- Top-level functions
- Import statements
- Variable declarations
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser



logger = logging.getLogger(__name__)

class DartParser(BaseParser):
    """Parse Dart source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".dart"],
    }

    def __init__(self, config: dict | None = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".dart"])
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
        """Parse Dart source code."""
        lines = source.splitlines()
        
        return {
            "file": filepath,
            "functions": self._extract_functions(source, lines),
            "classes": self._extract_classes(source, lines),
            "imports": self._extract_imports(source, lines),
            "variables": self._extract_variables(source, lines),
        }

    def _extract_imports(self, source: str, lines: list[str]) -> list[dict]:
        """Extract import/export statements."""
        imports = []
        # import 'package:...'; or import 'dart:...';
        import_re = re.compile(
            r'^(?:import|export)\s+["\']([^"\']+)["\'](?:\s+as\s+(\w+))?',
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

    def _extract_classes(self, source: str, lines: list[str]) -> list[dict]:
        """Extract class definitions."""
        classes = []
        # class Name { ... } or class Name extends Base { ... }
        class_re = re.compile(
            r'^\s*(?:abstract\s+)?class\s+(\w+)(?:\s+(?:extends|implements|with)\s+([^{]+?))?(?:\s*\{)',
            re.MULTILINE,
        )
        
        for m in class_re.finditer(source):
            name = m.group(1)
            bases_str = m.group(2) or ""
            line_start = source[:m.start()].count("\n") + 1
            line_end = self._find_block_end(source, m.end(), lines)
            
            class_lines = lines[line_start - 1:line_end]
            class_source = "\n".join(class_lines)
            
            classes.append({
                "name": name,
                "line_start": line_start,
                "line_end": line_end,
                "source": class_source,
                "methods": self._extract_class_methods(class_source),
                "bases": [b.strip() for b in bases_str.split(",")] if bases_str else [],
            })
        return classes

    def _extract_functions(self, source: str, lines: list[str]) -> list[dict]:
        """Extract top-level function definitions."""
        functions = []
        # Type name(...) or name(...)
        func_re = re.compile(
            r'^(?:(?:async\s+)?(?:Future|Stream|void|int|String|bool|dynamic|\w+)\s+)?(\w+)\s*\([^)]*\)\s*(?:async\s*)?[{=]',
            re.MULTILINE,
        )
        
        seen_funcs = set()
        for m in func_re.finditer(source):
            name = m.group(1)
            # Skip constructors and methods already extracted in classes
            if name[0].isupper():
                continue
            if name in seen_funcs:
                continue
                
            line_start = source[:m.start()].count("\n") + 1
            line_end = self._find_block_end(source, m.end(), lines)
            
            # Check if this is at top-level (not inside a class)
            preceding = source[:m.start()]
            class_count = preceding.count(" class ") - preceding.count("}")
            if class_count > 0:
                continue
            
            func_lines = lines[line_start - 1:line_end]
            func_source = "\n".join(func_lines)
            
            functions.append({
                "name": name,
                "line_start": line_start,
                "line_end": line_end,
                "source": func_source,
                "calls": self._extract_calls(func_source),
            })
            seen_funcs.add(name)
        
        return functions

    def _extract_class_methods(self, class_source: str) -> list[dict]:
        """Extract methods from a class."""
        methods = []
        # Type name(...) or name(...)
        method_re = re.compile(
            r'(?:async\s+)?(?:Future|Stream|void|int|String|bool|dynamic|\w+\s+)?(\w+)\s*\([^)]*\)\s*(?:async\s*)?[{=;]',
        )
        
        seen = set()
        for m in method_re.finditer(class_source):
            name = m.group(1)
            if name not in seen:
                methods.append({"name": name})
                seen.add(name)
        
        return methods

    def _extract_variables(self, source: str, lines: list[str]) -> list[dict]:
        """Extract variable declarations."""
        variables = []
        # var name = ..., final name = ..., late name = ..., Type name = ...
        var_re = re.compile(
            r'^(?:\s*(?:late\s+)?(?:var|final|const|static|int|String|bool|double|dynamic|List|Map|\w+)\s+(\w+)\s*[=;])',
            re.MULTILINE,
        )
        
        for m in var_re.finditer(source):
            line_num = source[:m.start()].count("\n") + 1
            name = m.group(1)
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
            dart_keywords = {"if", "for", "while", "switch", "try", "catch", "assert"}
            if call_name not in dart_keywords:
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
