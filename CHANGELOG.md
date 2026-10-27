# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Serialize markdown tables compactly: no alignment padding, cell whitespace runs collapsed, and dot leaders (`.....`) collapsed to ` … `. Docling padded every cell to the widest entry in its column, so a table-of-contents page read as a wide table blew the KNBS Economic Survey 2016 (first 50 pages) up to 23.7 million characters; it now converts to about 450,000. Non-table text is unchanged.

## [0.2.2] - 2026-10-22

### Fixed

- Flatten rich table cell refs when assembling multi-page documents so Docling no longer rejects scanned annexes with `Document hierarchy is inconsistent`.
- Detect image-backed PDFs and auto-enable full-page OCR so table and body text use the page image instead of a corrupt searchable overlay (for example `ACCIÓN` no longer becomes `ACC16N`).

### Changed

- Born-digital tables use `do_cell_matching=False`; scanned OCR paths keep matching and prefer OCR cells over PDF word cells.
- Optional slow annex regression test is skipped by default (`pytest -m slow`); pre-commit excludes `slow` tests.

## [0.2.1] - 2026-10-07

### Fixed

- Retry failed Docling conversion chunks with the PDFium backend, preserving page order and the existing extraction pipeline. Recoverable PDF rendering failures no longer abort the entire document; failures in both backends still raise an error.
- Add regression coverage for recovering only the failed page and surfacing unrecoverable conversion errors.

## [0.2.0] - 2026-10-06

Performance and memory release. Public `PdfExtractor`, `DoclingDocument`, page references, and non-mutating `summarize_images()` contracts stay compatible.

### Changed

- Lazy non-OCR converter construction with `initialize_pipeline(InputFormat.PDF)` on the worker’s first PDF; reuse across later jobs. Explicit OCR remains supported and is never initialized automatically.
- Default to one native conversion worker and CPU acceleration. Reuse the source stream across sequential conversion calls instead of opening a fresh full-PDF stream per page.
- Bounded page chunks (default one page) with bounded Docling stage queues, preserving physical page numbers, `max_pages`, reading order, tables, captions, and cross-page headings.
- Render each page once for figure crops; release native bitmaps and PIL images explicitly. Disable duplicate Docling picture raster generation, with an on-demand crop fallback.
- Bounded producer/consumer pipeline for visual summaries; release encoded payloads after use. Large sections are batched (ordered batches sharing section context).
- Collect garbage at document boundaries instead of after every page once resources are cleaned up.
- Markdown section slicing splits lines once, tracks chunk lengths incrementally, and indexes picture bounds by page.

### Added

- Internal web-scout text-only path that skips raster generation when visuals are unused and produces Markdown plus layout metadata incrementally.
- PDF admission limits before download, streamed downloads under the admitted budget, fewer byte copies, and skip of cancelled queued conversions.
- Efficiency regression tests, a local PDF benchmark script, and a performance report under `docs/`.

### Performance

- Full API warm conversion improved about 36% on the measured fixture; text-only warm path about 57% versus the prior image-bearing baseline. See [docs/performance.md](docs/performance.md).

## [0.1.0] - 2026-10-01

Initial package release.

### Added

- Newspaper-order reflow into one hierarchical `DoclingDocument` (page, then left-to-right column, then top to bottom), with headings as the tree and cross-page sections inserted after later pages are read.
- `PdfExtractor` worker pool over PDF bytes, per-call `do_ocr` (default off), and optional `max_pages`.
- Optional `summarize_images` with a caller-supplied vision model, grouped by parent heading.
- `to_markdown` with page banners, plus helpers for document title, page slices, low-text checks, and layout spans.
- Unit tests for reflow, extraction, summaries, and markdown; optional Gemini integration test when `GEMINI_API_KEY` is set.

[0.2.2]: https://github.com/RSO9192/pdf-extractor-ai/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/RSO9192/pdf-extractor-ai/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/RSO9192/pdf-extractor-ai/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/RSO9192/pdf-extractor-ai/releases/tag/v0.1.0
