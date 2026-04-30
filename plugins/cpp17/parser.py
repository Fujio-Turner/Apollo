"""
plugins.cpp17 — C++17 source-file plugin for Apollo
===================================================

Parses C++ (``.cpp``, ``.hpp``) source files into Apollo's structured result dict.

C++ has no Python-callable AST in the standard library, so this is a
*regex-based* parser. It extracts:

* **Classes** and **structs** with their inheritance.
* **Functions** at file and class level (methods).
* **#include** statements (imports).
* **#define** and **#pragma** directives.
* **Variables** and **members** at class/file level.
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
    r"(?:template\s*<[^>]+>\s*)?"
    r"(?:class|struct)\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*:\s*(?:public|private|protected)?\s*(?P<bases>[\w:,\s]+?))?"
    r"\s*\{"
)

_FUNCTION_RE = re.compile(
    r"(?:(?:inline|constexpr|static|extern|virtual|explicit|override)\s+)*"
    r"(?:<[^>]+>\s+)?"
    r"(?:[\w:*&\[\]<>,\s]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^\)]*\)\s*"
    r"(?:const|noexcept|override)*\s*\{"
)

_METHOD_RE = re.compile(
    r"(?:(?:inline|virtual|static|const)\s+)*"
    r"(?:[\w:*&\[\]<>,\s]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^\)]*\)\s*"
    r"(?:const|noexcept|override|final)*\s*\{"
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
    r"(?:[\w:*&\[\]<>,\s]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:=|;)",
    re.MULTILINE,
)

_CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_][\w:]*)\s*\(")

_CPP_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "throw",
    "try", "case", "do", "else", "new", "delete", "nullptr",
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
        head = name.split(".")[0].split("::")[-1]
        if head in _CPP_KEYWORDS:
            continue
        line = body_start_line + body.count("\n", 0, m.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "args": [], "line": line})
    return out


class CppParser(BaseParser):
    """Regex-based parser for C++17 source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".cpp", ".hpp", ".cc", ".h", ".cxx", ".hxx"],
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "extract_calls": True,
        "ignore_dirs": ["build", "dist", "cmake_build", ".conan"],
        "ignore_files": ["*.o", "*.a", "*.so", "*.lib"],
        "ignore_dir_markers": ["CMakeLists.txt"],
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".cpp", ".hpp"])
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
            "imports": self._extract_includes(source),
            "variables": self._extract_variables(source),
            "comments": (
                self._extract_comments(source)
                if self.config.get("extract_comments", True)
                else []
            ),
        }

    def _extract_classes(self, source: str) -> list[dict]:
        """Extract class and struct definitions."""
        classes: list[dict] = []

        for m in _CLASS_RE.finditer(source):
            name = m.group("name")
            kind = "class" if source[m.start():m.start() + 20].find("class") != -1 else "struct"
            header_start = m.start()
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            
            bases = _split_bases(m.group("bases"))
            methods = self._extract_methods(source, open_brace + 1, close_brace)

            classes.append({
                "name": name,
                "type": kind,
                "line_start": _line_at(source, header_start),
                "line_end": _line_at(source, close_brace),
                "source": source[header_start : close_brace + 1],
                "bases": bases,
                "methods": methods,
                "decorators": [],
                "docstring": None,
                "class_vars": [],
                "is_dataclass": False,
                "is_namedtuple": False,
            })

        return classes

    def _extract_methods(self, source: str, body_start: int, body_end: int) -> list[dict]:
        """Extract methods within a class."""
        methods: list[dict] = []
        
        # Simple method signature pattern
        method_re = re.compile(
            r"(?:(?:public|private|protected|static|virtual|const)\s+)*"
            r"(?:[\w:*&\[\]<>,\s]+?)\s+"
            r"(?P<name>[a-z_]\w*)\s*\([^\)]*\)(?:\s*const)?\s*\{"
        )
        
        for m in method_re.finditer(source, body_start, body_end + 50):  # Allow some overflow
            open_brace = m.end() - 1
            if open_brace < body_start:
                continue
            close_brace = _find_matching_brace(source, open_brace)

            line_start = _line_at(source, m.start())
            line_end = _line_at(source, close_brace)
            inner = source[open_brace + 1 : close_brace]
            calls = (
                _extract_calls(inner, _line_at(source, open_brace + 1))
                if self.config.get("extract_calls", True)
                else []
            )

            methods.append({
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
        
        return methods

    def _extract_functions(self, source: str) -> list[dict]:
        """Extract top-level functions."""
        functions: list[dict] = []
        for m in _FUNCTION_RE.finditer(source):
            # Find the line containing the closing paren (function signature end)
            close_paren = source.rfind(")", m.start(), m.end())
            if close_paren == -1:
                continue
            line_with_sig_start = source.rfind("\n", 0, close_paren) + 1
            sig_line = source[line_with_sig_start:close_paren + 20]  # Get the line with signature
            
            # Skip if indented (likely a class method)
            if sig_line and sig_line[0] in (" ", "\t"):
                continue
            
            open_brace = source.find("{", m.end())
            if open_brace == -1:
                continue
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
            if name in seen or name in _CPP_KEYWORDS:
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
