"""Tables serialize to compact markdown rows without alignment padding."""

from __future__ import annotations

import re
from typing import Any

import pytest
from docling_core.transforms.serializer.markdown import MarkdownTableSerializer
from docling_core.types.doc.items.table.table_data import TableCell, TableData

from pdf_extractor_ai import markdown as markdown_module
from pdf_extractor_ai.markdown import to_markdown
from tests.support import add_text, assemble, new_page, provenance

_TOC_CELL = (
    "Table 0: Key Economic and Social Indicators 2011 - 2015"
    + "." * 90
    + "  9 Table 1.1: Key   Economic Indicators, 2013-2017"
    + ". " * 12
    + "12"
)
_SEPARATOR_RE = re.compile(r"^\|(?: :?---:? \|)+$")


def _table_data(rows: list[list[str]]) -> TableData:
    cells = [
        TableCell(
            start_row_offset_idx=row_idx,
            end_row_offset_idx=row_idx + 1,
            start_col_offset_idx=col_idx,
            end_col_offset_idx=col_idx + 1,
            text=text,
            column_header=row_idx == 0,
        )
        for row_idx, row in enumerate(rows)
        for col_idx, text in enumerate(row)
    ]
    return TableData(num_rows=len(rows), num_cols=len(rows[0]), table_cells=cells)


def _table_lines(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.startswith("|")]


def test_table_rows_are_compact_with_collapsed_leaders() -> None:
    page = new_page(1)
    page.add_table(
        data=_table_data(
            [
                ["Contents", "Page"],
                [_TOC_CELL, "9"],
                ["  Maize   production ", "-1,234.5"],
                ["Wheat", "1\u00a0234"],
                ["Rice", "12\u00a0345,6"],
            ]
        ),
        prov=provenance(1, 40, 80, 500, 400),
    )
    markdown = to_markdown(assemble(page))
    lines = _table_lines(markdown)

    assert lines[0] == "| Contents | Page |"
    assert _SEPARATOR_RE.match(lines[1])
    assert lines[2] == (
        "| Table 0: Key Economic and Social Indicators 2011 - 2015 … 9 "
        "Table 1.1: Key Economic Indicators, 2013-2017 … 12 | 9 |"
    )
    assert lines[3] == "| Maize production | -1,234.5 |"
    assert lines[4] == "| Wheat | 1\u00a0234 |"
    assert lines[5] == "| Rice | 12\u00a0345,6 |"
    assert len(lines) == 6
    assert "   " not in markdown
    assert "-" * 20 not in markdown


def test_non_table_text_keeps_its_spacing_and_dots() -> None:
    text = "Contents.......... 9   see   annex"
    page = new_page(1)
    add_text(page, 1, text, 30, 40, 210, 70)
    page.add_table(
        data=_table_data([["Crop", "Bags"], ["Maize", "42.5"]]),
        prov=provenance(1, 40, 80, 500, 400),
    )
    markdown = to_markdown(assemble(page))

    assert text in markdown.splitlines()
    assert _table_lines(markdown)[2] == "| Maize | 42.5 |"


def _crop_table_markdown() -> str:
    page = new_page(1)
    page.add_table(
        data=_table_data([["Crop", "Bags"], ["Maize", "42.5"]]),
        prov=provenance(1, 40, 80, 500, 400),
    )
    return to_markdown(assemble(page))


def test_docling_compact_table_hook_is_still_a_staticmethod() -> None:
    hook = MarkdownTableSerializer.__dict__.get("_compact_table")
    assert isinstance(hook, staticmethod), (
        "to_markdown overrides Docling's private MarkdownTableSerializer."
        "_compact_table(table_text) hook to compact tables; Docling changed it, "
        "so re-check _CompactTableSerializer against the new hook."
    )


def test_unexpected_hook_call_delegates_to_docling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def changed_hook(table_text: str, *, align: bool = True) -> str:
        return f"docling:{align}:{table_text}"

    monkeypatch.setattr(
        MarkdownTableSerializer, "_compact_table", staticmethod(changed_hook)
    )
    serializer = markdown_module._CompactTableSerializer()

    result = serializer._compact_table("| Crop | Bags |", align=False)

    assert result == "docling:False:| Crop | Bags |"


def test_failing_compaction_keeps_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: Any) -> str:
        raise TypeError("unexpected hook arguments")

    monkeypatch.setattr(markdown_module, "_compact_cell", broken)

    lines = _table_lines(_crop_table_markdown())

    assert lines[0] == "| Crop | Bags |"
    assert lines[2] == "| Maize | 42.5 |"
