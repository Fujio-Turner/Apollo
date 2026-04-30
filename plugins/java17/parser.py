"""
plugins.java17 — Java 17 source-file plugin for Apollo
=======================================================

Parses Java (``.java``) source files into Apollo's structured result
dict so the knowledge graph can carry ``defines`` / ``calls`` /
``inherits`` edges between Java entities.

Java has no Python-callable AST in the standard library, so this is a
*regex-based* parser. It extracts:

* **Classes / interfaces** with their ``extends`` parent and
  ``implements`` interfaces (combined into ``bases[]``), plus the
  source span (``line_start``/``line_end``/``source``) found by
  brace-matching from the header.
* **Methods**, scoped to the class they live in (so they end up under
  ``classes[].methods[]``, not as top-level ``functions[]``). Each
  method carries its own ``line_start``/``line_end``/``source`` and a
  ``calls[]`` list extracted from its body.
* **Imports** with line numbers.
* **Fields**, declared at the class top-level, surfaced as ``variables``.

For production use a real Java AST (e.g. ``javalang``) should ship as a
separate plugin alongside this one — see ``guides/making_plugins.md``.
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

# ``[modifiers] class Name [extends X] [implements I, J] {``
_CLASS_RE = re.compile(
    r"(?:public\s+|private\s+|protected\s+|abstract\s+|final\s+|static\s+)*"
    r"(?P<kind>class|interface|enum|record)\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*<[^{>]+>)?"
    r"(?:\s+extends\s+(?P<extends>[\w\.<>,\s]+?))?"
    r"(?:\s+implements\s+(?P<implements>[\w\.<>,\s]+?))?"
    r"\s*\{"
)

# Method header inside a class body. Matches:
#   [modifiers] [<T>] returnType name( ... ) [throws X] {
# We require a ``{`` so abstract / interface methods (``;``-terminated)
# are skipped.
_METHOD_RE = re.compile(
    r"(?:(?:public|private|protected|static|final|abstract|synchronized|native|default)\s+)*"
    r"(?:<[^>]+>\s+)?"
    r"(?:[\w\.\[\]<>,\s\?]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*"
    r"\([^\)]*\)"
    r"(?:\s*throws\s+[\w\.,\s]+)?"
    r"\s*\{"
)

_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?(?P<module>[\w\.]+)\s*;", re.MULTILINE)

# Field declaration inside a class body:
#   [modifiers] Type name [= value];
_FIELD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|volatile|transient)\s+)+"
    r"(?:[\w\.\[\]<>,\s\?]+?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:=|;)",
    re.MULTILINE,
)

_CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_][\w\.]*)\s*\(")
_JAVA_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "new", "throw",
    "synchronized", "do", "else", "try", "case",
})

_COMMENT_TAG_RE = re.compile(
    r"//\s*(TODO|FIXME|NOTE|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE
)


def _line_at(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def _find_matching_brace(source: str, open_pos: int) -> int:
    """Return the index of the ``}`` matching the ``{`` at *open_pos*.

    Skips strings, char literals, and ``//`` / ``/* */`` comments.
    """
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


def _split_types(text: Optional[str]) -> list[str]:
    """Split a comma-separated extends/implements clause into base names."""
    if not text:
        return []
    return [t.strip().split("<", 1)[0] for t in text.split(",") if t.strip()]


def _extract_calls(body: str, body_start_line: int) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in _CALL_RE.finditer(body):
        name = m.group("name")
        head = name.split(".")[0]
        if head in _JAVA_KEYWORDS:
            continue
        line = body_start_line + body.count("\n", 0, m.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "args": [], "line": line})
    return out


class JavaParser(BaseParser):
    """Regex-based parser for Java 17 source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".java"],
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "extract_calls": True,
        "ignore_dirs": ["target", "build", "out", ".gradle", ".mvn"],
        "ignore_files": ["*.class", "*.jar"],
        "ignore_dir_markers": [],
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".java"])
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

        classes, fields = self._extract_classes(source)
        return {
            "file": filepath,
            "functions": [],   # Java has no top-level functions
            "classes": classes,
            "imports": self._extract_imports(source),
            "variables": fields,
            "comments": (
                self._extract_comments(source)
                if self.config.get("extract_comments", True)
                else []
            ),
        }

    # ------------------------------------------------------------------

    def _extract_classes(self, source: str) -> tuple[list[dict], list[dict]]:
        """Walk class headers and collect classes + their methods + fields.

        Top-level fields (those declared inside the file's outermost
        class) are also collected into the file's ``variables`` list so
        they get a ``var`` node alongside their class.
        """
        classes: list[dict] = []
        all_fields: list[dict] = []
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
            class_vars = self._extract_fields(body, body_start_line=_line_at(source, body_start))

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
                "is_dataclass": kind == "record",
                "is_namedtuple": False,
            })
            all_fields.extend(class_vars)
        return classes, all_fields

    def _extract_methods(
        self, source: str, body_start: int, body_end: int
    ) -> list[dict]:
        methods: list[dict] = []
        for m in _METHOD_RE.finditer(source, body_start, body_end):
            # Skip nested-class openings re-matched as methods.
            header_text = source[m.start() : m.end()]
            if re.search(r"\b(class|interface|enum|record)\b", header_text):
                continue
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            if close_brace > body_end:
                continue
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

    def _extract_fields(self, body: str, body_start_line: int) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for m in _FIELD_RE.finditer(body):
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

    def _extract_imports(self, source: str) -> list[dict]:
        out: list[dict] = []
        for m in _IMPORT_RE.finditer(source):
            module = m.group("module")
            out.append({
                "module": module,
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
