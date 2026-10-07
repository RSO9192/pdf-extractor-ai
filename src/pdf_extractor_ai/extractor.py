"""Page-by-page PDF extraction on a pool of private Docling converters."""

from __future__ import annotations

import asyncio
import gc
import io
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from functools import partial
from typing import Any, Self, cast

from docling_core.types.doc.document import DoclingDocument

from pdf_extractor_ai.reflow import SectionAssembler

logger = logging.getLogger(__name__)


class _ReusableStream(io.BytesIO):
    # Docling unloads each conversion backend and closes its source. Ownership
    # remains with this worker until every bounded chunk has been converted.
    def close(self) -> None:
        pass

    def release(self) -> None:
        super().close()


def build_converter(do_ocr: bool, *, use_pdfium: bool = False) -> Any:
    """Build a Docling converter. Call this inside the worker that will use it."""
    from docling.datamodel.accelerator_options import (
        AcceleratorDevice,
        AcceleratorOptions,
    )
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    started = time.perf_counter()
    pipeline_options = PdfPipelineOptions(
        do_ocr=do_ocr,
        do_table_structure=True,
        generate_picture_images=False,
        queue_max_size=8,
        layout_batch_size=4,
        table_batch_size=4,
        images_scale=2.0,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
    )
    format_option = PdfFormatOption(pipeline_options=pipeline_options)
    if use_pdfium:
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

        format_option.backend = PyPdfiumDocumentBackend
    converter = DocumentConverter(format_options={InputFormat.PDF: format_option})

    converter.initialize_pipeline(InputFormat.PDF)
    logger.debug(
        "[pdf-pipeline-timing] ocr=%s seconds=%.3f",
        do_ocr,
        time.perf_counter() - started,
    )
    return converter


@dataclass(slots=True)
class _Job:
    pdf_bytes: bytes
    do_ocr: bool
    max_pages: int | None
    name: str
    future: Future[Any]
    markdown_only: bool = False
    cancelled: threading.Event | None = None


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
        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=4)
        self._admission = threading.Condition()
        self._pending_bytes = 0
        self._byte_limit = 64 * 1024 * 1024
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
        return cast(
            DoclingDocument,
            self._submit(
                pdf_bytes,
                do_ocr=do_ocr,
                max_pages=max_pages,
                name=name,
            ).result(),
        )

    async def extract_async(
        self,
        pdf_bytes: bytes,
        *,
        do_ocr: bool = False,
        max_pages: int | None = None,
        name: str = "document.pdf",
    ) -> DoclingDocument:
        future = await self._submit_async(
            pdf_bytes, do_ocr=do_ocr, max_pages=max_pages, name=name
        )
        return await asyncio.wrap_future(future)

    async def _extract_markdown_async(
        self, pdf_bytes: bytes, *, max_pages: int | None, name: str
    ) -> tuple[str, DoclingDocument]:
        future = await self._submit_async(
            pdf_bytes, do_ocr=False, max_pages=max_pages, name=name, markdown_only=True
        )
        return await asyncio.wrap_future(future)

    async def _submit_async(self, pdf_bytes: bytes, **kwargs: Any) -> Future[Any]:
        cancelled = threading.Event()
        submission = asyncio.get_running_loop().run_in_executor(
            None, partial(self._submit, pdf_bytes, cancelled=cancelled, **kwargs)
        )
        try:
            return await asyncio.shield(submission)
        except asyncio.CancelledError:
            cancelled.set()
            with self._admission:
                self._admission.notify_all()

            def discard(done: Any) -> None:
                if not done.cancelled() and done.exception() is None:
                    done.result().cancel()

            submission.add_done_callback(discard)
            raise

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._stopped:
                return
            self._stopped = True
            with self._admission:
                self._admission.notify_all()
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
        markdown_only: bool = False,
        cancelled: threading.Event | None = None,
    ) -> Future[Any]:
        if self._stopped:
            raise RuntimeError("PdfExtractor is shut down")
        with self._admission:
            while (
                not self._stopped
                and not (cancelled and cancelled.is_set())
                and self._pending_bytes
                and (self._pending_bytes + len(pdf_bytes) > self._byte_limit)
            ):
                self._admission.wait()
            if self._stopped or (cancelled and cancelled.is_set()):
                raise RuntimeError("PdfExtractor submission stopped")
            self._pending_bytes += len(pdf_bytes)
        future: Future[Any] = Future()
        with self._shutdown_lock:
            if self._stopped:
                with self._admission:
                    self._pending_bytes -= len(pdf_bytes)
                    self._admission.notify_all()
                raise RuntimeError("PdfExtractor is shut down")
            self._queue.put(
                _Job(
                    pdf_bytes=pdf_bytes,
                    do_ocr=do_ocr,
                    max_pages=max_pages,
                    name=name,
                    future=future,
                    markdown_only=markdown_only,
                    cancelled=cancelled,
                )
            )
        return future

    def _run(self) -> None:
        converters: dict[bool, Any] = {}
        while True:
            job = self._queue.get()
            if job is None:
                return
            started = time.perf_counter()
            try:
                if job.cancelled and job.cancelled.is_set():
                    job.future.cancel()
                if not job.future.set_running_or_notify_cancel():
                    continue
                converter = converters.get(job.do_ocr)
                if converter is None:
                    converter = self._factory(job.do_ocr)
                    converters[job.do_ocr] = converter
                job.future.set_result(_convert_pdf(converter, job))
            except Exception as exc:
                logger.exception("PDF extract failed for %s", job.name)
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                with self._admission:
                    self._pending_bytes -= len(job.pdf_bytes)
                    self._admission.notify_all()
                logger.debug(
                    "[pdf-timing] name=%s seconds=%.3f",
                    job.name,
                    time.perf_counter() - started,
                )
                del job
                gc.collect()


def _convert_pdf(converter: Any, job: _Job) -> Any:
    import pypdfium2 as pdfium
    from docling.exceptions import ConversionError
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
        from docling_core.types.doc.items.text import SectionHeaderItem, TitleItem

        from pdf_extractor_ai.markdown import to_markdown
        from pdf_extractor_ai.reflow import append_item

        assembler = SectionAssembler(name=job.name)
        markdown_parts: list[str] = []
        headings = DoclingDocument(name=job.name)
        chunk_size = max(
            1, min(8, int(os.getenv("PDF_EXTRACTOR_PAGE_CHUNK_SIZE", "1")))
        )
        # Test/caller supplied converters keep the original page-at-a-time contract.
        if not hasattr(converter, "initialize_pipeline"):
            chunk_size = 1
        stream = _ReusableStream(job.pdf_bytes)
        fallback = None
        try:
            source = DocumentStream(name=job.name, stream=stream)
            for start in range(1, limit + 1, chunk_size):
                stream.seek(0)
                page_range = (start, min(limit, start + chunk_size - 1))
                try:
                    result = converter.convert(source, page_range=page_range)
                except ConversionError:
                    logger.warning(
                        "Retrying PDF chunk with PDFium backend: name=%s pages=%s",
                        job.name,
                        page_range,
                        exc_info=True,
                    )
                    if fallback is None:
                        fallback = build_converter(job.do_ocr, use_pdfium=True)
                    stream.seek(0)
                    result = fallback.convert(source, page_range=page_range)
                try:
                    if job.markdown_only:
                        # Serialize one bounded chunk, retaining only headings for layout.
                        chunk = SectionAssembler(name=job.name)
                        chunk.add_page(result.document, retain_images=False)
                        doc = chunk.finish()
                        markdown_parts.append(to_markdown(doc))
                        for number, page in doc.pages.items():
                            headings.add_page(page_no=number, size=page.size)
                        for item, _ in doc.iterate_items():
                            if isinstance(item, (TitleItem, SectionHeaderItem)):
                                append_item(headings, item)
                        del doc, chunk
                    else:
                        assembler.add_page(result.document, pdf=pdf)
                finally:
                    del result
        finally:
            stream.release()
        if job.markdown_only:
            return "\n\n".join(markdown_parts), headings
        return assembler.finish()
    finally:
        pdf.close()
