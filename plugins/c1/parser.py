"""
plugins.c1 — C source-file plugin for Apollo
============================================

Parses C (``.c``, ``.h``) source files into Apollo's structured result dict.

C has no Python-callable AST in the standard library, so this is a
*regex-based* parser. It extracts:

* **Functions** at file level (C has no classes).
* **#include** statements (imports).
* **#define** and **#pragma** directives.
* **Variables** and **structs** at file level.
* **Calls** extracted from function bodies.
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser


logger = logging.getLogger(__name__)

# Regexes
_STRUCT_RE = re.compile(
    r"(?:typedef\s+)?struct\s+(?P<name>[A-Za-z_]\w*)?\s*\{"
)

_FUNCTION_RE = re.compile(
    r"(?:(?:static|inline|extern)\s+)*"
    r"(?:[\w*&\[\]<>,\s]+?)\s+"
    r"(?P<name>[a-z_]\w*)\s*\([^\)]*\)\s*"
    r"(?:__attribute__\s*\([^\)]*\))?\s*\{"
)

_INCLUDE_RE = re.compile(
    r'#include\s+[<"]([^>"]+)[>"]'
)

_DEFINE_RE = re.compile(
    r"^#define\s+(?P<name>[A-Za-z_]\w*)",
    re.MULTILINE,
)

_VARIABLE_RE = re.compile(
    r"^(?:(?:static|extern|const|volatile)\s+)*"
    r"(?:[\w*&\[\]<>,\s]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:=|;)",
    re.MULTILINE,
)

_CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_][\w]*)\s*\(")

_C_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "case", "do", "else",
    "return", "break", "continue", "goto", "sizeof",
})


def _line_at(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def _find_matching_brace(source: str, open_pos: int) -> int:
    """Return the index of the ``}`` matching the ``{`` at *open_pos*."""
    depth = 0
    i = open_pos
    n = len(source)
    in_str: Optional[str] = None
    in_line_comment = False
    in_block_comment = False
    
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        
        if ch in ('"', "'"):
            in_str = ch
            i += 1
            continue
        
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        
        i += 1
    
    return n - 1


def _extract_calls(body: str, body_start_line: int) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in _CALL_RE.finditer(body):
        name = m.group("name")
        if name in _C_KEYWORDS:
            continue
        line = body_start_line + body.count("\n", 0, m.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "args": [], "line": line})
    return out


class CParser(BaseParser):
    """Regex-based parser for C source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".c", ".h"],
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "extract_calls": True,
        "ignore_dirs": ["build", "dist"],
        "ignore_files": ["*.o", "*.a", "*.so"],
        "ignore_dir_markers": ["Makefile"],
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".c", ".h"])
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

        functions = self._extract_functions(source)
        
        return {
            "file": filepath,
            "functions": functions,
            "classes": self._extract_structs(source),  # C structs as pseudo-classes
            "imports": self._extract_includes(source),
            "variables": self._extract_variables(source),
            "comments": (
                self._extract_comments(source)
                if self.config.get("extract_comments", True)
                else []
            ),
        }

    def _extract_structs(self, source: str) -> list[dict]:
        """Extract struct definitions (as pseudo-classes)."""
        structs: list[dict] = []
        for m in _STRUCT_RE.finditer(source):
            name = m.group("name") or "anonymous_struct"
            header_start = m.start()
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            
            structs.append({
                "name": name,
                "type": "struct",
                "line_start": _line_at(source, header_start),
                "line_end": _line_at(source, close_brace),
                "source": source[header_start : close_brace + 1],
                "bases": [],
                "methods": [],
                "decorators": [],
                "docstring": None,
                "class_vars": [],
                "is_dataclass": False,
                "is_namedtuple": False,
            })
        
        return structs

    def _extract_functions(self, source: str) -> list[dict]:
        """Extract top-level functions."""
        functions: list[dict] = []
        for m in _FUNCTION_RE.finditer(source):
            open_brace = m.end() - 1  # Regex ends with {
            close_brace = _find_matching_brace(source, open_brace)

            line_start = _line_at(source, m.start())
            line_end = _line_at(source, close_brace)
            inner = source[open_brace + 1 : close_brace]
            calls = (
                _extract_calls(inner, _line_at(source, open_brace + 1))
                if self.config.get("extract_calls", True)
                else []
            )

            functions.append({
                "name": m.group("name"),
                "line_start": line_start,
                "line_end": line_end,
                "source": source[m.start() : close_brace + 1],
                "calls": calls,
                "args": [],
                "params": [],
                "decorators": [],
                "is_async": False,
                "is_nested": False,
                "is_test": False,
                "docstring": None,
                "complexity": 1,
                "loc": line_end - line_start + 1,
            })

        return functions

    def _extract_includes(self, source: str) -> list[dict]:
        out: list[dict] = []
        for m in _INCLUDE_RE.finditer(source):
            module = m.group(1)
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
        for m in _VARIABLE_RE.finditer(source):
            name = m.group("name")
            if name in seen or name in _C_KEYWORDS:
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
        comment_re = re.compile(r"//\s*(TODO|FIXME|NOTE|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE)
        for lineno, line in enumerate(source.splitlines(), start=1):
            m = comment_re.search(line)
            if not m:
                continue
            tag = m.group(1).upper()
            if tags and tag not in tags:
                continue
            out.append({"tag": tag, "text": m.group(2).strip(), "line": lineno})
        return out
