"""
Markdown AST parser — extracts sections, code blocks, links, tables,
task items, and frontmatter from Markdown files using mistune (v3) and
python-frontmatter.

Produces a richer result than the generic TextFileParser while still
including a whole-document ``documents`` entry for the embedding pipeline.
"""
from __future__ import annotations

import re
from pathlib import Path

import frontmatter
import mistune
from mistune.plugins.table import table as plugin_table
from mistune.plugins.task_lists import task_lists as plugin_task_lists

from .base import BaseParser

_MD_EXTENSIONS: frozenset[str] = frozenset({".md", ".markdown"})

# Maximum file size we'll attempt to read (1 MB).
_MAX_FILE_SIZE = 1_048_576

# Regex for locating a markdown heading line (used for line-number search).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


class MarkdownParser(BaseParser):
    """Parses Markdown files into a structured AST-based result."""

    def __init__(self) -> None:
        self._md = mistune.create_markdown(
            renderer="ast",
            plugins=[plugin_table, plugin_task_lists],
        )

    # ------------------------------------------------------------------
    # BaseParser interface
    # ------------------------------------------------------------------

    def can_parse(self, filepath: str) -> bool:
        return Path(filepath).suffix.lower() in _MD_EXTENSIONS

    def parse_file(self, filepath: str) -> dict | None:
        path = Path(filepath)
        if path.suffix.lower() not in _MD_EXTENSIONS:
            return None

        try:
            size = path.stat().st_size
            if size > _MAX_FILE_SIZE:
                return None
            raw = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):
            return None

        return self._parse_raw(raw, str(path))

    def parse_source(self, source: str, filepath: str) -> dict | None:
        if Path(filepath).suffix.lower() not in _MD_EXTENSIONS:
            return None
        if len(source) > _MAX_FILE_SIZE:
            return None
        return self._parse_raw(source, filepath)

    def _parse_raw(self, raw: str, filepath: str) -> dict | None:
        if not raw.strip():
            return None

        # 1. Frontmatter -------------------------------------------------
        try:
            post = frontmatter.loads(raw)
            fm = dict(post.metadata) if post.metadata else None
            body = post.content  # content without frontmatter
        except Exception:
            fm = None
            body = raw

        # 2. AST ----------------------------------------------------------
        ast_nodes: list[dict] = self._md(body)

        # 3. Line lookup helpers ------------------------------------------
        lines = body.split("\n")

        # 4. Walk the AST once and extract everything ---------------------
        sections = self._extract_sections(ast_nodes, body, lines)
        code_blocks = self._extract_code_blocks(ast_nodes, body, lines)
        links = self._extract_links(ast_nodes, body, lines)
        tables = self._extract_tables(ast_nodes, body, lines)
        task_items = self._extract_task_items(ast_nodes, body, lines)

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
            "imports": [],
            "variables": [],
            "documents": documents,
            "sections": sections,
            "code_blocks": code_blocks,
            "links": links,
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
            headings.append({
                "name": text,
                "level": level,
                "line_start": line_start,
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
