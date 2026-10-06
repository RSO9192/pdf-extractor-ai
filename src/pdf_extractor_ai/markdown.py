"""Serialize a reordered DoclingDocument to bannered markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass

from docling_core.transforms.serializer.markdown import (
    MarkdownDocSerializer,
    MarkdownParams,
)
from docling_core.types.doc.base import ImageRefMode
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.group import GroupItem
from docling_core.types.doc.items.node import NodeItem
from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.text import SectionHeaderItem, TextItem, TitleItem
from docling_core.types.doc.labels import DocItemLabel

from pdf_extractor_ai.reflow import (
    bbox_contained,
    heading_level,
    picture_boxes,
    top_left_bbox,
)

_IMAGE_PLACEHOLDER = "<!-- image -->"
_SUMMARY_PREFIX = "[Summarized Image:"
_PAGE_START_RE = re.compile(r"^========== page (\d+) start ==========$")
_PAGE_END_RE = re.compile(r"^========== page (\d+) end ==========$")
_PAGE_BANNER_RE = re.compile(
    r"^========== page \d+ (?:start|end) ==========$", re.MULTILINE
)
_ANY_IMAGE_PLACEHOLDER_RE = re.compile(r"<!--\s*(?:image|visual:[^>]+)\s*-->")
_DEFAULT_MIN_TEXT_CHARS = 200


def _page_start_banner(page: int) -> str:
    return f"========== page {page} start =========="


def _page_end_banner(page: int) -> str:
    return f"========== page {page} end =========="


@dataclass
class _PageBannerState:
    current_page: int | None = None
    open: bool = False

    def transition(self, page: int | None) -> list[str]:
        if page is None:
            return []
        if self.current_page == page and self.open:
            return []
        parts: list[str] = []
        if self.open and self.current_page is not None:
            parts.append(_page_end_banner(self.current_page))
        parts.append(_page_start_banner(page))
        self.current_page = page
        self.open = True
        return parts

    def close(self) -> list[str]:
        if not self.open or self.current_page is None:
            return []
        banner = _page_end_banner(self.current_page)
        self.open = False
        return [banner]


@dataclass(frozen=True, slots=True)
class PdfPageSpan:
    page: int
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class PdfSectionSpan:
    title: str
    level: int
    start_line: int
    end_line: int
    page_start: int | None
    page_end: int | None
    heading_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PdfDocumentLayout:
    document_title: str
    pages: tuple[PdfPageSpan, ...]
    sections: tuple[PdfSectionSpan, ...] = ()


def _page_no(item: NodeItem) -> int | None:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    page_no = getattr(prov[0], "page_no", None)
    if page_no is None:
        return None
    return int(page_no)


def _is_summary(item: NodeItem) -> bool:
    if not isinstance(item, TextItem) or isinstance(
        item, (TitleItem, SectionHeaderItem, GroupItem)
    ):
        return False
    return (item.text or "").startswith(_SUMMARY_PREFIX)


def _inside_picture(
    item: NodeItem, document: DoclingDocument, boxes: dict[int, list[object]]
) -> bool:
    if isinstance(item, (SectionHeaderItem, TitleItem, PictureItem)):
        return False
    located = top_left_bbox(item, document)
    if located is None:
        return False
    page_no, bbox = located
    return any(bbox_contained(bbox, outer) for outer in boxes.get(page_no, []))


def to_markdown(document: DoclingDocument) -> str:
    """Walk the document in tree order. Reading order is already fixed."""
    serializer = MarkdownDocSerializer(
        doc=document,
        params=MarkdownParams(
            image_mode=ImageRefMode.PLACEHOLDER,
            image_placeholder=_IMAGE_PLACEHOLDER,
        ),
    )
    boxes: dict[int, list[object]] = {}
    for page_no, bbox in picture_boxes(document):
        boxes.setdefault(page_no, []).append(bbox)
    state = _PageBannerState()
    parts: list[str] = []
    for item, _level in document.iterate_items(
        with_groups=False, traverse_pictures=False
    ):
        if isinstance(item, GroupItem):
            continue
        parts.extend(state.transition(_page_no(item)))
        if _is_summary(item) and isinstance(item, TextItem):
            text = (item.text or "").strip()
            if text:
                parts.append(text)
            continue
        if isinstance(item, PictureItem):
            parts.append(_IMAGE_PLACEHOLDER)
            continue
        if _inside_picture(item, document, boxes):
            continue
        try:
            text = (serializer.serialize(item=item).text or "").strip()
        except Exception:  # noqa: BLE001 - serializer failures fall back to item text
            text = (getattr(item, "text", None) or "").strip()
        text = _ANY_IMAGE_PLACEHOLDER_RE.sub("", text).strip()
        if text:
            parts.append(text)
    closing = state.close()
    markdown = "\n\n".join(parts)
    if closing:
        markdown = f"{markdown}\n\n{closing[0]}" if markdown.strip() else closing[0]
    return markdown


def document_title(
    document: DoclingDocument, fallback: str | None = None
) -> str | None:
    """Prefer a page-1 title, then the first shallowest heading, then ``fallback``."""
    best_rank: int | None = None
    best_texts: list[str] = []
    for item, _level in document.iterate_items():
        if _page_no(item) != 1:
            continue
        text = " ".join((getattr(item, "text", None) or "").split())
        if not text:
            continue
        label = getattr(item, "label", None)
        if label == DocItemLabel.TITLE:
            rank = 0
        elif label == DocItemLabel.SECTION_HEADER:
            level = getattr(item, "level", None)
            rank = level if isinstance(level, int) and level >= 1 else 1
        else:
            continue
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_texts = [text]
        elif rank == best_rank:
            best_texts.append(text)
    if best_texts:
        return max(best_texts, key=len)
    best_level: int | None = None
    best_title: str | None = None
    for item, _level in document.iterate_items():
        if not isinstance(item, (TitleItem, SectionHeaderItem)):
            continue
        text = (item.text or "").strip()
        if not text:
            continue
        level = heading_level(item)
        if best_level is None or level < best_level:
            best_level = level
            best_title = text
    if best_title:
        return best_title
    return fallback


def _stripped_text(markdown: str) -> str:
    text = _ANY_IMAGE_PLACEHOLDER_RE.sub("", markdown)
    text = _PAGE_BANNER_RE.sub("", text)
    return text.strip()


def is_low_text(markdown: str, min_chars: int = _DEFAULT_MIN_TEXT_CHARS) -> bool:
    """True when the banner-stripped text is shorter than ``min_chars``."""
    return len(_stripped_text(markdown)) < min_chars


def split_pages(markdown: str) -> dict[int, str]:
    """Return the reflowed text inside each page banner."""
    pages: dict[int, str] = {}
    current: int | None = None
    buf: list[str] = []
    for line in markdown.splitlines():
        start = _PAGE_START_RE.match(line.strip())
        if start:
            current = int(start.group(1))
            buf = []
            continue
        end = _PAGE_END_RE.match(line.strip())
        if end and current is not None:
            pages[current] = "\n".join(buf).strip()
            current = None
            buf = []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        pages[current] = "\n".join(buf).strip()
    return pages


def _markdown_heading(item: TitleItem | SectionHeaderItem) -> str:
    text = (getattr(item, "text", None) or "").strip()
    if isinstance(item, TitleItem):
        return f"# {text}"
    return f"{'#' * (heading_level(item) + 1)} {text}"


def _headings(document: DoclingDocument) -> list[TitleItem | SectionHeaderItem]:
    headings: list[TitleItem | SectionHeaderItem] = []
    for item, _level in document.iterate_items():
        if (
            isinstance(item, (TitleItem, SectionHeaderItem))
            and (item.text or "").strip()
        ):
            headings.append(item)
    return headings


def _page_spans(lines: list[str]) -> list[PdfPageSpan]:
    spans: list[PdfPageSpan] = []
    open_page: int | None = None
    open_start: int | None = None
    for idx, line in enumerate(lines, start=1):
        start_match = _PAGE_START_RE.match(line.strip())
        if start_match:
            if open_page is not None and open_start is not None:
                spans.append(
                    PdfPageSpan(page=open_page, start_line=open_start, end_line=idx - 1)
                )
            open_page = int(start_match.group(1))
            open_start = idx
            continue
        end_match = _PAGE_END_RE.match(line.strip())
        if end_match and open_page is not None and open_start is not None:
            spans.append(
                PdfPageSpan(page=open_page, start_line=open_start, end_line=idx)
            )
            open_page = None
            open_start = None
    if open_page is not None and open_start is not None:
        spans.append(
            PdfPageSpan(page=open_page, start_line=open_start, end_line=len(lines))
        )
    return spans


def _pages_for_line_range(
    pages: list[PdfPageSpan], start_line: int, end_line: int
) -> tuple[int | None, int | None]:
    overlapping = [
        page
        for page in pages
        if page.start_line <= end_line and page.end_line >= start_line
    ]
    if not overlapping:
        return None, None
    return overlapping[0].page, overlapping[-1].page


def layout_from_markdown(markdown: str, document: DoclingDocument) -> PdfDocumentLayout:
    """Map heading paths and page banners onto line spans in ``markdown``."""
    lines = markdown.splitlines()
    page_spans = _page_spans(lines)
    headings = _headings(document)
    located: list[tuple[TitleItem | SectionHeaderItem, int]] = []
    cursor = 0
    for heading in headings:
        marker = _markdown_heading(heading)
        found: int | None = None
        for idx in range(cursor, len(lines)):
            if lines[idx].strip() == marker:
                found = idx + 1
                cursor = idx + 1
                break
        if found is not None:
            located.append((heading, found))

    stack: list[tuple[int, str]] = []
    sections: list[PdfSectionSpan] = []
    for index, (heading, start_line) in enumerate(located):
        title = (heading.text or "").strip()
        level = heading_level(heading)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        if index + 1 < len(located):
            end_line = located[index + 1][1] - 1
        else:
            end_line = len(lines) if lines else start_line
        end_line = max(end_line, start_line)
        page_start, page_end = _pages_for_line_range(page_spans, start_line, end_line)
        sections.append(
            PdfSectionSpan(
                title=title,
                level=level,
                start_line=start_line,
                end_line=end_line,
                page_start=page_start,
                page_end=page_end,
                heading_path=tuple(text for _, text in stack),
            )
        )
    title = document_title(document, fallback=document.name) or document.name
    return PdfDocumentLayout(
        document_title=title,
        pages=tuple(page_spans),
        sections=tuple(sections),
    )
