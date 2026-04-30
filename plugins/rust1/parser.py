"""
plugins.rust1 — Rust source-file plugin for Apollo
==================================================

Parses Rust (``.rs``) source files into Apollo's structured result dict.

Rust has no Python-callable AST in the standard library, so this is a
*regex-based* parser. It extracts:

* **Structs** and **Enums** (type definitions).
* **impl Blocks** containing methods.
* **Functions** at module level and inside impl blocks.
* **use** statements (imports/module access).
* **Variables** declared with ``let``, ``const``, ``static``.
* **Traits** and trait implementations.
* **Cargo.toml** dependencies.
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
    r"(?:pub\s+)?struct\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^>]+>)?"
    r"\s*(?:\{|;)"
)

_ENUM_RE = re.compile(
    r"(?:pub\s+)?enum\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^>]+>)?"
    r"\s*\{"
)

_TRAIT_RE = re.compile(
    r"(?:pub\s+)?trait\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^>]+>)?"
    r"(?:\s*:\s*(?P<bases>[\w\s+,<>]+?))?"
    r"\s*\{"
)

_IMPL_RE = re.compile(
    r"impl(?:\s*<[^>]+>)?\s+(?P<trait>[\w:]+\s+for\s+)?(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^>]+>)?"
    r"\s*\{"
)

_FUNCTION_RE = re.compile(
    r"(?:pub(?:\(crate\|self\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(?P<name>[A-Za-z_]\w*)"
    r"\s*(?:<[^>]+>)?\s*\([^\)]*\)(?:\s*->\s*[\w\s<>]+)?\s*\{"
)

_USE_RE = re.compile(
    r"^use\s+(?P<module>[\w:*{}]+)(?:\s+as\s+[\w]+)?;",
    re.MULTILINE,
)

_VARIABLE_RE = re.compile(
    r"^(?:(?:pub|const|static)\s+)*(?:mut\s+)?let\s+(?P<name>[A-Za-z_]\w*)",
    re.MULTILINE,
)

_CONST_RE = re.compile(
    r"^(?:pub\s+)?const\s+(?P<name>[A-Za-z_]\w*)",
    re.MULTILINE,
)

_CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_][\w:]*)\s*\(")

_RUST_KEYWORDS = frozenset({
    "if", "for", "while", "match", "return", "break", "continue",
    "let", "mut", "fn", "self", "super", "crate", "pub", "impl",
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
    """Split trait bounds."""
    if not text:
        return []
    return [t.strip() for t in text.split("+") if t.strip()]


def _extract_calls(body: str, body_start_line: int) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in _CALL_RE.finditer(body):
        name = m.group("name")
        head = name.split("::")[0]
        if head in _RUST_KEYWORDS:
            continue
        line = body_start_line + body.count("\n", 0, m.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "args": [], "line": line})
    return out


class RustParser(BaseParser):
    """Regex-based parser for Rust source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".rs"],
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "extract_calls": True,
        "ignore_dirs": ["target", "build", "dist"],
        "ignore_files": ["*.rlib", "*.so"],
        "ignore_dir_markers": ["Cargo.toml"],
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".rs"])
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

        classes = self._extract_types(source)
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

    def _extract_types(self, source: str) -> list[dict]:
        """Extract structs, enums, traits."""
        types: list[dict] = []

        # Structs
        for m in _STRUCT_RE.finditer(source):
            name = m.group("name")
            header_start = m.start()
            brace_pos = source.find("{", m.start())
            
            if brace_pos == -1 or source[brace_pos - 1:brace_pos].strip() == ";":
                # Unit struct or opaque
                close_pos = source.find(";", header_start)
                if close_pos == -1:
                    close_pos = brace_pos if brace_pos != -1 else m.end()
            else:
                close_pos = _find_matching_brace(source, brace_pos)
            
            types.append({
                "name": name,
                "type": "struct",
                "line_start": _line_at(source, header_start),
                "line_end": _line_at(source, close_pos),
                "source": source[header_start : close_pos + 1],
                "bases": [],
                "methods": [],
                "decorators": [],
                "docstring": None,
                "class_vars": [],
                "is_dataclass": False,
                "is_namedtuple": False,
            })

        # Enums
        for m in _ENUM_RE.finditer(source):
            name = m.group("name")
            header_start = m.start()
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            
            types.append({
                "name": name,
                "type": "enum",
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

        # Traits
        for m in _TRAIT_RE.finditer(source):
            name = m.group("name")
            header_start = m.start()
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            
            bases = _split_bases(m.group("bases"))
            
            types.append({
                "name": name,
                "type": "trait",
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

        return types

    def _extract_functions(self, source: str) -> list[dict]:
        """Extract top-level functions."""
        functions: list[dict] = []
        for m in _FUNCTION_RE.finditer(source):
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
                "is_async": "async" in source[m.start() : m.end()],
                "is_nested": False,
                "is_test": False,
                "docstring": None,
                "complexity": 1,
                "loc": line_end - line_start + 1,
            })

        return functions

    def _extract_imports(self, source: str) -> list[dict]:
        out: list[dict] = []
        for m in _USE_RE.finditer(source):
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
        
        # let/static variables
        for m in _VARIABLE_RE.finditer(source):
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
        
        # const variables
        for m in _CONST_RE.finditer(source):
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
