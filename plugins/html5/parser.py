"""
plugins.html5 — HTML5 plugin for Apollo
=======================================

This plugin parses HTML5 documents (``.html`` / ``.htm``) into Apollo's
structured result dictionary so that web pages, generated docs, and
static-site output can be indexed, searched, and visualized alongside
source code, Markdown, and PDFs.

It uses the **standard library's** :mod:`html.parser` so the plugin has
zero third-party dependencies and works out of the box on any Python
install. That keeps the plugin honest as a reference for "structured
markup parsed with built-ins" — see :mod:`plugins.markdown_gfm` for the
"structured markup parsed with a third-party AST library" pattern.

What this plugin extracts
-------------------------
For every parseable HTML file, :meth:`HtmlParser.parse_source` returns a
``dict`` shaped like this::

    {
        "file":         str,
        # Required "code-shape" keys are kept empty so the result plugs
        # cleanly into pipelines that expect them.
        "functions":    [],
        "classes":      [],
        "imports":      [],
        "variables":    [],
        # HTML-specific keys:
        "documents":    [ {name, doc_type, content,
                           line_start, line_end} ],
        "sections":     [ {name, level, line_start, line_end,
                           parent_section} ],
        "code_blocks":  [ {language, content,
                           line_start, line_end} ],
        "links":        [ {url, text, is_image, line, link_type} ],
        "meta":         [ {name, content, line} ],
        "title":        str | None,    # contents of <title>
    }

Design notes
------------
* **Whole document for embeddings.** Like the Markdown and PDF plugins,
  we always emit a single ``documents`` entry containing the full raw
  HTML so the embedding pipeline gets something to index. The
  structured fields above are what the graph and UI consume.
* **Section hierarchy.** ``h1``–``h6`` headings are treated as sections
  with parent/child nesting computed via a stack-based walk, mirroring
  the Markdown plugin's behaviour.
* **Code blocks.** ``<script>`` and ``<style>`` blocks are emitted as
  ``code_blocks`` with ``language`` set to the inferred MIME / language
  (``"javascript"``, ``"css"``, …) so a web page's inline assets are
  searchable too.
* **Failure mode is silent.** Files larger than ``_MAX_FILE_SIZE`` and
  unreadable files return ``None`` — the caller falls back to the
  generic text indexer.
"""
from __future__ import annotations

import logging
from html.parser import HTMLParser as _StdlibHTMLParser
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------

# File extensions this plugin claims.
_HTML_EXTENSIONS: frozenset[str] = frozenset({".html", ".htm", ".xhtml"})

# Maximum file size we'll attempt to read (5 MB). Larger HTML files are
# almost always generated artifacts (single-page-app dumps, archived
# wiki exports) whose parsing slows indexing without adding much value.
_MAX_FILE_SIZE = 5 * 1_048_576

# Heading tags we treat as sections.
_HEADING_TAGS: frozenset[str] = frozenset(
    {"h1", "h2", "h3", "h4", "h5", "h6"}
)

# Tags whose textual content we want to surface as code blocks.
_CODE_TAGS: frozenset[str] = frozenset({"script", "style"})


class HtmlParser(BaseParser):
    """
    Parse an HTML5 file into Apollo's structured result dict.

    Quick start
    -----------
    ::

        parser = HtmlParser()
        if parser.can_parse("index.html"):
            data = parser.parse_file("index.html")
            print(data["title"])
            for section in data["sections"]:
                print(" " * section["level"], section["name"])

    Pipeline (``parse_source`` → :meth:`_parse_raw`)
    -----------------------------------------------
    1. **Walk** — :class:`_HtmlCollector` (a small subclass of the
       stdlib HTML parser) feeds tokens to a single pass that records
       headings, links, images, scripts/styles, meta tags, and the
       ``<title>``.
    2. **Section hierarchy** — headings are post-processed with a stack
       walk so each section knows its ``parent_section``.
    3. **Document entry** — a whole-file ``documents`` entry is emitted
       for the embeddings pipeline.
    """

    # ------------------------------------------------------------------
    # BaseParser interface
    # ------------------------------------------------------------------

    def can_parse(self, filepath: str) -> bool:
        """Return True for ``.html`` / ``.htm`` / ``.xhtml`` files."""
        return Path(filepath).suffix.lower() in _HTML_EXTENSIONS

    def parse_file(self, filepath: str) -> dict | None:
        """Read *filepath* from disk and delegate to :meth:`_parse_raw`.

        Returns ``None`` for the wrong extension, files larger than
        :data:`_MAX_FILE_SIZE`, or any I/O error.
        """
        path = Path(filepath)
        if path.suffix.lower() not in _HTML_EXTENSIONS:
            return None

        try:
            size = path.stat().st_size
            if size > _MAX_FILE_SIZE:
                logger.debug("skipping %s: %d bytes exceeds limit", path, size)
                return None
            raw = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("failed to read %s: %s", path, exc)
            return None

        return self._parse_raw(raw, str(path))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        """Parse from an already-loaded source string."""
        if Path(filepath).suffix.lower() not in _HTML_EXTENSIONS:
            return None
        if len(source) > _MAX_FILE_SIZE:
            return None
        return self._parse_raw(source, filepath)

    def _parse_raw(self, raw: str, filepath: str) -> dict | None:
        """Run the full extraction pipeline on a raw HTML string."""
        if not raw.strip():
            return None

        collector = _HtmlCollector()
        try:
            collector.feed(raw)
            collector.close()
        except Exception:
            # html.parser is famously forgiving — but never say never.
            logger.debug("html.parser raised on %s; skipping", filepath)
            return None

        sections = _attach_parents(collector.headings)
        code_blocks = collector.code_blocks
        links = collector.links
        meta = collector.meta
        title = collector.title

        total_lines = raw.count("\n") + 1
        documents = [
            {
                "name": Path(filepath).name,
                "doc_type": "html",
                "content": raw,
                "line_start": 1,
                "line_end": total_lines,
            }
        ]

        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": [],
            "variables": [],
            "documents": documents,
            "sections": sections,
            "code_blocks": code_blocks,
            "links": links,
            "meta": meta,
            "title": title,
        }


# ======================================================================
# Internal collector (stdlib HTMLParser subclass)
# ======================================================================


class _HtmlCollector(_StdlibHTMLParser):
    """Single-pass collector that pulls structured entities out of HTML.

    The stdlib :class:`html.parser.HTMLParser` walks tokens for us; we
    just keep small bits of state (``_in_heading``, ``_in_code`` …) and
    accumulate text into the right bucket as it arrives.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

        self.title: str | None = None
        self.headings: list[dict] = []
        self.links: list[dict] = []
        self.code_blocks: list[dict] = []
        self.meta: list[dict] = []

        # Mutable state for nested tag tracking.
        self._in_title = False
        self._title_buf: list[str] = []

        # When inside an h1–h6 we remember (level, line, buffer).
        self._heading_level: int | None = None
        self._heading_line: int | None = None
        self._heading_buf: list[str] = []

        # When inside <script>/<style> we remember the language and line.
        self._code_tag: str | None = None
        self._code_lang: str | None = None
        self._code_line: int | None = None
        self._code_buf: list[str] = []

        # When inside <a> we remember href + accumulating text.
        self._anchor_href: str | None = None
        self._anchor_line: int | None = None
        self._anchor_buf: list[str] = []

    # ------------------------------------------------------------------
    # Tag handlers
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        line, _ = self.getpos()
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag == "title":
            self._in_title = True
            self._title_buf = []
            return

        if tag in _HEADING_TAGS:
            self._heading_level = int(tag[1])
            self._heading_line = line
            self._heading_buf = []
            return

        if tag in _CODE_TAGS:
            self._code_tag = tag
            self._code_line = line
            self._code_buf = []
            if tag == "script":
                self._code_lang = (
                    attrs_dict.get("type") or "javascript"
                ).lower()
                if self._code_lang in {
                    "text/javascript",
                    "application/javascript",
                }:
                    self._code_lang = "javascript"
            else:  # style
                self._code_lang = "css"
            return

        if tag == "a":
            self._anchor_href = attrs_dict.get("href")
            self._anchor_line = line
            self._anchor_buf = []
            return

        if tag == "img":
            src = attrs_dict.get("src", "")
            alt = attrs_dict.get("alt", "")
            self.links.append({
                "url": src,
                "text": alt,
                "is_image": True,
                "line": line,
                "link_type": _classify_link(src),
            })
            return

        if tag == "link":
            href = attrs_dict.get("href", "")
            rel = attrs_dict.get("rel", "")
            if href:
                self.links.append({
                    "url": href,
                    "text": rel,
                    "is_image": False,
                    "line": line,
                    "link_type": _classify_link(href),
                })
            return

        if tag == "meta":
            name = (
                attrs_dict.get("name")
                or attrs_dict.get("property")
                or attrs_dict.get("http-equiv")
                or ""
            )
            content = attrs_dict.get("content", "")
            if name or content:
                self.meta.append({
                    "name": name,
                    "content": content,
                    "line": line,
                })
            return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        line, _ = self.getpos()

        if tag == "title" and self._in_title:
            self.title = "".join(self._title_buf).strip() or None
            self._in_title = False
            self._title_buf = []
            return

        if tag in _HEADING_TAGS and self._heading_level is not None:
            self.headings.append({
                "name": "".join(self._heading_buf).strip(),
                "level": self._heading_level,
                "line_start": self._heading_line or line,
                "line_end": line,
            })
            self._heading_level = None
            self._heading_line = None
            self._heading_buf = []
            return

        if tag == self._code_tag and self._code_tag is not None:
            content = "".join(self._code_buf)
            self.code_blocks.append({
                "language": self._code_lang,
                "content": content,
                "line_start": self._code_line or line,
                "line_end": line,
            })
            self._code_tag = None
            self._code_lang = None
            self._code_line = None
            self._code_buf = []
            return

        if tag == "a" and self._anchor_href is not None:
            text = "".join(self._anchor_buf).strip()
            self.links.append({
                "url": self._anchor_href,
                "text": text,
                "is_image": False,
                "line": self._anchor_line or line,
                "link_type": _classify_link(self._anchor_href),
            })
            self._anchor_href = None
            self._anchor_line = None
            self._anchor_buf = []
            return

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf.append(data)
        if self._heading_level is not None:
            self._heading_buf.append(data)
        if self._code_tag is not None:
            self._code_buf.append(data)
        if self._anchor_href is not None:
            self._anchor_buf.append(data)


# ======================================================================
# Module-level helpers
# ======================================================================


def _classify_link(url: str) -> str:
    """Bucket a URL into ``external`` / ``anchor`` / ``internal``.

    Same rule the Markdown plugin uses, kept consistent so the graph
    layer can colour and crawl HTML and Markdown links the same way.
    """
    if not url:
        return "internal"
    if url.startswith(("http://", "https://", "//")):
        return "external"
    if url.startswith("#"):
        return "anchor"
    return "internal"


def _attach_parents(headings: list[dict]) -> list[dict]:
    """Stack-walk headings to populate each section's ``parent_section``.

    A level-3 heading nests under the most recent shallower-level (e.g.
    level-2 or level-1) heading, mirroring the Markdown plugin's
    behaviour so the rest of Apollo can treat both the same way.
    """
    sections: list[dict] = []
    parent_stack: list[tuple[int, str]] = []  # (level, name)

    for h in headings:
        while parent_stack and parent_stack[-1][0] >= h["level"]:
            parent_stack.pop()
        parent_section = parent_stack[-1][1] if parent_stack else None

        sections.append({
            "name": h["name"],
            "level": h["level"],
            "line_start": h["line_start"],
            "line_end": h["line_end"],
            "content": h["name"],
            "parent_section": parent_section,
        })
        parent_stack.append((h["level"], h["name"]))

    return sections
