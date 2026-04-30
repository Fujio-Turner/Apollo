"""
plugins.go1 — Go 1.x source-file plugin for Apollo
===================================================

Parses Go (``.go``) source files into Apollo's structured result dict.

Like every Apollo language plugin, the goal is **relationships, not just
entities** (see ``docs/DESIGN.md`` and ``guides/making_plugins.md``):

* ``functions[]`` carry ``line_start`` / ``line_end`` / ``source`` and a
  ``calls[]`` list extracted from each function body — that's what powers
  ``calls`` edges in the knowledge graph.
* ``classes[]`` (Go's structs + interfaces) carry ``bases[]`` (embedded
  types or interface parents) and ``methods[]`` (functions with a
  receiver) — that's what powers ``inherits`` and ``defines`` edges.
* ``imports[]`` and ``variables[]`` carry a ``line`` so they are
  navigable from the UI.

Go has no Python-callable AST, so this is a *regex-based* parser. It is
not a full Go compiler — but it is structured to extract all the
information the graph builder dereferences without crashing, plus the
edges Apollo needs to be useful (calls, inherits, defines).

For non-trivial production use a real Go AST (e.g. via ``tree-sitter-go``)
should ship as a separate plugin alongside this one.
"""
from __future__ import annotations
import logging

import re
from pathlib import Path
from typing import Optional

from apollo.parser.base import BaseParser


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Module-level regexes
# ---------------------------------------------------------------------

# ``func (recv RecvType) Name(...) ReturnType {`` or
# ``func Name(...) ReturnType {``. We capture the optional receiver,
# the function name, and rely on a follow-up brace scan to find the
# end of the body.
_FUNC_RE = re.compile(
    r"(?P<header>func\s+(?:\((?P<recv>[^)]*)\)\s+)?(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)\s*(?:[^\{]*?))\{",
    re.DOTALL,
)

# ``type Name struct {`` and ``type Name interface {``. Bases are
# discovered by walking the body for embedded types (lines that look
# like a single identifier or qualified name with no following ``(``).
_TYPE_RE = re.compile(
    r"type\s+(?P<name>[A-Za-z_]\w*)\s+(?P<kind>struct|interface)\s*\{"
)

# ``import "x"`` (single) and ``import (\n "x"\n "y"\n)`` (group).
_SINGLE_IMPORT_RE = re.compile(
    r'^\s*import\s+(?:(?P<alias>\w+)\s+)?["\'](?P<module>[^"\']+)["\']',
    re.MULTILINE,
)
_IMPORT_BLOCK_RE = re.compile(r"import\s*\(\s*(?P<body>[^)]*)\)", re.DOTALL)
_IMPORT_BLOCK_LINE_RE = re.compile(
    r'^\s*(?:(?P<alias>\w+)\s+)?["\'](?P<module>[^"\']+)["\']',
    re.MULTILINE,
)

# Top-level ``var Name = ...``, ``var Name Type``, ``const Name = ...``.
_VAR_RE = re.compile(
    r"^\s*(?:var|const)\s+(?P<name>[A-Za-z_]\w*)\b", re.MULTILINE
)
_VAR_BLOCK_RE = re.compile(r"(?:var|const)\s*\(\s*(?P<body>[^)]*)\)", re.DOTALL)
_VAR_BLOCK_LINE_RE = re.compile(r"^\s*(?P<name>[A-Za-z_]\w*)\b", re.MULTILINE)

# A bare callsite inside a function body: ``ident(`` or ``a.b.ident(``.
# Filtered against Go control-flow keywords below.
_CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_][\w\.]*)\s*\(")
_GO_KEYWORDS = frozenset({
    "if", "for", "switch", "select", "return", "go", "defer", "func",
    "chan", "map", "range", "case", "type", "struct", "interface",
})

# ``// TODO`` style comments.
_COMMENT_TAG_RE = re.compile(
    r"//\s*(TODO|FIXME|NOTE|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE
)


def _line_at(source: str, pos: int) -> int:
    """Return the 1-based line number of byte offset *pos* in *source*."""
    return source.count("\n", 0, pos) + 1


def _find_matching_brace(source: str, open_pos: int) -> int:
    """Return the index of the ``}`` matching the ``{`` at *open_pos*.

    Skips braces inside ``"..."``, `` `...` `` raw strings, ``'..'``
    runes, ``// ...`` line comments, and ``/* ... */`` block comments.
    Returns ``len(source) - 1`` if the file is truncated mid-block so
    callers always get *some* range to slice instead of crashing.
    """
    depth = 0
    i = open_pos
    n = len(source)
    in_str: Optional[str] = None  # quote char we're inside, or None
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
            if ch == "\\" and in_str != "`":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        # Not in string / comment.
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
    """Find ``ident(`` callsites inside a function body."""
    calls: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in _CALL_RE.finditer(body):
        name = m.group("name")
        head = name.split(".")[0]
        if head in _GO_KEYWORDS:
            continue
        line = body_start_line + body.count("\n", 0, m.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        calls.append({"name": name, "args": [], "line": line})
    return calls


def _struct_bases(body: str) -> list[str]:
    """Embedded fields in a struct become "bases" (Go composition)."""
    bases: list[str] = []
    for raw in body.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        # An embedded field is a single token (optionally ``*Foo`` or
        # ``pkg.Foo``) with no following whitespace+identifier. Field
        # declarations like ``Name string`` have at least two tokens.
        tokens = line.split()
        if len(tokens) == 1:
            base = tokens[0].lstrip("*")
            if re.fullmatch(r"[A-Za-z_][\w\.]*", base):
                bases.append(base)
    return bases


def _interface_bases(body: str) -> list[str]:
    """Embedded interface names inside an interface block."""
    bases: list[str] = []
    for raw in body.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line or "(" in line:
            # ``Method()`` is a method, not an embedded interface.
            continue
        if re.fullmatch(r"[A-Za-z_][\w\.]*", line):
            bases.append(line)
    return bases


class GoParser(BaseParser):
    """Regex-based parser for Go 1.x source files."""

    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".go"],
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "extract_calls": True,
        "ignore_dirs": ["vendor", "build", "dist", "bin"],
        "ignore_files": ["*.mod", "*.sum"],
        "ignore_dir_markers": [],
    }

    def __init__(self, config: Optional[dict] = None) -> None:
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or [".go"])
        )

    # ------------------------------------------------------------------
    # BaseParser interface
    # ------------------------------------------------------------------

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

        types = self._extract_types(source)
        functions, methods_by_recv = self._extract_callables(source)

        # Attach methods (functions with a receiver) to the class whose
        # receiver type matches.
        for cls in types:
            cls_methods = methods_by_recv.get(cls["name"], [])
            cls["methods"] = cls_methods

        return {
            "file": filepath,
            "functions": functions,
            "classes": types,
            "imports": self._extract_imports(source),
            "variables": self._extract_variables(source),
            "comments": (
                self._extract_comments(source)
                if self.config.get("extract_comments", True)
                else []
            ),
        }

    # ------------------------------------------------------------------
    # Functions & methods
    # ------------------------------------------------------------------

    def _extract_callables(
        self, source: str
    ) -> tuple[list[dict], dict[str, list[dict]]]:
        """Return (top-level funcs, {receiver_type: [methods]})."""
        funcs: list[dict] = []
        methods_by_recv: dict[str, list[dict]] = {}
        for m in _FUNC_RE.finditer(source):
            name = m.group("name")
            recv = m.group("recv")
            header_start = m.start()
            open_brace = m.end() - 1  # the '{' the regex captured
            close_brace = _find_matching_brace(source, open_brace)
            line_start = _line_at(source, header_start)
            line_end = _line_at(source, close_brace)
            func_source = source[header_start : close_brace + 1]
            body = source[open_brace + 1 : close_brace]
            calls = (
                _extract_calls(body, _line_at(source, open_brace + 1))
                if self.config.get("extract_calls", True)
                else []
            )
            entry = {
                "name": name,
                "line_start": line_start,
                "line_end": line_end,
                "source": func_source,
                "calls": calls,
                "args": [],
                "params": [],
                "decorators": [],
                "is_async": False,
                "is_nested": False,
                "is_test": name.startswith("Test"),
                "docstring": None,
                "complexity": 1,
                "loc": line_end - line_start + 1,
            }
            if recv:
                # Receiver looks like ``g *Greeter`` or ``Greeter``.
                recv_type = recv.strip().split()[-1].lstrip("*")
                methods_by_recv.setdefault(recv_type, []).append(entry)
            else:
                funcs.append(entry)
        return funcs, methods_by_recv

    # ------------------------------------------------------------------
    # Structs and interfaces (Apollo "classes")
    # ------------------------------------------------------------------

    def _extract_types(self, source: str) -> list[dict]:
        out: list[dict] = []
        for m in _TYPE_RE.finditer(source):
            name = m.group("name")
            kind = m.group("kind")
            header_start = m.start()
            open_brace = m.end() - 1
            close_brace = _find_matching_brace(source, open_brace)
            line_start = _line_at(source, header_start)
            line_end = _line_at(source, close_brace)
            body = source[open_brace + 1 : close_brace]
            bases = (
                _struct_bases(body) if kind == "struct" else _interface_bases(body)
            )
            out.append({
                "name": name,
                "type": kind,
                "line_start": line_start,
                "line_end": line_end,
                "source": source[header_start : close_brace + 1],
                "bases": bases,
                "methods": [],   # populated by parse_source from receivers
                "decorators": [],
                "docstring": None,
                "class_vars": [],
                "is_dataclass": False,
                "is_namedtuple": False,
            })
        return out

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _extract_imports(self, source: str) -> list[dict]:
        imports: list[dict] = []
        # Single-line ``import "x"``.
        for m in _SINGLE_IMPORT_RE.finditer(source):
            imports.append({
                "module": m.group("module"),
                "names": [],
                "alias": m.group("alias"),
                "line": _line_at(source, m.start()),
                "level": 0,
            })
        # Grouped ``import ( ... )``.
        for block in _IMPORT_BLOCK_RE.finditer(source):
            block_body = block.group("body")
            block_start = block.start("body")
            for line_match in _IMPORT_BLOCK_LINE_RE.finditer(block_body):
                imports.append({
                    "module": line_match.group("module"),
                    "names": [],
                    "alias": line_match.group("alias"),
                    "line": _line_at(source, block_start + line_match.start()),
                    "level": 0,
                })
        return imports

    # ------------------------------------------------------------------
    # Variables / constants
    # ------------------------------------------------------------------

    def _extract_variables(self, source: str) -> list[dict]:
        seen: set[tuple[str, int]] = set()
        variables: list[dict] = []
        for m in _VAR_RE.finditer(source):
            name = m.group("name")
            line = _line_at(source, m.start())
            key = (name, line)
            if key in seen:
                continue
            seen.add(key)
            variables.append({"name": name, "line": line, "value": None})
        for block in _VAR_BLOCK_RE.finditer(source):
            block_start = block.start("body")
            for line_match in _VAR_BLOCK_LINE_RE.finditer(block.group("body")):
                name = line_match.group("name")
                line = _line_at(source, block_start + line_match.start())
                key = (name, line)
                if key in seen:
                    continue
                seen.add(key)
                variables.append({"name": name, "line": line, "value": None})
        return variables

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

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
