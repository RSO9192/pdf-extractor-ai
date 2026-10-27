"""KNBS Economic Survey 2016: padded tables must not blow up the markdown."""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_extractor_ai import PdfExtractor, to_markdown

PDF_PATH = Path(__file__).parent / "test_data" / "2016-Economic-Survey.pdf"
_MAX_PAGES = 50
_MAX_CHARS = 1_000_000
_MAX_LINE_CHARS = 5_000

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not PDF_PATH.exists(), reason="Kenya survey PDF not present"),
]


@pytest.fixture(scope="module")
def markdown() -> str:
    with PdfExtractor(num_workers=1) as extractor:
        document = extractor.extract(
            PDF_PATH.read_bytes(), max_pages=_MAX_PAGES, name=PDF_PATH.name
        )
    return to_markdown(document)


def test_kenya_survey_markdown_stays_compact(markdown: str) -> None:
    assert len(markdown) < _MAX_CHARS
    assert "Maize" in markdown


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Page 10 table of contents comes out of TableFormer as a 238x342 grid "
        "repeating one 287-char text in 469 cells when do_cell_matching is off"
    ),
)
def test_kenya_survey_markdown_has_no_overlong_lines(markdown: str) -> None:
    assert max(len(line) for line in markdown.splitlines()) <= _MAX_LINE_CHARS
