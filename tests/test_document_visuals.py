"""Optional image summaries replace picture nodes before markdown."""

from __future__ import annotations

import pytest
from docling_core.types.doc.document import DoclingDocument

from pdf_extractor_ai.image_summarizer import SectionVisual, summarize_images
from pdf_extractor_ai.markdown import to_markdown
from tests.support import add_heading, add_picture, add_text, assemble, new_page


class _OkModel:
    def __init__(self) -> None:
        self.calls = 0
        self.titles: list[str] = []

    async def describe_section(
        self,
        *,
        section_title: str,
        section_text: str,
        visuals: list[SectionVisual],
    ) -> dict[str, str]:
        self.calls += 1
        self.titles.append(section_title)
        assert visuals[0].visual_id == "s0-v0"
        assert "Chart" in section_text or section_title == "Charts"
        return {"s0-v0": "Cereal production line chart."}


class _FailModel:
    async def describe_section(
        self,
        *,
        section_title: str,
        section_text: str,
        visuals: list[SectionVisual],
    ) -> dict[str, str]:
        raise RuntimeError("boom")


class _SilentModel:
    def __init__(self) -> None:
        self.calls = 0

    async def describe_section(
        self,
        *,
        section_title: str,
        section_text: str,
        visuals: list[SectionVisual],
    ) -> dict[str, str]:
        self.calls += 1
        return {}


def _chart_document() -> DoclingDocument:
    page = new_page(1)
    add_heading(page, 1, "Charts", 1, 30, 20)
    add_text(page, 1, "Chart context", 30, 60, 210, 90)
    add_picture(page, 1, 40, 120, 280, 360, size=(160, 140))
    return assemble(page)


@pytest.mark.asyncio
async def test_summarize_skips_sections_without_pictures() -> None:
    page = new_page(1)
    add_heading(page, 1, "Summary", 1, 30, 20)
    add_text(page, 1, "Plain text only.", 30, 60, 210, 90)
    model = _SilentModel()
    result = await summarize_images(
        assemble(page), model, model_name="gemini/gemini-3.7-flash"
    )
    assert model.calls == 0
    assert "Plain text only." in to_markdown(result)


@pytest.mark.asyncio
async def test_summarize_leaves_picture_when_model_fails() -> None:
    source = _chart_document()
    result = await summarize_images(
        source, _FailModel(), model_name="gemini/gemini-3.7-flash"
    )
    assert result.pictures
    markdown = to_markdown(result)
    assert "<!-- image -->" in markdown
    assert "[Summarized Image:" not in markdown


@pytest.mark.asyncio
async def test_summarize_replaces_picture_before_markdown() -> None:
    source = _chart_document()
    model = _OkModel()
    result = await summarize_images(source, model, model_name="gemini/gemini-3.7-flash")
    assert result is not source
    assert source.pictures
    assert result.pictures == []
    markdown = to_markdown(result)
    assert "[Summarized Image: gemini/gemini-3.7-flash]" in markdown
    assert "page: 1" in markdown
    assert "bounding box:" in markdown
    assert "Summary:\nCereal production line chart." in markdown
    assert "<!-- image -->" not in markdown
    assert model.calls == 1
    assert model.titles == ["Charts"]
