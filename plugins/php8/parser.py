r"""
plugins.php8 — PHP 8 source-file plugin for Apollo
===================================================

Parses PHP (``.php``) source files into Apollo's structured result dict
so the knowledge graph can carry ``defines`` / ``calls`` / ``inherits``
edges between PHP entities.

PHP has no Python-callable AST in the standard library, so this is a
*regex-based* parser. It extracts:

* **Classes / interfaces / traits** with ``extends`` parent + ``implements``
  interfaces collapsed into ``bases[]``, plus ``line_start`` /
  ``line_end`` / ``source`` from a brace-matched body.
* **Methods** scoped to their class, each with their own line span /
  source / ``calls[]`` list — these become ``classes[].methods[]`` so
  the builder draws ``defines`` edges from class → method.
* **Top-level functions** (``function foo() { ... }`` outside any class).
* **Imports**: ``use Namespace\Class`` and
  ``require/include[_once] 'file.php'``.
* **Properties** (class fields like ``public $name``) surfaced as
  ``variables`` so they become navigable nodes.

For production use a real PHP parser (``phply``, ``php-parser``) should
ship as a separate plugin alongside this one — see
``guides/making_plugins.md``.
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------

_CLASS_RE = re.compile(
    r"(?:abstract\s+|final\s+|readonly\s+)*"
    r"(?P<kind>class|interface|trait|enum)\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s+extends\s+(?P<extends>[\w\\,\s]+?))?"
    r"(?:\s+implements\s+(?P<implements>[\w\\,\s]+?))?"
    r"\s*\{"
)
_FUNC_RE = re.compile(
    r"(?:(?:public|private|protected|static|final|abstract)\s+)*"
    r"function\s+(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)"
    r"(?:\s*:\s*[\?\w\\\|\s]+)?"
    r"\s*\{"
)
_USE_RE = re.compile(
    r"^\s*use\s+(?P<module>[\w\\]+)(?:\s+as\s+(?P<alias>\w+))?\s*;",
    re.MULTILINE,
)
_REQUIRE_RE = re.compile(
    r"\b(?:require|include|require_once|include_once)\s*\(?\s*['\"](?P<module>[^'\"]+)['\"]"
)
_PROPERTY_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|readonly)\s+)+"
    r"(?:\??[\w\\\|]+\s+)?"
    r"\$(?P<name>[A-Za-z_]\w*)\s*(?:=|;)",
    re.MULTILINE,
)
_GLOBAL_VAR_RE = re.compile(
    r"^\s*\$(?P<name>[A-Za-z_]\w*)\s*=", re.MULTILINE
)

_CALL_RE = re.compile(r"(?:->|::)?\b(?P<name>[A-Za-z_][\w\\]*)\s*\(")
_PHP_KEYWORDS = frozenset({
    "if", "for", "foreach", "while", "switch", "catch", "return", "new",
    "throw", "function", "echo", "print", "isset", "unset", "empty",
    "array", "list", "include", "require", "include_once", "require_once",
    "do", "else", "elseif", "try", "case", "match",
})

_COMMENT_TAG_RE = re.compile(
    r"(?://|#)\s*(TODO|FIXME|NOTE|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE
)


def _line_at(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def _find_matching_brace(source: str, open_pos: int) -> int:
    depth = 0
    i = open_pos
    n = len(source)
    in_str: Optional[str] = None
    in_line_comment = False
    in_block_comment = False
    in_hash_comment = False
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
        if in_hash_comment:
            if ch == "\n":
                in_hash_comment = False
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
        if ch == "#":
            in_hash_comment = True
            i += 1
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


def _split_types(text: Optional[str]) -> list[str]:
    if not text:
        return []
    return [t.strip() for t in text.split(",") if t.strip()]


def _extract_calls(body: str, body_start_line: int) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in _CALL_RE.finditer(body):
        name = m.group("name")
        head = name.split("\\")[0]
        if head in _PHP_KEYWORDS:
            continue
        line = body_start_line + body.count("\n", 0, m.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "args": [], "line": line})
    return out


class PHPParser(BaseParser):
    """Regex-based parser for PHP 8 source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".php"],
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "extract_calls": True,
        "ignore_dirs": ["vendor", "var", "cache", "storage"],
        "ignore_files": ["*.phar"],
        "ignore_dir_markers": [],
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".php"])
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

        classes, class_ranges = self._extract_classes(source)
        functions = self._extract_top_level_functions(source, class_ranges)
        return {
            "file": filepath,
            "functions": functions,
            "classes": classes,
            "imports": self._extract_imports(source),
            "variables": self._extract_variables(source, class_ranges),
            "comments": (
                self._extract_comments(source)
                if self.config.get("extract_comments", True)
                else []
            ),
        }

    # ------------------------------------------------------------------

    def _extract_classes(
        self, source: str
    ) -> tuple[list[dict], list[tuple[int, int]]]:
        classes: list[dict] = []
        ranges: list[tuple[int, int]] = []
        for m in _CLASS_RE.finditer(source):
            name = m.group("name")
            kind = m.group("kind")
            header_start = m.start()
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            body_start = open_brace + 1
            body = source[body_start:close_brace]

            bases = _split_types(m.group("extends")) + _split_types(
                m.group("implements")
            )

            methods = self._extract_methods(source, body_start, close_brace)
            class_vars = self._extract_properties(
                body, body_start_line=_line_at(source, body_start)
            )
            ranges.append((header_start, close_brace))
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
                "class_vars": class_vars,
                "is_dataclass": False,
                "is_namedtuple": False,
            })
        return classes, ranges

    def _extract_methods(
        self, source: str, body_start: int, body_end: int
    ) -> list[dict]:
        methods: list[dict] = []
        for m in _FUNC_RE.finditer(source, body_start, body_end):
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            if close_brace > body_end:
                continue
            line_start = _line_at(source, m.start())
            line_end = _line_at(source, close_brace)
            body = source[open_brace + 1 : close_brace]
            calls = (
                _extract_calls(body, _line_at(source, open_brace + 1))
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

    def _extract_top_level_functions(
        self, source: str, class_ranges: list[tuple[int, int]]
    ) -> list[dict]:
        functions: list[dict] = []
        for m in _FUNC_RE.finditer(source):
            start = m.start()
            if any(s <= start <= e for s, e in class_ranges):
                continue
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            line_start = _line_at(source, start)
            line_end = _line_at(source, close_brace)
            body = source[open_brace + 1 : close_brace]
            calls = (
                _extract_calls(body, _line_at(source, open_brace + 1))
                if self.config.get("extract_calls", True)
                else []
            )
            name = m.group("name")
            functions.append({
                "name": name,
                "line_start": line_start,
                "line_end": line_end,
                "source": source[start : close_brace + 1],
                "calls": calls,
                "args": [],
                "params": [],
                "decorators": [],
                "is_async": False,
                "is_nested": False,
                "is_test": name.startswith("test"),
                "docstring": None,
                "complexity": 1,
                "loc": line_end - line_start + 1,
            })
        return functions

    def _extract_properties(self, body: str, body_start_line: int) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for m in _PROPERTY_RE.finditer(body):
            name = m.group("name")
            if name in seen:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "line": body_start_line + body.count("\n", 0, m.start()),
                "annotation": None,
                "value": None,
            })
        return out

    def _extract_variables(
        self, source: str, class_ranges: list[tuple[int, int]]
    ) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for m in _GLOBAL_VAR_RE.finditer(source):
            start = m.start()
            if any(s <= start <= e for s, e in class_ranges):
                continue
            name = m.group("name")
            if name in seen:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "line": _line_at(source, start),
                "value": None,
            })
        return out

    def _extract_imports(self, source: str) -> list[dict]:
        out: list[dict] = []
        for m in _USE_RE.finditer(source):
            module = m.group("module")
            out.append({
                "module": module,
                "names": [],
                "alias": m.group("alias"),
                "line": _line_at(source, m.start()),
                "level": 0,
            })
        for m in _REQUIRE_RE.finditer(source):
            out.append({
                "module": m.group("module"),
                "names": [],
                "alias": None,
                "line": _line_at(source, m.start()),
                "level": 0,
            })
        return out

    def _extract_comments(self, source: str) -> list[dict]:
        out: list[dict] = []
        tags = {t.upper() for t in (self.config.get("comment_tags") or [])}
        for lineno, line in enumerate(source.splitlines(), start=1):
            m = _COMMENT_TAG_RE.search(line)
            if not m:
                continue
            tag = m.group(1).upper()
            if tags and tag not in tags:
                continue
            out.append({"tag": tag, "text": m.group(2).strip(), "line": lineno})
        return out
