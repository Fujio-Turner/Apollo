"""
Non-code file parser — indexes Markdown, JSON, YAML, CSV, and plain text files.

Unlike AST-based parsers, this extracts no functions/classes/calls. Instead it
captures the full text content as a single "document" entity so that the
embedding pipeline can generate vectors from it and the graph can include
non-code files in searches and spatial coordinates.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .base import BaseParser

# Extensions this parser handles, mapped to a human-readable type tag.
_TEXT_EXTENSIONS: dict[str, str] = {
    # .md and .markdown are handled by MarkdownParser (markdown_parser.py).
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".csv": "csv",
    ".txt": "text",
    ".text": "text",
    ".rst": "text",
    ".log": "text",
    ".cfg": "text",
    ".ini": "text",
    ".toml": "toml",
    ".xml": "text",
    ".html": "text",
    ".htm": "text",
}

TEXT_EXTENSIONS: set[str] = set(_TEXT_EXTENSIONS.keys())

# Maximum file size we'll attempt to read (1 MB).
_MAX_FILE_SIZE = 1_048_576


class TextFileParser(BaseParser):
    """Parses non-code files and returns their content for embedding."""

    def can_parse(self, filepath: str) -> bool:
        return Path(filepath).suffix.lower() in _TEXT_EXTENSIONS

    def parse_file(self, filepath: str) -> dict | None:
        path = Path(filepath)
        suffix = path.suffix.lower()
        if suffix not in _TEXT_EXTENSIONS:
            return None

        # Skip very large files
        try:
            size = path.stat().st_size
            if size > _MAX_FILE_SIZE:
                return None
            raw = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):
            return None

        return self._parse_raw(raw, str(path), suffix)

    def parse_source(self, source: str, filepath: str) -> dict | None:
        suffix = Path(filepath).suffix.lower()
        if suffix not in _TEXT_EXTENSIONS:
            return None
        if len(source) > _MAX_FILE_SIZE:
            return None
        return self._parse_raw(source, filepath, suffix)

    def _parse_raw(self, raw: str, filepath: str, suffix: str) -> dict | None:
        if not raw.strip():
            return None

        doc_type = _TEXT_EXTENSIONS[suffix]
        content = _extract_content(raw, doc_type)
        if not content or not content.strip():
            return None

        return {
            "file": filepath,
            "functions": [],
            "classes": [],
            "imports": [],
            "variables": [],
            "documents": [
                {
                    "name": Path(filepath).name,
                    "doc_type": doc_type,
                    "content": content,
                    "line_start": 1,
                    "line_end": raw.count("\n") + 1,
                }
            ],
        }


def _extract_content(raw: str, doc_type: str) -> str:
    """Return a text representation suitable for embedding."""
    if doc_type == "json":
        return _extract_json(raw)
    if doc_type == "csv":
        return _extract_csv(raw)
    # markdown, yaml, toml, plain text — use as-is
    return raw


def _extract_json(raw: str) -> str:
    """Flatten a JSON document into a readable string."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw  # Fallback to raw text

    parts: list[str] = []
    _flatten_json(data, parts, prefix="")
    return "\n".join(parts) if parts else raw


def _flatten_json(obj, parts: list[str], prefix: str):
    """Recursively flatten JSON into 'key: value' lines."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            _flatten_json(v, parts, key)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}[{i}]"
            _flatten_json(v, parts, key)
    else:
        parts.append(f"{prefix}: {obj}")


def _extract_csv(raw: str) -> str:
    """Convert CSV rows into a readable representation."""
    try:
        reader = csv.reader(io.StringIO(raw))
        rows = list(reader)
    except csv.Error:
        return raw

    if not rows:
        return raw

    # Use header row as keys if present
    header = rows[0]
    lines: list[str] = [", ".join(header)]
    for row in rows[1:]:
        pairs = [f"{h}: {v}" for h, v in zip(header, row) if v]
        if pairs:
            lines.append("; ".join(pairs))
    return "\n".join(lines)
