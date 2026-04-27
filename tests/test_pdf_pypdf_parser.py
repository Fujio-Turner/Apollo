"""Smoke tests for the pdf_pypdf plugin."""
from __future__ import annotations

import io

import pytest

pypdf = pytest.importorskip("pypdf")

from apollo.plugins import discover_plugins
from plugins.pdf_pypdf import PdfParser


def _make_pdf(tmp_path, text: str = "Hello Apollo PDF plugin.", title: str = "Test Doc"):
    """Build a minimal one-page PDF on disk and return its path.

    Uses pypdf's writer so we don't depend on a heavier rendering lib.
    """
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    # Inject a tiny content stream so extract_text() returns our text.
    # We use a hand-written PDF text-showing operator: BT / Tf / Tj / ET.
    # pypdf doesn't have a high-level "draw text" API, so we cheat by
    # writing a content stream directly.
    from pypdf.generic import (
        ContentStream,
        DecodedStreamObject,
        NameObject,
    )

    # Build the page-content stream that draws the text in Helvetica 12pt.
    safe = text.replace("(", r"\(").replace(")", r"\)")
    raw = (
        b"BT /F1 12 Tf 72 720 Td (" + safe.encode("latin-1") + b") Tj ET"
    )
    stream = DecodedStreamObject()
    stream.set_data(raw)
    page[NameObject("/Contents")] = stream

    # Register a Helvetica font so the operator above resolves.
    from pypdf.generic import DictionaryObject, TextStringObject
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    page[NameObject("/Resources")] = resources

    writer.add_metadata({"/Title": title})

    out = tmp_path / "sample.pdf"
    with open(out, "wb") as fh:
        writer.write(fh)
    return out


class TestPdfPluginDiscovery:
    def test_pdf_plugin_is_discovered(self):
        plugins = discover_plugins()
        assert any(isinstance(p, PdfParser) for p in plugins)


class TestPdfPluginRecognisesExtension:
    def test_recognises_pdf_extension(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4\n%%EOF\n")  # minimal placeholder
        assert PdfParser().can_parse(str(f))

    def test_rejects_non_pdf_extension(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        assert not PdfParser().can_parse(str(f))


class TestPdfPluginParsesRealPdf:
    def test_parses_one_page_pdf(self, tmp_path):
        path = _make_pdf(tmp_path, text="Hello Apollo PDF plugin.",
                         title="Apollo PDF Test")
        result = PdfParser().parse_file(str(path))
        assert result is not None
        assert result["file"] == str(path)
        # Required code-shape keys are present (and empty).
        for key in ("functions", "classes", "imports", "variables"):
            assert result[key] == []
        # PDF-specific keys.
        assert result["page_count"] == 1
        assert len(result["pages"]) == 1
        assert result["pages"][0]["page_number"] == 1
        assert "Hello Apollo PDF plugin." in result["pages"][0]["content"]
        assert result["title"] == "Apollo PDF Test"
        assert result["metadata"]["title"] == "Apollo PDF Test"
        # Whole-document entry for embeddings.
        assert len(result["documents"]) == 1
        assert result["documents"][0]["doc_type"] == "pdf"
        assert "Hello Apollo PDF plugin." in result["documents"][0]["content"]

    def test_returns_none_for_garbage_pdf(self, tmp_path):
        f = tmp_path / "broken.pdf"
        f.write_bytes(b"this is not a real pdf at all")
        assert PdfParser().parse_file(str(f)) is None
