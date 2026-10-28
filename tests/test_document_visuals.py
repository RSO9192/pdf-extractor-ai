"""Optional image summaries replace picture nodes before markdown."""

from __future__ import annotations

import pytest
from docling_core.types.doc.document import DoclingDocument
from PIL import Image

from pdf_extractor_ai.image_summarizer import SectionVisual, summarize_images
from pdf_extractor_ai.markdown import to_markdown
from pdf_extractor_ai.picture_filter import PictureFilter
from tests.support import add_heading, add_picture, add_text, assemble, new_page


class _WidthClassifier:
    """Classify test pictures by width so one document can mix kinds."""

    def __init__(self, by_width: dict[int, str] | None = None, *, fail: bool = False) -> None:
        self.by_width = by_width or {}
        self.fail = fail

    def classify(self, images: list[Image.Image]) -> list[tuple[str, float]]:
        if self.fail:
            raise RuntimeError("weights unavailable")
        return [(self.by_width.get(image.width, "bar_chart"), 0.95) for image in images]


_CHART_FILTER = PictureFilter(classifier=_WidthClassifier())


class _RecordingModel:
    def __init__(self) -> None:
        self.calls = 0
        self.visual_ids: list[list[str]] = []

    async def describe_section(
        self,
        *,
        section_title: str,
        section_text: str,
        visuals: list[SectionVisual],
    ) -> dict[str, str]:
        self.calls += 1
        self.visual_ids.append([visual.visual_id for visual in visuals])
        return {visual.visual_id: f"Chart {visual.visual_id}" for visual in visuals}


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
        assemble(page), model, model_name="gemini/gemini-3.7-flash", picture_filter=_CHART_FILTER
    )
    assert model.calls == 0
    assert "Plain text only." in to_markdown(result)


@pytest.mark.asyncio
async def test_summarize_leaves_picture_when_model_fails() -> None:
    source = _chart_document()
    result = await summarize_images(
        source, _FailModel(), model_name="gemini/gemini-3.7-flash", picture_filter=_CHART_FILTER
    )
    assert result.pictures
    markdown = to_markdown(result)
    assert "<!-- image -->" in markdown
    assert "[Summarized Image:" not in markdown


@pytest.mark.asyncio
async def test_summarize_replaces_picture_before_markdown() -> None:
    source = _chart_document()
    model = _OkModel()
    result = await summarize_images(
        source, model, model_name="gemini/gemini-3.7-flash", picture_filter=_CHART_FILTER
    )
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


def _section_with(*sizes: tuple[int, int]) -> DoclingDocument:
    page = new_page(1)
    add_heading(page, 1, "Charts", 1, 30, 20)
    add_text(page, 1, "Chart context", 30, 60, 210, 90)
    for index, size in enumerate(sizes):
        top = 120 + index * 150
        add_picture(page, 1, 40, top, 280, top + 140, size=size)
    return assemble(page)


@pytest.mark.asyncio
async def test_summarize_skips_filtered_picture() -> None:
    model = _RecordingModel()
    picture_filter = PictureFilter(classifier=_WidthClassifier({200: "logo"}))
    result = await summarize_images(
        _section_with((200, 100)), model, model_name="m", picture_filter=picture_filter
    )
    assert model.calls == 0
    assert "<!-- image -->" in to_markdown(result)


@pytest.mark.asyncio
async def test_summarize_all_skipped_section_makes_no_call() -> None:
    model = _RecordingModel()
    picture_filter = PictureFilter(classifier=_WidthClassifier({200: "icon"}))
    result = await summarize_images(
        _section_with((200, 100), (200, 100)), model, model_name="m", picture_filter=picture_filter
    )
    assert model.calls == 0
    assert len(result.pictures) == 2


@pytest.mark.asyncio
async def test_summarize_mixed_section_ids_contiguous() -> None:
    model = _RecordingModel()
    picture_filter = PictureFilter(classifier=_WidthClassifier({200: "photograph"}))
    result = await summarize_images(
        _section_with((200, 100), (300, 100), (300, 100)),
        model,
        model_name="m",
        picture_filter=picture_filter,
    )
    assert model.visual_ids == [["s0-v0", "s0-v1"]]
    markdown = to_markdown(result)
    assert len(result.pictures) == 1
    assert markdown.count("[Summarized Image: m]") == 2
    assert markdown.index("<!-- image -->") < markdown.index("[Summarized Image: m]")


@pytest.mark.asyncio
async def test_summarize_filter_disabled_sends_all() -> None:
    model = _RecordingModel()
    await summarize_images(_section_with((200, 100)), model, model_name="m", picture_filter=None)
    assert model.calls == 1


@pytest.mark.asyncio
async def test_summarize_fails_open_when_classifier_raises() -> None:
    model = _RecordingModel()
    picture_filter = PictureFilter(classifier=_WidthClassifier(fail=True))
    result = await summarize_images(
        _section_with((200, 100)), model, model_name="m", picture_filter=picture_filter
    )
    assert model.calls == 1
    assert result.pictures == []
