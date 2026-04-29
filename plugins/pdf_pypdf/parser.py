"""
plugins.pdf_pypdf — PDF plugin for Apollo (powered by ``pypdf``)
================================================================

This plugin parses PDF documents (``.pdf``) into Apollo's structured
result dictionary so PDFs can be indexed, searched, and visualized
alongside source code and Markdown documentation.

It is the third reference plugin (alongside :mod:`plugins.python3` and
:mod:`plugins.markdown_gfm`) and serves as a template for any plugin
that needs to extract text and structure from a binary document format
via a third-party library (PDF, DOCX, EPUB, ODT, …).

What this plugin extracts
-------------------------
For every parseable ``.pdf`` file, :meth:`PdfParser.parse_file`
returns a ``dict`` shaped like this::

    {
        "file":         str,
        # Required "code-shape" keys are kept empty so the result plugs
        # cleanly into pipelines that expect them (graph, embeddings).
        "functions":    [],
        "classes":      [],
        "imports":      [],
        "variables":    [],
        # PDF-specific keys:
        "documents":    [ {name, doc_type, content,
                           line_start, line_end} ],
        "pages":        [ {page_number, content,
                           line_start, line_end} ],
        "sections":     [ {name, level, page,
                           line_start, line_end,
                           parent_section} ],
        "metadata":     dict | None,   # /Title, /Author, /Subject, ...
        "title":        str | None,    # metadata title or first outline entry
        "page_count":   int,
    }

Design notes
------------
* **Whole document for embeddings.** Like the Markdown plugin, we
  always emit a single ``documents`` entry containing the full extracted
  text. This is what the embedding pipeline consumes; the structured
  fields above are what the graph and UI consume.
* **One page = one block.** Each page becomes its own entry in
  ``pages`` with absolute line ranges within the joined text. This lets
  search results jump to the originating page.
* **Outline → sections.** PDF "bookmarks" / table of contents are
  flattened into the ``sections`` list with their nesting ``level`` and
  the page they point at. Sections nest under the most recent
  shallower-level section, mirroring the Markdown plugin's section
  hierarchy.
* **Lazy import.** ``pypdf`` is imported only when actually needed so
  the rest of Apollo stays importable on systems without it. If the
  library is missing or the file is encrypted/corrupt, the parser
  returns ``None`` and the caller falls back to the generic text
  indexer.
* **Failure mode is silent.** Files larger than ``_MAX_FILE_SIZE``,
  unreadable files, or files that ``pypdf`` chokes on all return
  ``None``.

Dependencies
------------
* `pypdf <https://pypdf.readthedocs.io/>`_ — pure-Python PDF reader.

Install with::

    pip install -r plugins/pdf_pypdf/requirements.txt
"""
from __future__ import annotations

import logging
from pathlib import Path

from apollo.parser.base import BaseParser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------

# File extensions this plugin claims.
_PDF_EXTENSIONS: frozenset[str] = frozenset({".pdf"})

# Maximum file size we'll attempt to read (50 MB). Larger PDFs are
# almost always scanned books / image-only documents whose text
# extraction is expensive and rarely useful for indexing.
_MAX_FILE_SIZE = 50 * 1_048_576

# Page separator inserted between page texts when joining into a single
# document body. The form-feed character (\f) is the conventional page
# break and is preserved by pypdf in some workflows.
_PAGE_SEPARATOR = "\n\n\f\n\n"


class PdfParser(BaseParser):
    """
    Parse a PDF file into Apollo's structured result dict.

    Quick start
    -----------
    ::

        parser = PdfParser()
        if parser.can_parse("paper.pdf"):
            data = parser.parse_file("paper.pdf")
            print(data["title"], "—", data["page_count"], "pages")
            for section in data["sections"]:
                print(" " * section["level"], section["name"])

    Pipeline (``parse_file`` → :meth:`_parse_reader`)
    -------------------------------------------------
    1. **Open** the file with ``pypdf.PdfReader`` (lazy import).
    2. **Extract** text page-by-page using ``page.extract_text()``.
    3. **Join** pages into a single body, tracking each page's line
       range within the joined text.
    4. **Outline** — walk ``reader.outline`` and flatten it into
       sections with parent/child relationships.
    5. **Metadata** — copy ``reader.metadata`` into a plain dict.
    6. **Document entry** — emit a single whole-file ``documents``
       entry for the embeddings pipeline.

    Each step is independent and side-effect free; you can replace one
    without touching the others.
    """

    #: Source-of-truth defaults for this plugin's runtime knobs. Mirrors
    #: ``plugins/pdf_pypdf/config.json``.
    DEFAULT_CONFIG: dict = {
        "enabled": True,
        "extensions": [".pdf"],
        "max_file_size_bytes": _MAX_FILE_SIZE,
        "extract_pages": True,
        "extract_outline": True,
        "extract_metadata": True,
        "decrypt_with_empty_password": True,
        "ignore_dirs": [],
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
            for ext in (self.config.get("extensions") or _PDF_EXTENSIONS)
        )

    # ------------------------------------------------------------------
    # BaseParser interface
    # ------------------------------------------------------------------

    @property
    def _max_size(self) -> int:
        """Effective ``max_file_size_bytes`` from config (with default)."""
        return int(self.config.get("max_file_size_bytes") or _MAX_FILE_SIZE)

    def can_parse(self, filepath: str) -> bool:
        """Return True for configured PDF extensions.

        Returns ``False`` when the plugin has been disabled via config
        OR when ``pypdf`` is not importable, so Apollo can fall back to
        the generic text indexer instead of raising.
        """
        if not self.config.get("enabled", True):
            return False
        if Path(filepath).suffix.lower() not in self._extensions:
            return False
        try:
            import pypdf  # noqa: F401
        except ImportError:
            return False
        return True

    def parse_file(self, filepath: str) -> dict | None:
        """Read *filepath* from disk and delegate to :meth:`_parse_reader`.

        Returns ``None`` for the wrong extension, files larger than
        ``self.config["max_file_size_bytes"]``, missing ``pypdf``,
        encrypted PDFs we can't unlock, or any I/O / parser error.
        """
        path = Path(filepath)
        if path.suffix.lower() not in self._extensions:
            return None

        try:
            size = path.stat().st_size
            if size > self._max_size:
                logger.debug("skipping %s: %d bytes exceeds limit", path, size)
                return None
        except OSError as exc:
            logger.warning("failed to stat %s: %s", path, exc)
            return None

        # Lazy import — Apollo stays importable without pypdf.
        try:
            from pypdf import PdfReader
            from pypdf.errors import PdfReadError
        except ImportError:
            logger.debug("pypdf not installed; skipping %s", path)
            return None

        try:
            reader = PdfReader(str(path))
        except (OSError, PdfReadError, Exception) as exc:
            logger.warning("failed to open %s as PDF: %s: %s",
                           path, type(exc).__name__, exc)
            return None

        # Try to unlock with the empty password if encrypted (gated by
        # the ``decrypt_with_empty_password`` config flag); if that
        # fails, give up and let the generic indexer take over.
        if getattr(reader, "is_encrypted", False):
            if not self.config.get("decrypt_with_empty_password", True):
                logger.debug("encrypted PDF %s skipped (decrypt_with_empty_password=False)", path)
                return None
            try:
                if not reader.decrypt(""):
                    logger.debug("encrypted PDF %s could not be unlocked with empty password", path)
                    return None
            except Exception:
                logger.exception("error decrypting %s", path)
                return None

        try:
            return self._parse_reader(reader, str(path))
        except Exception:
            logger.exception("unexpected failure parsing %s", path)
            return None

    # ``parse_source`` is intentionally not overridden — PDFs are binary
    # and the in-memory ``source`` string the caller passes us is not
    # useful for parsing. The base class will fall back to ``parse_file``.

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _parse_reader(self, reader, filepath: str) -> dict | None:
        """Run the full extraction pipeline against an opened ``PdfReader``.

        Returns ``None`` for empty documents (zero pages or whitespace
        only) so the caller can treat the file as unparseable.
        """
        # 1. Per-page text extraction ------------------------------------
        pages, body = self._extract_pages(reader)

        if not pages or not body.strip():
            return None

        # 2. Metadata ----------------------------------------------------
        metadata = (
            _coerce_metadata(getattr(reader, "metadata", None))
            if self.config.get("extract_metadata", True)
            else None
        )

        # 3. Outline (table of contents) → sections ----------------------
        sections = (
            self._extract_sections(reader, pages)
            if self.config.get("extract_outline", True)
            else []
        )

        # ``extract_pages`` toggles whether per-page entries appear in
        # the result. The body text we pass to embeddings is always
        # populated regardless — that's what makes the document
        # searchable.
        emit_pages = self.config.get("extract_pages", True)

        # 4. Title -------------------------------------------------------
        title = _derive_title(metadata, sections, filepath)

        # 5. Whole-document entry (same shape as TextFileParser) ---------
        total_lines = body.count("\n") + 1
        documents = [
            {
                "name": Path(filepath).name,
                "doc_type": "pdf",
                "content": body,
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
            "pages": pages if emit_pages else [],
            "sections": sections,
            "metadata": metadata,
            "title": title,
            "page_count": len(pages),
        }

    # ------------------------------------------------------------------
    # Page extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_pages(reader) -> tuple[list[dict], str]:
        """Return (pages, joined_body).

        ``pages`` is a list of dicts with ``page_number`` (1-based),
        ``content``, and ``line_start`` / ``line_end`` indices into the
        joined body. Pages where text extraction fails are recorded
        with empty content so page numbers remain stable.
        """
        pages: list[dict] = []
        body_parts: list[str] = []
        line_cursor = 1  # 1-based line index into the joined body

        for idx, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""

            line_start = line_cursor
            line_count = text.count("\n") + (1 if text else 0)
            line_end = line_start + max(line_count - 1, 0)

            pages.append({
                "page_number": idx,
                "content": text,
                "line_start": line_start,
                "line_end": line_end,
            })

            body_parts.append(text)

            # Account for the separator's newlines in the cursor so
            # subsequent pages report accurate absolute line numbers.
            sep_lines = _PAGE_SEPARATOR.count("\n")
            line_cursor = line_end + sep_lines + 1

        body = _PAGE_SEPARATOR.join(body_parts)
        return pages, body

    # ------------------------------------------------------------------
    # Outline / table of contents → sections
    # ------------------------------------------------------------------

    def _extract_sections(self, reader, pages: list[dict]) -> list[dict]:
        """Flatten ``reader.outline`` into the standard sections shape.

        Outline trees in pypdf are nested lists where a list element
        represents a parent's children. We do a stack-based walk so each
        section's ``parent_section`` points at the most recent
        shallower-level section's ``name``.
        """
        try:
            outline = reader.outline
        except Exception:
            return []

        if not outline:
            return []

        flat: list[tuple[int, object]] = []  # (level, destination)
        _flatten_outline(outline, level=1, out=flat)
        if not flat:
            return []

        # Build a quick lookup from page object → 1-based page index so
        # we can resolve outline destinations to a page number.
        page_index = {}
        for idx, page in enumerate(reader.pages, start=1):
            try:
                page_index[id(page)] = idx
            except Exception:
                continue

        sections: list[dict] = []
        parent_stack: list[tuple[int, str]] = []  # (level, name)

        for level, dest in flat:
            name = _outline_title(dest)
            if not name:
                continue

            page_no = _resolve_outline_page(reader, dest, page_index)
            line_start = 1
            line_end = pages[-1]["line_end"] if pages else 1
            if page_no is not None and 1 <= page_no <= len(pages):
                line_start = pages[page_no - 1]["line_start"]
                line_end = pages[page_no - 1]["line_end"]

            while parent_stack and parent_stack[-1][0] >= level:
                parent_stack.pop()
            parent_section = parent_stack[-1][1] if parent_stack else None

            sections.append({
                "name": name,
                "level": level,
                "page": page_no,
                "line_start": line_start,
                "line_end": line_end,
                "parent_section": parent_section,
            })
            parent_stack.append((level, name))

        return sections


# ======================================================================
# Module-level helpers
# ======================================================================

def _flatten_outline(outline, level: int, out: list) -> None:
    """Walk pypdf's nested outline structure into ``[(level, dest), ...]``.

    pypdf represents outlines as a list where a plain entry is a
    bookmark and a nested list is the children of the previous entry.
    """
    for item in outline:
        if isinstance(item, list):
            _flatten_outline(item, level=level + 1, out=out)
        else:
            out.append((level, item))


def _outline_title(dest) -> str | None:
    """Return the title string of an outline destination, or ``None``."""
    title = getattr(dest, "title", None)
    if title:
        return str(title)
    # Older pypdf versions sometimes expose ``/Title`` via mapping.
    try:
        return str(dest["/Title"])
    except Exception:
        return None


def _resolve_outline_page(reader, dest, page_index: dict) -> int | None:
    """Return the 1-based page number an outline entry points at."""
    # Newer pypdf: reader.get_destination_page_number(dest) returns a
    # 0-based int. Older versions raise or return None for some entries.
    try:
        n = reader.get_destination_page_number(dest)
        if isinstance(n, int) and n >= 0:
            return n + 1
    except Exception:
        pass

    # Fallback: dest may carry a ``page`` attribute that's a PageObject.
    page_obj = getattr(dest, "page", None)
    if page_obj is not None and id(page_obj) in page_index:
        return page_index[id(page_obj)]

    return None


def _coerce_metadata(metadata) -> dict | None:
    """Convert ``pypdf`` metadata (DocumentInformation) into a plain dict.

    pypdf exposes metadata both as attributes (``.title``, ``.author``)
    and as a mapping (``/Title``, ``/Author``). We normalise to the
    short string keys ``title``, ``author``, ``subject``, ``creator``,
    ``producer``, ``keywords`` and silently drop anything we can't
    stringify.
    """
    if metadata is None:
        return None

    fields = ("title", "author", "subject", "creator", "producer", "keywords")
    out: dict = {}
    for field in fields:
        value = getattr(metadata, field, None)
        if value:
            try:
                out[field] = str(value)
            except Exception:
                continue

    return out or None


def _derive_title(
    metadata: dict | None,
    sections: list[dict],
    filepath: str,
) -> str | None:
    """Pick the best available title for the document.

    Preference order: metadata title → first top-level outline entry →
    ``None`` (caller can fall back to the file's basename).
    """
    if metadata and metadata.get("title"):
        return metadata["title"]
    for section in sections:
        if section["level"] == 1:
            return section["name"]
    return None
