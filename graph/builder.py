# SPDX-License-Identifier: BUSL-1.1
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
import re
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
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


def _minimal_resolve_record(parsed: dict) -> dict:
    """Extract the small subset of a ``parsed`` dict that :func:`_resolve_calls`
    actually consumes (``rel_path`` + ``imports`` + per-function/method
    call lists). Used by the streaming build path so the heavy
    ``parsed`` dicts can be dropped immediately after
    :meth:`GraphBuilder._build_file_nodes` writes them into the graph —
    instead of being kept in memory for the entire build the way the
    legacy ``parsed_files`` list did.

    The returned dict is intentionally shaped exactly like the slice of
    the parsed dict that ``_resolve_calls`` reads, so the resolve loop
    can stay unchanged.
    """
    return {
        "rel_path": parsed["rel_path"],
        # ``imports`` is the only large-ish nested list we have to keep;
        # each entry is a small dict (module + names + alias + line) and
        # ``_resolve_calls`` does need every one of them to build its
        # import map. We *don't* keep ``type_checking_imports`` /
        # ``comments`` / ``strings`` / ``documents`` / ``sections`` /
        # ``code_blocks`` / ``links`` / ``tables`` / ``task_items`` /
        # ``module_docstring`` / ``patterns`` / ``source`` — those were
        # consumed by ``_build_file_nodes`` and are no longer needed.
        "imports": parsed.get("imports") or [],
        "functions": [
            {"name": f["name"], "calls": f.get("calls") or []}
            for f in (parsed.get("functions") or [])
        ],
        "classes": [
            {
                "name": c["name"],
                "methods": [
                    {"name": m["name"], "calls": m.get("calls") or []}
                    for m in (c.get("methods") or [])
                ],
            }
            for c in (parsed.get("classes") or [])
        ],
    }


def _rehash_file_for_incremental(
    src_file: Path,
    rel_path: str,
    cur_mtime: int,
    cur_size: int,
    prev_sha: str | None,
) -> dict | None:
    """Phase 9 of PLAN_INDEX_MEMORY_AND_CONCURRENCY — read + sha256 +
    md5 a single file whose stat differs from ``prev_hashes``.

    Returns a result dict the caller composes into its
    ``files_to_parse`` list + ``new_hashes`` map, or ``None`` on
    OSError (vanished mid-walk / permission denied — same swallow
    behaviour as the legacy inline block).

    Returned shape::

        {
          "rel_path":      <str>,
          "new_hash":      {"sha256", "mtime_ns", "size"},
          "changed":       <bool>,       # False = content unchanged
          "source_text":   <str | None>, # populated only when changed
          "file_md5_hex":  <str | None>, # populated only when changed
        }

    Both legacy callers (``GraphBuilder.build_incremental`` and
    ``ResolveFullStrategy.run``) used to do this work inline, in a
    single main-thread loop. Phase 9 hoists it into a helper so a
    ``ThreadPoolExecutor`` can spread the read+hash across IO + GIL-
    releasing crypto, dramatically reducing wall-clock when many
    files' stats changed at once.
    """
    try:
        content = src_file.read_bytes()
    except OSError:
        return None
    file_hash = hashlib.sha256(content).hexdigest()
    new_hash = {
        "sha256": file_hash,
        "mtime_ns": cur_mtime,
        "size": cur_size,
    }
    if file_hash == prev_sha:
        # Content unchanged despite metadata change — no need to
        # decode bytes / compute md5 / hold onto the source text.
        return {
            "rel_path": rel_path,
            "new_hash": new_hash,
            "changed": False,
            "source_text": None,
            "file_md5_hex": None,
        }
    file_md5_hex = hashlib.md5(content).hexdigest()
    source_text = content.decode("utf-8", errors="replace")
    return {
        "rel_path": rel_path,
        "new_hash": new_hash,
        "changed": True,
        "source_text": source_text,
        "file_md5_hex": file_md5_hex,
    }


def _parallel_rehash(
    jobs: list[tuple[Path, str, int, int, str | None]],
    max_workers: int | None = None,
) -> list[dict]:
    """Fan out :func:`_rehash_file_for_incremental` across a
    ``ThreadPoolExecutor``.

    Each job tuple is ``(src_file, rel_path, cur_mtime, cur_size,
    prev_sha)``. Returns a list of result dicts in submission order
    (so callers preserving discovery order do not have to sort).

    The worker cap mirrors the plan §12 recommendation
    (``min(32, os.cpu_count() * 4)``). For small batches (≤ 8 jobs)
    we run inline — thread-pool setup cost otherwise dominates the
    savings on no-op incremental sweeps.
    """
    if not jobs:
        return []
    if len(jobs) <= 8:
        # Inline path — keeps the cheap case cheap.
        out: list[dict] = []
        for j in jobs:
            r = _rehash_file_for_incremental(*j)
            if r is not None:
                out.append(r)
        return out
    if max_workers is None:
        max_workers = min(32, (os.cpu_count() or 4) * 4)
    results: list[dict | None] = [None] * len(jobs)
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="apollo-rehash",
    ) as ex:
        futures = {
            ex.submit(_rehash_file_for_incremental, *j): i
            for i, j in enumerate(jobs)
        }
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception:
                results[i] = None
    return [r for r in results if r is not None]


def _push_embeds_to_queue(parsed: dict, rel_path: str, embed_queue) -> None:
    """Phase 6 of PLAN_INDEX_MEMORY_AND_CONCURRENCY — enqueue every
    embedding-eligible node for ``parsed`` onto ``embed_queue``.

    Reads source strings directly off the parser output (``functions``,
    ``classes[].methods``, ``documents``, ``sections``) — they're still
    in memory at this point even though Phase 4 stopped copying them
    onto the node attrs. The queue's own enqueue is a fast no-op for
    too-short texts and short-circuits on cache hits, so it's cheap to
    call indiscriminately.

    The ``code_block`` node type is omitted intentionally: pre-Phase-4
    it carried ``cb["content"]`` (the un-fenced code body); post-Phase-4
    ``get_source`` returns a slice that *includes* the ``` fence lines,
    which the embedder downstream is fine with but for new-write
    consistency we let the post-build ``embed_graph`` fallback handle
    code-block embeddings via the same get_source path.
    """
    for f in parsed.get("functions", []) or []:
        nid = f"func::{rel_path}::{f['name']}"
        src = f.get("source")
        if src:
            embed_queue.enqueue(nid, src)
    for c in parsed.get("classes", []) or []:
        class_nid = f"class::{rel_path}::{c['name']}"
        c_src = c.get("source")
        if c_src:
            embed_queue.enqueue(class_nid, c_src)
        for m in c.get("methods", []) or []:
            mnid = f"method::{rel_path}::{c['name']}::{m['name']}"
            m_src = m.get("source")
            if m_src:
                embed_queue.enqueue(mnid, m_src)
    for d in parsed.get("documents", []) or []:
        nid = f"doc::{rel_path}"
        d_src = d.get("content")
        if d_src:
            embed_queue.enqueue(nid, d_src)
    for s in parsed.get("sections", []) or []:
        nid = f"section::{rel_path}::L{s['line_start']}"
        s_src = s.get("content")
        if s_src:
            embed_queue.enqueue(nid, s_src)


def _parse_one(item: tuple) -> dict | None:
    """Parse a single file — top-level function for ProcessPoolExecutor.

    The work-item tuple is ``(parser, src_file, rel_path, source_text,
    file_md5_hex)``. ``file_md5_hex`` is set by ``build_incremental``
    where we already had the bytes in memory; for the full-build path
    it is ``None`` and ``_build_file_nodes`` falls back to a disk read.

    Resolution order:

    1. If ``parser`` is None the file has no language parser; return a
       minimal parsed dict so the file is still added as a plain ``file``
       node (so it shows up in the tree and can be inspected).
    2. If ``parser`` returned ``None`` (e.g. plugin's size cap hit, parse
       exception, blank file) we fall back to the same minimal dict
       *instead of dropping the file entirely* — otherwise the file would
       silently disappear from the index, which historically caused chat
       tools to report "no such file" for things the user could clearly
       see in the explorer (see DESIGN.md §"File-skipping invariants").
    """
    parser, src_file, rel_path, source_text, file_md5_hex = item

    def _minimal() -> dict:
        return {
            "rel_path": rel_path,
            "functions": [],
            "classes": [],
            "imports": [],
            "calls": [],
            "variables": [],
            "module_docstring": None,
            "patterns": [],
            "file_md5": file_md5_hex,
        }

    if parser is None:
        stub = _minimal()
        # Phase 4: best-effort capture of the file text for the
        # ``_file_text`` sidecar even on parser-less files (so the API's
        # /api/node/{file_id} responses can still show the contents).
        if source_text is None:
            try:
                source_text = src_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                source_text = None
        if source_text is not None:
            stub["_full_file_text"] = source_text
        return stub
    # Phase 4 of PLAN_INDEX_MEMORY_AND_CONCURRENCY — capture the full
    # file source text once per file so ``_build_file_nodes`` can stash
    # a single copy in ``graph.graph["_file_text"]`` instead of letting
    # every func/method/class/section/code_block carry its own slice.
    # For the full-build path (``source_text is None``) this means
    # routing through ``parse_source`` rather than ``parse_file`` so we
    # don't read the bytes twice — every concrete plugin already
    # implements ``parse_source`` (most ``parse_file``s just read +
    # delegate to it).
    if source_text is None:
        try:
            source_text = src_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return _minimal()
    parsed = parser.parse_source(source_text, str(src_file))
    if parsed is None:
        # Plugin claimed the extension but bailed (size cap, exception,
        # empty body, …). Keep a stub so the file still gets a `file::`
        # node — losing the symbols is acceptable, losing the file is not.
        stub = _minimal()
        stub["_full_file_text"] = source_text
        return stub
    parsed["rel_path"] = rel_path
    if file_md5_hex is not None and not parsed.get("file_md5"):
        parsed["file_md5"] = file_md5_hex
    # Phase 4 — attach the file text so ``_build_file_nodes`` can move
    # it into ``self.graph.graph["_file_text"]``. The temporary
    # ``_full_file_text`` key drops out of scope along with the parsed
    # dict on the next streaming-build iteration.
    parsed["_full_file_text"] = source_text
    return parsed


# ─────────────────────────────────────────────────────────────────────
# Phase 2 of PLAN_INDEX_MEMORY_AND_CONCURRENCY — ProcessPoolExecutor
# parser pool.
#
# Python AST parsing (the dominant cost in ``parse_and_build``) is
# GIL-bound, so the legacy ``ThreadPoolExecutor`` only spreads I/O. To
# actually use multiple CPU cores we run ``_parse_one`` in a process
# pool. Parser *instances* are not portable across processes (tree-
# sitter parsers hold C handles, etc.), so each work-item references
# the parser by a **stable string key** (``type(parser).__module__``)
# and the worker resolves it against a module-global table that is
# populated once per worker via the pool's ``initializer``.
#
# The pool mode is selected per-call (the streaming-build method takes
# a ``parser_pool=`` kwarg) or globally via the ``APOLLO_PARSER_POOL``
# environment variable (values: ``process`` (default) / ``thread`` /
# ``sync``). When ``process`` is requested but any enabled parser
# advertises ``safe_for_processes = False`` we transparently fall back
# to ``thread`` so plugins with unpickle-able state stay correct.
# ─────────────────────────────────────────────────────────────────────

# Populated once per worker process by ``_init_worker_parsers``. Maps
# the parser key (``type(parser).__module__``) → fresh ``BaseParser``
# instance built inside the worker.
_WORKER_PARSERS: dict[str, BaseParser] | None = None


def _parser_key(parser: BaseParser | None) -> str | None:
    """Stable per-parser identifier safe to ship across processes.

    Uses the parser class's defining module (e.g.
    ``plugins.python3.parser``) which is deterministic across worker
    processes because :func:`apollo.plugins.discover_plugins` walks the
    ``plugins/`` package the same way in every interpreter. Returns
    ``None`` for the parser-less fallback (text-only files / files
    whose extension no plugin claims).
    """
    if parser is None:
        return None
    return type(parser).__module__


def _init_worker_parsers() -> None:
    """ProcessPoolExecutor initializer: re-discover plugins per worker.

    Called exactly once at worker startup. Re-imports every plugin in
    a fresh interpreter (spawn-safe) so any C handles, grammars, or
    other unpickle-able state is constructed inside the worker rather
    than carried across the pickle boundary.

    Failures are swallowed (logged via ``discover_plugins`` itself) so
    a single broken plugin does not poison every worker — the worker
    table just won't contain that key and ``_parse_one_process`` will
    fall through to the parser-less stub path.
    """
    global _WORKER_PARSERS
    try:
        from apollo.plugins import discover_plugins
        parsers = discover_plugins()
    except Exception:
        parsers = []
    _WORKER_PARSERS = {_parser_key(p): p for p in parsers if p is not None}


def _parse_one_process(item: tuple) -> dict | None:
    """ProcessPool variant of :func:`_parse_one`.

    Work-item shape is ``(parser_key, src_file, rel_path, source_text,
    file_md5_hex)`` — i.e. identical to the thread-pool tuple except
    the first slot is the stable string key. The worker resolves the
    key against ``_WORKER_PARSERS`` (populated by
    :func:`_init_worker_parsers`) and delegates to :func:`_parse_one`.
    """
    parser_key, src_file, rel_path, source_text, file_md5_hex = item
    parser: BaseParser | None = None
    if parser_key is not None and _WORKER_PARSERS is not None:
        parser = _WORKER_PARSERS.get(parser_key)
    return _parse_one(
        (parser, src_file, rel_path, source_text, file_md5_hex)
    )


def _resolve_parser_pool_mode(
    parsers: list[BaseParser],
    requested: str | None = None,
) -> str:
    """Pick the parser-pool mode for the current build.

    Resolution order (highest precedence first):

    1. Explicit ``requested`` kwarg (``"thread"``/``"process"``/``"sync"``).
    2. ``APOLLO_PARSER_POOL`` environment variable.
    3. Default: ``"process"`` (Phase 2 — uses multiple CPU cores;
       auto-downgrades to ``"thread"`` if any parser opts out).

    If the resolved mode is ``"process"`` but any parser opts out via
    ``getattr(parser, "safe_for_processes", True) is False``, the mode
    is downgraded to ``"thread"`` and a one-line note is logged. This
    keeps plugins with C handles / unpickle-able state correct without
    requiring the caller to know the implementation details.
    """
    mode = (requested
            or os.environ.get("APOLLO_PARSER_POOL")
            or "process").lower()
    if mode not in ("thread", "process", "sync"):
        mode = "process"
    if mode == "process":
        unsafe = [
            type(p).__module__
            for p in parsers
            if getattr(p, "safe_for_processes", True) is False
        ]
        if unsafe:
            # Lazy import to avoid a logger configuration cycle.
            import logging
            logging.getLogger(__name__).info(
                "parser_pool=process requested but unsafe parsers "
                "detected (%s); falling back to thread pool",
                ", ".join(sorted(unsafe)),
            )
            mode = "thread"
    return mode


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
        # Lightweight stat cache populated during ``_discover_files`` so the
        # full-build / sweep paths can populate their hash cache without a
        # second os.walk + per-file ``read()`` + SHA256. Only mtime_ns and
        # size are recorded here — the SHA256 is computed lazily on the
        # next incremental sweep, and *only* for files whose stat actually
        # changed (matching the existing incremental fast-path semantics).
        # See ``FullBuildStrategy.run`` for the consumer.
        self._file_stats: dict[str, dict] = {}
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
        # Precompile the user-glob list into a single anchored regex so
        # the per-filename match in ``_discover_files`` becomes one
        # ``re.search`` instead of N ``fnmatch.fnmatch`` calls. ``None``
        # means "no globs" — short-circuit cheaper than a regex hit.
        if self._ignore_file_globs:
            patterns = "|".join(
                f"(?:{fnmatch.translate(p)})" for p in self._ignore_file_globs
            )
            self._ignore_file_re: re.Pattern | None = re.compile(patterns)
        else:
            self._ignore_file_re = None

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

    def build(self, root_dir: str, embed_queue=None) -> nx.DiGraph:
        """Scan a directory and build the full graph.

        Phase 6 of PLAN_INDEX_MEMORY_AND_CONCURRENCY: when
        ``embed_queue`` is supplied (an
        :class:`apollo.embeddings.embed_queue.EmbedQueue` instance),
        eligible nodes are pushed onto it the moment their file's
        ``_build_file_nodes`` call returns. The queue's background
        worker overlaps embedding compute with the remaining parses,
        so the total ``_do_index`` wall-clock drops from
        ``parse + embed`` to ``max(parse, embed)``. Caller must invoke
        ``embed_queue.close_and_join()`` to drain after ``build``
        returns.
        """
        root = Path(root_dir).resolve()
        if not root.is_dir():
            raise ValueError(f"Not a directory: {root}")

        self._root = root

        # Single walk: discover files and collect directory ancestry
        files_to_parse, dir_set = self._discover_files(root)

        # Build directory nodes lazily from discovered file paths
        self._build_dir_nodes_lazy(root, dir_set)

        # Stream parser-pool output directly into _build_file_nodes;
        # ``_parse_build_resolve_streaming`` returns small resolve
        # records (one per file) instead of the full parsed dicts so
        # the per-file source text + docstrings + sections + … are
        # freed as soon as the file's nodes are in the graph. See
        # PLAN_INDEX_MEMORY_AND_CONCURRENCY §4 (Phase 1). When
        # ``embed_queue`` is supplied, each file's embedding-eligible
        # nodes are enqueued the moment they land in the graph
        # (Phase 6).
        resolve_records = self._parse_build_resolve_streaming(
            files_to_parse, embed_queue=embed_queue,
        )

        # Phase 2: Resolve cross-file edges (symbol table is now
        # fully populated because every _build_file_nodes call ran
        # during streaming).
        assert self._symbol_table is not None, "symbol table missing"
        for rec in resolve_records:
            self._resolve_calls(rec)

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

        # Filter to changed files using stat-based prefilter.
        # ``_discover_files`` already captured (mtime_ns, size) per file
        # during its single os.walk pass, so we read from
        # ``self._file_stats`` instead of stat()'ing each file again.
        files_to_parse: list[tuple[BaseParser, Path, str, str | None, str | None]] = []
        discovered_stats = self._file_stats
        # Phase 9 of PLAN_INDEX_MEMORY_AND_CONCURRENCY — collect
        # rehash jobs (the stat-mismatched files) in one pass, then
        # fan them out via ``_parallel_rehash`` instead of doing the
        # disk read + SHA256 + MD5 inline on the main thread.
        rehash_jobs: list[tuple] = []
        rehash_parser_map: dict[str, BaseParser] = {}
        for parser, src_file, rel_path, _src_text, _md5 in files_to_parse_all:
            cached_st = discovered_stats.get(rel_path)
            if cached_st is None:
                # File vanished between discovery and now — skip silently.
                continue
            cur_mtime = cached_st["mtime_ns"]
            cur_size = cached_st["size"]

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
                    and prev_mtime == cur_mtime
                    and prev_size == cur_size):
                new_hashes[rel_path] = prev
                continue

            # Stat differs — queue for parallel read+hash.
            rehash_jobs.append((src_file, rel_path, cur_mtime, cur_size, prev_sha))
            rehash_parser_map[rel_path] = parser

        # Phase 9: parallel disk-read + SHA256 + MD5 across the
        # stat-mismatched files. The helper transparently falls back
        # to an inline loop for tiny batches.
        for rec in _parallel_rehash(rehash_jobs):
            rel_path = rec["rel_path"]
            new_hashes[rel_path] = rec["new_hash"]
            if not rec["changed"]:
                continue  # Content unchanged despite metadata change
            files_to_parse.append((
                rehash_parser_map[rel_path],
                # _discover_files puts the absolute Path here; reconstruct.
                self._root / rel_path,
                rel_path,
                rec["source_text"],
                rec["file_md5_hex"],
            ))

        # Stream parse → build → resolve (Phase 1). Same memory-saving
        # rationale as ``build()`` above: the per-file ``parsed`` dicts
        # are freed as soon as their nodes land in the graph.
        resolve_records = self._parse_build_resolve_streaming(files_to_parse)

        # Phase 2: Resolve cross-file edges
        for rec in resolve_records:
            self._resolve_calls(rec)

        return self.graph, new_hashes

    def _discover_files(
        self, root: Path
    ) -> tuple[list[tuple[BaseParser, Path, str, None, None]], set[str]]:
        """Single os.walk pass: discover parseable files and their directories.

        Performance notes
        -----------------
        * Skip-list / venv checks are ordered cheapest-first so the common
          path (``d.startswith(".")`` or a member of ``_skip_dirs``)
          short-circuits before we ever ``stat()`` for a venv marker.
        * Per-dir ``rel_dir`` is computed by string-slicing
          ``dirpath`` — ``os.path.relpath`` re-splits both arguments
          which is order-of-magnitude slower for the deeply-nested calls
          this loop makes.
        * The plugin-contributed ignore globs were re-evaluated per
          (file × pattern) pair via ``fnmatch.fnmatch``. They're now
          combined into a single precompiled regex (``_ignore_file_re``)
          built once in ``__init__``.
        """
        files: list[tuple[BaseParser, Path, str, None, None]] = []
        dir_set: set[str] = set()
        dir_set.add("")  # root directory
        # Reset the stat cache for this discovery pass — re-using the same
        # builder for a second project would otherwise leak entries.
        self._file_stats = {}

        root_str = str(root)
        root_prefix_len = len(root_str) + 1  # +1 for the path separator
        skip_dirs = self._skip_dirs
        venv_markers = self._venv_markers
        has_venv_markers = bool(venv_markers)
        ignore_file_re = self._ignore_file_re
        has_filters = self._filters is not None
        file_stats = self._file_stats  # local alias — hot loop

        for dirpath, dirnames, filenames in os.walk(root):
            # Compute this directory's path relative to root once via a
            # cheap slice. ``dirpath == root_str`` is the project root.
            if dirpath == root_str:
                rel_dir_here = ""
            else:
                rel_dir_here = dirpath[root_prefix_len:]

            kept = []
            for d in dirnames:
                # Cheapest checks first — string ops, then set membership,
                # only fall through to ``stat()``-based venv detection
                # for directories that survived both filters.
                if d.startswith(".") or d in skip_dirs:
                    continue
                if has_venv_markers and _is_venv_dir(
                    os.path.join(dirpath, d), venv_markers
                ):
                    continue
                if has_filters:
                    rel_dir = f"{rel_dir_here}/{d}" if rel_dir_here else d
                    if not self._is_dir_included(rel_dir):
                        continue
                kept.append(d)
            dirnames[:] = kept
            dirnames.sort()

            for fname in filenames:
                if fname.startswith("."):
                    continue
                # Plugin-contributed file globs (e.g. python3 → ``*.pyc``)
                # collapsed into one precompiled regex.
                if ignore_file_re is not None and ignore_file_re.match(fname):
                    continue

                # Build rel_path by slicing the joined absolute path —
                # avoids ``Path.relative_to`` which re-walks both halves.
                if rel_dir_here:
                    rel_path = f"{rel_dir_here}/{fname}"
                else:
                    rel_path = fname

                # User filters: extension whitelist + glob excludes.
                if has_filters and not self._is_file_included(rel_path):
                    continue

                src_file = Path(dirpath) / fname

                # Capture stat once during discovery so callers (the full
                # build path, the resolve-full sweep) don't have to re-walk
                # the tree just to fill their hash cache. We *only* record
                # mtime_ns + size here — the SHA256 stays unset and is
                # computed lazily on the next incremental sweep, and only
                # for files whose stat actually changed. This is the
                # "Do less" win: avoids reading every file a second time
                # and SHA256-ing it on first index of a large project.
                try:
                    st = src_file.stat()
                    file_stats[rel_path] = {
                        "mtime_ns": st.st_mtime_ns,
                        "size": st.st_size,
                    }
                except OSError:
                    # Vanished mid-walk / permission error — skip silently;
                    # the parse step will see the same OSError and drop it.
                    pass

                # Every file becomes a node. A parser is optional — files
                # without one are still indexed as plain `file` nodes so
                # they appear in the tree / can be inspected.
                # 5-tuple: (parser, src_file, rel_path, source_text,
                # file_md5_hex). The full build doesn't pre-read bytes
                # so source_text and file_md5_hex are both None.
                parser = self._find_parser(str(src_file))
                files.append((parser, src_file, rel_path, None, None))

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
        self,
        files: list[tuple[BaseParser, Path, str, str | None, str | None]],
    ) -> list[dict]:
        """Parse files concurrently using a thread pool.

        .. deprecated:: Phase 1 (PLAN_INDEX_MEMORY_AND_CONCURRENCY)
           Prefer :meth:`_parse_build_resolve_streaming` for the main
           build paths — it streams parser-pool output directly into
           ``_build_file_nodes`` so the large ``parsed`` dicts can be
           garbage-collected mid-build. This method is kept for callers
           that still want a materialized list (e.g. tests, ad-hoc
           tooling).
        """
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

    def _parse_build_resolve_streaming(
        self,
        files: list[tuple[BaseParser, Path, str, str | None, str | None]],
        embed_queue=None,
        parser_pool: str | None = None,
    ) -> list[dict]:
        """Stream the parser pool output into ``_build_file_nodes`` and
        return a list of *minimal resolve records*.

        Phase 1 of PLAN_INDEX_MEMORY_AND_CONCURRENCY — stops holding the
        full ``parsed_files`` list in RAM while the graph builds.

        Each future's ``parsed`` dict is consumed as soon as it
        completes: nodes are written into ``self.graph``, then a tiny
        :func:`_minimal_resolve_record` is extracted, and the original
        ``parsed`` dict goes out of scope. For a 10 k-file project this
        replaces hundreds of MB of per-file source-text dicts kept alive
        through resolve with a few-KB-per-file resolve record (only
        ``imports`` + ``functions[name,calls]`` +
        ``classes[name,methods[name,calls]]``).

        Phase 6: when ``embed_queue`` is supplied, eligible nodes for
        the just-built file are pushed onto the queue right after
        ``_build_file_nodes`` returns. We read the source strings off
        the parser-supplied ``parsed`` dict (they're still alive at
        this point — they're consumed by ``_build_file_nodes`` for
        ``source_md5`` but dropped from the node attrs by Phase 4).
        Using the parser strings directly avoids a redundant
        file-text slice via :func:`graph.query.get_source` and keeps
        the enqueue path on the critical streaming-build loop fast.

        Phase 2: ``parser_pool`` selects the executor flavour
        (``"thread"`` / ``"process"`` / ``"sync"``). ``None`` falls
        back to the ``APOLLO_PARSER_POOL`` env var and finally to
        ``"thread"`` for backward compatibility. Process-mode parses
        on a ``ProcessPoolExecutor`` to side-step the GIL on AST-bound
        plugins (python3, markdown, …); sync-mode runs in the caller's
        thread (useful for deterministic test output / debugging).

        Returns
        -------
        list[dict]
            Resolve records (one per parsed file). The caller iterates
            these and calls :meth:`_resolve_calls` on each.
        """
        if not files:
            return []

        mode = _resolve_parser_pool_mode(self._parsers, parser_pool)
        records: list[dict] = []

        # ── sync mode: no pool, no pickling overhead, simplest GC path ──
        if mode == "sync":
            for item in files:
                try:
                    parsed = _parse_one(item)
                except Exception:
                    continue
                if parsed is None:
                    continue
                rel_path = parsed["rel_path"]
                self._build_file_nodes(parsed, rel_path)
                if embed_queue is not None:
                    _push_embeds_to_queue(parsed, rel_path, embed_queue)
                records.append(_minimal_resolve_record(parsed))
                del parsed
            return records

        # ── process mode: per-item key swap so parser instances stay
        # in the parent and the worker rebuilds its own copy ───────────
        if mode == "process":
            # Cap at len(files) so we don't spawn 16 workers for a
            # 3-file rebuild — the spawn cost would dwarf the parse.
            max_workers = min(len(files), os.cpu_count() or 4)
            # Allow tests/bench to override via env without touching
            # the call site (matches the legacy ``APOLLO_PARSER_MAX_WORKERS``).
            try:
                env_cap = int(os.environ.get("APOLLO_PARSER_MAX_WORKERS", "0"))
            except ValueError:
                env_cap = 0
            if env_cap > 0:
                max_workers = min(max_workers, env_cap)
            # Swap each item's parser instance for its stable key so
            # the work-item tuple is fully picklable.
            keyed_items = [
                (_parser_key(p), src, rel, src_text, md5)
                for (p, src, rel, src_text, md5) in files
            ]
            with ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=_init_worker_parsers,
            ) as executor:
                # ``chunksize`` amortizes the per-task pickling cost so
                # tiny files don't pay full IPC overhead each.
                for parsed in executor.map(
                    _parse_one_process, keyed_items, chunksize=8,
                ):
                    if parsed is None:
                        continue
                    rel_path = parsed["rel_path"]
                    self._build_file_nodes(parsed, rel_path)
                    if embed_queue is not None:
                        _push_embeds_to_queue(parsed, rel_path, embed_queue)
                    records.append(_minimal_resolve_record(parsed))
                    del parsed
            return records

        # ── thread mode (default / legacy behavior) ────────────────────
        max_workers = min(len(files), os.cpu_count() or 4)
        try:
            env_cap = int(os.environ.get("APOLLO_PARSER_MAX_WORKERS", "0"))
        except ValueError:
            env_cap = 0
        if env_cap > 0:
            max_workers = min(max_workers, env_cap)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_parse_one, f) for f in files]
            for future in as_completed(futures):
                try:
                    parsed = future.result()
                except Exception:
                    continue
                if parsed is None:
                    continue
                rel_path = parsed["rel_path"]
                # Build nodes *now*, while the parsed dict is still alive,
                # then immediately extract the tiny resolve record. The
                # parsed dict drops out of scope on the next loop iteration
                # — including the per-function/class ``source`` strings,
                # docstrings, markdown sections, code blocks, etc.
                self._build_file_nodes(parsed, rel_path)
                if embed_queue is not None:
                    # Phase 6: enqueue *after* the nodes are in the
                    # graph so the worker's
                    # ``self.graph.nodes[nid]["embedding"] = ...``
                    # never races against ``add_node``.
                    _push_embeds_to_queue(parsed, rel_path, embed_queue)
                records.append(_minimal_resolve_record(parsed))
                # Defensive: drop our local reference too so the GC has
                # nothing holding the dict alive at the top of the loop.
                del parsed
        return records

    def _find_parser(self, filepath: str) -> BaseParser | None:
        """Return the first parser that can handle *filepath*."""
        for parser in self._parsers:
            if parser.can_parse(filepath):
                return parser
        return None

    def _build_file_nodes(self, parsed: dict, rel_path: str):
        """Create file, function, class, and import nodes from parsed data."""
        file_id = f"file::{rel_path}"

        # Phase 4 of PLAN_INDEX_MEMORY_AND_CONCURRENCY: stash the file's
        # full source text in a graph-level sidecar map so we can drop
        # the per-node ``source`` attr that otherwise duplicates the
        # same characters across every func/method/class/section node.
        # ``_parse_one`` attaches ``_full_file_text`` to the parsed
        # dict; pop it here so the temporary key doesn't leak into any
        # downstream consumer of the parsed dict (the resolve records
        # already exclude it).
        full_text = parsed.pop("_full_file_text", None)
        if isinstance(full_text, str):
            ft_map = self.graph.graph.setdefault("_file_text", {})
            ft_map[rel_path] = full_text

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
            # Phase 4: no per-node ``source`` attr — readers slice from
            # ``graph.graph["_file_text"]`` via :func:`graph.query.get_source`
            # using the ``line_start``/``line_end`` range stored below.
            self.graph.add_node(
                func_id,
                type="function",
                name=func["name"],
                path=rel_path,
                line_start=func["line_start"],
                line_end=func["line_end"],
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
            # Phase 4: see comment above on functions — source is now
            # sliced lazily from ``_file_text``.
            self.graph.add_node(
                class_id,
                type="class",
                name=cls["name"],
                path=rel_path,
                line_start=cls["line_start"],
                line_end=cls["line_end"],
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
                # Phase 4: no per-node ``source`` attr — see comment on
                # functions above. ``line_start``/``line_end`` are kept
                # so :func:`graph.query.get_source` can slice the
                # file-text sidecar on demand.
                self.graph.add_node(
                    method_id,
                    type="method",
                    name=method["name"],
                    path=rel_path,
                    line_start=method["line_start"],
                    line_end=method["line_end"],
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

        # Imports. Plugin emit shapes vary: the python3 plugin always
        # ships a ``names`` list, but doc-style plugins (markdown_gfm,
        # html5, …) emit ``{module, alias, line, kind}`` without
        # ``names``. Read defensively so any plugin's import contract
        # works without crashing the build.
        self._file_imports[rel_path] = parsed["imports"]
        for imp in parsed["imports"]:
            names = imp.get("names") or []
            module = imp.get("module", "")
            line = imp.get("line", 0)
            if names:
                label = f"from {module} import {', '.join(names)}"
            else:
                label = f"import {module}"
            imp_id = f"import::{rel_path}::{module}::L{line}"
            self.graph.add_node(
                imp_id,
                type="import",
                name=label,
                path=rel_path,
                module=module,
                names=names,
                line=line,
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
            # Phase 4: document content is a slice of the file-text
            # sidecar; ``get_source`` will return it on demand.
            self.graph.add_node(
                doc_id,
                type="document",
                name=doc["name"],
                doc_type=doc["doc_type"],
                path=rel_path,
                line_start=doc["line_start"],
                line_end=doc["line_end"],
                frontmatter=parsed.get("frontmatter"),
                title=parsed.get("title"),
            )
            self.graph.add_edge(file_id, doc_id, type="defines")

        # Markdown sections (heading-based hierarchy)
        for sec in parsed.get("sections", []):
            sec_id = f"section::{rel_path}::L{sec['line_start']}"
            # Phase 4: section content is reconstructed from the
            # file-text sidecar on read; ``line_start``/``line_end``
            # span the heading + body region the parser identified.
            self.graph.add_node(
                sec_id,
                type="section",
                name=sec["name"],
                path=rel_path,
                level=sec["level"],
                line_start=sec["line_start"],
                line_end=sec["line_end"],
                parent_section=sec.get("parent_section"),
            )
            self.graph.add_edge(file_id, sec_id, type="defines")

        # Markdown code blocks (embedded code snippets)
        for cb in parsed.get("code_blocks", []):
            cb_id = f"codeblock::{rel_path}::L{cb['line_start']}"
            label = f"```{cb['language']}" if cb.get("language") else "```"
            # Phase 4: code-block content is sliced lazily from
            # ``_file_text``. The sliced region includes the ``` fence
            # lines, which is a small textual difference vs. the
            # pre-Phase-4 ``cb["content"]`` (which the parser stripped);
            # this is acceptable for embedding/keyword purposes — the
            # ~6 extra fence chars do not meaningfully change the
            # semantic-search behaviour.
            self.graph.add_node(
                cb_id,
                type="code_block",
                name=label,
                path=rel_path,
                language=cb.get("language"),
                line_start=cb["line_start"],
                line_end=cb["line_end"],
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

        # Build a local import map: short_name -> qualified module path.
        # Read import fields defensively because non-Python plugins
        # (markdown_gfm, html5) omit ``names`` from their import dicts.
        import_map: dict[str, str] = {}
        for imp in parsed["imports"]:
            names = imp.get("names") or []
            module = imp.get("module", "")
            if names:
                for name in names:
                    import_map[name] = f"{module}.{name}"
            else:
                parts = module.split(".")
                short = imp.get("alias") or (parts[-1] if parts else module)
                import_map[short] = module

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
