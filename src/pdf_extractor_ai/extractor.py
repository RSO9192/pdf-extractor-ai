"""Page-by-page PDF extraction on a pool of private Docling converters."""

from __future__ import annotations

import asyncio
import gc
import io
import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Self

from docling_core.types.doc.document import DoclingDocument

from pdf_extractor_ai.reflow import SectionAssembler

logger = logging.getLogger(__name__)


def build_converter(do_ocr: bool) -> Any:
    """Build a Docling converter. Call this inside the worker that will use it."""
    from docling.datamodel.accelerator_options import (
        AcceleratorDevice,
        AcceleratorOptions,
    )
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions(
        do_ocr=do_ocr,
        do_table_structure=True,
        generate_picture_images=True,
        images_scale=2.0,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )


@dataclass(slots=True)
class _Job:
    pdf_bytes: bytes
    do_ocr: bool
    max_pages: int | None
    name: str
    future: Future[DoclingDocument]


class PdfExtractor:
    """Extract PDFs from a shared queue. Each worker owns its Docling converters."""

    def __init__(
        self,
        num_workers: int = 1,
        *,
        converter_factory: Callable[[bool], Any] | None = None,
    ) -> None:
        if num_workers < 1:
            raise ValueError("num_workers must be at least 1")
        self._factory = converter_factory or build_converter
        self._queue: queue.Queue[_Job | None] = queue.Queue()
        self._shutdown_lock = threading.Lock()
        self._stopped = False
        self._threads = [
            threading.Thread(target=self._run, name=f"pdf-extract-{index}", daemon=True)
            for index in range(num_workers)
        ]
        for thread in self._threads:
            thread.start()

    def extract(
        self,
        pdf_bytes: bytes,
        *,
        do_ocr: bool = False,
        max_pages: int | None = None,
        name: str = "document.pdf",
    ) -> DoclingDocument:
        return self._submit(
            pdf_bytes,
            do_ocr=do_ocr,
            max_pages=max_pages,
            name=name,
        ).result()

    async def extract_async(
        self,
        pdf_bytes: bytes,
        *,
        do_ocr: bool = False,
        max_pages: int | None = None,
        name: str = "document.pdf",
    ) -> DoclingDocument:
        future = self._submit(
            pdf_bytes,
            do_ocr=do_ocr,
            max_pages=max_pages,
            name=name,
        )
        return await asyncio.wrap_future(future)

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._stopped:
                return
            self._stopped = True
            for _thread in self._threads:
                self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=30)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.shutdown()

    def _submit(
        self,
        pdf_bytes: bytes,
        *,
        do_ocr: bool,
        max_pages: int | None,
        name: str,
    ) -> Future[DoclingDocument]:
        if self._stopped:
            raise RuntimeError("PdfExtractor is shut down")
        future: Future[DoclingDocument] = Future()
        self._queue.put(
            _Job(
                pdf_bytes=pdf_bytes,
                do_ocr=do_ocr,
                max_pages=max_pages,
                name=name,
                future=future,
            )
        )
        return future

    def _run(self) -> None:
        converters: dict[bool, Any] = {}
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                converter = converters.get(job.do_ocr)
                if converter is None:
                    converter = self._factory(job.do_ocr)
                    converters[job.do_ocr] = converter
                job.future.set_result(_convert_pdf(converter, job))
            except Exception as exc:
                logger.exception("PDF extract failed for %s", job.name)
                if not job.future.done():
                    job.future.set_exception(exc)


def _convert_pdf(converter: Any, job: _Job) -> DoclingDocument:
    import pypdfium2 as pdfium
    from docling_core.types.io import DocumentStream

    try:
        pdf = pdfium.PdfDocument(job.pdf_bytes)
    except Exception as exc:
        if type(exc).__name__ != "PdfiumError":
            raise
        raise ValueError(f"PDF has no pages: {job.name}") from exc
    try:
        page_count = len(pdf)
        if page_count < 1:
            raise ValueError(f"PDF has no pages: {job.name}")
        limit = page_count if job.max_pages is None else min(page_count, job.max_pages)
        if limit < 1:
            raise ValueError(f"PDF has no pages: {job.name}")
        assembler = SectionAssembler(name=job.name)
        for page_no in range(1, limit + 1):
            source = DocumentStream(name=job.name, stream=io.BytesIO(job.pdf_bytes))
            result = converter.convert(source, page_range=(page_no, page_no))
            try:
                assembler.add_page(result.document, pdf=pdf)
            finally:
                del result
                gc.collect()
        return assembler.finish()
    finally:
        pdf.close()
