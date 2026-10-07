"""Worker pool: private converters, one page at a time, shared queue."""

from __future__ import annotations

import io
import threading
from concurrent.futures import ThreadPoolExecutor

import pypdfium2 as pdfium
import pytest
from docling_core.types.doc.document import DoclingDocument

from pdf_extractor_ai.extractor import PdfExtractor
from tests.support import add_picture, add_text, new_page


class _Result:
    def __init__(self, document: DoclingDocument) -> None:
        self.document = document


class _Converter:
    def __init__(self, do_ocr: bool, pages: dict[int, DoclingDocument]) -> None:
        self.do_ocr = do_ocr
        self.ident = threading.get_ident()
        self.pages = pages
        self.calls: list[tuple[int, int]] = []
        self.failures = 0

    def convert(self, source: object, page_range: tuple[int, int] = (1, 1)) -> _Result:
        self.calls.append(page_range)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("boom")
        return _Result(self.pages[page_range[0]])


def _pdf(pages: int) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _index in range(pages):
        pdf.new_page(612, 792)
    buf = io.BytesIO()
    pdf.save(buf)
    pdf.close()
    return buf.getvalue()


def _page(page_no: int, text: str) -> DoclingDocument:
    document = new_page(page_no)
    add_text(document, page_no, text, 30, 40, 210, 80)
    return document


def test_extract_converts_one_page_at_a_time() -> None:
    pages = {1: _page(1, "page-1"), 2: _page(2, "page-2")}
    created: list[_Converter] = []
    main = threading.get_ident()

    def factory(do_ocr: bool) -> _Converter:
        converter = _Converter(do_ocr, pages)
        created.append(converter)
        return converter

    extractor = PdfExtractor(num_workers=1, converter_factory=factory)
    try:
        document = extractor.extract(_pdf(2), name="sample.pdf")
    finally:
        extractor.shutdown()
    assert isinstance(document, DoclingDocument)
    assert created[0].ident != main
    assert created[0].do_ocr is False
    assert created[0].calls == [(1, 1), (2, 2)]
    texts = [item.text for item in document.texts]
    assert texts == ["page-1", "page-2"]


def test_max_pages_stops_early() -> None:
    pages = {1: _page(1, "only"), 2: _page(2, "skipped")}
    created: list[_Converter] = []

    def factory(do_ocr: bool) -> _Converter:
        converter = _Converter(do_ocr, pages)
        created.append(converter)
        return converter

    with PdfExtractor(converter_factory=factory) as extractor:
        document = extractor.extract(_pdf(2), max_pages=1)
    assert created[0].calls == [(1, 1)]
    assert [item.text for item in document.texts] == ["only"]


def test_empty_pdf_raises() -> None:
    def factory(do_ocr: bool) -> _Converter:
        return _Converter(do_ocr, {})

    with (
        PdfExtractor(converter_factory=factory) as extractor,
        pytest.raises(ValueError, match="no pages"),
    ):
        extractor.extract(b"", name="empty.pdf")


def test_default_extract_does_not_request_ocr_and_caches_by_flag() -> None:
    page = {1: _page(1, "body")}
    flags: list[bool] = []
    converters: list[_Converter] = []

    def factory(do_ocr: bool) -> _Converter:
        flags.append(do_ocr)
        converter = _Converter(do_ocr, page)
        converters.append(converter)
        return converter

    with PdfExtractor(converter_factory=factory) as extractor:
        extractor.extract(_pdf(1))
        extractor.extract(_pdf(1), do_ocr=True)
        extractor.extract(_pdf(1))
    assert flags == [False, True]
    assert len({id(converter) for converter in converters}) == 2
    assert converters[0].calls == [(1, 1), (1, 1)]
    assert converters[1].calls == [(1, 1)]


def test_workers_do_not_share_converters() -> None:
    page = {1: _page(1, "job")}
    barrier = threading.Barrier(2)
    created: list[_Converter] = []

    def factory(do_ocr: bool) -> _Converter:
        converter = _Converter(do_ocr, page)
        created.append(converter)
        original = converter.convert

        def convert(source: object, page_range: tuple[int, int] = (1, 1)) -> _Result:
            assert threading.get_ident() == converter.ident
            barrier.wait(timeout=5)
            return original(source, page_range=page_range)

        converter.convert = convert  # type: ignore[method-assign]
        return converter

    extractor = PdfExtractor(num_workers=2, converter_factory=factory)
    try:
        pdf = _pdf(1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(extractor.extract, pdf, name="a.pdf")
            second = pool.submit(extractor.extract, pdf, name="b.pdf")
            assert first.result(timeout=10).texts[0].text == "job"
            assert second.result(timeout=10).texts[0].text == "job"
    finally:
        extractor.shutdown()
        assert all(not thread.is_alive() for thread in extractor._threads)
    assert len(created) == 2
    assert created[0].ident != created[1].ident
    assert all(converter.do_ocr is False for converter in created)


def test_failed_job_does_not_stop_the_worker() -> None:
    page = {1: _page(1, "after")}
    holder: list[_Converter] = []

    def factory(do_ocr: bool) -> _Converter:
        converter = _Converter(do_ocr, page)
        converter.failures = 1
        holder.append(converter)
        return converter

    with PdfExtractor(converter_factory=factory) as extractor:
        with pytest.raises(RuntimeError, match="boom"):
            extractor.extract(_pdf(1))
        document = extractor.extract(_pdf(1))
    assert document.texts[0].text == "after"


def test_extract_leaves_pictures_for_a_later_summary_step() -> None:
    page = new_page(1)
    add_picture(page, 1, 40, 40, 240, 240, size=(100, 100))
    add_text(page, 1, "caption", 30, 300, 210, 340)

    def factory(do_ocr: bool) -> _Converter:
        return _Converter(do_ocr, {1: page})

    with PdfExtractor(converter_factory=factory) as extractor:
        document = extractor.extract(_pdf(1))
    assert document.pictures
    assert document.pictures[0].image is not None


@pytest.mark.asyncio
async def test_extract_async_returns_the_document() -> None:
    def factory(do_ocr: bool) -> _Converter:
        return _Converter(do_ocr, {1: _page(1, "async")})

    async with _async_extractor(factory) as extractor:
        document = await extractor.extract_async(_pdf(1))
    assert document.texts[0].text == "async"


class _async_extractor:
    def __init__(self, factory: object) -> None:
        self._factory = factory
        self._extractor: PdfExtractor | None = None

    async def __aenter__(self) -> PdfExtractor:
        self._extractor = PdfExtractor(converter_factory=self._factory)  # type: ignore[arg-type]
        return self._extractor

    async def __aexit__(self, *_args: object) -> None:
        if self._extractor is not None:
            self._extractor.shutdown()


def test_conversion_error_retries_only_failed_page_with_pdfium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docling.exceptions import ConversionError

    pages = {1: _page(1, "first"), 2: _page(2, "recovered"), 3: _page(3, "last")}
    primary = _Converter(False, pages)
    fallback = _Converter(False, pages)
    original = primary.convert

    def convert(source: object, page_range: tuple[int, int] = (1, 1)) -> _Result:
        if page_range == (2, 2):
            primary.calls.append(page_range)
            raise ConversionError("Page 2 failed to parse")
        return original(source, page_range)

    primary.convert = convert  # type: ignore[method-assign]
    fallback_calls: list[tuple[bool, bool]] = []

    def build(do_ocr: bool, *, use_pdfium: bool = False) -> _Converter:
        fallback_calls.append((do_ocr, use_pdfium))
        return fallback

    monkeypatch.setattr("pdf_extractor_ai.extractor.build_converter", build)
    with PdfExtractor(converter_factory=lambda _: primary) as extractor:
        document = extractor.extract(_pdf(3), name="broken.pdf")
    assert primary.calls == [(1, 1), (2, 2), (3, 3)]
    assert fallback.calls == [(2, 2)]
    assert fallback_calls == [(False, True)]
    assert [item.text for item in document.texts] == ["first", "recovered", "last"]


def test_pdfium_fallback_failure_is_not_silently_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docling.exceptions import ConversionError

    class BrokenConverter:
        def convert(self, source: object, page_range: tuple[int, int]) -> _Result:
            raise ConversionError("Both backends failed")

    monkeypatch.setattr(
        "pdf_extractor_ai.extractor.build_converter",
        lambda *args, **kwargs: BrokenConverter(),
    )
    with (
        PdfExtractor(converter_factory=lambda _: BrokenConverter()) as extractor,
        pytest.raises(ConversionError, match="Both backends failed"),
    ):
        extractor.extract(_pdf(1), name="broken.pdf")
