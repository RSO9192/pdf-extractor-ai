"""Reorder a Docling page into newspaper reading order and a heading tree."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, cast

from docling_core.types.doc.base import Size
from docling_core.types.doc.common.content_layer import ContentLayer
from docling_core.types.doc.common.reference import ImageRef
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.group import GroupItem, ListGroup
from docling_core.types.doc.items.node import NodeItem
from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.text import (
    ListItem,
    SectionHeaderItem,
    TextItem,
    TitleItem,
)
from docling_core.types.doc.labels import DocItemLabel
from PIL import Image as PILImage

_WIDE_ITEM_FRACTION = 0.18
_COLUMN_GAP_FRACTION = 0.12
_MAX_COLUMNS = 3
_MIN_VISUAL_SIDE_PX = 80
_CROP_RENDER_SCALE = 2.0

SortKey = tuple[int, int, float, float, int, int]


@dataclass(slots=True)
class _Carry:
    last_key: tuple[int, int, float, float] = (1, 0, 0.0, 0.0)
    seq: int = 0
    idx: int = 0


@dataclass(slots=True)
class _Placed:
    sort_key: SortKey
    item: NodeItem
    caption: str = ""


def layout_sort_key(
    page: int,
    col: int,
    top: float,
    left: float = 0.0,
    seq: int = 0,
    idx: int = 0,
) -> SortKey:
    """Newspaper order: physical page, left-to-right column, then top-to-bottom."""
    return (page, col, top, left, seq, idx)


def column_centers(lefts: list[float], page_width: float) -> list[float]:
    """Cluster item left-edges into at most three column anchors."""
    if page_width <= 0 or not lefts:
        return [0.0]
    xs = sorted(lefts)
    min_gap = page_width * _COLUMN_GAP_FRACTION
    clusters: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] > min_gap and len(clusters) < _MAX_COLUMNS:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    return [sum(cluster) / len(cluster) for cluster in clusters]


def column_count(lefts: list[float], page_width: float) -> int:
    return len(column_centers(lefts, page_width))


def column_index(left: float, centers: list[float]) -> int:
    if not centers:
        return 0
    return min(range(len(centers)), key=lambda i: abs(left - centers[i]))


def _is_heading(item: NodeItem) -> bool:
    return isinstance(item, (TitleItem, SectionHeaderItem))


def heading_level(item: NodeItem) -> int:
    if isinstance(item, TitleItem):
        return 1
    level = getattr(item, "level", 1)
    if isinstance(level, int) and level >= 1:
        return level
    return 1


def _page_size(document: DoclingDocument, page_no: int | None) -> Size | None:
    if page_no is None:
        return None
    page = document.pages.get(page_no)
    if page is None:
        return None
    return page.size


def top_left_bbox(item: NodeItem, document: DoclingDocument) -> tuple[int, Any] | None:
    """Return ``(page_no, bbox)`` with a top-left origin, or None."""
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    first = prov[0]
    page_no = getattr(first, "page_no", None)
    bbox = getattr(first, "bbox", None)
    if page_no is None or bbox is None:
        return None
    size = _page_size(document, int(page_no))
    height = size.height if size is not None else None
    if height is not None and hasattr(bbox, "to_top_left_origin"):
        bbox = bbox.to_top_left_origin(height)
    return int(page_no), bbox


def format_bbox(item: NodeItem, document: DoclingDocument) -> str:
    located = top_left_bbox(item, document)
    if located is None:
        return "unknown"
    _page_no, bbox = located
    origin = getattr(getattr(bbox, "coord_origin", None), "value", "TOPLEFT")
    return f"l={bbox.l:.1f}, t={bbox.t:.1f}, r={bbox.r:.1f}, b={bbox.b:.1f} ({origin})"


def _item_page_xy(
    item: NodeItem, document: DoclingDocument
) -> tuple[int, float, float] | None:
    located = top_left_bbox(item, document)
    if located is None:
        return None
    page_no, bbox = located
    return page_no, float(bbox.t), float(getattr(bbox, "l", 0.0))


def _png_bytes(pil_image: PILImage.Image) -> bytes:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return buf.getvalue()


def _is_significant(pil_image: PILImage.Image | None) -> bool:
    if pil_image is None:
        return False
    width, height = pil_image.size
    return min(width, height) >= _MIN_VISUAL_SIDE_PX


def _crop_region_from_pdf(
    pdf: Any,
    *,
    page_no: int,
    bbox: Any,
    doc_page_size: Size | None,
) -> bytes | None:
    """Crop ``bbox`` (top-left page coords) from a pypdfium2 document."""
    index = page_no - 1
    if index < 0 or index >= len(pdf):
        return None
    page = pdf[index]
    try:
        pdf_w, pdf_h = page.get_size()
        doc_w = doc_page_size.width if doc_page_size is not None else pdf_w
        doc_h = doc_page_size.height if doc_page_size is not None else pdf_h
        x_scale = pdf_w / doc_w if doc_w else 1.0
        y_scale = pdf_h / doc_h if doc_h else 1.0
        left = float(bbox.l) * x_scale
        top = float(bbox.t) * y_scale
        right = float(bbox.r) * x_scale
        bottom = float(bbox.b) * y_scale
        bitmap = page.render(scale=_CROP_RENDER_SCALE)
        pil = bitmap.to_pil()
        pixel = (
            max(0, int(left * _CROP_RENDER_SCALE)),
            max(0, int(top * _CROP_RENDER_SCALE)),
            min(pil.width, int(right * _CROP_RENDER_SCALE)),
            min(pil.height, int(bottom * _CROP_RENDER_SCALE)),
        )
        if pixel[2] <= pixel[0] or pixel[3] <= pixel[1]:
            return None
        return _png_bytes(pil.crop(pixel))
    finally:
        page.close()


def _resolve_picture(
    item: PictureItem,
    document: DoclingDocument,
    pdf: Any | None,
) -> ImageRef | None:
    png_bytes: bytes | None = None
    located = top_left_bbox(item, document)
    if pdf is not None and located is not None:
        page_no, bbox = located
        png_bytes = _crop_region_from_pdf(
            pdf,
            page_no=page_no,
            bbox=bbox,
            doc_page_size=_page_size(document, page_no),
        )
    pil_image: PILImage.Image | None = None
    if png_bytes is None:
        pil_image = item.get_image(document)
        if pil_image is None or not _is_significant(pil_image):
            return None
        return ImageRef.from_pil(pil_image, dpi=72)
    pil_image = PILImage.open(io.BytesIO(png_bytes))
    if not _is_significant(pil_image):
        return None
    return ImageRef.from_pil(pil_image, dpi=72)


def _caption_text(item: PictureItem, document: DoclingDocument) -> str:
    return (item.caption_text(document) or "").strip()


def _detach(item: NodeItem) -> NodeItem:
    copied = item.model_copy(deep=True)
    copied.children = []
    if isinstance(copied, PictureItem):
        copied.captions = []
    return copied


def _first_prov(item: NodeItem) -> Any | None:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    return prov[0].model_copy(deep=True)


def append_item(
    document: DoclingDocument,
    item: NodeItem,
    *,
    parent: NodeItem | None = None,
    caption: str = "",
) -> NodeItem:
    """Insert a detached Docling item under ``parent`` (body when parent is None)."""
    prov = _first_prov(item)
    if isinstance(item, TitleItem):
        return cast(
            "NodeItem",
            document.add_title(
                text=item.text or "", orig=item.orig, prov=prov, parent=parent
            ),
        )
    if isinstance(item, SectionHeaderItem):
        return cast(
            "NodeItem",
            document.add_heading(
                text=item.text or "",
                level=heading_level(item),
                orig=item.orig,
                prov=prov,
                parent=parent,
            ),
        )
    if isinstance(item, ListItem):
        list_parent: NodeItem | None = parent
        if not isinstance(parent, ListGroup):
            list_parent = document.add_list_group(parent=parent)
        return cast(
            "NodeItem",
            document.add_list_item(
                text=item.text or "",
                enumerated=bool(item.enumerated),
                marker=item.marker or "",
                orig=item.orig,
                prov=prov,
                parent=list_parent,
            ),
        )
    if isinstance(item, PictureItem):
        picture = document.add_picture(
            image=item.image,
            prov=prov,
            parent=parent,
        )
        if item.meta is not None:
            picture.meta = item.meta.model_copy(deep=True)
        if caption:
            cap = document.add_text(
                label=DocItemLabel.CAPTION,
                text=caption,
                parent=picture,
            )
            picture.captions.append(cap.get_ref())
        return cast("NodeItem", picture)
    if isinstance(item, TableItem):
        data = item.data.model_copy(deep=True)
        return cast("NodeItem", document.add_table(data=data, prov=prov, parent=parent))
    if isinstance(item, TextItem):
        return cast(
            "NodeItem",
            document.add_text(
                label=item.label,
                text=item.text or "",
                orig=item.orig,
                prov=prov,
                parent=parent,
                formatting=item.formatting,
                hyperlink=item.hyperlink,
            ),
        )
    raise TypeError(f"Unsupported Docling item: {type(item).__name__}")


def position_page(
    document: DoclingDocument,
    carry: _Carry | None = None,
    *,
    pdf: Any | None = None,
) -> tuple[list[_Placed], _Carry]:
    """Assign newspaper sort keys. Items without a box keep the previous position."""
    state = carry if carry is not None else _Carry()
    items = list(
        document.iterate_items(
            with_groups=True,
            traverse_pictures=True,
            included_content_layers={ContentLayer.BODY},
        )
    )
    lefts_by_page: dict[int, list[float]] = {}
    for item, _level in items:
        located = top_left_bbox(item, document)
        if located is None:
            continue
        page_no, bbox = located
        size = _page_size(document, page_no)
        page_w = size.width if size is not None else 0.0
        box_w = float(bbox.r) - float(bbox.l)
        if page_w and box_w >= _WIDE_ITEM_FRACTION * page_w:
            lefts_by_page.setdefault(page_no, []).append(float(bbox.l))
    centers_by_page = {
        page_no: column_centers(lefts, _page_width(document, page_no))
        for page_no, lefts in lefts_by_page.items()
    }

    placed: list[_Placed] = []
    for item, _level in items:
        idx = state.idx
        state.idx += 1
        xy = _item_page_xy(item, document)
        if xy is not None:
            page_no, top, left = xy
            centers = centers_by_page.get(page_no) or [0.0]
            col = column_index(left, centers)
            state.last_key = (page_no, col, top, left)
            state.seq = 0
            key = layout_sort_key(page_no, col, top, left, seq=0, idx=idx)
        else:
            state.seq += 1
            page_no, col, top, left = state.last_key
            key = layout_sort_key(page_no, col, top, left, seq=state.seq, idx=idx)
        if isinstance(item, GroupItem):
            continue
        if isinstance(item, PictureItem):
            image = _resolve_picture(item, document, pdf)
            if image is None:
                continue
            detached = _detach(item)
            if isinstance(detached, PictureItem):
                detached.image = image
            placed.append(_Placed(key, detached, _caption_text(item, document)))
            continue
        placed.append(_Placed(key, _detach(item)))
    return placed, state


def _page_width(document: DoclingDocument, page_no: int) -> float:
    size = _page_size(document, page_no)
    return size.width if size is not None else 0.0


class SectionAssembler:
    """Insert sections only after every page they span has been read."""

    def __init__(self, name: str = "document.pdf") -> None:
        self.document = DoclingDocument(name=name)
        self._carry = _Carry()
        self._open: list[_Placed] = []
        self._stack: list[tuple[int, NodeItem]] = []

    def add_page(self, page_doc: DoclingDocument, *, pdf: Any | None = None) -> None:
        self._copy_pages(page_doc)
        placed, self._carry = position_page(page_doc, self._carry, pdf=pdf)
        ordered = sorted(placed, key=lambda row: row.sort_key)
        for row in ordered:
            if _is_heading(row.item):
                self._flush(self._open)
                self._open = [row]
            else:
                self._open.append(row)

    def finish(self) -> DoclingDocument:
        self._flush(self._open)
        self._open = []
        return self.document

    def _copy_pages(self, page_doc: DoclingDocument) -> None:
        for page_no, page in page_doc.pages.items():
            if page_no in self.document.pages:
                continue
            self.document.add_page(
                page_no=page_no,
                size=Size(width=page.size.width, height=page.size.height),
            )

    def _current_parent(self) -> NodeItem | None:
        if not self._stack:
            return None
        return self._stack[-1][1]

    def _flush(self, items: list[_Placed]) -> None:
        if not items:
            return
        ordered = sorted(items, key=lambda row: row.sort_key)
        heading_at = next(
            (index for index, row in enumerate(ordered) if _is_heading(row.item)),
            None,
        )
        if heading_at is None:
            self._append_many(ordered, self._current_parent())
            return
        if heading_at > 0:
            self._append_many(ordered[:heading_at], self._current_parent())
        heading = ordered[heading_at]
        level = heading_level(heading.item)
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        node = append_item(
            self.document,
            heading.item,
            parent=self._current_parent(),
            caption=heading.caption,
        )
        self._stack.append((level, node))
        self._append_many(ordered[heading_at + 1 :], node)

    def _append_many(self, rows: list[_Placed], parent: NodeItem | None) -> None:
        list_group: ListGroup | None = None
        for row in rows:
            if isinstance(row.item, GroupItem):
                continue
            if isinstance(row.item, ListItem):
                if list_group is None:
                    list_group = self.document.add_list_group(parent=parent)
                append_item(
                    self.document,
                    row.item,
                    parent=list_group,
                    caption=row.caption,
                )
                continue
            list_group = None
            append_item(
                self.document,
                row.item,
                parent=parent,
                caption=row.caption,
            )


def picture_boxes(document: DoclingDocument) -> list[tuple[int, Any]]:
    boxes: list[tuple[int, Any]] = []
    for item, _level in document.iterate_items(traverse_pictures=True):
        if not isinstance(item, PictureItem):
            continue
        located = top_left_bbox(item, document)
        if located is not None:
            boxes.append(located)
    return boxes


def bbox_contained(inner: Any, outer: Any, slack: float = 12.0) -> bool:
    return (
        float(inner.l) >= float(outer.l) - slack
        and float(inner.t) >= float(outer.t) - slack
        and float(inner.r) <= float(outer.r) + slack
        and float(inner.b) <= float(outer.b) + slack
    )


__all__ = [
    "SectionAssembler",
    "append_item",
    "bbox_contained",
    "column_count",
    "column_index",
    "format_bbox",
    "heading_level",
    "layout_sort_key",
    "picture_boxes",
    "top_left_bbox",
]
