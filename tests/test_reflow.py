"""Column order, heading trees, and multi-page section assembly."""

from __future__ import annotations

from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.text import SectionHeaderItem
from docling_core.types.doc.labels import DocItemLabel

from pdf_extractor_ai.reflow import (
    SectionAssembler,
    column_count,
    column_index,
    layout_sort_key,
)
from tests.support import (
    add_heading,
    add_picture,
    add_text,
    assemble,
    child_texts,
    direct_texts,
    new_page,
)


def test_column_count_detects_two_and_three_columns() -> None:
    assert column_count([20.0, 360.0], page_width=600.0) == 2
    assert column_count([20.0, 220.0, 420.0], page_width=600.0) == 3
    assert column_count([20.0, 40.0], page_width=600.0) == 1
    assert column_count([0.0, 200.0, 400.0, 550.0], page_width=600.0) == 3


def test_column_index_uses_nearest_center() -> None:
    three = [28.0, 212.0, 396.0]
    assert column_index(28.0, three) == 0
    assert column_index(212.6, three) == 1
    assert column_index(396.9, three) == 2


def test_layout_sort_key_reads_columns_then_pages() -> None:
    left_lower = layout_sort_key(page=14, col=0, top=400.0)
    right_upper = layout_sort_key(page=14, col=1, top=80.0)
    next_page_left = layout_sort_key(page=15, col=0, top=40.0)
    next_page_middle = layout_sort_key(page=15, col=1, top=40.0)
    assert left_lower < right_upper < next_page_left < next_page_middle


def test_narrow_labels_do_not_create_columns() -> None:
    page = new_page(1)
    add_text(page, 1, "high-right", 400, 10, 430, 30)
    add_text(page, 1, "low-left", 20, 100, 50, 130)
    assert direct_texts(assemble(page)) == ["high-right", "low-left"]


def test_wide_items_read_left_column_before_right() -> None:
    page = new_page(1)
    add_text(page, 1, "right-high", 360, 40, 540, 80)
    add_text(page, 1, "left-low", 30, 400, 210, 440)
    assert direct_texts(assemble(page)) == ["left-low", "right-high"]


def test_missing_bbox_keeps_previous_position_across_pages() -> None:
    page1 = new_page(1)
    add_text(page1, 1, "A", 30, 50, 210, 80)
    page2 = new_page(2)
    page2.add_text(DocItemLabel.TEXT, "B")
    add_text(page2, 2, "C", 30, 10, 210, 40)
    assert direct_texts(assemble(page1, page2)) == ["A", "B", "C"]


def test_preamble_and_nested_headings() -> None:
    page = new_page(1)
    add_text(page, 1, "Before", 30, 10, 210, 34)
    add_heading(page, 1, "Intro", 1, 30, 50)
    add_text(page, 1, "Body", 30, 90, 210, 120)
    add_heading(page, 1, "Details", 2, 30, 140)
    add_text(page, 1, "Nested", 30, 180, 210, 210)
    document = assemble(page)
    assert direct_texts(document) == ["Before", "Intro"]
    assert child_texts(document, "Intro") == ["Body", "Details"]
    assert child_texts(document, "Details") == ["Nested"]


def test_multipage_section_waits_for_the_next_heading() -> None:
    page1 = new_page(1)
    add_heading(page1, 1, "Intro", 1, 30, 40)
    add_text(page1, 1, "right-bottom", 360, 400, 540, 440)
    add_text(page1, 1, "left-bottom", 30, 400, 210, 440)
    assembler = SectionAssembler(name="test")
    assembler.add_page(page1)
    assert all(item.text != "Intro" for item in assembler.document.texts)

    page2 = new_page(2)
    add_text(page2, 2, "p2-right", 360, 20, 540, 60)
    add_text(page2, 2, "p2-left", 30, 80, 210, 120)
    add_heading(page2, 2, "Next", 1, 360, 400)
    assembler.add_page(page2)
    assert child_texts(assembler.document, "Intro") == [
        "left-bottom",
        "right-bottom",
        "p2-left",
        "p2-right",
    ]
    assert all(item.text != "Next" for item in assembler.document.texts)

    document = assembler.finish()
    assert "Next" in direct_texts(document)


def test_picture_stays_under_heading_above_it_on_the_same_page() -> None:
    earlier = new_page(13)
    add_heading(earlier, 13, "Earlier", 1, 30, 20)
    page = new_page(14)
    add_picture(page, 14, 40, 590, 240, 760)
    add_heading(page, 14, "Lower", 1, 40, 560)
    add_heading(page, 14, "Upper", 1, 40, 40)
    later = new_page(17)
    add_heading(later, 17, "Later", 1, 40, 40)
    document = assemble(earlier, page, later)
    picture = document.pictures[0]
    assert isinstance(picture, PictureItem)
    assert picture.parent is not None
    parent = picture.parent.resolve(document)
    assert isinstance(parent, SectionHeaderItem)
    assert parent.text == "Lower"


def test_tiny_pictures_are_left_out() -> None:
    page = new_page(1)
    add_picture(page, 1, 40, 40, 80, 80, size=(40, 40))
    add_text(page, 1, "Keep", 30, 200, 210, 240)
    document = assemble(page)
    assert document.pictures == []
    assert direct_texts(document) == ["Keep"]


def test_significant_pictures_are_kept() -> None:
    page = new_page(1)
    add_picture(page, 1, 40, 40, 240, 240, size=(80, 100))
    document = assemble(page)
    assert len(document.pictures) == 1
