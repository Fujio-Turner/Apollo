"""
Read-only file & source inspection (Phase 12.3a).

Single source of truth for the file inspection operations exposed both as AI
tools (chat/service.py) and as HTTP endpoints (web/server.py).

Strictly read-only: no file is ever written, renamed, or deleted by this module.

Every function takes the live `nx.DiGraph` so it can validate paths against
file/directory nodes already in the index. The optional `root_dir` extends the
allowlist to anything under the originally-indexed directory.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import os
import re
from pathlib import Path
from typing import Iterable

import networkx as nx


# ── Limits (keep responses bounded so the AI's context doesn't explode) ──
MAX_SECTION_LINES = 800
MAX_FILE_SEARCH_MATCHES = 200
MAX_PROJECT_SEARCH_MATCHES = 500
MAX_PROJECT_SNIPPET_BYTES = 200_000


class FileAccessError(Exception):
    """Raised when a path is outside the allowed sandbox or the file is missing."""

    def __init__(self, message: str, status_code: int = 403):
        super().__init__(message)
        self.status_code = status_code


class FileChangedError(Exception):
    """Raised when an `expected_md5` check fails."""

    def __init__(self, expected: str, actual: str):
        super().__init__(f"File changed: expected md5 {expected}, got {actual}")
        self.expected = expected
        self.actual = actual
        self.status_code = 409


# ── Path safety ────────────────────────────────────────────────────────────

def _index_root(graph: nx.DiGraph) -> Path | None:
    """The absolute path of the indexed project root, recorded by the builder
    on the `dir::.` node as `abs_path`."""
    if graph is None:
        return None
    root_node = graph.nodes.get("dir::.") or {}
    abs_root = root_node.get("abs_path")
    if not abs_root:
        return None
    try:
        return Path(abs_root).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _allowed_paths(graph: nx.DiGraph) -> set[str]:
    """Set of every file/directory path the index already knows about (resolved).

    Each node's path is first joined with its own `abs_path` if present, then
    falls back to the indexed project root, then to the process CWD as a last
    resort. This ensures the sandbox check works even when the server is
    launched from a different directory than where the index was built.
    """
    out: set[str] = set()
    index_root = _index_root(graph)
    for _, data in graph.nodes(data=True):
        if data.get("type") not in ("file", "directory"):
            continue
        abs_p = data.get("abs_path")
        if abs_p:
            try:
                out.add(str(Path(abs_p).expanduser().resolve(strict=False)))
                continue
            except (OSError, RuntimeError):
                pass
        p = data.get("path") or ""
        if not p:
            continue
        raw = Path(p).expanduser()
        if not raw.is_absolute() and index_root is not None:
            raw = index_root / raw
        try:
            out.add(str(raw.resolve(strict=False)))
        except (OSError, RuntimeError):
            pass
    return out


def safe_path(path: str, graph: nx.DiGraph, root_dir: str | None) -> Path:
    """Resolve `path` and ensure it lies inside the indexed sandbox.

    A path is allowed if either:
      - it (or any parent) appears as a `file`/`directory` node in the graph, or
      - it lies under `root_dir`.
    """
    try:
        raw = Path(path).expanduser()
        # If the caller passed a relative path (which is what file/dir nodes
        # store), resolve it against the indexed root rather than the
        # process CWD. Prefer `root_dir`, then the absolute path recorded on
        # the `dir::.` node by the graph builder. Falls back to CWD-resolution
        # if neither is available.
        if not raw.is_absolute():
            base: Path | None = None
            if root_dir:
                try:
                    base = Path(root_dir).expanduser().resolve(strict=False)
                except (OSError, RuntimeError):
                    base = None
            if base is None:
                base = _index_root(graph)
            if base is not None:
                raw = base / raw
        resolved = raw.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise FileAccessError(f"Cannot resolve path: {path} ({e})")

    s = str(resolved)
    allowed = _allowed_paths(graph)

    # Direct hit, or under any allowed directory.
    if s in allowed:
        return resolved
    for a in allowed:
        if s.startswith(a.rstrip("/") + "/"):
            return resolved

    # Fall back to root_dir.
    if root_dir:
        try:
            root = Path(root_dir).expanduser().resolve(strict=False)
            if s == str(root) or s.startswith(str(root).rstrip("/") + "/"):
                return resolved
        except (OSError, RuntimeError):
            pass

    raise FileAccessError(f"Path not in indexed sandbox: {path}")


def _check_md5(path: Path, expected: str | None) -> str:
    """Return the file's current md5; raise if `expected` is provided and differs."""
    actual = file_md5(path)
    if expected and actual != expected:
        raise FileChangedError(expected, actual)
    return actual


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Tools ──────────────────────────────────────────────────────────────────

def file_stats(graph: nx.DiGraph, root_dir: str | None, path: str) -> dict:
    """Cheap structural summary of a file (no source returned)."""
    p = safe_path(path, graph, root_dir)
    if not p.is_file():
        raise FileAccessError(f"Not a file: {p}", status_code=404)

    size = p.stat().st_size
    md5 = file_md5(p)
    suffix = p.suffix.lower()
    language = {
        ".py": "python", ".md": "markdown", ".json": "json",
        ".yaml": "yaml", ".yml": "yaml", ".txt": "text",
        ".js": "javascript", ".ts": "typescript", ".html": "html",
        ".css": "css",
    }.get(suffix, "unknown")

    line_count = 0
    with open(p, "rb") as f:
        for _ in f:
            line_count += 1

    fn_count = 0
    cls_count = 0
    imports: list[str] = []
    if language == "python":
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                tree = ast.parse(f.read(), filename=str(p))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn_count += 1
                elif isinstance(node, ast.ClassDef):
                    cls_count += 1
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else ""))
                elif isinstance(node, ast.ImportFrom):
                    mod = ("." * (node.level or 0)) + (node.module or "")
                    names = ", ".join(
                        a.name + (f" as {a.asname}" if a.asname else "")
                        for a in node.names
                    )
                    imports.append(f"from {mod} import {names}")
        except SyntaxError as e:
            return {
                "path": str(p),
                "size_bytes": size,
                "line_count": line_count,
                "md5": md5,
                "language": language,
                "function_count": 0,
                "class_count": 0,
                "top_level_imports": [],
                "ast_error": f"{type(e).__name__}: {e}",
            }

    return {
        "path": str(p),
        "size_bytes": size,
        "line_count": line_count,
        "md5": md5,
        "language": language,
        "function_count": fn_count,
        "class_count": cls_count,
        "top_level_imports": imports[:50],
    }


# Cap for the "view whole file" endpoint used by the web UI.
MAX_FILE_CONTENT_BYTES = 2_000_000  # 2 MB


def file_content(
    graph: nx.DiGraph,
    root_dir: str | None,
    path: str,
) -> dict:
    """Return the full text contents of `path` (capped at MAX_FILE_CONTENT_BYTES)."""
    p = safe_path(path, graph, root_dir)
    if not p.is_file():
        raise FileAccessError(f"Not a file: {p}", status_code=404)

    size = p.stat().st_size
    suffix = p.suffix.lower()
    language = {
        ".py": "python", ".md": "markdown", ".markdown": "markdown",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".txt": "text",
        ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
        ".html": "html", ".htm": "html", ".xml": "xml", ".svg": "xml",
        ".css": "css", ".scss": "scss", ".less": "less",
        ".sh": "bash", ".bash": "bash", ".zsh": "bash",
        ".rb": "ruby", ".go": "go", ".rs": "rust", ".java": "java",
        ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
        ".cs": "csharp", ".swift": "swift", ".kt": "kotlin",
        ".sql": "sql", ".toml": "toml", ".ini": "ini", ".env": "ini",
        ".dockerfile": "dockerfile",
    }.get(suffix, "")
    if not language and p.name.lower() == "dockerfile":
        language = "dockerfile"

    truncated = False
    read_bytes = min(size, MAX_FILE_CONTENT_BYTES)
    with open(p, "rb") as f:
        raw = f.read(read_bytes)
    if size > MAX_FILE_CONTENT_BYTES:
        truncated = True

    # Decode as text; binary files come back with replacement chars but at
    # least don't blow up. The UI shows a short hint.
    try:
        text = raw.decode("utf-8")
        is_binary = False
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        is_binary = b"\x00" in raw[:8192]

    # Build a relative path. Prefer `root_dir` when it covers `p`; otherwise
    # fall back to the longest directory-node prefix from the graph (this is
    # what the user actually indexed, which may differ from `root_dir`).
    rel_path = ""
    candidate_roots: list[Path] = []
    if root_dir:
        try:
            candidate_roots.append(Path(root_dir).expanduser().resolve(strict=False))
        except (OSError, RuntimeError):
            pass
    for _, data in graph.nodes(data=True):
        if data.get("type") == "directory":
            dp = data.get("path") or ""
            if dp:
                try:
                    candidate_roots.append(Path(dp).expanduser().resolve(strict=False))
                except (OSError, RuntimeError):
                    pass
    # Pick the SHORTEST root that is an ancestor of p — i.e. the topmost
    # indexed directory, so the relative path stays meaningful (e.g.
    # "graph/query.py" rather than just "query.py").
    best: Path | None = None
    for r in candidate_roots:
        try:
            p.relative_to(r)
        except ValueError:
            continue
        if best is None or len(str(r)) < len(str(best)):
            best = r
    if best is not None and best != p:
        try:
            rel_path = str(p.relative_to(best))
        except ValueError:
            rel_path = ""

    return {
        "path": str(p),
        "relative_path": rel_path,
        "size_bytes": size,
        "language": language,
        "extension": suffix,
        "content": text,
        "truncated": truncated,
        "is_binary": is_binary,
    }


def get_file_section(
    graph: nx.DiGraph,
    root_dir: str | None,
    path: str,
    start_line: int,
    end_line: int,
    expected_md5: str | None = None,
) -> dict:
    """Return inclusive 1-indexed `[start_line, end_line]` lines from `path`."""
    p = safe_path(path, graph, root_dir)
    if not p.is_file():
        raise FileAccessError(f"Not a file: {p}", status_code=404)
    if start_line < 1 or end_line < start_line:
        raise FileAccessError(
            f"Invalid range: start_line={start_line}, end_line={end_line}", status_code=400
        )
    if end_line - start_line + 1 > MAX_SECTION_LINES:
        end_line = start_line + MAX_SECTION_LINES - 1

    md5 = _check_md5(p, expected_md5)

    out: list[dict] = []
    with open(p, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            if i < start_line:
                continue
            if i > end_line:
                break
            out.append({"n": i, "text": line.rstrip("\n")})

    return {
        "path": str(p),
        "start_line": start_line,
        "end_line": min(end_line, out[-1]["n"] if out else end_line),
        "md5": md5,
        "lines": out,
        "truncated": (end_line - start_line + 1) >= MAX_SECTION_LINES,
    }


def get_function_source(
    graph: nx.DiGraph,
    root_dir: str | None,
    path: str,
    name: str,
    expected_md5: str | None = None,
) -> dict:
    """AST-extract the full source of a function/method by name.

    `name` may be a bare function name (`foo`), a qualified method name
    (`MyClass.foo`), or `MyClass` to fetch the whole class body.
    """
    p = safe_path(path, graph, root_dir)
    if not p.is_file():
        raise FileAccessError(f"Not a file: {p}", status_code=404)
    md5 = _check_md5(p, expected_md5)

    with open(p, encoding="utf-8", errors="replace") as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename=str(p))
    except SyntaxError as e:
        raise FileAccessError(f"Cannot parse {p}: {e}", status_code=422)

    target_class, _, target_func = name.partition(".") if "." in name else ("", "", name)
    if not target_func and target_class:
        target_func = target_class
        target_class = ""

    found = None
    for node in ast.walk(tree):
        if target_class and not target_func:
            # Whole class
            if isinstance(node, ast.ClassDef) and node.name == target_class:
                found = node
                break
        elif target_class:
            if isinstance(node, ast.ClassDef) and node.name == target_class:
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == target_func:
                        found = sub
                        break
                if found:
                    break
        else:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
                found = node
                break

    if found is None:
        raise FileAccessError(f"Symbol not found in {p}: {name}", status_code=404)

    # Include decorators (their lineno is earlier than the def line)
    start = found.lineno
    if getattr(found, "decorator_list", None):
        start = min(d.lineno for d in found.decorator_list)
    end = getattr(found, "end_lineno", None) or start

    lines = source.splitlines()
    extracted = "\n".join(lines[start - 1:end])

    return {
        "path": str(p),
        "name": name,
        "kind": type(found).__name__,
        "start_line": start,
        "end_line": end,
        "md5": md5,
        "source": extracted,
    }


def _compile_pattern(pattern: str, regex: bool) -> re.Pattern:
    if not regex:
        pattern = re.escape(pattern)
    try:
        return re.compile(pattern)
    except re.error as e:
        raise FileAccessError(f"Invalid regex: {e}", status_code=400)


# ── Shared text classifier (Phase 8 §8.10 — re-used by find_symbol_usages
#    and Phase 1 of PLAN_LLM_ROUND_REDUCTION). ───────────────────────────

# Tokens that signal a declaration / binding for `symbol` on a line.
# Order matters — the first match wins so `def foo(...)` is "declaration"
# even when the line also contains a call.
_DECL_HINTS = (
    re.compile(r"^\s*(?:async\s+)?def\s+{name}\b"),
    re.compile(r"^\s*class\s+{name}\b"),
    re.compile(r"^\s*(?:export\s+(?:default\s+)?)?function\s*\*?\s*{name}\b"),
    re.compile(r"^\s*(?:export\s+(?:default\s+)?)?class\s+{name}\b"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+{name}\b"),
    re.compile(r"^\s*{name}\s*[:=]\s*(?:function\b|\([^)]*\)\s*=>)"),
)
_WRITE_HINT = re.compile(r"\b{name}\s*(?:\[[^\]]*\])?\s*(?:=(?!=)|\+=|-=|\*=|/=|%=|//=|\*\*=|&=|\|=|\^=|>>=|<<=)")
_CALL_HINT = re.compile(r"\b{name}\s*\(")
_MUTATING_METHOD_NAMES = ("set", "add", "delete", "clear", "push", "pop",
                          "shift", "unshift", "splice", "fill", "update",
                          "remove", "insert", "append", "extend", "sort",
                          "reverse")
_MUTATING_CALL = re.compile(
    r"\b{name}\.(?:" + "|".join(_MUTATING_METHOD_NAMES) + r")\s*\("
)


def _classify_hit(line: str, symbol: str) -> str:
    """Classify a single line that contains ``symbol``.

    Returns one of: ``declaration``, ``write``, ``call``, ``comment``,
    ``string``, ``read``. Heuristic / single-line; never raises.

    Shared by ``find_symbol_usages`` and the Phase 1 hit-classifier in the
    sibling round-reduction plan, so changes here ripple to both consumers.
    """
    if not symbol:
        return "read"
    name = re.escape(symbol)
    stripped = line.lstrip()
    # Comment first — comment-only lines never count as declarations.
    if stripped.startswith(("#", "//", "/*", "*", "<!--")):
        return "comment"

    for tpl in _DECL_HINTS:
        if re.search(tpl.pattern.format(name=name), line):
            return "declaration"

    if re.search(_MUTATING_CALL.pattern.format(name=name), line):
        return "write"

    if re.search(_WRITE_HINT.pattern.format(name=name), line):
        # Don't misclassify equality / default-arg patterns. The regex
        # already excludes `==`; nothing more to do here.
        return "write"

    if re.search(_CALL_HINT.pattern.format(name=name), line):
        return "call"

    # String-literal-only mention (very rough — only flag when the symbol
    # appears inside a quoted span and nowhere else on the line).
    quoted = [m.group(0) for m in re.finditer(r"(['\"])(?:\\.|(?!\1).)*\1", line)]
    if quoted and any(symbol in q for q in quoted):
        # Strip quoted spans and see if the symbol still appears outside.
        stripped_no_q = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "", line)
        if symbol not in stripped_no_q:
            return "string"

    return "read"


def file_search(
    graph: nx.DiGraph,
    root_dir: str | None,
    path: str,
    pattern: str,
    context: int = 5,
    regex: bool = True,
    expected_md5: str | None = None,
) -> dict:
    """Grep within a single file. Returns matches with N lines of context."""
    p = safe_path(path, graph, root_dir)
    if not p.is_file():
        raise FileAccessError(f"Not a file: {p}", status_code=404)
    md5 = _check_md5(p, expected_md5)
    rx = _compile_pattern(pattern, regex)

    with open(p, encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\n") for ln in f]

    matches: list[dict] = []
    for i, line in enumerate(lines):
        if rx.search(line):
            matches.append({
                "line_no": i + 1,
                "text": line,
                "context_before": lines[max(0, i - context):i],
                "context_after": lines[i + 1:i + 1 + context],
            })
            if len(matches) >= MAX_FILE_SEARCH_MATCHES:
                break

    return {
        "path": str(p),
        "pattern": pattern,
        "regex": regex,
        "context": context,
        "md5": md5,
        "match_count": len(matches),
        "truncated": len(matches) >= MAX_FILE_SEARCH_MATCHES,
        "matches": matches,
    }


def _iter_files(root: Path, globs: list[str]) -> Iterable[Path]:
    skip_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__",
                 "_apollo", "_apollo_web", ".apollo",
                 "target", "dist", "build"}
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fname in files:
            if any(fnmatch.fnmatch(fname, g) for g in globs):
                yield Path(cur) / fname


def project_search(
    graph: nx.DiGraph,
    root_dir: str | None,
    pattern: str,
    root: str | None = None,
    context: int = 5,
    file_glob: str = "*.py",
    regex: bool = True,
) -> dict:
    """Grep across the indexed project. `file_glob` may be comma-separated."""
    if root is None:
        if not root_dir:
            raise FileAccessError("No root configured; pass `root` explicitly.", status_code=400)
        search_root = Path(root_dir).expanduser().resolve()
    else:
        search_root = safe_path(root, graph, root_dir)
    if not search_root.is_dir():
        raise FileAccessError(f"Not a directory: {search_root}", status_code=404)

    globs = [g.strip() for g in file_glob.split(",") if g.strip()] or ["*.py"]
    rx = _compile_pattern(pattern, regex)

    matches: list[dict] = []
    snippet_bytes = 0
    truncated = False

    for path in _iter_files(search_root, globs):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = [ln.rstrip("\n") for ln in f]
        except OSError:
            continue

        for i, line in enumerate(lines):
            if not rx.search(line):
                continue
            entry = {
                "path": str(path),
                "line_no": i + 1,
                "text": line,
                "context_before": lines[max(0, i - context):i],
                "context_after": lines[i + 1:i + 1 + context],
            }
            entry_size = sum(len(s) for s in entry["context_before"]) + len(entry["text"]) + sum(
                len(s) for s in entry["context_after"]
            )
            snippet_bytes += entry_size
            matches.append(entry)
            if len(matches) >= MAX_PROJECT_SEARCH_MATCHES or snippet_bytes >= MAX_PROJECT_SNIPPET_BYTES:
                truncated = True
                break
        if truncated:
            break

    return {
        "root": str(search_root),
        "pattern": pattern,
        "regex": regex,
        "context": context,
        "file_glob": file_glob,
        "match_count": len(matches),
        "truncated": truncated,
        "matches": matches,
    }
