"""
plugins.html5 — HTML5 plugin for Apollo
=======================================

This plugin parses HTML5 documents (``.html`` / ``.htm`` / ``.xhtml``)
into Apollo's structured result dictionary so that web pages, generated
docs, and static-site output can be indexed, searched, and visualized
alongside source code, Markdown, and PDFs.

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
        # Code-shape keys (matches python3 / markdown_gfm schema):
        "functions":    [],
        "classes":      [],
        "imports":      [ {module, alias, line, kind} ],
        "variables":    [],
        "comments":     [ {tag, content, line} ],
        # HTML-specific keys:
        "documents":    [ {name, doc_type, content,
                           line_start, line_end} ],
        "sections":     [ {name, level, line_start, line_end,
                           content, parent_section} ],
        "code_blocks":  [ {language, content,
                           line_start, line_end} ],
        "links":        [ {url, text, is_image, line, link_type} ],
        "meta":         [ {name, content, line} ],
        "title":        str | None,    # contents of <title>
    }

Design notes
------------
* **Document content is visible text, not raw HTML.** The embedding
  pipeline indexes ``documents[0].content``. Indexing tag soup wastes
  tokens on ``<div class="...">`` boilerplate, so we strip scripts,
  styles, and tags and keep the user-visible text instead.
* **Section content carries real text.** ``h1``–``h6`` headings are
  treated as sections with parent/child nesting computed via a
  stack-based walk. Each section's ``content`` is the visible text
  between that heading and the next same-or-shallower heading — making
  per-section search and embeddings actually useful.
* **Imports = the asset graph.** ``<script src>``, ``<link rel=stylesheet>``,
  ``<img src>``, ``<iframe src>``, and ``<source src>`` are emitted as
  ``imports`` so the graph builder can draw HTML→CSS / HTML→JS /
  HTML→image edges the same way it draws Python imports.
* **Code blocks cover docs too.** ``<script>`` and ``<style>`` are
  emitted as ``code_blocks`` (as before) and so are ``<pre><code class="language-…">``
  blocks — the convention used by virtually every static-site generator,
  rustdoc, sphinx, and friends — so embedded examples in API docs are
  indexed with the right language.
* **Comments mirror python3.** ``<!-- TODO: … -->`` / ``FIXME`` /
  ``NOTE`` / ``HACK`` / ``XXX`` HTML comments are picked up by the same
  regex pattern python3 uses, into the same ``comments`` shape.
* **Failure mode is silent.** Files larger than ``_MAX_FILE_SIZE`` and
  unreadable files return ``None`` — the caller falls back to the
  generic text indexer.
"""
from __future__ import annotations

import logging
import re
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
# ``pre`` is added so ``<pre><code class="language-…">`` blocks (the
# convention used by static-site generators) are indexed too.
_CODE_TAGS: frozenset[str] = frozenset({"script", "style", "pre"})

# Same TODO/FIXME pattern as the python3 plugin so consumers can match
# tagged HTML comments and tagged Python comments uniformly.
_HTML_COMMENT_TAG_RE = re.compile(
    r"\b(TODO|FIXME|NOTE|HACK|XXX)\b[:\s]*(.*)", re.IGNORECASE
)

# Pulls "rust" out of class="language-rust foo" / "lang-rust" / etc.
# Used for ``<pre><code class="language-…">`` blocks.
_CODE_LANG_CLASS_RE = re.compile(r"\b(?:language|lang)-([\w+\-]+)")

# Tags whose raw contents must NOT bleed into per-section ``content`` or
# the visible-text ``documents`` body. Script and style are code, not
# prose; including their bodies would pollute embeddings.
_NON_TEXT_TAGS: frozenset[str] = frozenset({"script", "style"})

# Asset-bearing tags whose URL attribute creates a file-graph edge. The
# value is the attribute name to read for the URL.
_ASSET_TAGS: dict[str, str] = {
    "script": "src",
    "link": "href",
    "img": "src",
    "iframe": "src",
    "source": "src",
    "video": "src",
    "audio": "src",
}


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
       headings, links, images, scripts/styles/pres, asset references,
       meta tags, HTML comments, and the ``<title>``.
    2. **Section hierarchy & content** — sections are opened/closed by
       the heading stack; visible text accumulates into every open
       section so per-section search has real text to match on.
    3. **Document entry** — a whole-file ``documents`` entry is emitted
       holding the *visible text* (not raw HTML) for the embeddings
       pipeline.
    """

    #: Source-of-truth defaults for this plugin's runtime knobs. Mirrors
    #: ``plugins/html5/config.json``.
    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".html", ".htm", ".xhtml"],
        "max_file_size_bytes": _MAX_FILE_SIZE,
        "extract_sections": True,
        "extract_code_blocks": True,
        "extract_links": True,
        "extract_meta": True,
        "extract_imports": True,
        "asset_tags": dict(_ASSET_TAGS),
        "extract_comments": True,
        "comment_tags": ["TODO", "FIXME", "NOTE", "HACK", "XXX"],
        "ignore_dirs": [
            "_site", "public", ".jekyll-cache", ".jekyll-metadata",
            "_book", ".docusaurus", ".next", ".nuxt", ".cache",
        ],
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
            ext.lower()
            for ext in (self.config.get("extensions") or _HTML_EXTENSIONS)
        )

    # ------------------------------------------------------------------
    # BaseParser interface
    # ------------------------------------------------------------------

    @property
    def _max_size(self) -> int:
        """Effective ``max_file_size_bytes`` from config (with default)."""
        return int(self.config.get("max_file_size_bytes") or _MAX_FILE_SIZE)

    def can_parse(self, filepath: str) -> bool:
        """Return True for configured HTML extensions.

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
        """Parse from an already-loaded source string."""
        if Path(filepath).suffix.lower() not in self._extensions:
            return None
        if len(source) > self._max_size:
            return None
        return self._parse_raw(source, filepath)

    def _parse_raw(self, raw: str, filepath: str) -> dict | None:
        """Run the full extraction pipeline on a raw HTML string."""
        if not raw.strip():
            return None

        collector = _HtmlCollector(
            asset_tags=self.config.get("asset_tags") or _ASSET_TAGS,
            comment_tags=self.config.get("comment_tags"),
            extract_imports=bool(self.config.get("extract_imports", True)),
            extract_comments=bool(self.config.get("extract_comments", True)),
        )
        try:
            collector.feed(raw)
            collector.close()
        except Exception:
            # html.parser is famously forgiving — but never say never.
            logger.debug("html.parser raised on %s; skipping", filepath)
            return None

        sections = collector.sections if self.config.get("extract_sections", True) else []
        code_blocks = (
            collector.code_blocks if self.config.get("extract_code_blocks", True) else []
        )
        links = collector.links if self.config.get("extract_links", True) else []
        meta = collector.meta if self.config.get("extract_meta", True) else []
        title = collector.title
        imports = collector.imports
        comments = collector.comments
        visible_text = _normalize_whitespace("".join(collector.text_chunks))

        total_lines = raw.count("\n") + 1
        documents = [
            {
                "name": Path(filepath).name,
                "doc_type": "html",
                # Visible text, not raw HTML — embeddings benefit hugely
                # from not seeing tag soup. Raw HTML is still on disk.
                "content": visible_text or raw,
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

    def __init__(
        self,
        asset_tags: dict | None = None,
        comment_tags=None,
        extract_imports: bool = True,
        extract_comments: bool = True,
    ) -> None:
        super().__init__(convert_charrefs=True)

        # Per-instance config so the parser can be reconfigured at
        # runtime (e.g. when the user toggles a knob in Settings).
        self._asset_tags: dict[str, str] = (
            dict(asset_tags) if asset_tags else dict(_ASSET_TAGS)
        )
        self._extract_imports = extract_imports
        self._extract_comments = extract_comments
        if comment_tags:
            alt = "|".join(re.escape(t) for t in comment_tags)
            self._comment_tag_re = re.compile(
                rf"\b({alt})\b[:\s]*(.*)", re.IGNORECASE
            )
        else:
            self._comment_tag_re = _HTML_COMMENT_TAG_RE

        self.title: str | None = None
        self.sections: list[dict] = []
        self.links: list[dict] = []
        self.code_blocks: list[dict] = []
        self.meta: list[dict] = []
        self.imports: list[dict] = []
        self.comments: list[dict] = []
        self.text_chunks: list[str] = []

        # Mutable state for nested tag tracking.
        self._in_title = False
        self._title_buf: list[str] = []

        # When inside an h1–h6 we remember (level, line, buffer).
        self._heading_level: int | None = None
        self._heading_line: int | None = None
        self._heading_buf: list[str] = []

        # When inside <script>/<style>/<pre> we remember language + line.
        self._code_tag: str | None = None
        self._code_lang: str | None = None
        self._code_line: int | None = None
        self._code_buf: list[str] = []

        # When inside <a> we remember href + accumulating text.
        self._anchor_href: str | None = None
        self._anchor_line: int | None = None
        self._anchor_buf: list[str] = []

        # Stack of currently-open sections; each is a *reference* into
        # ``self.sections``. Visible text is appended to each open
        # section's ``_buf`` so a section's content covers everything
        # until the next same-or-shallower heading.
        self._open_sections: list[dict] = []

        # Suppression depth for "do not count this text as visible
        # prose" — bumped while inside <script>/<style>.
        self._non_text_depth = 0

    # ------------------------------------------------------------------
    # Tag handlers
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        line, _ = self.getpos()
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}

        # Track suppression for visible-text accumulation.
        if tag in _NON_TEXT_TAGS:
            self._non_text_depth += 1

        # Asset/import edges. Done first so it works regardless of any
        # text-content state below.
        self._maybe_emit_import(tag, attrs_dict, line)

        if tag == "title":
            self._in_title = True
            self._title_buf = []
            return

        if tag in _HEADING_TAGS:
            level = int(tag[1])
            # Close any sections at this depth or deeper before the new
            # heading opens, so their content stops at the right point.
            self._close_sections_at_or_below(level, line - 1)
            self._heading_level = level
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
            elif tag == "style":
                self._code_lang = "css"
            else:  # pre — language is not yet known; the inner <code>
                # element's class usually carries it.
                self._code_lang = None
            return

        # <code class="language-foo"> *inside* a <pre> we're already
        # tracking — pick up the language hint and otherwise let the
        # outer <pre> handle line tracking and buffering.
        if (
            tag == "code"
            and self._code_tag == "pre"
            and self._code_lang is None
        ):
            cls = attrs_dict.get("class", "")
            m = _CODE_LANG_CLASS_RE.search(cls)
            if m:
                self._code_lang = m.group(1).lower()
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
            # Title text is also visible text on most pages — the
            # browser shows it in the tab. Skipping it here.
            return

        if tag in _HEADING_TAGS and self._heading_level is not None:
            name = "".join(self._heading_buf).strip()
            parent = (
                self._open_sections[-1]["name"]
                if self._open_sections
                else None
            )
            sec = {
                "name": name,
                "level": self._heading_level,
                "line_start": self._heading_line or line,
                "line_end": line,
                "content": "",
                "parent_section": parent,
                "_buf": [],
            }
            self.sections.append(sec)
            self._open_sections.append(sec)
            self._heading_level = None
            self._heading_line = None
            self._heading_buf = []
            return

        if tag == self._code_tag and self._code_tag is not None:
            content = "".join(self._code_buf)
            # Default <pre> with no language hint to "text" so the
            # downstream consumer can still distinguish from prose.
            language = self._code_lang or ("text" if tag == "pre" else None)
            # External scripts (``<script src=...></script>``) have no
            # inline content; skip them — they're already captured as
            # an ``imports`` entry and an empty code block adds noise.
            if content.strip():
                self.code_blocks.append({
                    "language": language,
                    "content": content,
                    "line_start": self._code_line or line,
                    "line_end": line,
                })
            self._code_tag = None
            self._code_lang = None
            self._code_line = None
            self._code_buf = []
            if tag in _NON_TEXT_TAGS:
                self._non_text_depth = max(0, self._non_text_depth - 1)
            return

        # Close suppression for non-code non-text tags (e.g. </style>
        # without a matching open — defensive).
        if tag in _NON_TEXT_TAGS:
            self._non_text_depth = max(0, self._non_text_depth - 1)

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
            return  # don't double-count code body as section text
        if self._anchor_href is not None:
            self._anchor_buf.append(data)
        # Visible-text accumulation: skip while inside <script>/<style>.
        if self._non_text_depth == 0 and not self._in_title:
            self.text_chunks.append(data)
            # And feed every currently-open section's content buffer.
            if self._heading_level is None:
                for sec in self._open_sections:
                    sec["_buf"].append(data)

    def handle_comment(self, data: str) -> None:
        if not self._extract_comments:
            return
        line, _ = self.getpos()
        m = self._comment_tag_re.search(data)
        if not m:
            return
        self.comments.append({
            "tag": m.group(1).upper(),
            "content": m.group(2).strip(),
            "line": line,
        })

    def close(self) -> None:
        super().close()
        # Flush any sections still open at EOF.
        # Use a generous "line at EOF" so line_end is preserved as the
        # last real heading-end line.
        self._close_sections_at_or_below(0, line=None, eof=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _close_sections_at_or_below(
        self, level: int, line: int | None, *, eof: bool = False
    ) -> None:
        """Pop sections whose level is ``>= level`` and finalise content."""
        while self._open_sections and self._open_sections[-1]["level"] >= level:
            sec = self._open_sections.pop()
            sec["content"] = _normalize_whitespace(
                "".join(sec.pop("_buf", []))
            )
            if not eof and line is not None and line > sec["line_end"]:
                sec["line_end"] = line
        # When called with level=0 at EOF, this empties the stack.
        if eof:
            while self._open_sections:
                sec = self._open_sections.pop()
                sec["content"] = _normalize_whitespace(
                    "".join(sec.pop("_buf", []))
                )

    def _maybe_emit_import(
        self, tag: str, attrs: dict[str, str], line: int
    ) -> None:
        """Record an asset reference as an ``imports`` entry, if any."""
        if not self._extract_imports:
            return
        attr = self._asset_tags.get(tag)
        if not attr:
            return
        url = attrs.get(attr, "").strip()
        if not url:
            return
        # For <link> only treat real "asset"-y rels as imports — feeds,
        # canonicals, alternates, etc. would be noisy edges.
        if tag == "link":
            rel = attrs.get("rel", "").lower()
            if not any(r in rel for r in ("stylesheet", "preload", "modulepreload", "icon")):
                return
            kind = "stylesheet" if "stylesheet" in rel else rel.split()[0] if rel else "link"
        elif tag == "script":
            kind = "script"
        elif tag == "img":
            kind = "image"
        elif tag == "iframe":
            kind = "iframe"
        else:  # source / video / audio
            kind = "media"

        self.imports.append({
            "module": url,
            "alias": None,
            "line": line,
            "kind": kind,
        })


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


_WS_RE = re.compile(r"\s+")


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace to single spaces and strip ends.

    HTML rendering treats most whitespace as collapsible; doing the same
    here keeps section ``content`` and the visible-text ``documents``
    body compact for embeddings.
    """
    return _WS_RE.sub(" ", text).strip()
