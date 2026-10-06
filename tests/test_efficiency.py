"""Regression checks for bounded native work and unchanged extraction outputs."""

import asyncio
import io
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from docling_core.types.doc.document import DoclingDocument

from pdf_extractor_ai.extractor import PdfExtractor
from pdf_extractor_ai.image_summarizer import summarize_images
from pdf_extractor_ai.markdown import layout_from_markdown, to_markdown
from pdf_extractor_ai.reflow import SectionAssembler
from tests.support import add_heading, add_picture, add_text, new_page
from tests.test_extractor import _pdf


class ChunkConverter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []
        self.streams: list[io.BytesIO] = []
        self.initializations: list[Any] = []

    def initialize_pipeline(self, format: Any) -> None:
        self.initializations.append(format)

    def convert(self, source: Any, page_range: tuple[int, int]) -> SimpleNamespace:
        self.calls.append(page_range)
        self.streams.append(source.stream)
        document = DoclingDocument(name=source.name)
        for page in range(page_range[0], page_range[1] + 1):
            document.add_page(page_no=page, size=new_page(page).pages[page].size)
            add_heading(document, page, f"Heading {page}", 1, 30, 40)
            add_text(document, page, f"Body {page}", 30, 80, 210, 120)
        source.stream.close()  # Docling unloads/ closes its input after each chunk.
        return SimpleNamespace(document=document)


def test_chunking_reuses_stream_and_preserves_page_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PDF_EXTRACTOR_PAGE_CHUNK_SIZE", "4")
    converter = ChunkConverter()
    with PdfExtractor(converter_factory=lambda _: converter) as extractor:
        doc = extractor.extract(_pdf(10), max_pages=9)
    assert converter.calls == [(1, 4), (5, 8), (9, 9)]
    assert len({id(stream) for stream in converter.streams}) == 1
    assert converter.streams[0].closed
    assert sorted(doc.pages) == list(range(1, 10))
    assert [text.text for text in doc.texts if text.text.startswith("Body")] == [
        f"Body {p}" for p in range(1, 10)
    ]


@pytest.mark.asyncio
async def test_streaming_markdown_keeps_headings_spans_and_no_rasters() -> None:
    converter = ChunkConverter()
    with PdfExtractor(converter_factory=lambda _: converter) as extractor:
        markdown, headings = await extractor._extract_markdown_async(
            _pdf(3), max_pages=3, name="report.pdf"
        )
    layout = layout_from_markdown(markdown, headings)
    assert [p.page for p in layout.pages] == [1, 2, 3]
    assert [s.title for s in layout.sections] == ["Heading 1", "Heading 2", "Heading 3"]
    assert not headings.pictures
    assert "Body 3" in markdown


def test_cancelled_queued_job_never_converts(monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = threading.Event(), threading.Event()
    converter = ChunkConverter()
    original = converter.convert

    def convert(*args: Any, **kwargs: Any) -> SimpleNamespace:
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(converter, "convert", convert)
    extractor = PdfExtractor(converter_factory=lambda _: converter)
    try:
        first = extractor._submit(_pdf(1), do_ocr=False, max_pages=1, name="first")
        assert entered.wait(5)
        second = extractor._submit(_pdf(1), do_ocr=False, max_pages=1, name="cancelled")
        assert second.cancel()
        release.set()
        first.result(5)
    finally:
        release.set()
        extractor.shutdown()
    assert len(converter.calls) == 1
    assert extractor._pending_bytes == 0


def test_text_only_reflow_preserves_picture_provenance_without_rendering() -> None:
    doc = new_page(1)
    add_picture(doc, 1, 40, 40, 240, 240, size=(100, 100))
    assembler = SectionAssembler()
    assembler.add_page(doc, retain_images=False)
    result = assembler.finish()
    assert len(result.pictures) == 1
    assert result.pictures[0].image is None
    assert "<!-- image -->" in to_markdown(result)


@pytest.mark.asyncio
async def test_visual_batches_are_bounded_and_input_is_not_modified() -> None:
    doc = new_page(1)
    for i in range(11):
        add_picture(doc, 1, 40, 40 + i * 5, 240, 240 + i * 5, size=(100, 100))
    active = peak = 0
    batches: list[int] = []

    class Model:
        async def describe_section(
            self, *, section_title: str, section_text: str, visuals: list[Any]
        ) -> dict[str, str]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            batches.append(len(visuals))
            await asyncio.sleep(0.01)
            active -= 1
            return {v.visual_id: "chart summary" for v in visuals}

    output = await summarize_images(doc, Model(), model_name="test", concurrency=2)
    assert max(batches) <= 4
    assert peak <= 2
    assert len(doc.pictures) == 11
    assert not output.pictures


def test_default_factory_initializes_cpu_non_ocr_pipeline_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docling.document_converter as module

    created: list[Any] = []

    class Converter(ChunkConverter):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            created.append(self)
            self.options = next(
                iter(kwargs["format_options"].values())
            ).pipeline_options

    monkeypatch.setattr(module, "DocumentConverter", Converter)
    with PdfExtractor() as extractor:
        assert created == []
        extractor.extract(_pdf(1))
        extractor.extract(_pdf(1))
    assert len(created) == 1
    assert len(created[0].initializations) == 1
    assert created[0].options.do_ocr is False
    assert created[0].options.generate_picture_images is False
    assert created[0].options.accelerator_options.device.value == "cpu"


def test_all_figures_on_a_page_share_one_native_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pypdfium2 as pdfium

    renders = 0
    original = pdfium.PdfPage.render

    def render(page: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal renders
        renders += 1
        return original(page, *args, **kwargs)

    monkeypatch.setattr(pdfium.PdfPage, "render", render)
    doc = new_page(1)
    add_picture(doc, 1, 40, 40, 180, 180, size=(100, 100))
    add_picture(doc, 1, 40, 200, 180, 340, size=(100, 100))
    pdf = pdfium.PdfDocument(_pdf(1))
    try:
        assembler = SectionAssembler()
        assembler.add_page(doc, pdf=pdf)
        result = assembler.finish()
        assert len(result.pictures) == 2
        assert renders == 1
        assert all(picture.image is not None for picture in result.pictures)
    finally:
        pdf.close()


@pytest.mark.asyncio
async def test_cancelled_byte_admission_does_not_queue_another_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()
    converter = ChunkConverter()
    original = converter.convert

    def convert(*args: Any, **kwargs: Any) -> SimpleNamespace:
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(converter, "convert", convert)
    data = _pdf(1)
    extractor = PdfExtractor(converter_factory=lambda _: converter)
    extractor._byte_limit = len(data)
    try:
        first = extractor._submit(data, do_ocr=False, max_pages=1, name="running")
        assert await asyncio.to_thread(entered.wait, 5)
        queued = asyncio.create_task(
            extractor._submit_async(data, do_ocr=False, max_pages=1, name="waiting")
        )
        await asyncio.sleep(0.03)
        assert not queued.done()
        assert extractor._pending_bytes == len(data)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        release.set()
        await asyncio.wrap_future(first)
    finally:
        release.set()
        await asyncio.to_thread(extractor.shutdown)
    assert len(converter.calls) == 1
    assert extractor._pending_bytes == 0


def test_text_only_path_filters_tiny_decorations() -> None:
    doc = new_page(1)
    add_picture(doc, 1, 10, 10, 20, 20, size=(20, 20))
    assembler = SectionAssembler()
    assembler.add_page(doc, retain_images=False)
    assert not assembler.finish().pictures
