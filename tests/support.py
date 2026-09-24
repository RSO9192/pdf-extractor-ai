"""Helpers for building small Docling documents in unit tests."""

from __future__ import annotations

from typing import cast

from docling_core.types.doc.base import BoundingBox, Size
from docling_core.types.doc.common.reference import ImageRef, ProvenanceItem
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.text import SectionHeaderItem, TextItem
from docling_core.types.doc.labels import DocItemLabel
from PIL import Image

from pdf_extractor_ai.reflow import SectionAssembler


def provenance(
    page: int, left: float, top: float, right: float, bottom: float
) -> ProvenanceItem:
    return ProvenanceItem(
        page_no=page,
        bbox=BoundingBox(l=left, t=top, r=right, b=bottom),
        charspan=(0, 1),
    )


def new_page(page_no: int, width: float = 600, height: float = 800) -> DoclingDocument:
    document = DoclingDocument(name=f"page-{page_no}")
    document.add_page(page_no=page_no, size=Size(width=width, height=height))
    return document


def add_text(
    document: DoclingDocument,
    page: int,
    text: str,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> TextItem:
    return cast(
        "TextItem",
        document.add_text(
            DocItemLabel.TEXT,
            text,
            prov=provenance(page, left, top, right, bottom),
        ),
    )


def add_heading(
    document: DoclingDocument,
    page: int,
    text: str,
    level: int,
    left: float,
    top: float,
    *,
    width: float = 180,
    height: float = 24,
) -> SectionHeaderItem:
    return cast(
        "SectionHeaderItem",
        document.add_heading(
            text,
            level=level,
            prov=provenance(page, left, top, left + width, top + height),
        ),
    )


def add_picture(
    document: DoclingDocument,
    page: int,
    left: float,
    top: float,
    right: float,
    bottom: float,
    *,
    size: tuple[int, int] = (100, 100),
) -> PictureItem:
    image = Image.new("RGB", size, "blue")
    return cast(
        "PictureItem",
        document.add_picture(
            image=ImageRef.from_pil(image, dpi=72),
            prov=provenance(page, left, top, right, bottom),
        ),
    )


def assemble(*pages: DoclingDocument) -> DoclingDocument:
    assembler = SectionAssembler(name="test")
    for page in pages:
        assembler.add_page(page)
    return assembler.finish()


def direct_texts(document: DoclingDocument) -> list[str]:
    texts: list[str] = []
    for child_ref in document.body.children:
        child = child_ref.resolve(document)
        if isinstance(child, TextItem):
            texts.append(child.text or "")
    return texts


def child_texts(document: DoclingDocument, heading: str) -> list[str]:
    for item, _level in document.iterate_items():
        if isinstance(item, SectionHeaderItem) and item.text == heading:
            texts: list[str] = []
            for child_ref in item.children:
                child = child_ref.resolve(document)
                if (
                    isinstance(child, TextItem)
                    and not isinstance(child, SectionHeaderItem)
                    or isinstance(child, SectionHeaderItem)
                ):
                    texts.append(child.text or "")
            return texts
    raise AssertionError(f"Missing heading {heading}")
