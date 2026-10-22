"""Full-page image detection for scanned PDFs."""

from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium

from pdf_extractor_ai.scanned import is_scanned_pdf, page_is_scanned

_TEST_DATA = Path(__file__).parent / "test_data"


def _blank_pdf() -> bytes:
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 300)
    out = io.BytesIO()
    pdf.save(out)
    pdf.close()
    return out.getvalue()


def test_blank_page_is_not_scanned() -> None:
    pdf = pdfium.PdfDocument(_blank_pdf())
    try:
        assert page_is_scanned(pdf[0]) is False
    finally:
        pdf.close()


def test_fixture_annex_is_scanned_and_digital_report_is_not() -> None:
    annex = (_TEST_DATA / "per211232annx1.pdf").read_bytes()
    digital = (_TEST_DATA / "cd9804en.pdf").read_bytes()
    assert is_scanned_pdf(annex, max_pages=5) is True
    assert is_scanned_pdf(digital, max_pages=5) is False

    annex_pdf = pdfium.PdfDocument(annex)
    try:
        assert page_is_scanned(annex_pdf[78]) is True
    finally:
        annex_pdf.close()
