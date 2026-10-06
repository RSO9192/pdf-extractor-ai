"""Replace picture nodes with summaries from a caller-supplied vision model."""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Protocol

from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.group import GroupItem, ListGroup
from docling_core.types.doc.items.node import NodeItem
from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.text import SectionHeaderItem, TextItem, TitleItem
from docling_core.types.doc.labels import DocItemLabel
from PIL import Image as PILImage

from pdf_extractor_ai.reflow import append_item, format_bbox

logger = logging.getLogger(__name__)

_DEFAULT_CONCURRENCY = 3


@dataclass(frozen=True, slots=True)
class SectionVisual:
    visual_id: str
    png_bytes: bytes
    caption: str
    page: int | None
    bbox: str


class VisualLanguageModel(Protocol):
    async def describe_section(
        self,
        *,
        section_title: str,
        section_text: str,
        visuals: list[SectionVisual],
    ) -> dict[str, str]:
        """Return a map of visual id to summary text."""


@dataclass(slots=True)
class _SectionJob:
    title: str
    text: str
    pictures: list[PictureItem]
    index: int


def _page_no(item: NodeItem) -> int | None:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    page_no = getattr(prov[0], "page_no", None)
    return int(page_no) if page_no is not None else None


def _png_bytes(image: PILImage.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _caption(item: PictureItem, document: DoclingDocument) -> str:
    return (item.caption_text(document) or "").strip()


def summary_block(*, model_name: str, summary: str, page: int | None, bbox: str) -> str:
    page_label = page if page is not None else "unknown"
    return (
        f"[Summarized Image: {model_name}]\n"
        f"page: {page_label}\n"
        f"bounding box: {bbox}\n"
        f"Summary:\n{summary.strip()}"
    )


def _collect(document: DoclingDocument) -> list[_SectionJob]:
    jobs: list[_SectionJob] = []

    def visit(node: NodeItem, title: str) -> None:
        texts: list[str] = [title] if title else []
        pictures: list[PictureItem] = []
        nested: list[TitleItem | SectionHeaderItem] = []
        for child_ref in node.children:
            child = child_ref.resolve(document)
            if isinstance(child, (TitleItem, SectionHeaderItem)):
                nested.append(child)
            elif isinstance(child, PictureItem):
                pictures.append(child)
            elif isinstance(child, GroupItem):
                _gather_group(document, child, texts, pictures, nested)
            elif isinstance(child, TextItem) and (child.text or "").strip():
                texts.append(child.text)
        if pictures:
            section_text = "\n\n".join(texts)
            for start in range(0, len(pictures), 4):
                jobs.append(
                    _SectionJob(
                        title=title,
                        text=section_text,
                        pictures=pictures[start : start + 4],
                        index=len(jobs),
                    )
                )
        for heading in nested:
            visit(heading, (heading.text or "").strip())

    visit(document.body, "")
    return jobs


def _gather_group(
    document: DoclingDocument,
    node: NodeItem,
    texts: list[str],
    pictures: list[PictureItem],
    nested: list[TitleItem | SectionHeaderItem],
) -> None:
    for child_ref in node.children:
        child = child_ref.resolve(document)
        if isinstance(child, (TitleItem, SectionHeaderItem)):
            nested.append(child)
        elif isinstance(child, PictureItem):
            pictures.append(child)
        elif isinstance(child, GroupItem):
            _gather_group(document, child, texts, pictures, nested)
        elif isinstance(child, TextItem) and (child.text or "").strip():
            texts.append(child.text)


def _clone(source: DoclingDocument, replacements: dict[str, str]) -> DoclingDocument:
    cloned = DoclingDocument(name=source.name)
    for page_no, page in source.pages.items():
        cloned.add_page(page_no=page_no, size=page.size)
    _walk(source, source.body, cloned, None, replacements)
    return cloned


def _walk(
    source: DoclingDocument,
    node: NodeItem,
    cloned: DoclingDocument,
    parent: NodeItem | None,
    replacements: dict[str, str],
) -> None:
    for child_ref in node.children:
        child = child_ref.resolve(source)
        if isinstance(child, ListGroup):
            group = cloned.add_list_group(parent=parent)
            _walk(source, child, cloned, group, replacements)
            continue
        if isinstance(child, GroupItem):
            _walk(source, child, cloned, parent, replacements)
            continue
        if isinstance(child, PictureItem) and child.self_ref in replacements:
            prov = child.prov[0].model_copy(deep=True) if child.prov else None
            cloned.add_text(
                label=DocItemLabel.TEXT,
                text=replacements[child.self_ref],
                prov=prov,
                parent=parent,
            )
            continue
        caption = _caption(child, source) if isinstance(child, PictureItem) else ""
        inserted = append_item(cloned, child, parent=parent, caption=caption)
        if isinstance(child, (TitleItem, SectionHeaderItem)):
            _walk(source, child, cloned, inserted, replacements)


async def summarize_images(
    document: DoclingDocument,
    model: VisualLanguageModel,
    *,
    model_name: str,
    concurrency: int = _DEFAULT_CONCURRENCY,
) -> DoclingDocument:
    """Return a new document with summarized pictures replaced by text blocks.

    The input document is not modified. A missing summary or a model error leaves
    that picture in place.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    jobs = iter(_collect(document))
    replacements: dict[str, str] = {}

    def encode(job: _SectionJob) -> list[tuple[PictureItem, SectionVisual]]:
        visuals: list[tuple[PictureItem, SectionVisual]] = []
        for picture in job.pictures:
            image = picture.get_image(document)
            if image is None:
                continue
            visuals.append(
                (
                    picture,
                    SectionVisual(
                        visual_id=f"s{job.index}-v{len(visuals)}",
                        png_bytes=_png_bytes(image),
                        caption=_caption(picture, document),
                        page=_page_no(picture),
                        bbox=format_bbox(picture, document),
                    ),
                )
            )
        return visuals

    async def worker() -> None:
        for job in jobs:
            # Encode only admitted work, off the event loop.
            encoded = await asyncio.to_thread(encode, job)
            if not encoded:
                continue
            try:
                summaries = await model.describe_section(
                    section_title=job.title,
                    section_text=job.text,
                    visuals=[visual for _, visual in encoded],
                )
                for picture, visual in encoded:
                    summary = (summaries or {}).get(visual.visual_id, "")
                    if summary.strip():
                        replacements[picture.self_ref] = summary_block(
                            model_name=model_name,
                            summary=summary,
                            page=visual.page,
                            bbox=visual.bbox,
                        )
            except Exception:
                logger.warning(
                    "Image summary failed for section %r", job.title, exc_info=True
                )
            finally:
                del encoded

    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return await asyncio.to_thread(_clone, document, replacements)
