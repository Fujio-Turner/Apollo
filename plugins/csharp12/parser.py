"""
plugins.csharp12 — C# 12 source-file plugin for Apollo
======================================================

Parses C# (``.cs``) source files into Apollo's structured result dict.

C# has no Python-callable AST in the standard library, so this is a
*regex-based* parser. It extracts:

* **Classes** with their ``base`` parent and ``interface`` implementations.
* **Interfaces** treated as pseudo-classes.
* **Methods** and **Properties** scoped to classes.
* **Namespaces** and using statements (imports).
* **Variables** / fields declared at class/module level.
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser


logger = logging.getLogger(__name__)

# Regexes
_NAMESPACE_RE = re.compile(
    r"^namespace\s+(?P<name>[\w\.]+)\s*\{",
    re.MULTILINE,
)

_CLASS_RE = re.compile(
    r"(?:public\s+|private\s+|protected\s+|internal\s+|sealed\s+|abstract\s+|partial\s+|static\s+)*"
    r"(?P<kind>class|struct|record)\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^>]+>)?"
    r"(?:\s*:\s*(?P<bases>[\w\.,\s<>]+?))?"
    r"\s*\{"
)

_INTERFACE_RE = re.compile(
    r"(?:public\s+|private\s+|protected\s+|internal\s+)?interface\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^>]+>)?"
    r"(?:\s*:\s*(?P<bases>[\w\.,\s<>]+?))?"
    r"\s*\{"
)

_METHOD_RE = re.compile(
    r"(?:(?:public|private|protected|internal|static|abstract|sealed|override|virtual|async)\s+)*"
    r"(?:[\w\[\]<>,\s]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^\)]*\)\s*"
    r"(?:where\s+[\w:,\s<>]+?)?"
    r"\s*\{"
)

_PROPERTY_RE = re.compile(
    r"(?:(?:public|private|protected|internal|static)\s+)*"
    r"(?:[\w\[\]<>,\s]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*"
    r"(?:\{[^}]*\})"
)

_USING_RE = re.compile(
    r"^using\s+(?:static\s+)?(?P<module>[\w\.]+)(?:\s*=\s*[\w\.]+)?;",
    re.MULTILINE,
)

_FIELD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|readonly|const)\s+)+"
    r"(?:[\w\[\]<>,\s]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:=|;)",
    re.MULTILINE,
)

_CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_][\w\.]*)\s*\(")

_CSHARP_KEYWORDS = frozenset({
    "if", "for", "foreach", "while", "switch", "catch", "return", "throw",
    "async", "await", "new", "try", "case", "do", "else", "var", "const",
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
    """Split comma-separated base class/interface list."""
    if not text:
        return []
    return [t.strip().split("<", 1)[0] for t in text.split(",") if t.strip()]


def _extract_calls(body: str, body_start_line: int) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in _CALL_RE.finditer(body):
        name = m.group("name")
        head = name.split(".")[0]
        if head in _CSHARP_KEYWORDS:
            continue
        line = body_start_line + body.count("\n", 0, m.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "args": [], "line": line})
    return out


class CSharpParser(BaseParser):
    """Regex-based parser for C# 12 source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".cs"],
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "extract_calls": True,
        "ignore_dirs": ["bin", "obj", ".vs", "packages", "dist"],
        "ignore_files": ["*.dll", "*.exe", "*.pdb"],
        "ignore_dir_markers": ["*.csproj"],
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".cs"])
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
        
        return {
            "file": filepath,
            "functions": [],  # C# has no top-level functions
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
        """Extract class and interface definitions."""
        classes: list[dict] = []

        # Classes / structs / records
        for m in _CLASS_RE.finditer(source):
            name = m.group("name")
            kind = m.group("kind")
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
                "is_dataclass": kind == "record",
                "is_namedtuple": False,
            })

        # Interfaces
        for m in _INTERFACE_RE.finditer(source):
            name = m.group("name")
            header_start = m.start()
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            
            bases = _split_bases(m.group("bases"))

            classes.append({
                "name": name,
                "type": "interface",
                "line_start": _line_at(source, header_start),
                "line_end": _line_at(source, close_brace),
                "source": source[header_start : close_brace + 1],
                "bases": bases,
                "methods": [],
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
        
        # Simpler regex for method signatures
        method_re = re.compile(
            r"(?:(?:public|private|protected|internal|static|override|virtual|async)\s+)*"
            r"(?:[\w\[\]<>,\s]+?)\s+"
            r"(?P<name>[A-Z][A-Za-z0-9]*)\s*\([^\)]*\)\s*\{"
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
                "is_async": "async" in source[m.start() : m.end()],
                "is_nested": False,
                "is_test": False,
                "docstring": None,
                "complexity": 1,
                "loc": line_end - line_start + 1,
            })
        
        return methods

    def _extract_imports(self, source: str) -> list[dict]:
        out: list[dict] = []
        for m in _USING_RE.finditer(source):
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
        for m in _FIELD_RE.finditer(source):
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
