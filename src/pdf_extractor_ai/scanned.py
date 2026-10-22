"""Detect image-backed PDF pages that need OCR instead of the text overlay."""

from __future__ import annotations

from typing import Any

_FULL_PAGE_COVERAGE = 0.85
_SAMPLE_PAGES = 5


def page_is_scanned(page: Any, *, coverage: float = _FULL_PAGE_COVERAGE) -> bool:
    """True when a bitmap covers most of the page (typical scanned PDF)."""
    import pypdfium2 as pdfium

    width, height = page.get_size()
    page_area = float(width) * float(height)
    if page_area <= 0:
        return False
    best = 0.0
    for obj in page.get_objects():
        if obj.type != pdfium.raw.FPDF_PAGEOBJ_IMAGE:
            continue
        left, bottom, right, top = obj.get_bounds()
        area = max(0.0, float(right) - float(left)) * max(
            0.0, float(top) - float(bottom)
        )
        best = max(best, area)
    return best / page_area >= coverage


def is_scanned_pdf(
    pdf_bytes: bytes,
    *,
    max_pages: int | None = None,
    sample: int = _SAMPLE_PAGES,
    coverage: float = _FULL_PAGE_COVERAGE,
) -> bool:
    """True when a majority of sampled pages look like full-page scans."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:
        if type(exc).__name__ != "PdfiumError":
            raise
        return False
    try:
        page_count = len(pdf)
        if page_count < 1:
            return False
        limit = page_count if max_pages is None else min(page_count, max_pages)
        if limit < 1:
            return False
        if limit <= sample:
            indices = list(range(limit))
        else:
            # First, middle, last, plus evenly spaced samples.
            step = max(1, (limit - 1) // (sample - 1))
            indices = sorted({0, limit // 2, limit - 1, *range(0, limit, step)})[
                :sample
            ]
        hits = 0
        for index in indices:
            page = pdf[index]
            try:
                if page_is_scanned(page, coverage=coverage):
                    hits += 1
            finally:
                page.close()
        return hits * 2 >= len(indices)
    finally:
        pdf.close()
