"""
plugins.javascript1 — JavaScript / ECMAScript plugin for Apollo
================================================================

Parses JavaScript (``.js`` / ``.jsx`` / ``.mjs``) source files into
Apollo's structured result dict so the knowledge graph can carry
``defines`` / ``calls`` / ``inherits`` edges between JS entities.

JavaScript has no Python-callable AST in the standard library, so this
is a *regex-based* parser. It extracts:

* **Functions** (``function f(...)`` and ``const f = (...)``-style
  arrow functions) with ``line_start`` / ``line_end`` / ``source`` and
  a ``calls[]`` list.
* **Classes** with ``extends`` ``bases[]`` and methods nested under
  ``classes[].methods[]``. Each method gets its own line span / source /
  calls.
* **Imports / requires** with line numbers.
* **Top-level variables** (``const`` / ``let`` / ``var``) with line.

For production use a real JS AST (e.g. ``esprima`` or
``tree-sitter-javascript``) should ship as a separate plugin alongside
this one — see ``guides/making_plugins.md``.
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

_FUNC_DECL_RE = re.compile(
    r"(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{"
)
_ARROW_FUNC_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*="
    r"\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*\{"
)

_CLASS_RE = re.compile(
    r"(?:export\s+(?:default\s+)?)?class\s+(?P<name>[A-Za-z_$][\w$]*)"
    r"(?:\s+extends\s+(?P<base>[A-Za-z_$][\w$\.]*))?"
    r"\s*\{"
)

# Method header inside a class body: ``[static] [async] name(args) {``
# or ``[get|set] name(args) {``. We anchor at start-of-line (after
# whitespace) to avoid matching arbitrary ``foo(`` inside expressions.
_METHOD_RE = re.compile(
    r"^\s*(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+|\*\s*)?"
    r"(?P<name>[A-Za-z_$#][\w$]*)\s*\([^)]*\)\s*\{",
    re.MULTILINE,
)

_IMPORT_RE = re.compile(
    r"""(?:^|\s)import\s+
        (?:
            (?P<default>[A-Za-z_$][\w$]*)
            (?:\s*,\s*\{(?P<also>[^}]+)\})?
            |
            \{(?P<named>[^}]+)\}
            |
            \*\s+as\s+(?P<starAs>[A-Za-z_$][\w$]*)
        )
        \s+from\s+['"](?P<module>[^'"]+)['"]
    """,
    re.MULTILINE | re.VERBOSE,
)
_BARE_IMPORT_RE = re.compile(r"""(?:^|\s)import\s+['"](?P<module>[^'"]+)['"]""")
_REQUIRE_RE = re.compile(r"""require\s*\(\s*['"](?P<module>[^'"]+)['"]\s*\)""")

_VAR_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=",
    re.MULTILINE,
)

_CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_$][\w$\.]*)\s*\(")
_JS_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "new", "throw",
    "function", "typeof", "delete", "void", "await", "yield", "in", "of",
    "do", "else", "try", "case",
})

_COMMENT_TAG_RE = re.compile(
    r"//\s*(TODO|FIXME|NOTE|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE
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
    in_regex = False
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
        if in_regex:
            if ch == "\\":
                i += 2
                continue
            if ch == "/":
                in_regex = False
            elif ch == "\n":
                in_regex = False
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
        if ch in ('"', "'", "`"):
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
        head = name.split(".")[0]
        if head in _JS_KEYWORDS:
            continue
        line = body_start_line + body.count("\n", 0, m.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "args": [], "line": line})
    return out


class JavaScriptParser(BaseParser):
    """Regex-based parser for JavaScript / ECMAScript source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".js", ".jsx", ".mjs"],
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "extract_calls": True,
        "ignore_dirs": [
            "node_modules", "dist", "build", ".next", ".nuxt", "coverage",
        ],
        "ignore_files": ["*.min.js"],
        "ignore_dir_markers": [],
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower()
            for ext in (self.config.get("extensions") or [".js", ".jsx", ".mjs"])
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
        functions = self._extract_functions(source, class_ranges)

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
            base = m.group("base")
            header_start = m.start()
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            body_start = open_brace + 1
            body = source[body_start:close_brace]
            methods = self._extract_methods(source, body_start, close_brace)
            ranges.append((header_start, close_brace))
            classes.append({
                "name": name,
                "type": "class",
                "line_start": _line_at(source, header_start),
                "line_end": _line_at(source, close_brace),
                "source": source[header_start : close_brace + 1],
                "bases": [base] if base else [],
                "methods": methods,
                "decorators": [],
                "docstring": None,
                "class_vars": [],
                "is_dataclass": False,
                "is_namedtuple": False,
            })
        return classes, ranges

    def _extract_methods(
        self, source: str, body_start: int, body_end: int
    ) -> list[dict]:
        methods: list[dict] = []
        for m in _METHOD_RE.finditer(source, body_start, body_end):
            name = m.group("name")
            # Skip control-flow keywords mistakenly captured as methods.
            if name in _JS_KEYWORDS:
                continue
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
                "name": name,
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

    def _extract_functions(
        self, source: str, class_ranges: list[tuple[int, int]]
    ) -> list[dict]:
        functions: list[dict] = []
        for regex in (_FUNC_DECL_RE, _ARROW_FUNC_RE):
            for m in regex.finditer(source):
                start = m.start()
                if any(s <= start <= e for s, e in class_ranges):
                    continue  # skip funcs *inside* a class body
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

    def _extract_imports(self, source: str) -> list[dict]:
        out: list[dict] = []
        for m in _IMPORT_RE.finditer(source):
            module = m.group("module")
            names: list[str] = []
            alias = None
            if m.group("named"):
                names = [
                    n.strip().split(" as ")[0].strip()
                    for n in m.group("named").split(",")
                    if n.strip()
                ]
            if m.group("also"):
                names += [
                    n.strip().split(" as ")[0].strip()
                    for n in m.group("also").split(",")
                    if n.strip()
                ]
            if m.group("default"):
                alias = m.group("default")
            elif m.group("starAs"):
                alias = m.group("starAs")
            out.append({
                "module": module,
                "names": names,
                "alias": alias,
                "line": _line_at(source, m.start()),
                "level": 0,
            })
        for m in _BARE_IMPORT_RE.finditer(source):
            out.append({
                "module": m.group("module"),
                "names": [],
                "alias": None,
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

    def _extract_variables(
        self, source: str, class_ranges: list[tuple[int, int]]
    ) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for m in _VAR_RE.finditer(source):
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
