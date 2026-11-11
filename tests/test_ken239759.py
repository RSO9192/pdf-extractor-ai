"""OCR crashes internally on the scanned Kenya PDF."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
import pytest

from pdf_extractor_ai import PdfExtractor, is_scanned_pdf, to_markdown
from pdf_extractor_ai.markdown import is_low_text

PDF_PATH = Path(__file__).parent / "test_data" / "ken239759.pdf"
MARKDOWN_PATH = PDF_PATH.with_suffix(".md")
_MIN_TEXT_CHARS = 20_000


@pytest.mark.slow
def test_ken239759_parses_and_extracts_text() -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    assert is_scanned_pdf(pdf_bytes, max_pages=5)
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        page_count = len(pdf)
    finally:
        pdf.close()

    with PdfExtractor(num_workers=1) as extractor:
        document = extractor.extract(pdf_bytes, name=PDF_PATH.name)

    assert len(document.pages) == page_count
    document.validate_tree(document.body, raise_on_error=True)
    markdown = to_markdown(document)
    MARKDOWN_PATH.write_text(markdown, encoding="utf-8")
    assert not is_low_text(markdown, min_chars=_MIN_TEXT_CHARS)
