"""Page banners, titles, and layout maps."""

from __future__ import annotations

from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

from pdf_extractor_ai.markdown import (
    document_title,
    is_low_text,
    layout_from_markdown,
    split_pages,
    to_markdown,
)
from tests.support import (
    add_heading,
    add_picture,
    add_text,
    assemble,
    new_page,
    provenance,
)


def test_page_banners_open_across_a_page_and_close_at_the_end() -> None:
    page1 = new_page(1)
    add_text(page1, 1, "Hello", 30, 40, 210, 70)
    add_text(page1, 1, "Again", 30, 90, 210, 120)
    page2 = new_page(2)
    add_text(page2, 2, "World", 30, 40, 210, 70)
    markdown = to_markdown(assemble(page1, page2))
    assert markdown.index("========== page 1 start ==========") < markdown.index(
        "Hello"
    )
    assert markdown.index("Hello") < markdown.index("Again")
    assert markdown.index("Again") < markdown.index("========== page 1 end ==========")
    assert markdown.index("========== page 1 end ==========") < markdown.index(
        "========== page 2 start =========="
    )
    assert markdown.index("World") < markdown.index("========== page 2 end ==========")
    assert markdown.count("========== page 1 start ==========") == 1


def test_is_low_text_ignores_banners_and_placeholders() -> None:
    markdown = (
        "========== page 1 start ==========\n\n"
        "Hello\n\n"
        "<!-- image -->\n\n"
        "<!-- visual:s0-v0 -->\n\n"
        "========== page 1 end =========="
    )
    assert is_low_text(markdown, min_chars=5) is False
    assert is_low_text(markdown, min_chars=6) is True


def test_document_title_prefers_page_one_rank_then_longest() -> None:
    page = new_page(1)
    add_heading(page, 1, "Subtitle", 2, 30, 10)
    add_heading(page, 1, "Main Title", 1, 30, 50)
    add_heading(page, 1, "Other", 1, 30, 90)
    document = assemble(page)
    assert document_title(document, "fallback.pdf") == "Main Title"
    assert (
        document_title(DoclingDocument(name="empty"), "fallback.pdf") == "fallback.pdf"
    )


def test_document_title_prefers_title_label_over_a_longer_heading() -> None:
    page = new_page(1)
    page.add_title(
        "Hi",
        prov=provenance(1, 30, 10, 210, 40),
    )
    add_heading(page, 1, "A very long section header", 1, 30, 60)
    assert document_title(assemble(page), "fallback.pdf") == "Hi"


def test_document_title_uses_first_shallowest_heading_off_page_one() -> None:
    page = new_page(2)
    add_heading(page, 2, "Later Title", 1, 30, 40)
    add_heading(page, 2, "Deeper", 2, 30, 80)
    assert document_title(assemble(page), "fallback.pdf") == "Later Title"


def test_layout_and_split_pages_follow_heading_paths() -> None:
    page1 = new_page(1)
    add_heading(page1, 1, "Main Title", 1, 30, 40)
    add_text(page1, 1, "Intro text", 30, 80, 210, 110)
    page2 = new_page(2)
    add_heading(page2, 2, "Methods", 2, 30, 40)
    add_text(page2, 2, "Details", 30, 80, 210, 110)
    document = assemble(page1, page2)
    markdown = to_markdown(document)
    layout = layout_from_markdown(markdown, document)
    assert layout.document_title == "Main Title"
    assert [page.page for page in layout.pages] == [1, 2]
    assert layout.sections[0].title == "Main Title"
    assert layout.sections[0].level == 1
    assert layout.sections[0].heading_path == ("Main Title",)
    assert layout.sections[1].heading_path == ("Main Title", "Methods")
    assert layout.sections[0].page_start == 1
    assert layout.sections[1].page_end == 2
    pages = split_pages(markdown)
    assert "Intro text" in pages[1]
    assert "Details" in pages[2]


def test_in_figure_text_is_omitted() -> None:
    page = new_page(1)
    add_picture(page, 1, 40, 100, 300, 400, size=(120, 120))
    add_text(page, 1, "Inside", 60, 140, 200, 180)
    add_text(page, 1, "Outside", 30, 20, 210, 50)
    markdown = to_markdown(assemble(page))
    assert "Outside" in markdown
    assert "Inside" not in markdown
    assert "<!-- image -->" in markdown


def test_summary_text_is_emitted_verbatim() -> None:
    page = new_page(1)
    page.add_text(
        DocItemLabel.TEXT,
        "[Summarized Image: gemini]\npage: 1\nSummary:\nChart line",
        prov=provenance(1, 40, 40, 240, 240),
    )
    markdown = to_markdown(assemble(page))
    assert "[Summarized Image: gemini]\npage: 1\nSummary:\nChart line" in markdown
