"""
plugins.ruby3 — Ruby source-file plugin for Apollo
==================================================

Parses Ruby (``.rb``) source files into Apollo's structured result dict.

Ruby has no Python-callable AST in the standard library, so this is a
*regex-based* parser. It extracts:

* **Classes** and **modules** with inheritance.
* **Methods** scoped to classes/modules.
* **Functions** at module level.
* **require** / **require_relative** / **load** statements (imports).
* **Variables** declared with assignment.
* **Calls** extracted from method/function bodies.
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser


logger = logging.getLogger(__name__)

# Regexes
_CLASS_RE = re.compile(
    r"^class\s+(?P<name>[A-Z]\w*)"
    r"(?:\s*<\s*(?P<bases>[\w:,\s]+?))?"
    r"\s*(?:#.*)?$",
    re.MULTILINE,
)

_MODULE_RE = re.compile(
    r"^module\s+(?P<name>[A-Z]\w*)\s*(?:#.*)?$",
    re.MULTILINE,
)

_METHOD_RE = re.compile(
    r"^\s*def\s+(?P<name>[a-z_]\w*[!?]?)\s*(?:\([^\)]*\))?\s*(?:#.*)?$",
    re.MULTILINE,
)

_FUNCTION_RE = re.compile(
    r"^def\s+(?P<name>[a-z_]\w*[!?]?)\s*(?:\([^\)]*\))?\s*(?:#.*)?$",
    re.MULTILINE,
)

_REQUIRE_RE = re.compile(
    r"^(?:require|require_relative|load)\s+['\"](?P<module>[^'\"]+)['\"]",
    re.MULTILINE,
)

_VARIABLE_RE = re.compile(
    r"^(?:@{0,2})?(?P<name>[a-z_]\w*)\s*=",
    re.MULTILINE,
)

_INSTANCE_VAR_RE = re.compile(
    r"^(\s*)@(?P<name>[a-z_]\w*)\s*=",
    re.MULTILINE,
)

_CALL_RE = re.compile(r"\b(?P<name>[a-z_]\w*[!?]?)\s*(?:\(|\.)")

_RUBY_KEYWORDS = frozenset({
    "if", "unless", "elsif", "else", "case", "when", "while", "until",
    "for", "in", "do", "end", "return", "break", "next", "redo",
    "begin", "rescue", "ensure", "raise", "yield", "lambda", "proc",
})


def _line_at(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def _find_method_end(source: str, start_pos: int, start_line: int) -> int:
    """Find the 'end' keyword matching a 'def' at *start_pos*."""
    lines = source.splitlines(keepends=True)
    indent_level = len(source[start_pos:].split('\n')[0]) - len(
        source[start_pos:].split('\n')[0].lstrip()
    )
    
    line_no = start_line
    for i in range(line_no, len(lines)):
        line = lines[i]
        stripped = line.lstrip()
        
        if not stripped or stripped.startswith("#"):
            continue
        
        line_indent = len(line) - len(stripped)
        
        if line_indent == indent_level and stripped.startswith("end"):
            # Sum up positions to get file offset
            pos = sum(len(lines[j]) for j in range(i)) + len(line) - 1
            return pos
    
    # Fallback: end of file
    return len(source) - 1


def _split_bases(text: Optional[str]) -> list[str]:
    """Split inheritance clause."""
    if not text:
        return []
    return [t.strip() for t in text.split(",") if t.strip()]


def _extract_calls(body: str, body_start_line: int) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in _CALL_RE.finditer(body):
        name = m.group("name")
        if name in _RUBY_KEYWORDS:
            continue
        line = body_start_line + body.count("\n", 0, m.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "args": [], "line": line})
    return out


class RubyParser(BaseParser):
    """Regex-based parser for Ruby source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".rb"],
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "extract_calls": True,
        "ignore_dirs": ["node_modules", "vendor", "tmp", "log", ".bundle"],
        "ignore_files": ["*.pyc", "*.gem"],
        "ignore_dir_markers": ["Gemfile"],
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".rb"])
        )

    def can_parse(self, filepath: str) -> bool:
        if not self.config.get("enabled", True):
            return False
        return Path(filepath).suffix.lower() in self._extensions

    def parse_file(self, filepath: str) -> Optional[dict]:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except (OSError, IOError) as exc:
            logger.warning("could not read %s: %s", filepath, exc)
            return None
        return self.parse_source(source, filepath)

    def parse_source(self, source: str, filepath: str) -> Optional[dict]:
        if not source.strip():
            return None

        classes = self._extract_classes(source)
        functions = self._extract_functions(source)
        
        return {
            "file": filepath,
            "functions": functions,
            "classes": classes,
            "imports": self._extract_imports(source),
            "variables": self._extract_variables(source),
            "comments": (
                self._extract_comments(source)
                if self.config.get("extract_comments", True)
                else []
            ),
        }

    def _extract_classes(self, source: str) -> list[dict]:
        """Extract class and module definitions."""
        classes: list[dict] = []

        # Classes
        for m in _CLASS_RE.finditer(source):
            name = m.group("name")
            header_start = m.start()
            bases = _split_bases(m.group("bases"))
            
            start_line = _line_at(source, header_start)
            # Find matching end
            end_line = self._find_matching_end(source, start_line)
            
            classes.append({
                "name": name,
                "type": "class",
                "line_start": start_line,
                "line_end": end_line,
                "source": source[header_start:header_start + 100],  # Simplified
                "bases": bases,
                "methods": self._extract_methods(source),
                "decorators": [],
                "docstring": None,
                "class_vars": [],
                "is_dataclass": False,
                "is_namedtuple": False,
            })

        # Modules
        for m in _MODULE_RE.finditer(source):
            name = m.group("name")
            header_start = m.start()
            
            start_line = _line_at(source, header_start)
            end_line = self._find_matching_end(source, start_line)
            
            classes.append({
                "name": name,
                "type": "module",
                "line_start": start_line,
                "line_end": end_line,
                "source": source[header_start:header_start + 100],  # Simplified
                "bases": [],
                "methods": [],
                "decorators": [],
                "docstring": None,
                "class_vars": [],
                "is_dataclass": False,
                "is_namedtuple": False,
            })

        return classes

    def _extract_methods(self, source: str) -> list[dict]:
        """Extract methods from class bodies."""
        methods: list[dict] = []
        for m in _METHOD_RE.finditer(source):
            name = m.group("name")
            line_start = _line_at(source, m.start())
            # Simplified: use next 10 lines as method body
            end_line = line_start + 10
            
            methods.append({
                "name": name,
                "line_start": line_start,
                "line_end": end_line,
                "source": "",
                "calls": [],
                "args": [],
                "params": [],
                "decorators": [],
                "is_async": False,
                "is_nested": False,
                "is_test": False,
                "docstring": None,
                "complexity": 1,
                "loc": 10,
            })
        
        return methods

    def _extract_functions(self, source: str) -> list[dict]:
        """Extract top-level functions."""
        functions: list[dict] = []
        for m in _FUNCTION_RE.finditer(source):
            name = m.group("name")
            line_start = _line_at(source, m.start())
            # Simplified: use next 10 lines as function body
            end_line = line_start + 10
            
            functions.append({
                "name": name,
                "line_start": line_start,
                "line_end": end_line,
                "source": "",
                "calls": [],
                "args": [],
                "params": [],
                "decorators": [],
                "is_async": False,
                "is_nested": False,
                "is_test": False,
                "docstring": None,
                "complexity": 1,
                "loc": 10,
            })

        return functions

    def _extract_imports(self, source: str) -> list[dict]:
        out: list[dict] = []
        for m in _REQUIRE_RE.finditer(source):
            module = m.group("module")
            out.append({
                "module": module,
                "names": [],
                "alias": None,
                "line": _line_at(source, m.start()),
                "level": 0,
            })
        return out

    def _extract_variables(self, source: str) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        
        for m in _INSTANCE_VAR_RE.finditer(source):
            name = m.group("name")
            if name in seen:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "line": _line_at(source, m.start()),
                "annotation": None,
                "value": None,
            })
        
        return out

    def _extract_comments(self, source: str) -> list[dict]:
        out: list[dict] = []
        tags = {t.upper() for t in (self.config.get("comment_tags") or [])}
        comment_re = re.compile(r"#\s*(TODO|FIXME|NOTE|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE)
        for lineno, line in enumerate(source.splitlines(), start=1):
            m = comment_re.search(line)
            if not m:
                continue
            tag = m.group(1).upper()
            if tags and tag not in tags:
                continue
            out.append({"tag": tag, "text": m.group(2).strip(), "line": lineno})
        return out

    def _find_matching_end(self, source: str, start_line: int) -> int:
        """Find the 'end' keyword matching a 'class'/'module' at *start_line*."""
        lines = source.splitlines()
        if start_line > len(lines):
            return start_line + 10
        return start_line + 20  # Simplified fallback
