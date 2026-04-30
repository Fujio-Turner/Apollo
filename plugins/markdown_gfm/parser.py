"""
plugins.markdown_gfm — GitHub Flavored Markdown plugin for Apollo
=================================================================

This plugin parses Markdown files (``.md`` / ``.markdown``) into a
structured result dictionary so Apollo can index, search, and visualize
documentation alongside source code. It is one of the two reference
plugins (the other is :mod:`plugins.python3`) and is a good template for
any structured-text or markup-style language plugin.

"GFM" in the module name means **GitHub Flavored Markdown**: we enable
mistune's ``table`` and ``task_lists`` plugins so that pipe tables and
``- [ ]`` checkboxes parse correctly. Strict CommonMark or other
flavors (Pandoc, MultiMarkdown, AsciiDoc, …) would be a separate plugin
file alongside this one — see ``guides/making_plugins.md``.

What this plugin extracts
-------------------------
For every parseable Markdown file, :meth:`MarkdownParser.parse_source`
returns a ``dict`` shaped like this::

    {
        "file":         str,
        # Code-shape keys (matches python3 / html5 schema):
        "functions":    [],
        "classes":      [],
        "imports":      [ {module, alias, line, kind} ],
        "variables":    [],
        "comments":     [ {tag, content, line} ],
        # Markdown-specific keys:
        "documents":    [ {name, doc_type, content, line_start, line_end} ],
        "sections":     [ {name, level, line_start, line_end, content,
                           parent_section, anchor} ],
        "code_blocks":  [ {language, content, line_start, line_end} ],
        "links":        [ {url, text, is_image, line, link_type} ],
        "wikilinks":    [ {target, alias, line} ],
        "callouts":     [ {kind, title, content,
                           line_start, line_end} ],
        "tables":       [ {headers, rows, line_start, line_end} ],
        "task_items":   [ {text, checked, line} ],
        "frontmatter":  dict | None,   # YAML/TOML metadata block
        "title":        str | None,    # frontmatter "title" or first H1
    }

Imports & the doc graph
-----------------------
Markdown's ``imports`` are the *cross-document graph*: relative links
to ``other.md``, image/asset references, and ``[[wikilinks]]`` are all
turned into ``imports`` entries with a ``kind`` ("doc", "image",
"asset", "wikilink") so the graph builder draws doc→doc and doc→asset
edges the same way it draws Python ``import`` edges.

Design notes
------------
* **Whole document for embeddings.** We always include a single
  ``documents`` entry containing the full raw text. This is what the
  embedding pipeline consumes; the structured fields above are what the
  graph and UI consume.
* **Section hierarchy.** :meth:`_extract_sections` uses a stack-based
  walk over headings to compute each section's parent, so that a level-3
  heading correctly nests under the most recent level-2.
* **Line numbers.** mistune's AST does not carry line numbers, so we
  rediscover them by string-searching the original source. Helpers like
  :func:`_find_heading_line`, :func:`_find_text_line`, and
  :func:`_find_block_lines` do that recovery.
* **Failure mode is silent.** Files larger than ``_MAX_FILE_SIZE`` and
  unreadable files return ``None`` — the caller falls back to the
  generic text indexer.

Dependencies
------------
* `mistune <https://mistune.lepture.com/>`_ v3 — the AST renderer.
* `python-frontmatter <https://python-frontmatter.readthedocs.io/>`_ —
  parses YAML/TOML frontmatter blocks at the top of a file.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import frontmatter
import mistune
from mistune.plugins.table import table as plugin_table
from mistune.plugins.task_lists import task_lists as plugin_task_lists

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------

# File extensions this plugin claims. ``.markdown`` is rare but valid.
_MD_EXTENSIONS: frozenset[str] = frozenset({".md", ".markdown"})

# Maximum file size we'll attempt to read (1 MB). Larger files are
# rejected — they're almost always generated artifacts (changelogs from
# bots, exported wikis, etc.) and parsing them slows indexing without
# adding much value.
_MAX_FILE_SIZE = 1_048_576

# Regex for locating a markdown heading line (used for line-number
# recovery when mistune's AST doesn't carry line info).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# Wikilinks: ``[[target]]`` or ``[[target|alias]]``. Used by Obsidian,
# Foam, GitHub wikis, and many other personal-knowledge-base systems.
_WIKILINK_RE = re.compile(
    r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]"
)

# Trailing ``{#anchor}`` on a heading line (kramdown / pandoc).
_HEADING_ANCHOR_RE = re.compile(r"\s*\{#([\w\-]+)\}\s*$")

# HTML comments — used for ``<!-- TODO: ... -->`` markers in prose.
_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)

# Same TODO/FIXME pattern as the python3 / html5 plugins so consumers
# can find tagged comments uniformly across languages.
_COMMENT_TAG_RE = re.compile(
    r"\b(TODO|FIXME|NOTE|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE
)
_BLOCKQUOTE_TAG_RE = re.compile(
    r"^\s*>\s*(TODO|FIXME|NOTE|HACK|XXX)\b[:\s]*(.*)$", re.IGNORECASE
)

# Callout / admonition flavours we recognise. All are case-insensitive.
# GFM:    ``> [!NOTE] Optional title\n> body``
# MkDocs: ``!!! warning "Optional title"\n    body``
# Pandoc: ``:::note Optional title\nbody\n:::``
_GFM_CALLOUT_RE = re.compile(
    r"^\s*>\s*\[!(?P<kind>NOTE|TIP|WARNING|IMPORTANT|CAUTION|DANGER)\]"
    r"(?:\s+(?P<title>.*))?$",
    re.IGNORECASE,
)
_GFM_CALLOUT_BODY_RE = re.compile(r"^\s*>\s?(.*)$")
_MKDOCS_CALLOUT_RE = re.compile(
    r'^\s*!!!\s+(?P<kind>[\w\-]+)(?:\s+"(?P<title>[^"]*)")?\s*$'
)
_PANDOC_FENCE_OPEN_RE = re.compile(
    r"^\s*:::\s*(?P<kind>[\w\-]+)(?:\s+(?P<title>.*))?$"
)
_PANDOC_FENCE_CLOSE_RE = re.compile(r"^\s*:::\s*$")

# Extensions that map to typed import kinds for the graph layer.
_IMPORT_DOC_EXTS: frozenset[str] = frozenset({".md", ".markdown"})
_IMPORT_IMAGE_EXTS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp"}
)
_IMPORT_STYLE_EXTS: frozenset[str] = frozenset({".css", ".scss", ".less"})
_IMPORT_SCRIPT_EXTS: frozenset[str] = frozenset({".js", ".mjs", ".ts", ".tsx"})


class MarkdownParser(BaseParser):
    """
    Parse a Markdown file into Apollo's structured result dict.

    Quick start
    -----------
    ::

        parser = MarkdownParser()
        if parser.can_parse("README.md"):
            data = parser.parse_file("README.md")
            print(data["title"])
            for section in data["sections"]:
                print(" " * section["level"], section["name"])

    Pipeline (``parse_source`` → :meth:`_parse_raw`)
    -----------------------------------------------
    1. **Frontmatter** — ``python-frontmatter`` strips and parses any
       leading ``---`` YAML/TOML block.
    2. **AST** — mistune produces a list of dicts; we keep its native
       shape rather than wrapping it in objects.
    3. **Walks** — six small extractors collect sections, code blocks,
       links, tables, task items, and the document title.
    4. **Document entry** — a single whole-file ``documents`` entry is
       always added so the embeddings pipeline gets something to index.

    Each ``_extract_*`` method is independent and side-effect free; you
    can replace one without touching the others.
    """

    #: Source-of-truth defaults for this plugin's runtime knobs. Mirrors
    #: ``plugins/markdown_gfm/config.json``. The merged config (defaults
    #: ⊕ user override) is stored on ``self.config`` after construction.
    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".md", ".markdown"],
        "max_file_size_bytes": _MAX_FILE_SIZE,
        "extract_frontmatter": True,
        "extract_sections": True,
        "extract_code_blocks": True,
        "extract_links": True,
        "extract_wikilinks": True,
        "extract_callouts": True,
        "extract_tables": True,
        "extract_task_items": True,
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "ignore_dirs": [".obsidian", ".trash"],
        "ignore_files": [],
        "ignore_dir_markers": [],
    }

    def __init__(self, config: dict | None = None) -> None:
        """Initialise the parser with its merged config dict."""
        merged = dict(self.DEFAULT_CONFIG)
        if config:
            merged.update(config)
        self.config: dict = merged
        self._extensions = frozenset(
            ext.lower() for ext in (self.config.get("extensions") or _MD_EXTENSIONS)
        )

        # Build the AST renderer once per parser instance. ``table`` and
        # ``task_lists`` are mistune plugins (not Apollo plugins!) that
        # add GFM-style support for ``| col |`` tables and ``- [ ]``
        # checkboxes respectively.
        self._md = mistune.create_markdown(
            renderer="ast",
            plugins=[plugin_table, plugin_task_lists],
        )

    # ------------------------------------------------------------------
    # BaseParser interface
    # ------------------------------------------------------------------

    @property
    def _max_size(self) -> int:
        """Effective ``max_file_size_bytes`` from config (with default)."""
        return int(self.config.get("max_file_size_bytes") or _MAX_FILE_SIZE)

    def can_parse(self, filepath: str) -> bool:
        """Return True for configured Markdown extensions (case-insensitive).

        Returns ``False`` when the plugin has been disabled via config.
        """
        if not self.config.get("enabled", True):
            return False
        return Path(filepath).suffix.lower() in self._extensions

    def parse_file(self, filepath: str) -> dict | None:
        """Read *filepath* from disk and delegate to :meth:`_parse_raw`.

        Returns ``None`` for the wrong extension, files larger than
        ``self.config["max_file_size_bytes"]``, or any I/O error.
        """
        path = Path(filepath)
        if path.suffix.lower() not in self._extensions:
            return None

        try:
            size = path.stat().st_size
            if size > self._max_size:
                logger.debug("skipping %s: %d bytes exceeds limit", path, size)
                return None
            raw = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("failed to read %s: %s", path, exc)
            return None

        return self._parse_raw(raw, str(path))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse from an already-loaded source string.

        Skipped when the path's extension isn't markdown, or the source
        exceeds ``self.config["max_file_size_bytes"]`` characters.
        """
        if Path(filepath).suffix.lower() not in self._extensions:
            return None
        if len(source) > self._max_size:
            return None
        return self._parse_raw(source, filepath)

    def _parse_raw(self, raw: str, filepath: str) -> dict | None:
        """Run the full extraction pipeline on a raw Markdown string.

        Empty/whitespace-only input returns ``None`` so the caller can
        treat the file as unparseable. All exceptions thrown by
        ``frontmatter`` are swallowed (we just treat the file as having
        no frontmatter) so a malformed YAML block doesn't kill indexing
        of an otherwise-valid document.
        """
        if not raw.strip():
            return None

        # 1. Frontmatter -------------------------------------------------
        if self.config.get("extract_frontmatter", True):
            try:
                post = frontmatter.loads(raw)
                fm = dict(post.metadata) if post.metadata else None
                body = post.content  # content without frontmatter
            except Exception:
                logger.debug("frontmatter parse failed for %s; treating as no frontmatter", filepath)
                fm = None
                body = raw
        else:
            fm = None
            body = raw

        # 2. AST ----------------------------------------------------------
        ast_nodes: list[dict] = self._md(body)

        # 3. Line lookup helpers ------------------------------------------
        lines = body.split("\n")

        # 4. Walk the AST once and extract everything (each extractor is
        #    independently gated by its ``extract_<thing>`` config key).
        sections = (
            self._extract_sections(ast_nodes, body, lines)
            if self.config.get("extract_sections", True)
            else []
        )
        code_blocks = (
            self._extract_code_blocks(ast_nodes, body, lines)
            if self.config.get("extract_code_blocks", True)
            else []
        )
        links = (
            self._extract_links(ast_nodes, body, lines)
            if self.config.get("extract_links", True)
            else []
        )
        tables = (
            self._extract_tables(ast_nodes, body, lines)
            if self.config.get("extract_tables", True)
            else []
        )
        task_items = (
            self._extract_task_items(ast_nodes, body, lines)
            if self.config.get("extract_task_items", True)
            else []
        )

        # 4b. Markdown-native extras (regex-based, ignore code fences) ----
        wikilinks = (
            _extract_wikilinks(lines)
            if self.config.get("extract_wikilinks", True)
            else []
        )
        callouts = (
            _extract_callouts(lines)
            if self.config.get("extract_callouts", True)
            else []
        )
        comments = (
            _extract_comments(body, lines, tags=self.config.get("comment_tags"))
            if self.config.get("extract_comments", True)
            else []
        )
        imports = _build_imports(links, wikilinks)

        # 5. Title --------------------------------------------------------
        title = self._derive_title(fm, sections)

        # 6. Whole-document entry (same shape as TextFileParser) ----------
        total_lines = raw.count("\n") + 1
        documents = [
            {
                "name": Path(filepath).name,
                "doc_type": "markdown",
                "content": raw,
                "line_start": 1,
                "line_end": total_lines,
            }
        ]

        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": imports,
            "variables": [],
            "comments": comments,
            "documents": documents,
            "sections": sections,
            "code_blocks": code_blocks,
            "links": links,
            "wikilinks": wikilinks,
            "callouts": callouts,
            "tables": tables,
            "task_items": task_items,
            "frontmatter": fm,
            "title": title,
        }

    # ------------------------------------------------------------------
    # Section extraction (heading hierarchy)
    # ------------------------------------------------------------------

    def _extract_sections(
        self, ast_nodes: list[dict], body: str, lines: list[str],
    ) -> list[dict]:
        headings: list[dict] = []
        for node in ast_nodes:
            if node.get("type") != "heading":
                continue
            text = _collect_text(node)
            level = node["attrs"]["level"]
            line_start = _find_heading_line(lines, level, text)
            # Strip an explicit ``{#anchor}`` suffix from the heading
            # name (kramdown / pandoc style) and remember it; otherwise
            # auto-slug the heading text the way GitHub does so anchor
            # links resolve against the same id either way.
            name, anchor = _split_heading_anchor(text)
            headings.append({
                "name": name,
                "level": level,
                "line_start": line_start,
                "anchor": anchor,
            })

        if not headings:
            return []

        # Sort by line_start to ensure correct ordering.
        headings.sort(key=lambda h: h["line_start"])

        total_lines = len(lines)
        sections: list[dict] = []
        # Stack stores (level, heading_text) for hierarchy tracking.
        stack: list[tuple[int, str]] = []

        for idx, h in enumerate(headings):
            # Determine line_end: up to (but not including) the next heading,
            # or the last line of the document.
            if idx + 1 < len(headings):
                line_end = headings[idx + 1]["line_start"] - 1
            else:
                line_end = total_lines

            # Strip trailing blank lines.
            while line_end > h["line_start"] and not lines[line_end - 1].strip():
                line_end -= 1

            content = "\n".join(lines[h["line_start"] - 1 : line_end])

            # Build parent via stack.
            while stack and stack[-1][0] >= h["level"]:
                stack.pop()
            parent_section = stack[-1][1] if stack else None
            stack.append((h["level"], h["name"]))

            sections.append({
                "name": h["name"],
                "level": h["level"],
                "line_start": h["line_start"],
                "line_end": line_end,
                "content": content,
                "parent_section": parent_section,
                "anchor": h["anchor"],
            })

        return sections

    # ------------------------------------------------------------------
    # Code blocks
    # ------------------------------------------------------------------

    def _extract_code_blocks(
        self, ast_nodes: list[dict], body: str, lines: list[str],
    ) -> list[dict]:
        blocks: list[dict] = []
        search_start = 0
        for node in ast_nodes:
            if node.get("type") != "block_code":
                continue
            code = node.get("raw", "")
            lang = (node.get("attrs") or {}).get("info") or None

            line_start, line_end, search_start = _find_block_lines(
                body, code, lines, search_start, fenced=node.get("style") == "fenced",
            )
            blocks.append({
                "language": lang,
                "content": code,
                "line_start": line_start,
                "line_end": line_end,
            })
        return blocks

    # ------------------------------------------------------------------
    # Links & images
    # ------------------------------------------------------------------

    def _extract_links(
        self, ast_nodes: list[dict], body: str, lines: list[str],
    ) -> list[dict]:
        links: list[dict] = []
        self._walk_for_links(ast_nodes, body, lines, links)
        return links

    def _walk_for_links(
        self,
        nodes: list[dict],
        body: str,
        lines: list[str],
        out: list[dict],
    ) -> None:
        for node in nodes:
            ntype = node.get("type")
            if ntype in ("link", "image"):
                url = (node.get("attrs") or {}).get("url", "")
                text = _collect_text(node)
                is_image = ntype == "image"
                link_type = _classify_link(url)
                line = _find_text_line(lines, url)
                out.append({
                    "url": url,
                    "text": text,
                    "is_image": is_image,
                    "line": line,
                    "link_type": link_type,
                })
            children = node.get("children")
            if children and isinstance(children, list):
                self._walk_for_links(children, body, lines, out)

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------

    def _extract_tables(
        self, ast_nodes: list[dict], body: str, lines: list[str],
    ) -> list[dict]:
        tables: list[dict] = []
        for node in ast_nodes:
            if node.get("type") != "table":
                continue
            headers: list[str] = []
            rows: list[list[str]] = []
            for child in node.get("children", []):
                if child.get("type") == "table_head":
                    for cell in child.get("children", []):
                        headers.append(_collect_text(cell))
                elif child.get("type") == "table_body":
                    for row_node in child.get("children", []):
                        row: list[str] = []
                        for cell in row_node.get("children", []):
                            row.append(_collect_text(cell))
                        rows.append(row)

            # Find the table in the raw text using the first header.
            if headers:
                line_start = _find_text_line(lines, headers[0])
            else:
                line_start = 1

            # Table spans: header row + separator + data rows.
            table_height = 2 + len(rows)  # head + separator + body rows
            line_end = min(line_start + table_height - 1, len(lines))

            tables.append({
                "headers": headers,
                "rows": rows,
                "line_start": line_start,
                "line_end": line_end,
            })
        return tables

    # ------------------------------------------------------------------
    # Task items
    # ------------------------------------------------------------------

    def _extract_task_items(
        self, ast_nodes: list[dict], body: str, lines: list[str],
    ) -> list[dict]:
        items: list[dict] = []
        self._walk_for_tasks(ast_nodes, body, lines, items)
        return items

    def _walk_for_tasks(
        self,
        nodes: list[dict],
        body: str,
        lines: list[str],
        out: list[dict],
    ) -> None:
        for node in nodes:
            if node.get("type") == "task_list_item":
                text = _collect_text(node)
                checked = (node.get("attrs") or {}).get("checked", False)
                line = _find_text_line(lines, text)
                out.append({
                    "text": text,
                    "checked": checked,
                    "line": line,
                })
            children = node.get("children")
            if children and isinstance(children, list):
                self._walk_for_tasks(children, body, lines, out)

    # ------------------------------------------------------------------
    # Title derivation
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_title(
        fm: dict | None, sections: list[dict],
    ) -> str | None:
        if fm and "title" in fm:
            return str(fm["title"])
        for s in sections:
            if s["level"] == 1:
                return s["name"]
        return None


# ======================================================================
# Module-level helpers
# ======================================================================

def _collect_text(node: dict) -> str:
    """Recursively collect raw text from an AST node and its children."""
    parts: list[str] = []
    raw = node.get("raw")
    if raw:
        parts.append(raw)
    for child in node.get("children", []):
        parts.append(_collect_text(child))
    return "".join(parts)


def _classify_link(url: str) -> str:
    """Bucket a link URL into one of three coarse categories.

    Returns one of:

    * ``"external"`` — absolute ``http(s)://`` URL.
    * ``"anchor"``   — same-document fragment (``#section``).
    * ``"internal"`` — anything else (relative paths, ``mailto:``, etc.).

    The graph layer uses this to colour links in the UI and to skip
    crawling external URLs during reverse-link resolution.
    """
    if url.startswith(("http://", "https://")):
        return "external"
    if url.startswith("#"):
        return "anchor"
    return "internal"


def _find_heading_line(
    lines: list[str], level: int, text: str,
) -> int:
    """Find the 1-based line number of a heading in the raw lines."""
    prefix = "#" * level + " "
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(prefix) and text in stripped:
            return idx + 1
    return 1


def _find_text_line(lines: list[str], text: str) -> int:
    """Find the 1-based line number containing *text*."""
    for idx, line in enumerate(lines):
        if text in line:
            return idx + 1
    return 1


def _iter_non_code_lines(lines: list[str]):
    """Yield ``(idx, line)`` for lines that are *not* inside a fenced
    code block.

    All the regex-based extractors below skip code fences so a
    ``[[wikilink]]`` example in a code block doesn't get mistaken for
    a real link, etc.
    """
    in_code = False
    fence: str | None = None
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if not in_code:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_code = True
                fence = stripped[:3]
                continue
            yield idx, line
        else:
            if fence is not None and stripped.startswith(fence):
                in_code = False
                fence = None
            continue


def _extract_wikilinks(lines: list[str]) -> list[dict]:
    """Find ``[[target]]`` / ``[[target|alias]]`` outside code fences."""
    out: list[dict] = []
    for idx, line in _iter_non_code_lines(lines):
        for m in _WIKILINK_RE.finditer(line):
            target = m.group(1).strip()
            alias = (m.group(2) or "").strip() or None
            out.append({
                "target": target,
                "alias": alias,
                "line": idx + 1,
            })
    return out


def _build_md_comment_re(tags) -> tuple[re.Pattern, re.Pattern]:
    """Build (inline, blockquote) regexes for the configured tag set."""
    if not tags:
        return _COMMENT_TAG_RE, _BLOCKQUOTE_TAG_RE
    alt = "|".join(re.escape(t) for t in tags)
    inline = re.compile(rf"\b({alt})\b[:\s]*(.*)", re.IGNORECASE)
    blockquote = re.compile(
        rf"^\s*>\s*({alt})\b[:\s]*(.*)$", re.IGNORECASE
    )
    return inline, blockquote


def _extract_comments(body: str, lines: list[str], tags=None) -> list[dict]:
    """Find tagged comments — ``<!-- TODO: … -->`` and ``> TODO:`` —
    using the same tag set as the python3 / html5 plugins.

    *tags* is the list of tag names to recognise; ``None`` falls back to
    the default ``TODO/FIXME/NOTE/HACK/XXX`` set so older callers keep
    working.
    """
    inline_re, bq_re = _build_md_comment_re(tags)
    out: list[dict] = []

    # 1. HTML comments anywhere in the body.
    for m in _HTML_COMMENT_RE.finditer(body):
        inner = m.group(1)
        line = body[: m.start()].count("\n") + 1
        tag_m = inline_re.search(inner)
        if tag_m:
            out.append({
                "tag": tag_m.group(1).upper(),
                "content": tag_m.group(2).strip(),
                "line": line,
            })

    # 2. Tagged blockquote lines (``> TODO: …``) outside code fences.
    for idx, line in _iter_non_code_lines(lines):
        m = bq_re.match(line)
        if m:
            out.append({
                "tag": m.group(1).upper(),
                "content": m.group(2).strip(),
                "line": idx + 1,
            })

    return out


def _extract_callouts(lines: list[str]) -> list[dict]:
    """Find admonition / callout blocks in three common flavours.

    Recognised flavours:

    * **GFM** — ``> [!NOTE] Optional title`` followed by ``> body``
      lines (GitHub, Obsidian).
    * **MkDocs** — ``!!! warning "Optional title"`` followed by
      4-space-indented body lines.
    * **Pandoc** — ``:::note Optional title`` … ``:::`` fenced block.

    Each entry carries ``kind`` (upper-cased), an optional ``title``,
    the inner ``content`` as plain text, and ``line_start`` / ``line_end``.
    """
    out: list[dict] = []

    # We want to skip code-fence regions but still need indices, so
    # walk manually and track fence state.
    in_code = False
    fence: str | None = None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if not in_code and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_code = True
            fence = stripped[:3]
            i += 1
            continue
        if in_code:
            if fence is not None and stripped.startswith(fence):
                in_code = False
                fence = None
            i += 1
            continue

        # ---- GFM: > [!NOTE] ... -------------------------------------
        m = _GFM_CALLOUT_RE.match(line)
        if m:
            kind = m.group("kind").upper()
            title = (m.group("title") or "").strip() or None
            line_start = i + 1
            j = i + 1
            content_lines: list[str] = []
            while j < len(lines):
                bq = _GFM_CALLOUT_BODY_RE.match(lines[j])
                if not bq:
                    break
                content_lines.append(bq.group(1))
                j += 1
            out.append({
                "kind": kind,
                "title": title,
                "content": "\n".join(content_lines).strip(),
                "line_start": line_start,
                "line_end": j,
            })
            i = j
            continue

        # ---- MkDocs: !!! kind "title" -------------------------------
        m = _MKDOCS_CALLOUT_RE.match(line)
        if m:
            kind = m.group("kind").upper()
            title = (m.group("title") or "").strip() or None
            line_start = i + 1
            j = i + 1
            content_lines = []
            while j < len(lines):
                ln = lines[j]
                if not ln.strip():
                    content_lines.append("")
                    j += 1
                    continue
                if ln.startswith("    "):
                    content_lines.append(ln[4:])
                    j += 1
                elif ln.startswith("\t"):
                    content_lines.append(ln[1:])
                    j += 1
                else:
                    break
            while content_lines and not content_lines[-1].strip():
                content_lines.pop()
            out.append({
                "kind": kind,
                "title": title,
                "content": "\n".join(content_lines).strip(),
                "line_start": line_start,
                "line_end": j,
            })
            i = j
            continue

        # ---- Pandoc-fenced: :::kind ... ::: -------------------------
        if not _PANDOC_FENCE_CLOSE_RE.match(line):
            m = _PANDOC_FENCE_OPEN_RE.match(line)
            if m:
                kind = m.group("kind").upper()
                title = (m.group("title") or "").strip() or None
                line_start = i + 1
                j = i + 1
                content_lines = []
                while j < len(lines):
                    if _PANDOC_FENCE_CLOSE_RE.match(lines[j]):
                        j += 1
                        break
                    content_lines.append(lines[j])
                    j += 1
                out.append({
                    "kind": kind,
                    "title": title,
                    "content": "\n".join(content_lines).strip(),
                    "line_start": line_start,
                    "line_end": j,
                })
                i = j
                continue

        i += 1

    return out


def _split_heading_anchor(text: str) -> tuple[str, str]:
    """Return ``(clean_name, anchor_id)`` for a heading.

    If the heading text ends in ``{#explicit-id}`` (kramdown / pandoc),
    that id is used and stripped from the name. Otherwise the anchor
    is the GitHub-style auto-slug of the cleaned name.
    """
    m = _HEADING_ANCHOR_RE.search(text)
    if m:
        clean = text[: m.start()].rstrip()
        return clean, m.group(1)
    return text, _slugify(text)


def _slugify(text: str) -> str:
    """GitHub-style slug: lowercase, drop punctuation, spaces → hyphens.

    Not a perfect match for GitHub's algorithm in every edge case (which
    is itself underspecified), but it is good enough that round-tripping
    a heading through this function produces stable ids for the common
    cases.
    """
    s = text.strip().lower()
    # Drop everything that isn't word-char, whitespace, or hyphen.
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.UNICODE)
    # Whitespace runs → single hyphen.
    s = re.sub(r"\s+", "-", s)
    # Collapse repeated hyphens.
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _classify_import(url: str, is_image: bool) -> str | None:
    """Decide whether *url* is an import edge, and what kind.

    Returns one of ``"doc"``, ``"image"``, ``"stylesheet"``,
    ``"script"``, ``"asset"``, or ``None`` when the link should *not*
    be promoted to ``imports`` (external URLs, anchors, mail links).
    """
    if not url:
        return None
    lower = url.lower()
    if lower.startswith(("http://", "https://", "//", "mailto:", "tel:", "#")):
        return None
    # Strip a query/fragment before extension matching.
    path_part = url.split("#", 1)[0].split("?", 1)[0].lower()
    ext = ""
    dot = path_part.rfind(".")
    slash = max(path_part.rfind("/"), path_part.rfind("\\"))
    if dot > slash:
        ext = path_part[dot:]
    if ext in _IMPORT_DOC_EXTS:
        return "doc"
    if is_image or ext in _IMPORT_IMAGE_EXTS:
        return "image"
    if ext in _IMPORT_STYLE_EXTS:
        return "stylesheet"
    if ext in _IMPORT_SCRIPT_EXTS:
        return "script"
    return "asset"


def _build_imports(
    links: list[dict], wikilinks: list[dict],
) -> list[dict]:
    """Promote relative links + wikilinks to typed import edges."""
    out: list[dict] = []
    for link in links:
        kind = _classify_import(link.get("url", ""), link.get("is_image", False))
        if kind is None:
            continue
        out.append({
            "module": link["url"],
            "alias": None,
            "line": link["line"],
            "kind": kind,
        })
    for wl in wikilinks:
        out.append({
            "module": wl["target"],
            "alias": wl["alias"],
            "line": wl["line"],
            "kind": "wikilink",
        })
    return out


def _find_block_lines(
    body: str,
    code: str,
    lines: list[str],
    search_start: int,
    fenced: bool,
) -> tuple[int, int, int]:
    """Return (line_start, line_end, new_search_start) for a code block."""
    # For fenced blocks, look for the opening fence marker.
    if fenced:
        pos = body.find("```", search_start)
        if pos == -1:
            pos = body.find("~~~", search_start)
    else:
        # Indented code block — search for the code content.
        pos = body.find(code.rstrip("\n"), search_start)

    if pos == -1:
        return 1, 1, search_start

    line_start = body[:pos].count("\n") + 1
    # Code block lines = fence open + code lines + fence close (for fenced).
    code_line_count = code.count("\n") + (0 if code.endswith("\n") else 1)
    if fenced:
        line_end = line_start + code_line_count + 1  # +1 for closing fence
    else:
        line_end = line_start + code_line_count - 1

    # Advance search_start past this block.
    new_search_start = pos + max(len(code), 3)
    return line_start, line_end, new_search_start

