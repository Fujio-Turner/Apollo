"""
Graph builder — constructs a NetworkX directed graph from parsed source files.

Node IDs follow the pattern:
    dir::src/utils
    file::src/utils/mailer.py
    func::src/utils/mailer.py::emails
    class::src/utils/mailer.py::MailService
    method::src/utils/mailer.py::MailService::send
    var::src/utils/mailer.py::MY_CONST
    import::src/utils/mailer.py::os::L1
    comment::src/utils/mailer.py::L10
    string::src/utils/mailer.py::L15
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import networkx as nx

from apollo.parser import PythonParser
from apollo.parser.base import BaseParser
from apollo.parser.text_parser import TEXT_EXTENSIONS

# Markdown extensions handled by MarkdownParser (not in TEXT_EXTENSIONS).
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}

# Extensions the parsers know how to extract symbols from. Other files are
# still indexed as plain `file` nodes (so they show up in the tree and can
# be inspected) but no functions/classes/imports are extracted from them.
_SOURCE_EXTENSIONS = (
    {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs"}
    | TEXT_EXTENSIONS
    | _MARKDOWN_EXTENSIONS
)

# Directories that are ALWAYS skipped — Apollo's own per-project state and
# version-control metadata. These must never be indexed regardless of the
# user's custom filters (they're not source code, they're internal storage,
# and indexing them creates feedback loops with the file watcher).
#
# Apollo's per-project store lives in ``<project>/_apollo/`` (manifest,
# annotations, reindex history, cblite db, …) and the web UI's per-project
# state lives in ``<project>/_apollo_web/``. The legacy dot-prefixed
# ``.apollo`` is also listed for backward compatibility — older projects
# still have one and the file watcher / reindex service write to it.
_ALWAYS_SKIP_DIRS: frozenset[str] = frozenset({
    "_apollo",       # Apollo's per-project store (current name).
    "_apollo_web",   # Apollo web UI's per-project state.
    ".apollo",       # Legacy / workspace-root variant.
    ".git",          # Git metadata.
})

# **Core** skip list — language-agnostic build/IDE noise that no plugin
# would reasonably want indexed. Per-language entries (``venv``,
# ``node_modules``, ``__pycache__`` …) live in each plugin's
# ``config.json`` under ``ignore_dirs`` and are merged at index time
# from the *enabled* plugins; see :func:`_compose_ignore_set`.
_CORE_SKIP_DIRS: frozenset[str] = _ALWAYS_SKIP_DIRS | frozenset({
    # Build / dist / generated (cross-language)
    "build", "dist", "_build", ".build",
    # Coverage / profiling
    "htmlcov", ".coverage",
    # IDE / editor
    ".idea", ".vscode",
})

# Backward-compat alias — older code (and some tests) still reference
# ``_SKIP_DIRS`` directly. Keep it pointing at the broad legacy union so
# anything that imports it gets at least the historical coverage. Plugin
# discovery will *additively* contribute on top of it via the merged
# ignore set computed in ``GraphBuilder``.
_SKIP_DIRS: frozenset[str] = _CORE_SKIP_DIRS | frozenset({
    # Python (kept here for back-compat — primary source is python3 plugin)
    "venv", ".venv", "env", ".env", "virtualenv",
    "site-packages", "dist-packages",
    ".eggs", ".tox", ".nox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "__pypackages__", "__pycache__",
    # JavaScript / TypeScript
    "node_modules", "bower_components",
    # Go
    "vendor",
    # Rust
    "target",
})

# Default sentinel files that mark a directory as a Python virtual
# environment. Kept for back-compat; the python3 plugin's
# ``ignore_dir_markers`` is the authoritative source.
_VENV_MARKERS: tuple[str, ...] = ("pyvenv.cfg", "conda-meta")


def _compose_ignore_set(parsers: list[BaseParser] | None) -> tuple[
    frozenset[str], list[str], tuple[str, ...]
]:
    """Compose the (ignore_dirs, ignore_files, ignore_dir_markers) triple.

    Walks each enabled parser's ``self.config`` (when it has one) and
    unions its ``ignore_dirs`` / ``ignore_files`` / ``ignore_dir_markers``
    on top of the language-agnostic :data:`_CORE_SKIP_DIRS` baseline.

    Parsers without a ``config`` attribute (older plugins, the bundled
    text parser) contribute nothing — the core baseline still applies,
    so back-compat is preserved.
    """
    dirs: set[str] = set(_CORE_SKIP_DIRS)
    files: list[str] = []
    markers: list[str] = []
    for p in parsers or []:
        cfg = getattr(p, "config", None)
        if not isinstance(cfg, dict):
            continue
        for d in cfg.get("ignore_dirs") or []:
            if isinstance(d, str) and d:
                dirs.add(d)
        for f in cfg.get("ignore_files") or []:
            if isinstance(f, str) and f:
                files.append(f)
        for m in cfg.get("ignore_dir_markers") or []:
            if isinstance(m, str) and m:
                markers.append(m)
    return frozenset(dirs), files, tuple(markers)


def _is_venv_dir(dirpath: str, markers: tuple[str, ...] = _VENV_MARKERS) -> bool:
    """Detect virtualenv-style dirs by sentinel file (e.g. ``pyvenv.cfg``).

    The ``markers`` tuple is composed from each enabled plugin's
    ``ignore_dir_markers`` config key — see :func:`_compose_ignore_set`.
    The default value is kept for backward-compatibility with any caller
    that doesn't pass an explicit value.
    """
    for marker in markers:
        if os.path.exists(os.path.join(dirpath, marker)):
            return True
    return False


def _parse_one(item: tuple) -> dict | None:
    """Parse a single file — top-level function for ProcessPoolExecutor.

    If `parser` is None the file has no language parser; return a minimal
    parsed dict so the file is still added as a plain `file` node.
    """
    parser, src_file, rel_path, source_text = item
    if parser is None:
        return {
            "rel_path": rel_path,
            "functions": [],
            "classes": [],
            "imports": [],
            "calls": [],
            "variables": [],
            "module_docstring": None,
            "patterns": [],
        }
    if source_text is not None:
        parsed = parser.parse_source(source_text, str(src_file))
    else:
        parsed = parser.parse_file(str(src_file))
    if parsed is not None:
        parsed["rel_path"] = rel_path
    return parsed


class GraphBuilder:
    """Builds a knowledge graph from a directory of source files."""

    def __init__(
        self,
        parsers: list[BaseParser] | None = None,
        filters: dict | None = None,
    ):
        self.graph = nx.DiGraph()
        self._parsers: list[BaseParser] = parsers or [PythonParser()]
        self._symbol_table: dict[str, str] = {}  # qualified_name -> node_id
        self._file_imports: dict[str, list[dict]] = {}  # file -> imports
        self._root: Path | None = None
        # User-defined filters from ProjectManifest.filters (apollo.json).
        # When None or mode=="all", only built-in core + plugin ignores apply.
        self._filters = self._normalize_filters(filters)
        # Compose the indexer's ignore set from the enabled plugins'
        # ``config.json``. Each plugin contributes its language-specific
        # entries (e.g. python3 → ``venv``, ``__pycache__``); the
        # core list (``.git``, ``build`` …) is always included.
        self._skip_dirs, self._ignore_file_globs, self._venv_markers = (
            _compose_ignore_set(self._parsers)
        )

    @staticmethod
    def _normalize_filters(filters: dict | None) -> dict | None:
        if not filters:
            return None
        mode = filters.get("mode", "all")
        include_dirs = [d.strip("/").rstrip(os.sep) for d in (filters.get("include_dirs") or []) if d]
        exclude_dirs = [d.strip("/").rstrip(os.sep) for d in (filters.get("exclude_dirs") or []) if d]
        # Lowercase, strip leading dot, for ext whitelist
        include_doc_types = {
            t.lower().lstrip(".") for t in (filters.get("include_doc_types") or []) if t
        }
        exclude_file_globs = list(filters.get("exclude_file_globs") or [])
        return {
            "mode": mode,
            "include_dirs": include_dirs,
            "exclude_dirs": exclude_dirs,
            "include_doc_types": include_doc_types,
            "exclude_file_globs": exclude_file_globs,
        }

    def _is_dir_included(self, rel_dir: str) -> bool:
        """Check whether a directory (relative to root) should be walked."""
        # Hard skip Apollo's own state dir / VCS metadata, regardless of any
        # user filter. ``_discover_files`` already prunes dot-folders, but we
        # double-check here so that future code paths (or relaxed dot rules)
        # can never accidentally index ``.apollo`` / ``.git``.
        rel_norm = rel_dir.replace(os.sep, "/")
        if rel_norm:
            first = rel_norm.split("/", 1)[0]
            if first in _ALWAYS_SKIP_DIRS:
                return False
        f = self._filters
        if not f:
            return True
        # User exclude_dirs: match by name OR by relative path prefix.
        for excl in f["exclude_dirs"]:
            excl_norm = excl.replace(os.sep, "/")
            if (
                rel_norm == excl_norm
                or rel_norm.startswith(excl_norm + "/")
                or os.path.basename(rel_norm) == excl_norm
            ):
                return False
        # In custom mode with include_dirs, prune anything outside the whitelist.
        if f["mode"] == "custom" and f["include_dirs"]:
            for inc in f["include_dirs"]:
                inc_norm = inc.replace(os.sep, "/")
                # rel is inside the included dir, OR is an ancestor of it
                # (so we can descend into it).
                if (
                    rel_norm == inc_norm
                    or rel_norm.startswith(inc_norm + "/")
                    or inc_norm.startswith(rel_norm + "/")
                ):
                    return True
            return False
        return True

    def _is_file_included(self, rel_path: str) -> bool:
        """Check whether a file (relative to root) should be indexed."""
        f = self._filters
        if not f:
            return True
        rel_norm = rel_path.replace(os.sep, "/")
        # Glob excludes (path or basename match)
        base = os.path.basename(rel_norm)
        for pat in f["exclude_file_globs"]:
            if fnmatch.fnmatch(rel_norm, pat) or fnmatch.fnmatch(base, pat):
                return False
        # Extension whitelist
        if f["include_doc_types"]:
            ext = os.path.splitext(base)[1].lower().lstrip(".")
            if ext not in f["include_doc_types"]:
                return False
        return True

    def build(self, root_dir: str) -> nx.DiGraph:
        """Scan a directory and build the full graph."""
        root = Path(root_dir).resolve()
        if not root.is_dir():
            raise ValueError(f"Not a directory: {root}")

        self._root = root

        # Single walk: discover files and collect directory ancestry
        files_to_parse, dir_set = self._discover_files(root)

        # Build directory nodes lazily from discovered file paths
        self._build_dir_nodes_lazy(root, dir_set)

        # Parse files in parallel
        parsed_files = self._parse_files_parallel(files_to_parse)

        # Build nodes sequentially (NetworkX is not thread-safe)
        for parsed in parsed_files:
            self._build_file_nodes(parsed, parsed["rel_path"])

        # Phase 2: Resolve cross-file edges
        for parsed in parsed_files:
            self._resolve_calls(parsed)

        return self.graph

    def build_incremental(
        self, root_dir: str, prev_hashes: dict[str, str] | None = None
    ) -> tuple[nx.DiGraph, dict[str, str]]:
        """Build the graph, only re-parsing files whose content changed.

        *prev_hashes* maps ``rel_path → {sha256, mtime_ns, size}`` or
        legacy ``rel_path → sha256_hex`` from the last run.
        Returns ``(graph, new_hashes)`` so the caller can persist the hash
        map for the next invocation.
        """
        root = Path(root_dir).resolve()
        if not root.is_dir():
            raise ValueError(f"Not a directory: {root}")

        self._root = root
        prev_hashes = prev_hashes or {}
        new_hashes: dict[str, str] = {}

        # Single walk: discover files and collect directory ancestry
        files_to_parse_all, dir_set = self._discover_files(root)

        # Build directory nodes lazily
        self._build_dir_nodes_lazy(root, dir_set)

        # Filter to changed files using stat-based prefilter
        files_to_parse: list[tuple[BaseParser, Path, str, str | None]] = []
        for parser, src_file, rel_path, _ in files_to_parse_all:
            try:
                st = src_file.stat()
            except OSError:
                continue

            prev = prev_hashes.get(rel_path)
            # Support both legacy (plain hash string) and new (dict) formats
            if isinstance(prev, dict):
                prev_mtime = prev.get("mtime_ns")
                prev_size = prev.get("size")
                prev_sha = prev.get("sha256")
            else:
                prev_mtime = None
                prev_size = None
                prev_sha = prev  # legacy: plain sha256 string

            # Fast path: if mtime and size unchanged, skip read entirely
            if (prev_mtime is not None
                    and prev_mtime == st.st_mtime_ns
                    and prev_size == st.st_size):
                new_hashes[rel_path] = prev
                continue

            # Metadata changed — read and hash
            try:
                content = src_file.read_bytes()
            except OSError:
                continue
            file_hash = hashlib.sha256(content).hexdigest()
            source_text = content.decode("utf-8", errors="replace")

            new_hashes[rel_path] = {
                "sha256": file_hash,
                "mtime_ns": st.st_mtime_ns,
                "size": st.st_size,
            }

            if file_hash == prev_sha:
                continue  # Content unchanged despite metadata change

            # Pass source_text so parser doesn't re-read from disk
            files_to_parse.append((parser, src_file, rel_path, source_text))

        # Parse changed files in parallel
        parsed_files = self._parse_files_parallel(files_to_parse)

        # Build nodes sequentially (NetworkX is not thread-safe)
        for parsed in parsed_files:
            self._build_file_nodes(parsed, parsed["rel_path"])

        # Phase 2: Resolve cross-file edges
        for parsed in parsed_files:
            self._resolve_calls(parsed)

        return self.graph, new_hashes

    def _discover_files(
        self, root: Path
    ) -> tuple[list[tuple[BaseParser, Path, str, None]], set[str]]:
        """Single os.walk pass: discover parseable files and their directories."""
        files: list[tuple[BaseParser, Path, str, None]] = []
        dir_set: set[str] = set()
        dir_set.add("")  # root directory

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune hidden dirs, plugin-contributed skip dirs, and
            # virtualenv-style directories (sentinel files come from each
            # enabled plugin's ``ignore_dir_markers``).
            kept = []
            for d in dirnames:
                if (
                    d.startswith(".")
                    or d in self._skip_dirs
                    or _is_venv_dir(os.path.join(dirpath, d), self._venv_markers)
                ):
                    continue
                # Compute the dir's path relative to root and consult user filters.
                child_abs = os.path.join(dirpath, d)
                rel_dir = os.path.relpath(child_abs, root)
                if rel_dir == ".":
                    rel_dir = ""
                if not self._is_dir_included(rel_dir):
                    continue
                kept.append(d)
            dirnames[:] = kept
            dirnames.sort()

            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                # Plugin-contributed file globs (e.g. python3 → ``*.pyc``).
                if self._ignore_file_globs and any(
                    fnmatch.fnmatch(fname, pat) for pat in self._ignore_file_globs
                ):
                    continue

                src_file = Path(dirpath) / fname
                rel_path = str(src_file.relative_to(root))

                # User filters: extension whitelist + glob excludes.
                if not self._is_file_included(rel_path):
                    continue

                # Every file becomes a node. A parser is optional — files
                # without one are still indexed as plain `file` nodes so
                # they appear in the tree / can be inspected.
                parser = self._find_parser(str(src_file))
                files.append((parser, src_file, rel_path, None))

                # Collect all ancestor directories
                parent = os.path.dirname(rel_path)
                while parent and parent not in dir_set:
                    dir_set.add(parent)
                    parent = os.path.dirname(parent)

        return files, dir_set

    def _build_dir_nodes_lazy(self, root: Path, dir_set: set[str]):
        """Create directory nodes only for directories that contain indexed files."""
        # Create root node
        root_id = "dir::."
        self.graph.add_node(root_id, type="directory", name=root.name, path="", abs_path=str(root))

        for rel in sorted(dir_set):
            if not rel:
                continue
            dir_id = f"dir::{rel}"
            self.graph.add_node(
                dir_id, type="directory",
                name=os.path.basename(rel), path=rel,
            )
            parent = os.path.dirname(rel)
            parent_id = f"dir::{parent}" if parent else "dir::."
            self.graph.add_edge(parent_id, dir_id, type="contains")

    def _parse_files_parallel(
        self, files: list[tuple[BaseParser, Path, str, str | None]]
    ) -> list[dict]:
        """Parse files concurrently using a thread pool."""
        if not files:
            return []

        results: list[dict] = []
        max_workers = min(len(files), os.cpu_count() or 4)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_parse_one, f): f for f in files}
            for future in as_completed(futures):
                try:
                    parsed = future.result()
                except Exception:
                    continue
                if parsed is not None:
                    results.append(parsed)
        return results

    def _find_parser(self, filepath: str) -> BaseParser | None:
        """Return the first parser that can handle *filepath*."""
        for parser in self._parsers:
            if parser.can_parse(filepath):
                return parser
        return None

    def _build_file_nodes(self, parsed: dict, rel_path: str):
        """Create file, function, class, and import nodes from parsed data."""
        file_id = f"file::{rel_path}"

        file_md5 = parsed.get("file_md5")
        if file_md5 is None:
            file_abs = self._root / rel_path
            try:
                file_md5 = hashlib.md5(file_abs.read_bytes()).hexdigest()
            except (OSError, IOError):
                file_md5 = None

        self.graph.add_node(
            file_id,
            type="file",
            name=os.path.basename(rel_path),
            path=rel_path,
            file_md5=file_md5,
            module_docstring=parsed.get("module_docstring"),
            patterns=parsed.get("patterns", []),
        )

        # Connect file to its parent directory
        parent_dir = os.path.dirname(rel_path)
        dir_id = f"dir::{parent_dir}" if parent_dir else "dir::."
        if dir_id in self.graph:
            self.graph.add_edge(dir_id, file_id, type="contains")

        # Functions
        for func in parsed["functions"]:
            func_id = f"func::{rel_path}::{func['name']}"
            func_md5 = hashlib.md5(func.get("source", "").encode()).hexdigest()
            self.graph.add_node(
                func_id,
                type="function",
                name=func["name"],
                path=rel_path,
                line_start=func["line_start"],
                line_end=func["line_end"],
                source=func["source"],
                args=func.get("args", []),
                params=func.get("params", []),
                return_annotation=func.get("return_annotation"),
                source_md5=func_md5,
                decorators=func.get("decorators", []),
                docstring=func.get("docstring"),
                is_async=func.get("is_async", False),
                is_nested=func.get("is_nested", False),
                is_test=func.get("is_test", False),
                signature_hash=func.get("signature_hash"),
                complexity=func.get("complexity", 0),
                loc=func.get("loc", 0),
                context_managers=func.get("context_managers", []),
                exceptions=func.get("exceptions", []),
            )
            self.graph.add_edge(file_id, func_id, type="defines")

            # If this is a test function, try to link to the function it tests
            if func.get("is_test", False):
                test_name = func["name"]
                target_name = None
                if test_name.startswith("test_"):
                    target_name = test_name[len("test_"):]
                if target_name and target_name in self._symbol_table:
                    self.graph.add_edge(func_id, self._symbol_table[target_name], type="tests")

            # Register in symbol table
            module_name = self._path_to_module(rel_path)
            self._symbol_table[f"{module_name}.{func['name']}"] = func_id
            self._symbol_table[func["name"]] = func_id

        # Classes
        for cls in parsed["classes"]:
            class_id = f"class::{rel_path}::{cls['name']}"
            self.graph.add_node(
                class_id,
                type="class",
                name=cls["name"],
                path=rel_path,
                line_start=cls["line_start"],
                line_end=cls["line_end"],
                source=cls["source"],
                bases=cls["bases"],
                decorators=cls.get("decorators", []),
                docstring=cls.get("docstring"),
                class_vars=cls.get("class_vars", []),
                is_dataclass=cls.get("is_dataclass", False),
                is_namedtuple=cls.get("is_namedtuple", False),
            )
            self.graph.add_edge(file_id, class_id, type="defines")

            # Class variables
            for cv in cls.get("class_vars", []):
                cv_id = f"var::{rel_path}::{cls['name']}::{cv['name']}"
                self.graph.add_node(
                    cv_id,
                    type="variable",
                    name=cv["name"],
                    path=rel_path,
                    line=cv.get("line"),
                    annotation=cv.get("annotation"),
                    value=cv.get("value"),
                )
                self.graph.add_edge(class_id, cv_id, type="defines")

            module_name = self._path_to_module(rel_path)
            self._symbol_table[f"{module_name}.{cls['name']}"] = class_id
            self._symbol_table[cls["name"]] = class_id

            # Methods
            for method in cls["methods"]:
                method_id = f"method::{rel_path}::{cls['name']}::{method['name']}"
                method_source = method.get("source", "")
                self.graph.add_node(
                    method_id,
                    type="method",
                    name=method["name"],
                    path=rel_path,
                    line_start=method["line_start"],
                    line_end=method["line_end"],
                    source=method_source,
                    parent_class=cls["name"],
                    args=method.get("args", []),
                    params=method.get("params", []),
                    return_annotation=method.get("return_annotation"),
                    decorators=method.get("decorators", []),
                    docstring=method.get("docstring"),
                    signature_hash=method.get("signature_hash"),
                    complexity=method.get("complexity", 0),
                    loc=method.get("loc", 0),
                    context_managers=method.get("context_managers", []),
                    exceptions=method.get("exceptions", []),
                )
                self.graph.add_edge(class_id, method_id, type="defines")

                self._symbol_table[f"{module_name}.{cls['name']}.{method['name']}"] = method_id
                self._symbol_table[f"{cls['name']}.{method['name']}"] = method_id

            # Inheritance edges
            for base in cls["bases"]:
                if base in self._symbol_table:
                    self.graph.add_edge(class_id, self._symbol_table[base], type="inherits")

        # Imports
        self._file_imports[rel_path] = parsed["imports"]
        for imp in parsed["imports"]:
            if imp["names"]:
                label = f"from {imp['module']} import {', '.join(imp['names'])}"
            else:
                label = f"import {imp['module']}"
            imp_id = f"import::{rel_path}::{imp['module']}::L{imp['line']}"
            self.graph.add_node(
                imp_id,
                type="import",
                name=label,
                path=rel_path,
                module=imp["module"],
                names=imp["names"],
                line=imp["line"],
                level=imp.get("level", 0),
            )
            self.graph.add_edge(file_id, imp_id, type="imports")

        # Type-checking imports
        for imp in parsed.get("type_checking_imports", []):
            if imp["names"]:
                label = f"from {imp['module']} import {', '.join(imp['names'])}"
            else:
                label = f"import {imp['module']}"
            imp_id = f"import::{rel_path}::{imp['module']}::L{imp['line']}"
            self.graph.add_node(
                imp_id,
                type="import",
                name=label,
                path=rel_path,
                module=imp["module"],
                names=imp["names"],
                line=imp["line"],
                level=imp.get("level", 0),
                type_checking=True,
            )
            self.graph.add_edge(file_id, imp_id, type="imports")

        # Variables
        for var in parsed["variables"]:
            var_id = f"var::{rel_path}::{var['name']}"
            self.graph.add_node(
                var_id,
                type="variable",
                name=var["name"],
                path=rel_path,
                line=var["line"],
                value=var.get("value"),
            )
            self.graph.add_edge(file_id, var_id, type="defines")
            module_name = self._path_to_module(rel_path)
            self._symbol_table[f"{module_name}.{var['name']}"] = var_id
            self._symbol_table[var["name"]] = var_id

        # Comments
        for comment in parsed.get("comments", []):
            comment_id = f"comment::{rel_path}::L{comment['line']}"
            tag = comment.get("tag", "")
            text = comment.get("text", "")
            display = f"{tag}: {text}" if tag else text
            self.graph.add_node(
                comment_id,
                type="comment",
                name=display[:100],
                path=rel_path,
                tag=tag,
                text=text,
                line=comment["line"],
            )
            self.graph.add_edge(file_id, comment_id, type="defines")

        # Strings
        for string in parsed.get("strings", []):
            string_id = f"string::{rel_path}::L{string['line']}"
            self.graph.add_node(
                string_id,
                type="string",
                name=string.get("value", "")[:80],
                path=rel_path,
                kind=string.get("kind"),
                value=string.get("value"),
                line=string["line"],
            )
            self.graph.add_edge(file_id, string_id, type="defines")

        # Documents (non-code files: Markdown, JSON, YAML, CSV, text)
        for doc in parsed.get("documents", []):
            doc_id = f"doc::{rel_path}"
            self.graph.add_node(
                doc_id,
                type="document",
                name=doc["name"],
                doc_type=doc["doc_type"],
                path=rel_path,
                line_start=doc["line_start"],
                line_end=doc["line_end"],
                source=doc["content"],
                frontmatter=parsed.get("frontmatter"),
                title=parsed.get("title"),
            )
            self.graph.add_edge(file_id, doc_id, type="defines")

        # Markdown sections (heading-based hierarchy)
        for sec in parsed.get("sections", []):
            sec_id = f"section::{rel_path}::L{sec['line_start']}"
            self.graph.add_node(
                sec_id,
                type="section",
                name=sec["name"],
                path=rel_path,
                level=sec["level"],
                line_start=sec["line_start"],
                line_end=sec["line_end"],
                source=sec["content"],
                parent_section=sec.get("parent_section"),
            )
            self.graph.add_edge(file_id, sec_id, type="defines")

        # Markdown code blocks (embedded code snippets)
        for cb in parsed.get("code_blocks", []):
            cb_id = f"codeblock::{rel_path}::L{cb['line_start']}"
            label = f"```{cb['language']}" if cb.get("language") else "```"
            self.graph.add_node(
                cb_id,
                type="code_block",
                name=label,
                path=rel_path,
                language=cb.get("language"),
                line_start=cb["line_start"],
                line_end=cb["line_end"],
                source=cb["content"],
            )
            self.graph.add_edge(file_id, cb_id, type="defines")

        # Markdown links and images
        for lnk in parsed.get("links", []):
            lnk_id = f"link::{rel_path}::L{lnk['line']}"
            self.graph.add_node(
                lnk_id,
                type="link",
                name=lnk.get("text") or lnk["url"],
                path=rel_path,
                url=lnk["url"],
                line=lnk["line"],
                link_type=lnk.get("link_type"),
                is_image=lnk.get("is_image", False),
            )
            self.graph.add_edge(file_id, lnk_id, type="defines")

        # Markdown tables
        for tbl in parsed.get("tables", []):
            tbl_id = f"table::{rel_path}::L{tbl['line_start']}"
            header_str = " | ".join(tbl.get("headers", []))
            self.graph.add_node(
                tbl_id,
                type="table",
                name=header_str[:100] if header_str else "table",
                path=rel_path,
                headers=tbl.get("headers", []),
                rows=tbl.get("rows", []),
                line_start=tbl["line_start"],
                line_end=tbl["line_end"],
            )
            self.graph.add_edge(file_id, tbl_id, type="defines")

        # Markdown task items
        for task in parsed.get("task_items", []):
            task_id = f"task::{rel_path}::L{task['line']}"
            prefix = "☑" if task.get("checked") else "☐"
            self.graph.add_node(
                task_id,
                type="task_item",
                name=f"{prefix} {task['text'][:80]}",
                path=rel_path,
                text=task["text"],
                checked=task.get("checked", False),
                line=task["line"],
            )
            self.graph.add_edge(file_id, task_id, type="defines")

    def _resolve_calls(self, parsed: dict):
        """Resolve function calls to their targets using the symbol table."""
        rel_path = parsed["rel_path"]

        # Build a local import map: short_name -> qualified module path
        import_map: dict[str, str] = {}
        for imp in parsed["imports"]:
            if imp["names"]:
                for name in imp["names"]:
                    import_map[name] = f"{imp['module']}.{name}"
            else:
                parts = imp["module"].split(".")
                short = imp["alias"] or parts[-1]
                import_map[short] = imp["module"]

        # Resolve calls in functions
        for func in parsed["functions"]:
            func_id = f"func::{rel_path}::{func['name']}"
            self._resolve_call_list(func_id, func["calls"], import_map)

        # Resolve calls in methods
        for cls in parsed["classes"]:
            for method in cls["methods"]:
                method_id = f"method::{rel_path}::{cls['name']}::{method['name']}"
                self._resolve_call_list(method_id, method["calls"], import_map)

    def _resolve_call_list(
        self, caller_id: str, calls: list, import_map: dict[str, str]
    ):
        """Try to resolve each call name to a node in the symbol table."""
        for call in calls:
            # Support both old format (str) and new format (dict)
            if isinstance(call, dict):
                call_name = call["name"]
                call_args = call.get("args", [])
                call_line = call.get("line")
            else:
                call_name = call
                call_args = []
                call_line = None
            target_id = self._resolve_single_call(call_name, import_map)
            if target_id and target_id != caller_id:
                self.graph.add_edge(
                    caller_id, target_id, type="calls",
                    call_args=call_args, call_line=call_line,
                )

    def _resolve_single_call(
        self, call_name: str, import_map: dict[str, str]
    ) -> str | None:
        """Resolve a single call name to a node ID."""
        # Direct match
        if call_name in self._symbol_table:
            return self._symbol_table[call_name]

        # Try via import map: e.g., "mailer.emails" where "mailer" is imported
        parts = call_name.split(".")
        if parts[0] in import_map:
            qualified = import_map[parts[0]]
            if len(parts) > 1:
                qualified = f"{qualified}.{'.'.join(parts[1:])}"
            if qualified in self._symbol_table:
                return self._symbol_table[qualified]

        # Try just the last part (e.g., "self.emails" -> "emails")
        if len(parts) > 1:
            short = parts[-1]
            if short in self._symbol_table:
                return self._symbol_table[short]

        return None

    def _path_to_module(self, rel_path: str) -> str:
        """Convert a file path to a Python module name."""
        module = rel_path.replace(os.sep, ".").replace("/", ".")
        if module.endswith(".py"):
            module = module[:-3]
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        return module
